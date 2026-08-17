import sqlite3

import pytest

from mediaferry.clock import now_iso
from mediaferry.ids import new_id


def a_job(db, **over):
    row = {
        "id": new_id(),
        "type": "import",
        "status": "queued",
        "params_json": "{}",
        "created_at": now_iso(),
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    db.execute(f"INSERT INTO job ({cols}) VALUES ({marks})", tuple(row.values()))  # noqa: S608
    return row["id"]


def test_job_status_is_constrained(db):
    with pytest.raises(sqlite3.IntegrityError):
        a_job(db, status="halfway")


def test_job_type_is_constrained(db):
    with pytest.raises(sqlite3.IntegrityError):
        a_job(db, type="rm_rf")


def test_lease_columns_are_all_null_or_all_set(db):
    """片方だけ残ると、期限切れ判定が「期限なし」に化ける."""
    with pytest.raises(sqlite3.IntegrityError):
        a_job(db, lease_token="t")
    a_job(db, lease_token="t", lease_expires_at=now_iso())


def test_job_event_seq_is_unique_per_job(db):
    job_id = a_job(db)
    db.execute(
        "INSERT INTO job_event (job_id, seq, level, message, at) VALUES (?, 1, 'info', 'x', ?)",
        (job_id, now_iso()),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO job_event (job_id, seq, level, message, at) VALUES (?, 1, 'info', 'y', ?)",
            (job_id, now_iso()),
        )


def test_job_event_id_is_monotonic_across_jobs(db):
    """SSE は id の昇順で再開するので、ジョブをまたいで単調でなければならない."""
    first, second = a_job(db), a_job(db)
    for job_id in (first, second):
        db.execute(
            "INSERT INTO job_event (job_id, seq, level, message, at) VALUES (?, 1, 'info', 'x', ?)",
            (job_id, now_iso()),
        )
    ids = [r[0] for r in db.execute("SELECT id FROM job_event ORDER BY id")]
    assert ids == sorted(ids) and len(set(ids)) == 2


def test_job_event_ids_are_not_reused_after_a_job_is_deleted(db):
    """AUTOINCREMENT が無いと rowid が再利用され、SSE が既読の id を再発行する.

    Last-Event-ID より小さい id は配信済みとして飛ばされるので、再利用された
    イベントはクライアントに永久に届かない。
    """
    first = a_job(db)
    db.execute(
        "INSERT INTO job_event (job_id, seq, level, message, at) VALUES (?, 1, 'info', 'x', ?)",
        (first, now_iso()),
    )
    used = db.execute("SELECT max(id) FROM job_event").fetchone()[0]
    db.execute("DELETE FROM job WHERE id = ?", (first,))

    second = a_job(db)
    db.execute(
        "INSERT INTO job_event (job_id, seq, level, message, at) VALUES (?, 1, 'info', 'y', ?)",
        (second, now_iso()),
    )
    assert db.execute("SELECT max(id) FROM job_event").fetchone()[0] > used


def test_job_events_go_away_with_the_job(db):
    job_id = a_job(db)
    db.execute(
        "INSERT INTO job_event (job_id, seq, level, message, at) VALUES (?, 1, 'info', 'x', ?)",
        (job_id, now_iso()),
    )
    db.execute("DELETE FROM job WHERE id = ?", (job_id,))
    assert db.execute("SELECT count(*) FROM job_event").fetchone()[0] == 0


def test_app_setting_keys_are_unique(db):
    db.execute("INSERT INTO app_setting VALUES ('LOG_LEVEL', 'info', ?)", (now_iso(),))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO app_setting VALUES ('LOG_LEVEL', 'debug', ?)", (now_iso(),))
