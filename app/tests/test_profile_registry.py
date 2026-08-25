import pytest

from mediaferry.core.profiles.model import definition_to_json, load_builtin_definitions
from mediaferry.db.profiles import ProfileRegistry, UnknownProfile


def test_sync_seeds_the_builtins(db):
    registry = ProfileRegistry(db)
    assert "dji-osmo" in registry.sync_builtins()
    ref = registry.current("dji-osmo")
    assert ref.revision == 1
    assert ref.definition.slug == "dji-osmo"


def test_sync_is_idempotent(db):
    """2 度目は 1 つも版を作らない（同梱の本数に依存しない形で見る）."""
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    before = db.execute("SELECT count(*) FROM profile_revision").fetchone()[0]
    assert before == len(load_builtin_definitions())
    assert registry.sync_builtins() == []
    assert db.execute("SELECT count(*) FROM profile_revision").fetchone()[0] == before


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
    row = db.execute(
        "SELECT current_revision_id FROM device_profile WHERE slug = 'dji-osmo'"
    ).fetchone()
    assert row["current_revision_id"] == registry.current("dji-osmo").revision_id


def test_unknown_slug_raises(db):
    with pytest.raises(UnknownProfile):
        ProfileRegistry(db).current("nope")


def test_archived_profiles_are_not_active(db):
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    assert [ref.definition.slug for ref in registry.active()] == sorted(
        d.slug for d in load_builtin_definitions()
    )
    db.execute(
        "UPDATE device_profile SET archived_at = '2026-01-01T00:00:00+00:00'"
        " WHERE slug = 'dji-osmo'"
    )
    assert "dji-osmo" not in [ref.definition.slug for ref in registry.active()]
    db.execute("UPDATE device_profile SET archived_at = '2026-01-01T00:00:00+00:00'")
    assert registry.active() == []


# --- 版が進んだらスタックの見送りを未評価へ戻す（Phase 6 / §6） ---------


def a_skipped_record(db, slug):
    """`slug` のメディアに紐づく、スタック見送りのレコードを作る."""
    from mediaferry.clock import now_iso

    from .test_schema_artifacts import a_media_file
    from .test_schema_uploads import a_destination, an_upload

    registry = ProfileRegistry(db)
    profile = registry.current(slug)
    media = a_media_file(db, (profile.profile_id, profile.revision_id))
    dest = a_destination(db)
    record = an_upload(
        db,
        dest,
        media,
        state="complete",
        destination_revision_id=dest[1],
        remote_asset_id="asset-1",
    )
    db.execute(
        "UPDATE upload_record SET stack_state = 'skipped', stack_reason = '相方が見つからない',"
        " updated_at = ? WHERE id = ?",
        (now_iso(), record),
    )
    return record


def state_of(db, record):
    return db.execute(
        "SELECT stack_state, stack_reason FROM upload_record WHERE id = ?", (record,)
    ).fetchone()


def a_copy_with_stacking(db, enabled):
    """編集できる複製を作る（ビルトインは編集できない）."""
    from dataclasses import replace

    from mediaferry.core.profiles.model import STACK_DISABLED, StackRule

    registry = ProfileRegistry(db)
    registry.sync_builtins()
    registry.duplicate("canon-eos", "my-canon", "私の Canon")
    current = registry.current("my-canon")
    rule = StackRule(enabled=True, extensions=("JPG", "CR2")) if enabled else STACK_DISABLED
    registry.update("my-canon", replace(current.definition, stack=rule))
    return registry


def test_a_new_revision_that_changes_the_stack_rule_reopens_the_skips(db):
    """**規則そのものが変わったら組み直す。**

    `stack` を無効から有効にしても、拡張子や許容差を変えても、既に見送った行は
    未評価へ戻らなければ二度と評価されない。
    """
    from dataclasses import replace

    from mediaferry.core.profiles.model import StackRule

    registry = a_copy_with_stacking(db, enabled=False)
    record = a_skipped_record(db, "my-canon")

    current = registry.current("my-canon")
    registry.update(
        "my-canon",
        replace(
            current.definition,
            stack=StackRule(enabled=True, extensions=("JPG", "CR2")),
        ),
    )

    assert state_of(db, record)["stack_state"] is None
    assert state_of(db, record)["stack_reason"] is None


def test_a_revision_that_leaves_the_stack_rule_alone_does_not_reopen(db):
    """**名前やタグだけの編集で全件を再評価しない**（大きいライブラリでは重い）."""
    from dataclasses import replace

    registry = a_copy_with_stacking(db, enabled=True)
    record = a_skipped_record(db, "my-canon")

    current = registry.current("my-canon")
    registry.update("my-canon", replace(current.definition, name="別の名前"))

    assert state_of(db, record)["stack_state"] == "skipped"


def test_a_builtin_sync_that_changes_the_stack_rule_reopens_the_skips(db):
    """**ビルトインは `_insert_revision` を通らない**（`_upsert_revision` が直に書く）.

    共通の helper にまとめないと、この経路だけ戻らない。
    """
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    record = a_skipped_record(db, "canon-eos")

    changed = definition_to_json(registry.current("canon-eos").definition).replace(
        '"extensions":["JPG","CR2"]', '"extensions":["CR2","JPG"]'
    )
    registry._upsert_revision("canon-eos", changed)  # noqa: SLF001

    assert state_of(db, record)["stack_state"] is None


def test_a_builtin_sync_that_leaves_the_stack_rule_alone_does_not_reopen(db):
    registry = ProfileRegistry(db)
    registry.sync_builtins()
    record = a_skipped_record(db, "canon-eos")

    changed = definition_to_json(registry.current("canon-eos").definition).replace(
        '"name":"Canon EOS"', '"name":"Canon"'
    )
    registry._upsert_revision("canon-eos", changed)  # noqa: SLF001

    assert state_of(db, record)["stack_state"] == "skipped"


def test_an_omitted_stack_equals_an_explicit_disabled_one(db):
    """**Phase 6 より前の定義は `stack` キーを持たない。**

    正規化せずに生の JSON で比べると、「省略 → 明示的な disabled」で規則は実質
    同じなのに全件を戻すことになる。
    """
    import json

    registry = ProfileRegistry(db)
    registry.sync_builtins()
    without = json.loads(definition_to_json(registry.current("dji-osmo").definition))
    without.pop("stack")
    registry._upsert_revision("dji-osmo", json.dumps(without, sort_keys=True))  # noqa: SLF001
    record = a_skipped_record(db, "dji-osmo")

    # 名前だけを変える。**正規形は `stack` を明示的な disabled として書き出す**ので、
    # 生の JSON で比べると「省略 → {enabled: false}」の差として見えてしまう。
    from mediaferry.core.profiles.model import parse_definition

    renamed = definition_to_json(parse_definition({**without, "name": "別の名前"}))
    assert '"stack"' in renamed
    registry._upsert_revision("dji-osmo", renamed)  # noqa: SLF001

    assert state_of(db, record)["stack_state"] == "skipped"


def test_a_stacked_record_is_never_reopened(db):
    from dataclasses import replace

    from mediaferry.core.profiles.model import STACK_DISABLED

    registry = a_copy_with_stacking(db, enabled=True)
    record = a_skipped_record(db, "my-canon")
    db.execute(
        "UPDATE upload_record SET stack_state = 'stacked', stack_reason = NULL,"
        " remote_stack_id = 's' WHERE id = ?",
        (record,),
    )

    current = registry.current("my-canon")
    registry.update("my-canon", replace(current.definition, stack=STACK_DISABLED))

    assert state_of(db, record)["stack_state"] == "stacked"


def test_another_profile_is_untouched(db):
    from dataclasses import replace

    from mediaferry.core.profiles.model import STACK_DISABLED

    registry = a_copy_with_stacking(db, enabled=True)
    record = a_skipped_record(db, "canon-eos")

    current = registry.current("my-canon")
    registry.update("my-canon", replace(current.definition, stack=STACK_DISABLED))

    assert state_of(db, record)["stack_state"] == "skipped"


def test_the_revision_and_the_reopen_are_one_transaction(db, monkeypatch):
    """**版だけ進んで見送りが残る窓を作らない。**"""
    from dataclasses import replace

    from mediaferry.core.profiles.model import StackRule

    registry = a_copy_with_stacking(db, enabled=False)
    record = a_skipped_record(db, "my-canon")
    before = registry.current("my-canon").revision

    from mediaferry.db import profiles as profiles_module

    def explode(*args, **kwargs):
        raise RuntimeError("戻しに失敗した")

    monkeypatch.setattr(profiles_module, "_stack_rule_of", explode)

    with pytest.raises(RuntimeError):
        registry.update(
            "my-canon",
            replace(
                registry.current("my-canon").definition,
                stack=StackRule(enabled=True, extensions=("JPG", "CR2")),
            ),
        )

    assert registry.current("my-canon").revision == before
    assert state_of(db, record)["stack_state"] == "skipped"
