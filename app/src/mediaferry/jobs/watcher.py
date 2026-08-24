"""接続中ボリュームの監視と自動取り込み（§9.2 / §12.1）.

`JobRunner` と並ぶ 2 本目の長寿命タスク。責務は 3 つだけ。

1. `list_volumes` を一定間隔で呼び、**観測トークンが変わったときだけ**
   `VolumeService.refresh()` を回す
2. **毎 tick**、対象と判定できた接続に `scan` を積み、`AUTO_IMPORT=trusted` の
   とき条件を満たす接続にはさらに `import` と `detect_groups` を積む
3. 消えた接続に紐づく**未実行**のジョブを無効化する

**2 を 1 の門の内側に入れてはいけない。** 信頼登録は `volume_instance.trusted_at`
を `UPDATE` するだけで mountd の指紋を動かさないので、カードを挿したまま画面で
承認しても観測トークンは変わらない。門の内側で判定すると、承認しても自動取り込みが
始まらない —— §12.1 の「一度承認すれば以後は挿すだけ」が成立しなくなる。同じことが
`AUTO_IMPORT` の変更やプロファイルの編集でも起きる。**「利用者が DB を変えた」ことは
観測トークンには現れない。**

**この watcher は 1 つのスコープを丸ごと持つ**（専用の DB 接続・`ProfileRegistry`・
`VolumeService`・`BrokerClient`）。`VolumeService.refresh()` は自分の
`BrokerClient` を使うので、API 側の `VolumeService` を借りると、停止のために
専用ソケットを閉じても黙り込んだ `refresh` は止まらない。かといって API 側と
DB 接続だけを共有すると、1 本の接続を 2 つの `RLock` で同時に使うことになる。
競合は SQLite の WAL と `BEGIN IMMEDIATE` で解決する。

**ジョブの handle は API 側の `VolumeService` にしかない。** こちらは判定しか
行わないので、停止で一式を閉じても走っている取り込みには触れない。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from ..clock import now_iso
from ..db.connection import Database, immediate
from ..db.jobs import JobStore
from ..db.profiles import ProfileRegistry
from ..settings import SettingsService
from .volumes import VolumeService

logger = logging.getLogger(__name__)

# 「空集合を観測した」。**「まだ観測していない」（None）と必ず区別する。**
# 同一視すると、前回の停止時に live のまま残った presence があるとき、空で
# 起動した最初の tick が「変化なし」と判定して detach_absent を飛ばし、
# 抜けているカードに自動取り込みを積む。
EMPTY = ("", -1)

# 積んでよい接続を DB の現在値から組み直す。**排他区間の中で読む。**
# 外で読むと、読んだ後・積む前に detach / archive / 信頼解除が commit されうる。
CANDIDATES = """
    SELECT p.id AS presence_id, p.volume_instance_id, p.broker_epoch, p.generation,
           p.major, p.minor, v.fs_uuid, v.profile_id, v.profile_revision_id
      FROM volume_presence p
      JOIN volume_instance v ON v.id = p.volume_instance_id
      -- プロファイルの現在性も同じ排他区間で確かめる。volume_instance の
      -- profile_id / profile_revision_id は「前回の判定の写し」でしかない。
      JOIN device_profile d ON d.id = v.profile_id
     WHERE p.detached_at IS NULL
       AND p.auto_import_at IS NULL
       AND v.trusted_at IS NOT NULL
       AND v.identity_confidence = 'high'
       AND v.provisional = 0
       AND d.archived_at IS NULL
       AND v.profile_revision_id = d.current_revision_id
     ORDER BY p.attached_at, p.id
"""

# 数えてよい接続。**取り込みより広い** —— 信頼も確度も要らない。
# プロファイルが決まっていること（`scan.roots` を読むため）だけが条件。
TO_COUNT = """
    SELECT p.id AS presence_id, p.volume_instance_id, p.broker_epoch, p.generation,
           p.major, p.minor, v.fs_uuid, v.profile_id, v.profile_revision_id
      FROM volume_presence p
      JOIN volume_instance v ON v.id = p.volume_instance_id
      JOIN device_profile d ON d.id = v.profile_id
     WHERE p.detached_at IS NULL
       AND p.auto_scan_at IS NULL
       AND d.archived_at IS NULL
       AND v.profile_revision_id = d.current_revision_id
     ORDER BY p.attached_at, p.id
"""


class VolumeWatcher:
    def __init__(
        self,
        database: Database,
        env: dict[str, str],
        client: Any,  # noqa: ANN401 - BrokerClient（差し替え可能にしておく）
        poll_interval: float = 5.0,
    ) -> None:
        # **専用の接続。** API 側と共有すると、1 本の接続を 2 つのロックで
        # 同時に使うことになる（§3「接続はスコープごとに 1 本」）。
        self._conn = database.connect()
        self._env = env
        self._client = client
        self._registry = ProfileRegistry(self._conn)
        self._volumes = VolumeService(self._conn, self._registry, client)
        self._poll_interval = poll_interval
        self._stopping = asyncio.Event()
        # None は「まだ観測していない」。EMPTY（空集合）と区別する。
        self._seen: tuple | None = None

    @property
    def observed(self) -> bool:
        """1 度でも観測したか. 空集合の観測も「観測した」に含む."""
        return self._seen is not None

    # ------------------------------------------------------------------
    def tick(self) -> list[str]:
        """1 周ぶん回す. 積んだジョブ id を返す.

        **順序に意味がある。** 判定（`refresh`）を先に済ませてから積む。
        逆にすると、挿した直後の tick が「前回の判定」で積むことになる。
        """
        token = self._token()
        if token != self._seen:
            self._seen = token
            self._volumes.refresh()
        self._invalidate_gone()
        return self._enqueue_ready()

    def _token(self) -> tuple:
        """門の入力. 観測トークンとプロファイルの現行版の指紋.

        **`generation` と `broker_epoch` は `VolumeInfo` の中にしかない**
        （`mountd/server.py::_do_list` は volumes を返すだけ）。最後の 1 枚を
        抜くと `volumes: []` になり読む場所が無いので、空を番兵として扱う。

        プロファイルの指紋も入れるのは、`require` を変えても mountd の観測は
        動かないため。編集・複製・archive のどれでもこの値が動く。
        """
        volumes = self._client.list_volumes()
        observation = (volumes[0].broker_epoch, volumes[0].generation) if volumes else EMPTY
        profiles = tuple(
            sorted((ref.profile_id, ref.revision_id) for ref in self._registry.active())
        )
        return (observation, profiles)

    # ------------------------------------------------------------------
    def _enqueue_ready(self) -> list[str]:
        """この周で積んだものを返す.

        **記録は積み終わってから、どの経路でも同じ数だけ出す。** 積む本体は
        途中で返る（`AUTO_IMPORT` が `trusted` でなければ `scan` だけで終わる）
        ので、記録をそちらに置くと経路によって落ちる。
        """
        jobs = self._enqueue_in_one_transaction()
        for job_id in jobs:
            logger.info("自動で積んだ: %s", job_id)
        return jobs

    def _enqueue_in_one_transaction(self) -> list[str]:
        jobs: list[str] = []
        store = JobStore(self._conn)
        with immediate(self._conn):
            # **「積んでよいか」の入力は全部、この排他区間の中で読む。**
            # AUTO_IMPORT は Tier.RUNTIME なので起動時のスナップショットを見ては
            # いけないが、外で読むのも同じ穴 —— 読んだ後・積む前に別接続の
            # `PUT /settings` が `off` を commit できてしまう（§12.1）。
            #
            # **数えるのは設定によらない。** 先に積むので、続けて積まれる
            # `import` は数え終わった行を読む（ジョブは 1 本ずつ直列に走る）。
            for row in self._conn.execute(TO_COUNT).fetchall():
                marked = self._conn.execute(
                    "UPDATE volume_presence SET auto_scan_at = ?"
                    " WHERE id = ? AND auto_scan_at IS NULL AND detached_at IS NULL",
                    (now_iso(), row["presence_id"]),
                ).rowcount
                if marked:
                    jobs.append(store.enqueue("scan", _params(row)))
            if SettingsService(self._conn, self._env).snapshot().auto_import != "trusted":
                return jobs
            for row in self._conn.execute(CANDIDATES).fetchall():
                # **印を付けるのと同じ条件を、同じトランザクションの中で取る。**
                # SQLite に行ロックは無いので、更新できた側だけが実行者になる。
                # 印付けと enqueue が原子的に成立するか、両方 rollback される。
                marked = self._conn.execute(
                    "UPDATE volume_presence SET auto_import_at = ?"
                    " WHERE id = ? AND auto_import_at IS NULL AND detached_at IS NULL",
                    (now_iso(), row["presence_id"]),
                ).rowcount
                if marked:
                    jobs.append(store.enqueue("import", _params(row)))
                    # **探すところまでやる。** 取り込んだだけでは、ホームの
                    # 「つなぐ」は出ない（現行の結合候補の数から導くため）。
                    jobs.append(
                        store.enqueue(
                            "detect_groups",
                            {
                                "profile_id": row["profile_id"],
                                "profile_revision_id": row["profile_revision_id"],
                            },
                        )
                    )
        return jobs

    def _invalidate_gone(self) -> None:
        """消えた接続に紐づく、まだ claim されていないジョブを畳む（§9.2）.

        **走っているジョブには触らない。** `expect` 検証と `StaleSelection` が
        既に守っており、ここで触ると「実行中のジョブを外から失敗させる」経路を
        新設することになる。**開いている handle にも触らない** —— `detach_absent`
        は `volume_presence` の列を更新するだけで、アンマウントではない。
        """
        with immediate(self._conn):
            self._conn.execute(
                "UPDATE job SET status = 'cancelled', error = ?, finished_at = ?"
                " WHERE status = 'queued'"
                "   AND json_extract(params_json, '$.presence_id') IN ("
                "       SELECT id FROM volume_presence WHERE detached_at IS NOT NULL)",
                ("選択したときの接続が失われた", now_iso()),
            )

    # ------------------------------------------------------------------
    async def run_forever(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.to_thread(self.tick)
            except Exception:  # noqa: BLE001 - 1 周の失敗で監視を降ろさない
                logger.exception("ボリュームの監視が 1 周失敗した")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval)

    async def stop(self) -> None:
        """降りるよう伝え、待っている `recv` を解く.

        `to_thread` の呼び出しは task の cancel では止まらず、`recv_message` に
        timeout も無い。**自分専用の接続だから閉じてよい** —— API 側と共有して
        いたら、走っている取り込みの handle 接続まで切ることになる。
        """
        self._stopping.set()
        self._client.close()

    def close(self) -> None:
        self._volumes.close_all()
        self._conn.close()


def _params(row: Any) -> dict[str, Any]:  # noqa: ANN401 - sqlite3.Row
    """`VolumeSelection.to_params()` と同じ形を DB の行から組み立てる.

    ジョブは選択した瞬間の presence を params に持つ（Phase 1 の契約）。
    `volume_instance_id` だけを渡すと、実行時に「最新の presence」を選ぶことに
    なり、抜き差しで別のカードが来ていてもブローカーの TOCTOU 検証を通る。
    """
    return {
        # VolumeObservation の欄。増やしても減らしてもいけない
        # （from_params が dataclass の fields で読み戻す）。
        "broker_epoch": row["broker_epoch"],
        "generation": row["generation"],
        "volume_key": f"{row['major']}:{row['minor']}",
        "major": row["major"],
        "minor": row["minor"],
        "fs_uuid": row["fs_uuid"],
        "volume_instance_id": row["volume_instance_id"],
        "presence_id": row["presence_id"],
        "profile_id": row["profile_id"],
        "profile_revision_id": row["profile_revision_id"],
    }
