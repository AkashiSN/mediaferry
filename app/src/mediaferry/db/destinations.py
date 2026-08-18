"""転送先プロファイルの保存（§8 / §12.3）.

**リビジョンは不変。** 編集のたびに新しい行が増える。`target_epoch` は
向き先が変わったときだけ進み、アップロード履歴を引き継いでよいかの境界になる。

`remote_user_id` は同一性ではなく guard（Phase 0 の実測。Immich は
サーバインスタンス ID を公開していない）。同じアカウントを指す宛先を 2 つ作るのは
正当な使い方なので、**警告は出すが拒否も統合もしない**。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..clock import now_iso
from ..core.destinations.identity import fingerprint
from ..core.destinations.urls import normalize_endpoint
from ..ids import new_id
from .connection import immediate
from .credentials import CredentialStore

# そのリビジョンの鍵がまだ要る状態。**承認待ちを必ず含める。**
# 含めないと、宛先を編集した直後に「承認に要る旧鍵」を消してしまい、
# 承認画面は残るのに永久に承認できないレコードができる。
_IN_FLIGHT = (
    "checking",
    "uploading",
    "asset_known",
    "tagging",
    "fixing_datetime",
    "awaiting_datetime_approval",
)


class DestinationNotFound(RuntimeError):
    pass


class IdentityUnknown(RuntimeError):
    """接続の検証で `remote_user_id` を観測できなかった.

    保存すると、preflight が必ず失敗する宛先ができる（§10）。
    """


class EpochDecisionRequired(RuntimeError):
    """同じユーザのままホストが変わった. 履歴を引き継ぐかを人が決める.

    DB を複製・復元した別ライブラリかもしれないし、経路を変えただけかもしれない。
    自動では判別できない。
    """


@dataclass(frozen=True)
class RevisionOutcome:
    """編集の結果. **破棄した件数まで返す**（API が利用者へ見せる）."""

    revision_id: str
    target_epoch: int
    invalidated_records: int


@dataclass(frozen=True)
class RemoteIdentity:
    """接続の検証で観測した向き先. 同一性ではない.

    **`remote_user_id` は指紋であって観測値そのものではない**
    （`core.destinations.identity`）。生の値を通す経路を残さないため、
    観測から作るときは `observed()` を使う。
    """

    remote_user_id: str | None
    server_instance_id: str | None

    @classmethod
    def observed(cls, user_id: str | None, server_instance_id: str | None = None) -> RemoteIdentity:
        """観測した識別子を指紋にして包む."""
        return cls(
            remote_user_id=fingerprint(user_id), server_instance_id=fingerprint(server_instance_id)
        )


class DestinationRepository:
    def __init__(self, conn: sqlite3.Connection, credentials: CredentialStore) -> None:
        self._conn = conn
        self._credentials = credentials

    def create(
        self,
        name: str,
        base_url: str,
        public_url: str | None,
        secret: str,
        identity: RemoteIdentity,
    ) -> str:
        """検証に成功した設定だけを保存する（§12.3）."""
        # URL と向き先の検証を先に通す。落ちたら 1 行も書かない。
        endpoints = _endpoints(base_url, public_url)
        _require_identity(identity)
        destination_id = new_id()
        # **1 トランザクション。** 途中で落ちても、現行リビジョンの無い宛先や
        # 孤立した credential を残さない（§8）。
        with immediate(self._conn):
            self._conn.execute(
                "INSERT INTO upload_destination (id, name, kind, enabled, created_at)"
                " VALUES (?, ?, 'immich', 1, ?)",
                (destination_id, name, now_iso()),
            )
            credential_id = self._credentials.store_locked(destination_id, secret)
            self._write_revision(
                destination_id=destination_id,
                revision=1,
                target_epoch=1,
                endpoints=endpoints,
                credential_id=credential_id,
                identity=identity,
            )
        return destination_id

    def add_revision(
        self,
        destination_id: str,
        base_url: str,
        public_url: str | None,
        secret: str,
        identity: RemoteIdentity,
        same_library: bool | None = None,
    ) -> RevisionOutcome:
        """編集を新しいリビジョンとして反映する.

        **旧 epoch の破棄まで同じトランザクションで行い、件数を返す**（§8）。
        別の呼び出しに分けると、間で落ちたときに理由の無い pending が残り、
        API も「何件止めたか」を返せない。
        """
        endpoints = _endpoints(base_url, public_url)
        _require_identity(identity)
        with immediate(self._conn):
            # **現行の読出しと版番号の決定もトランザクションの中で行う。**
            # 外に出すと、同時に 2 つの編集が同じ revision N を読み、片方が
            # UNIQUE 違反で 500 になる。
            current = self.current(destination_id)
            epoch = _next_epoch(current, endpoints[0], identity, same_library)
            credential_id = self._credentials.store_locked(destination_id, secret)
            revision_id = self._write_revision(
                destination_id=destination_id,
                revision=current["revision"] + 1,
                target_epoch=epoch,
                endpoints=endpoints,
                credential_id=credential_id,
                identity=identity,
            )
            invalidated = 0
            if epoch != current["target_epoch"]:
                # **同じトランザクションで**旧 epoch の未 claim 項目を破棄する（§8）。
                # 分けると、間で落ちたときに理由の無い pending が永久に残る。
                invalidated = self._invalidate_old_epoch_locked(destination_id, epoch)
        return RevisionOutcome(
            revision_id=revision_id, target_epoch=epoch, invalidated_records=invalidated
        )

    def current(self, destination_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT r.* FROM upload_destination d"
            " JOIN destination_revision r ON r.id = d.current_revision_id"
            " WHERE d.id = ?",
            (destination_id,),
        ).fetchone()
        if row is None:
            raise DestinationNotFound(f"転送先 {destination_id} に現行リビジョンが無い")
        return row

    def revision(self, revision_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM destination_revision WHERE id = ?", (revision_id,)
        ).fetchone()
        if row is None:
            raise DestinationNotFound(f"リビジョン {revision_id} が無い")
        return row

    def secret_of(self, revision_id: str) -> str:
        """送信の直前にだけ呼ぶ."""
        return self._credentials.reveal(self.revision(revision_id)["credential_id"])

    def get(self, destination_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM upload_destination WHERE id = ?", (destination_id,)
        ).fetchone()

    def list_destinations(self, include_archived: bool = False) -> list[sqlite3.Row]:
        if include_archived:
            return list(self._conn.execute("SELECT * FROM upload_destination ORDER BY created_at"))
        return list(
            self._conn.execute(
                "SELECT * FROM upload_destination WHERE archived_at IS NULL ORDER BY created_at"
            )
        )

    def rename_or_toggle(
        self, destination_id: str, name: str | None = None, enabled: bool | None = None
    ) -> None:
        """接続に関わらない編集. **リビジョンを増やさない**（§8 の「編集」ではない）."""
        assignments, params = [], []
        if name is not None:
            assignments.append("name = ?")
            params.append(name)
        if enabled is not None:
            assignments.append("enabled = ?")
            params.append(1 if enabled else 0)
        if not assignments:
            return
        with immediate(self._conn):
            self._conn.execute(
                f"UPDATE upload_destination SET {', '.join(assignments)} WHERE id = ?",  # noqa: S608
                (*params, destination_id),
            )

    def set_enabled(self, destination_id: str, enabled: bool) -> None:
        with immediate(self._conn):
            self._conn.execute(
                "UPDATE upload_destination SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, destination_id),
            )

    def archive(self, destination_id: str) -> None:
        """物理削除しない. 履歴と監査情報を残す."""
        with immediate(self._conn):
            self._conn.execute(
                "UPDATE upload_destination SET archived_at = ?, enabled = 0 WHERE id = ?",
                (now_iso(), destination_id),
            )

    def purge_superseded_credentials(self, destination_id: str) -> int:
        """現行でないリビジョンの資格情報を、使い終わっていれば消す（§12.3）.

        `destination_revision` は不変なので、リビジョンから参照が外れることは
        ない。**「進行中の `upload_record` がそのリビジョンを指していないこと」**を
        使い終わりの条件にする。版管理したまま旧鍵を持ち続けると、ローテートしても
        漏洩面が減らない。
        """
        marks = ", ".join("?" * len(_IN_FLIGHT))
        rows = self._conn.execute(
            "SELECT r.credential_id AS credential_id FROM destination_revision r"  # noqa: S608
            " JOIN upload_destination d ON d.id = r.destination_id"
            " WHERE r.destination_id = ? AND r.id <> d.current_revision_id"
            "   AND NOT EXISTS (SELECT 1 FROM upload_record u"
            f"                  WHERE u.destination_revision_id = r.id AND u.state IN ({marks}))",
            (destination_id, *_IN_FLIGHT),
        ).fetchall()
        purged = 0
        for row in rows:
            purged += self._credentials.purge(row["credential_id"])
        return purged

    def get_current_or_none(self, destination_id: str) -> sqlite3.Row | None:
        try:
            return self.current(destination_id)
        except DestinationNotFound:
            return None

    def same_account_warnings(
        self, identity: RemoteIdentity, exclude_id: str | None = None
    ) -> list[str]:
        """同じ Immich アカウントを指す宛先を挙げる. 拒否はしない."""
        if identity.remote_user_id is None:
            return []
        rows = self._conn.execute(
            "SELECT d.id AS id, d.name AS name FROM upload_destination d"
            " JOIN destination_revision r ON r.id = d.current_revision_id"
            " WHERE r.remote_user_id = ? AND d.archived_at IS NULL",
            (identity.remote_user_id,),
        )
        return [
            f"転送先「{row['name']}」が同じ Immich アカウントを指している"
            for row in rows
            if row["id"] != exclude_id
        ]

    # ------------------------------------------------------------------
    def _write_revision(
        self,
        destination_id: str,
        revision: int,
        target_epoch: int,
        endpoints: tuple[str, str | None],
        credential_id: str,
        identity: RemoteIdentity,
    ) -> str:
        """**呼び出し側が開いたトランザクションの中で使う。** 単独では開かない."""
        if not self._conn.in_transaction:
            raise RuntimeError("_write_revision は呼び出し側のトランザクションの中で使う")
        revision_id = new_id()
        base_url, public_url = endpoints
        self._conn.execute(
            "INSERT INTO destination_revision (id, destination_id, revision, target_epoch,"
            " base_url, public_url, credential_id, remote_user_id, server_instance_id,"
            " verified_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision_id,
                destination_id,
                revision,
                target_epoch,
                base_url,
                public_url,
                credential_id,
                identity.remote_user_id,
                identity.server_instance_id,
                now_iso(),
                now_iso(),
            ),
        )
        # 現行の差し替えは同じトランザクションで行う。分けると、
        # 「新しい版はあるが誰も使っていない」窓ができる。
        self._conn.execute(
            "UPDATE upload_destination SET current_revision_id = ? WHERE id = ?",
            (revision_id, destination_id),
        )
        return revision_id

    def _invalidate_old_epoch_locked(self, destination_id: str, current_epoch: int) -> int:
        """旧 epoch の未完了レコードを破棄する. **`complete` は履歴として残す**（§8）."""
        updated = self._conn.execute(
            "UPDATE upload_record SET invalidated_at = ?,"
            " invalidated_reason = '宛先の向き先が変わった', updated_at = ?"
            " WHERE destination_id = ? AND target_epoch < ? AND invalidated_at IS NULL"
            "   AND state <> 'complete'",
            (now_iso(), now_iso(), destination_id, current_epoch),
        )
        return updated.rowcount


def _require_identity(identity: RemoteIdentity) -> None:
    """向き先を観測できていない設定は保存しない."""
    if not identity.remote_user_id:
        raise IdentityUnknown("接続の検証で remote_user_id を取得できなかった。設定を保存しない")


def _endpoints(base_url: str, public_url: str | None) -> tuple[str, str | None]:
    """両方に同じ検証を掛ける. public_url は画面に描画されるので緩めない."""
    return normalize_endpoint(base_url), (
        None if public_url is None else normalize_endpoint(public_url)
    )


def _next_epoch(
    current: sqlite3.Row,
    base_url: str,
    identity: RemoteIdentity,
    same_library: bool | None,
) -> int:
    """向き先が変わったときだけ epoch を進める（§8）.

    `identity.remote_user_id` は `_require_identity` を通っているので非 None。
    """
    epoch = current["target_epoch"]
    if identity.remote_user_id != current["remote_user_id"]:
        # 別アカウント。履歴を引き継がない。
        return epoch + 1
    if _host_of(base_url) == _host_of(current["base_url"]):
        return epoch
    if same_library is None:
        raise EpochDecisionRequired(
            "ホストが変わったが同じユーザを指している。同じライブラリかを確認する"
        )
    return epoch if same_library else epoch + 1


def _host_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.hostname}:{parts.port}" if parts.port else str(parts.hostname)
