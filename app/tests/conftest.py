import pytest

from mediaferry.db.connection import Database
from mediaferry.db.migrate import apply_migrations


@pytest.fixture
def data_root(tmp_path):
    """§7 のレイアウト. staging は library と同じファイルシステムに要る."""
    root = tmp_path / "data"
    for name in ("library", "derived", "staging", "work", "var"):
        (root / name).mkdir(parents=True)
    return root


@pytest.fixture
def database(data_root):
    return Database(data_root / "var" / "mediaferry.sqlite3")


@pytest.fixture
def db(database):
    conn = database.connect()
    apply_migrations(conn)
    yield conn
    conn.close()
