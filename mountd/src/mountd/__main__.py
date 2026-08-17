"""mountd のエントリポイント."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from mountd.devices import enumerate_volumes
from mountd.mounts import MountManager
from mountd.server import BrokerServer


def _allowed_uids() -> frozenset[int] | None:
    raw = os.environ.get("MOUNTD_ALLOWED_UIDS", "").strip()
    if not raw:
        return None
    return frozenset(int(part) for part in raw.split(",") if part.strip())


def _socket_gid() -> int | None:
    raw = os.environ.get("MOUNTD_SOCKET_GID", "").strip()
    return int(raw) if raw else None


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("MOUNTD_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    socket_path = Path(os.environ.get("MOUNTD_SOCKET", "/run/mediaferry/broker.sock"))
    mount_root = Path(os.environ.get("MOUNTD_MOUNT_ROOT", "/run/mountd/mnt"))

    server = BrokerServer(
        socket_path=socket_path,
        mount_manager=MountManager(mount_root=mount_root),
        lister=enumerate_volumes,
        allowed_uids=_allowed_uids(),
        socket_gid=_socket_gid(),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
