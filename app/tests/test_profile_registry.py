import pytest

from mediaferry.core.profiles.model import definition_to_json
from mediaferry.db.profiles import ProfileRegistry, UnknownProfile


def test_sync_seeds_the_builtins(db):
    registry = ProfileRegistry(db)
    assert "dji-osmo" in registry.sync_builtins()
    ref = registry.current("dji-osmo")
    assert ref.revision == 1
    assert ref.definition.slug == "dji-osmo"


def test_sync_is_idempotent(db):
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    assert registry.sync_builtins() == []
    assert db.execute("SELECT count(*) FROM profile_revision").fetchone()[0] == 1


def test_a_changed_builtin_creates_a_new_revision_and_keeps_the_old_one(db):
    """過去データの解釈が変わらないよう、旧リビジョンは残す."""
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    old = registry.current("dji-osmo")

    changed = definition_to_json(old.definition).replace(
        '"tolerance_seconds":5', '"tolerance_seconds":9'
    )
    registry._upsert_revision("dji-osmo", changed)  # noqa: SLF001

    new = registry.current("dji-osmo")
    assert new.revision == 2
    assert new.definition.merge.tolerance_seconds == 9
    assert registry.definition_of(old.revision_id).merge.tolerance_seconds == 5


def test_current_points_at_the_latest_revision(db):
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    row = db.execute("SELECT current_revision_id FROM device_profile").fetchone()
    assert row["current_revision_id"] == registry.current("dji-osmo").revision_id


def test_unknown_slug_raises(db):
    with pytest.raises(UnknownProfile):
        ProfileRegistry(db).current("nope")


def test_archived_profiles_are_not_active(db):
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    assert [ref.definition.slug for ref in registry.active()] == ["dji-osmo"]
    db.execute("UPDATE device_profile SET archived_at = '2026-01-01T00:00:00+00:00'")
    assert registry.active() == []
