import subprocess
import sys
from pathlib import Path

import pytest

from mediaferry.adapters.ffprobe import ProbeResult
from mediaferry.adapters.publisher import ArtifactPublisher
from mediaferry.db.connection import Database
from mediaferry.db.jobs import JobStore
from mediaferry.db.migrate import apply_migrations
from mediaferry.jobs.reconcile import Reconciler

from .crash_child import PAYLOAD

CHILD = Path(__file__).parent / "crash_child.py"
STEPS = list(range(1, 12))


class _Probe:
    def describe(self, path, extension):
        return ProbeResult("video", 2.0, "ok")


def crash_at(data_root, step, kind):
    completed = subprocess.run(  # noqa: S603
        [sys.executable, str(CHILD), str(data_root), str(step), kind],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 9, (
        f"step {step} で落ちなかった: rc={completed.returncode} {completed.stderr}"
    )


def reconcile(data_root):
    conn = Database(data_root / "var" / "mediaferry.sqlite3").connect()
    apply_migrations(conn)
    publisher = ArtifactPublisher(conn, data_root, _Probe())
    report = Reconciler(conn, data_root, publisher, JobStore(conn)).run()
    return conn, report


@pytest.mark.parametrize("kind", ["import", "merge", "merge_prepared"])
@pytest.mark.parametrize("step", STEPS)
def test_reconciliation_recovers_from_a_crash_at_any_step(data_root, step, kind):
    crash_at(data_root, step, kind)
    conn, report = reconcile(data_root)

    final = (
        "library/dji-osmo/DCIM/A.MP4" if kind == "import" else "derived/dji-osmo/DCIM/MERGED.MP4"
    )
    rows = conn.execute("SELECT count(*) FROM media_file").fetchone()[0]

    if step <= 6:
        # staged より前は「作業がなかったこと」になる。呼び出し元が再実行する。
        assert rows == 0
        assert report.discarded == 1
        assert not (data_root / final).exists()
    else:
        # staged 以降は永続情報だけで公開を再開できる。
        assert rows == 1
        assert (data_root / final).read_bytes() == PAYLOAD
        state = conn.execute("SELECT state FROM artifact_staging").fetchone()["state"]
        assert state == "published"

    # どの段階で落ちても、staging に中間ファイルは残らない（空のディレクトリは可）。
    assert [p for p in (data_root / "staging").rglob("*") if p.is_file()] == []
    assert report.orphans == []
    conn.close()


@pytest.mark.parametrize("step", [8, 9, 10])
def test_the_source_entry_is_linked_after_recovery(data_root, step):
    crash_at(data_root, step, "import")
    conn, _ = reconcile(data_root)
    row = conn.execute("SELECT * FROM source_entry").fetchone()
    assert row["state"] == "published"
    assert row["media_file_id"] is not None
    conn.close()


@pytest.mark.parametrize("step", STEPS)
def test_reconciliation_is_idempotent(data_root, step):
    crash_at(data_root, step, "import")
    conn, _ = reconcile(data_root)
    before = conn.execute("SELECT count(*) FROM media_file").fetchone()[0]
    conn.close()
    conn, second = reconcile(data_root)
    assert conn.execute("SELECT count(*) FROM media_file").fetchone()[0] == before
    assert second.orphans == []
    conn.close()


def test_a_crash_after_staging_never_overwrites_a_conflicting_file(data_root):
    """公開の直前に外から同名の別ファイルが現れても上書きしない."""
    crash_at(data_root, 7, "import")
    target = data_root / "library/dji-osmo/DCIM/A.MP4"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"someone else's file")

    conn, _ = reconcile(data_root)
    assert target.read_bytes() == b"someone else's file"
    row = conn.execute("SELECT rel_path FROM media_file").fetchone()
    assert row["rel_path"] != "library/dji-osmo/DCIM/A.MP4"
    assert (data_root / row["rel_path"]).read_bytes() == PAYLOAD
    conn.close()
