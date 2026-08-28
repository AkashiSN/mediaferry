"""接続中ボリュームの列挙とプロファイル判定（§9.2）.

判定はボリュームごとに行う。デバイス単位ではない。記憶したプロファイルは
候補として使うが、require は必ず再検証する。記憶を無条件に信用しない。

判定のためだけに開いた dirfd は、確かめたらすぐ閉じる。取り込みのために
開くのは別の操作にする。
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from dataclasses import asdict, dataclass, fields

from ..adapters.broker_client import BrokerClient, VolumeHandle
from ..adapters.fs import DirfdTree, exists_beneath
from ..clock import now_iso
from ..core.manifest import content_manifest_digest
from ..core.profiles.matching import VolumeFacts, resolve_profile
from ..db.connection import immediate
from ..db.profiles import ProfileRegistry
from ..db.sources import (
    detach_absent,
    resolve_volume_instance,
    sync_presence,
    upsert_device,
)

# 取り込みの対象＝まだ運んでいない行。**`Importer.run` と同じ条件をここだけに持つ。**
# 2 か所に書くと、画面の「残り N 件」と実際に運ぶ件数がずれる。
PENDING_CLAUSE = "state IN ('seen', 'failed')"

# manifest に含める名前の上限。数万件のカードで全件読まない。
MANIFEST_LIMIT = 500
# 既知ファイルの残存率をどれだけ標本し、どこから「連続的」と見なすか。
SURVIVAL_SAMPLE = 50
SURVIVAL_THRESHOLD = 0.5


class StaleSelection(RuntimeError):
    """選択した時点のボリュームが、もうそこに無い."""


class VolumeBusy(RuntimeError):
    """実行中のジョブが掴んでいる."""


@dataclass(frozen=True)
class VolumeObservation:
    """今この瞬間に観測した「接続」の同一性.

    キューに積んだ操作が、選んだ時点と同じ接続に対して実行されることを
    確かめるために使う。

    **これは「接続」の同一性であって「媒体」の同一性ではない。** mountd の
    `generation` は観測した集合の指紋が変わったときだけ進むので、同じ UUID・
    型・容量のカードが観測の合間に同じノードで差し替わると据え置きになる。
    したがって、**開いてある dirfd を使い回してよい根拠にはできない**。
    """

    broker_epoch: str
    generation: int
    volume_key: str
    major: int
    minor: int
    fs_uuid: str

    @classmethod
    def of(cls, volume) -> VolumeObservation:  # noqa: ANN001
        return cls(
            broker_epoch=volume.broker_epoch,
            generation=volume.generation,
            volume_key=volume.volume_key,
            major=volume.major,
            minor=volume.minor,
            fs_uuid=volume.fs_uuid or "",
        )


@dataclass(frozen=True)
class VolumeSelection:
    """「この操作はこのボリュームのこの接続に対して行う」という固定."""

    volume_instance_id: str
    presence_id: str
    observation: VolumeObservation
    profile_id: str
    profile_revision_id: str

    def to_params(self) -> dict:
        params = asdict(self.observation)
        params.update(
            volume_instance_id=self.volume_instance_id,
            presence_id=self.presence_id,
            profile_id=self.profile_id,
            profile_revision_id=self.profile_revision_id,
        )
        return params

    @classmethod
    def from_params(cls, params: dict) -> VolumeSelection:
        return cls(
            volume_instance_id=params["volume_instance_id"],
            presence_id=params["presence_id"],
            observation=VolumeObservation(
                **{f.name: params[f.name] for f in fields(VolumeObservation)}
            ),
            profile_id=params["profile_id"],
            profile_revision_id=params["profile_revision_id"],
        )


@dataclass(frozen=True)
class VolumeView:
    volume_instance_id: str
    volume_key: str
    fs_label: str
    size_bytes: int
    profile_slug: str | None
    # §8 の「前回と同じカードだと言えるか」。プロファイルの一致度ではない。
    identity_confidence: str
    provisional: bool
    trusted: bool
    reason: str
    # 取り込む残りの件数。**まだ数えていないカードは `scanned_at` が None**
    # ——「0 件」と区別できないと、挿した直後に「取り込むものはありません」と
    # 断定してしまう。`scanned_at` はスキャンが最後まで走ったときに
    # `volume_instance` へ書かれる印で、**中身が空のカードでも入る**。
    pending_count: int
    scanned_at: str | None
    # このカードを掴んでいるジョブがあるか。**「いま抜いていいか」の答え。**
    busy: bool
    selection: VolumeSelection | None


class VolumeService:
    def __init__(
        self, conn: sqlite3.Connection, registry: ProfileRegistry, client: BrokerClient
    ) -> None:
        self._conn = conn
        self._registry = registry
        self._client = client
        # 実行中のジョブが掴んでいる handle。ジョブが終われば閉じる。
        self._open: dict[str, VolumeHandle] = {}
        self._lock = threading.RLock()

    def refresh(self) -> list[VolumeView]:
        """今この場にあるものを 1 つのスナップショットとして DB に反映する.

        **判定（probe）は、live 集合が確定してから行う。** 各ボリュームを
        「反映しながら判定」すると、別ポートへ挿し直した直後の refresh では
        旧 presence がまだ live なので「同一 identity の同時接続」と誤判定して
        確度が上がらないし、同じ identity の 2 枚を初めて同時に列挙したときは
        先に判定した方だけが high になる。
        """
        with self._lock:
            # スナップショットは 1 回だけ取る。pass ごとに取り直すと、
            # その間の抜き差しで pass の対象がずれる。
            volumes = self._client.list_volumes()

            # **pass 1 と 2 を 1 つのトランザクションに入れる。**
            # この層は 2 つのインスタンスが別々の接続で同時に動く（API 側と
            # VolumeWatcher）。囲まないと autocommit で 1 文ずつ流れ、
            # 相手の観測が pass の合間に挟まって「反映済みなのに detach された」
            # 接続ができる。判定（pass 3）はマウントを伴って長いので外に出す。
            with immediate(self._conn):
                # pass 1: 観測を DB へ反映する
                observed = []
                seen_presence: list[str] = []
                for volume in volumes:
                    device_id = upsert_device(self._conn, volume.usb)
                    volume_id = resolve_volume_instance(self._conn, volume, device_id)
                    presence_id = sync_presence(self._conn, volume_id, volume)
                    seen_presence.append(presence_id)
                    observed.append((volume, volume_id, presence_id))

                # pass 2: 消えた接続を detach する
                detach_absent(self._conn, seen_presence)

            # pass 3: 確定した live 集合を使って判定する
            definitions = [ref.definition for ref in self._registry.active()]
            return [
                self._probe(volume, volume_id, presence_id, definitions)
                for volume, volume_id, presence_id in observed
            ]

    def _probe(self, volume, volume_id, presence_id, definitions) -> VolumeView:  # noqa: ANN001
        remembered = self._conn.execute(
            "SELECT p.slug AS slug, v.trusted_at AS trusted_at,"
            " v.content_manifest_digest AS digest FROM volume_instance v"
            " LEFT JOIN device_profile p ON p.id = v.profile_id WHERE v.id = ?",
            (volume_id,),
        ).fetchone()
        facts = VolumeFacts(
            usb_vendor_id=volume.usb.vendor_id if volume.usb else "",
            usb_product_id=volume.usb.product_id if volume.usb else "",
            fs_label=volume.fs_label or "",
        )
        # **判定は必ず開き直す。開いてある handle を流用しない。**
        #
        # mountd の `generation` は uevent の数ではなく、観測した集合の
        # `(volume_key, fs_uuid, fs_type, size_bytes)` が前回と変わったときだけ
        # 進む（`mountd/server.py::_observe`）。uevent は購読せず polling なので、
        # 同じ UUID・型・容量のカードが同じ major:minor で
        # 観測の合間に差し替わると、**generation も epoch も据え置きのまま**に
        # なる。既存の dirfd は `open_tree` で切り離した旧カードを指したままな
        # ので、流用すると旧カードの中身で新カードを判定することになる。
        # 複製カードだけでなく、UUID を保持した再フォーマットも同じ。
        #
        # 代償は「GET /devices のたびに mount / umount が走る」こと。避けたければ
        # mountd 側に uevent を取りこぼさない incarnation を持たせて handle と
        # 一覧の両方へ刻印する必要があり、そこまでは要らないと判断している。
        observation = VolumeObservation.of(volume)
        handle = self._client.open_volume(volume)
        try:
            tree = DirfdTree(handle.dirfd)
            outcome = resolve_profile(definitions, facts, tree, remembered["slug"])
            digest = self._manifest_of(handle.dirfd, tree, outcome, definitions)
            confidence = self._identity_confidence(volume, volume_id, remembered, digest, handle)
        finally:
            with contextlib.suppress(Exception):
                self._client.close_volume(handle)

        profile_id = revision_id = None
        if outcome.slug is not None:
            ref = self._registry.current(outcome.slug)
            profile_id, revision_id = ref.profile_id, ref.revision_id
        # **判定の結果は残らず DB に置く。** watcher は「積んでよいか」を毎 tick
        # DB の現在値から組み直すので、VolumeView にしか無い値があると
        # 組み直せない（§12.1）。provisional もその 1 つ。
        self._conn.execute(
            "UPDATE volume_instance SET profile_id = ?, profile_revision_id = ?,"
            " identity_confidence = ?, provisional = ?, content_manifest_digest = ?,"
            " last_seen_at = ? WHERE id = ?",
            (
                profile_id,
                revision_id,
                confidence,
                1 if outcome.provisional else 0,
                digest,
                now_iso(),
                volume_id,
            ),
        )
        selection = None
        if profile_id is not None:
            selection = VolumeSelection(
                volume_instance_id=volume_id,
                presence_id=presence_id,
                observation=observation,
                profile_id=profile_id,
                profile_revision_id=revision_id,
            )
        pending_count, scanned_at = self._counts(volume_id)
        return VolumeView(
            volume_instance_id=volume_id,
            volume_key=volume.volume_key,
            fs_label=volume.fs_label or "",
            size_bytes=volume.size_bytes,
            profile_slug=outcome.slug,
            identity_confidence=confidence,
            provisional=outcome.provisional,
            trusted=remembered["trusted_at"] is not None,
            reason=outcome.reason,
            pending_count=pending_count,
            scanned_at=scanned_at,
            busy=volume_id in self._open,
            selection=selection,
        )

    def _counts(self, volume_instance_id: str) -> tuple[int, str | None]:
        """取り込む残りと、最後に数え終えた時刻.

        **残りは `source_entry` を数えるが、「数えたか」は行から導かない。**
        一致するファイルが無いカードは行を 1 つも作らないので、行から導くと
        スキャンが完全に成功しても「まだ数えていない」に見える（`mark_scanned`）。
        """
        pending = self._conn.execute(
            "SELECT sum(" + PENDING_CLAUSE + ") AS pending"  # noqa: S608
            " FROM source_entry WHERE volume_instance_id = ?",
            (volume_instance_id,),
        ).fetchone()
        scanned = self._conn.execute(
            "SELECT scanned_at FROM volume_instance WHERE id = ?",
            (volume_instance_id,),
        ).fetchone()
        return (pending["pending"] or 0), scanned["scanned_at"]

    def _manifest_of(self, dirfd, tree, outcome, definitions) -> str:  # noqa: ANN001
        roots = ("DCIM",)
        if outcome.slug is not None:
            roots = next(d.scan.roots for d in definitions if d.slug == outcome.slug)
        names = []
        for root in roots:
            names.extend(f"{root}/{name}" for name in tree.iter_names(root, MANIFEST_LIMIT))
        return content_manifest_digest(names)

    def _identity_confidence(self, volume, volume_id, remembered, digest, handle) -> str:  # noqa: ANN001
        """§8 の確度. **プロファイルの一致度とは無関係**.

        `high` にできるのは「前回と連続的だ」と言えるときだけ。read-only で
        扱う以上ボリュームに永続マーカーを書けないので、これは推測である
        （§12.1 に限界を明示する）。
        """
        if not volume.fs_uuid:
            return "low"
        if self._has_other_live_presence(volume_id, volume):
            return "low"
        if remembered["digest"] is None:
            # 初めて見るカードは §12.1 のとおり必ず承認を待つ。
            return "low"
        if remembered["digest"] == digest:
            return "high"
        return "high" if self._known_files_survive(volume_id, handle) else "low"

    def _has_other_live_presence(self, volume_id: str, volume) -> bool:  # noqa: ANN001
        row = self._conn.execute(
            "SELECT count(*) AS n FROM volume_presence WHERE volume_instance_id = ?"
            " AND detached_at IS NULL AND (major <> ? OR minor <> ?)",
            (volume_id, volume.major, volume.minor),
        ).fetchone()
        return row["n"] > 0

    def _known_files_survive(self, volume_id: str, handle) -> bool:  # noqa: ANN001
        rows = list(
            self._conn.execute(
                "SELECT rel_path FROM source_entry WHERE volume_instance_id = ?"
                " AND state = 'published' LIMIT ?",
                (volume_id, SURVIVAL_SAMPLE),
            )
        )
        if not rows:
            return False
        alive = sum(1 for row in rows if exists_beneath(handle.dirfd, row["rel_path"]))
        return alive / len(rows) >= SURVIVAL_THRESHOLD

    # ------------------------------------------------------------------
    def selection_for(self, volume_instance_id: str) -> VolumeSelection:
        matches = [
            view.selection
            for view in self.refresh()
            if view.volume_instance_id == volume_instance_id and view.selection is not None
        ]
        if not matches:
            raise StaleSelection(f"ボリューム {volume_instance_id} は今この場に無い")
        if len(matches) > 1:
            # 同じ identity のカードが 2 枚同時に挿さっている。どちらを指した
            # のか決められないので、勝手に選ばない（§8 の presence 分離の趣旨）。
            raise StaleSelection(
                "同じ識別子のボリュームが複数接続されている。どれを操作するか決められない"
            )
        return matches[0]

    def open(self, selection: VolumeSelection) -> VolumeHandle:
        """選択した瞬間の接続と同じものだけを開く.

        `volume_instance_id` だけで開き直すと、抜き差しで別のカードが同じ
        ノードに来ていても、その現在値から正しい expect を作ってしまい、
        ブローカーの検証をすり抜ける。

        **開いた handle をジョブ間でキャッシュしない。** `VolumeObservation` は
        物理媒体の同一性を保証しないので（`_probe` のコメント参照）、次の
        ジョブへ使い回すと「判定は新しいカード、読むのは古いカードの
        detached clone」という食い違いが起きる。単一ワーカーなので開き直す
        コストも実質かからない。
        """
        with self._lock:
            if selection.volume_instance_id in self._open:
                # 単一ワーカーなのでここへは来ない。来たら契約違反なので、
                # 同じ媒体である保証が無いまま共有せずに知らせる。
                raise VolumeBusy("このボリュームは既に開かれている")
            volume = self._match_selection(selection)
            handle = self._client.open_volume(volume)
            self._open[selection.volume_instance_id] = handle
            return handle

    def _match_selection(self, selection: VolumeSelection):  # noqa: ANN202
        presence = self._conn.execute(
            "SELECT detached_at FROM volume_presence WHERE id = ?", (selection.presence_id,)
        ).fetchone()
        if presence is None or presence["detached_at"] is not None:
            raise StaleSelection("選択した接続はもう存在しない")
        for volume in self._client.list_volumes():
            if VolumeObservation.of(volume) == selection.observation:
                return volume
        raise StaleSelection("選択した時点のボリュームが見つからない（抜き差しされた）")

    def release(self, selection: VolumeSelection) -> None:
        """ジョブが使い終わった handle を閉じる.

        次のジョブのために取っておかない。取っておくと、同じ observation の
        まま媒体が差し替わったときに古い dirfd を渡すことになる。
        """
        with self._lock:
            handle = self._open.pop(selection.volume_instance_id, None)
            if handle is not None:
                with contextlib.suppress(Exception):
                    self._client.close_volume(handle)

    def close(self, volume_instance_id: str) -> None:
        """画面からの取り外し操作. 実行中のジョブが掴んでいれば拒否する.

        ジョブが終われば `release` で閉じているので、通常は何もすることが無い。
        """
        with self._lock:
            if volume_instance_id in self._open:
                raise VolumeBusy("実行中のジョブがこのボリュームを使っている")

    def close_all(self) -> None:
        with self._lock:
            for volume_instance_id in list(self._open):
                handle = self._open.pop(volume_instance_id)
                with contextlib.suppress(Exception):
                    self._client.close_volume(handle)

    def opened(self) -> list[str]:
        return sorted(self._open)

    def trust(self, volume_instance_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE volume_instance SET trusted_at = ? WHERE id = ?",
                (now_iso(), volume_instance_id),
            )
