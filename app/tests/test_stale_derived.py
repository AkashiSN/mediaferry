"""古くなった派生物の削除（やり直しの後片付け）.

**`design.md` は「孤立ファイルは削除しない」と決めている。** ここが例外なのは、
対象が「**もう誰も参照していない、我々が作った派生物**」に限られるから ——
出所の分からない孤立ファイルとは性質が違う。元ファイルは対象外。
"""

import pytest

from mediaferry.clock import now_iso
from mediaferry.db.media import MediaRepository
from mediaferry.db.merges import GroupNotEditable
from mediaferry.db.profiles import ProfileRegistry

from .test_schema_artifacts import a_media_file, a_merge_group
from .test_schema_sources import a_volume
from .test_schema_uploads import a_destination, an_upload


@pytest.fixture
def world(db, data_root):
    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("dji-osmo")
    ref = (profile.profile_id, profile.revision_id)
    a_volume(db, profile=ref)
    return MediaRepository(db, data_root), profile, ref


def a_derived_with_group(db, data_root, ref, *, status="merged", superseded=False):
    rel = f"derived/dji-osmo/DCIM/OUT_{status}_{superseded}.MP4"
    path = data_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"merged output")
    media_id = a_media_file(db, ref, rel_path=rel, role="derived")
    group_id = a_merge_group(db, ref, f"digest-{status}-{superseded}", status=status)
    db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (media_id, group_id))
    if superseded:
        newer = a_merge_group(db, ref, f"digest-newer-{status}")
        db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (newer, group_id))
    return media_id, group_id, path


def test_a_superseded_output_can_be_deleted(world, db, data_root):
    """やり直した後の古い出力（実機では 74 GiB 残った）."""
    repo, _, ref = world
    media_id, group_id, path = a_derived_with_group(db, data_root, ref, superseded=True)
    repo.delete_stale_derived(media_id)
    assert not path.exists()
    remaining = db.execute("SELECT count(*) FROM media_file WHERE id = ?", (media_id,)).fetchone()[
        0
    ]
    assert remaining == 0
    # 出力の記録も外す（実体が無いのに指し続けない）。
    assert (
        db.execute(
            "SELECT output_media_file_id FROM merge_group WHERE id = ?", (group_id,)
        ).fetchone()[0]
        is None
    )


def test_a_discarded_groups_output_can_be_deleted(world, db, data_root):
    repo, _, ref = world
    media_id, _, path = a_derived_with_group(db, data_root, ref, status="skipped")
    repo.delete_stale_derived(media_id)
    assert not path.exists()


def test_the_output_of_a_live_group_is_kept(world, db, data_root):
    """**現行のグループの出力は消せない。** 選択肢に出ているものを消さない."""
    repo, _, ref = world
    media_id, _, path = a_derived_with_group(db, data_root, ref)
    with pytest.raises(GroupNotEditable):
        repo.delete_stale_derived(media_id)
    assert path.exists()


def test_an_original_is_never_deleted(world, db, data_root):
    """**元ファイルは対象外。** 取り込んだものを消す経路は作らない.

    **`role` の検査だけが立ちはだかる状態を作る。** 素直に書くと元ファイルには
    持ち主のグループが無いので、`role` を見なくても「出所が分からない」で断られ、
    **検査同士が互いをマスクする**（変異が素通りする）。
    """
    repo, _, ref = world
    rel = "library/dji-osmo/DCIM/A.MP4"
    (data_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (data_root / rel).write_bytes(b"original")
    media_id = a_media_file(db, ref, rel_path=rel)
    group_id = a_merge_group(db, ref, "digest-points-at-an-original", status="skipped")
    db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (media_id, group_id))
    with pytest.raises(GroupNotEditable):
        repo.delete_stale_derived(media_id)
    assert (data_root / rel).exists()


def test_a_derived_that_was_sent_is_kept(world, db, data_root):
    """送信の記録が指しているものは消さない（何を送ったか分からなくなる）."""
    from .test_schema_uploads import a_destination, an_upload

    repo, _, ref = world
    media_id, _, path = a_derived_with_group(db, data_root, ref, superseded=True)
    destination = a_destination(db)
    an_upload(db, destination, media_id)
    with pytest.raises(GroupNotEditable):
        repo.delete_stale_derived(media_id)
    assert path.exists()


def test_a_derived_with_no_owner_is_kept(world, db, data_root):
    """出所の分からない派生物は孤立と同じ扱い（画面に出して判断を仰ぐ）."""
    repo, _, ref = world
    rel = "derived/dji-osmo/DCIM/ORPHAN.MP4"
    (data_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (data_root / rel).write_bytes(b"x")
    media_id = a_media_file(db, ref, rel_path=rel, role="derived")
    with pytest.raises(GroupNotEditable):
        repo.delete_stale_derived(media_id)


# ----------------------------------------------------------------------
# 画面へ出す経路（`list_stale_derived`）
#
# **消せるのに画面から辿れなければ、無いのと同じ。** `list_groups` は
# `superseded_by_id` を持つ行をどの場合も返さないので、置き換えられたグループの
# 「できたファイル」は結合画面に出ない —— 実機で 66 GiB がそこに残っていた。
# **一覧の条件は `delete_stale_derived` の前提そのものにする**（規則を 2 か所に
# 分けない）。


def test_the_listing_shows_a_superseded_output(world, db, data_root):
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref, superseded=True)
    got = repo.list_stale_derived()
    assert [row["id"] for row in got] == [media_id]
    assert got[0]["rel_path"].endswith(".MP4")
    assert got[0]["reason"] == "superseded"


def test_the_listing_shows_a_discarded_groups_output(world, db, data_root):
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref, status="skipped")
    got = repo.list_stale_derived()
    assert [row["id"] for row in got] == [media_id]
    assert got[0]["reason"] == "skipped"


def test_the_listing_hides_the_output_of_a_live_group(world, db, data_root):
    """現行の出力を「もう使われていない」欄に出すと、消してよいものに見える."""
    repo, _, ref = world
    a_derived_with_group(db, data_root, ref)
    assert repo.list_stale_derived() == []


def test_the_listing_hides_an_original(world, db, data_root):
    """**`role` の検査だけが立ちはだかる形で見る**（削除側と同じ理由）."""
    repo, _, ref = world
    rel = "library/dji-osmo/DCIM/A.MP4"
    (data_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (data_root / rel).write_bytes(b"original")
    media_id = a_media_file(db, ref, rel_path=rel)
    group_id = a_merge_group(db, ref, "digest-points-at-an-original", status="skipped")
    db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (media_id, group_id))
    assert repo.list_stale_derived() == []


def test_the_listing_hides_a_derived_that_was_sent(world, db, data_root):
    """消せないものを出すと、押しても 409 で断られるボタンが並ぶ."""
    from .test_schema_uploads import a_destination, an_upload

    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref, superseded=True)
    an_upload(db, a_destination(db), media_id)
    assert repo.list_stale_derived() == []


def test_the_listing_hides_a_derived_with_no_owner(world, db, data_root):
    """出所の分からない派生物は孤立の側（`GET /orphans`）で扱う."""
    repo, _, ref = world
    rel = "derived/dji-osmo/DCIM/ORPHAN.MP4"
    (data_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (data_root / rel).write_bytes(b"x")
    a_media_file(db, ref, rel_path=rel, role="derived")
    assert repo.list_stale_derived() == []


# ----------------------------------------------------------------------
# 消せない理由を 1 か所で決める（`deletion_blocker`）
#
# **一覧・詳細・DELETE の 3 つがこの 1 つの判定を使う。** 押しても 409 で断られる
# ボタンを並べないため、規則を 2 か所に分けない。


def test_a_derived_never_sent_can_be_deleted(world, db, data_root):
    """一度も送っていない結合物は消せる."""
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    assert repo.deletion_blocker(media_id) is None


def test_a_derived_living_in_immich_is_kept(world, db, data_root):
    """**Immich に実在するものは消せない.** 何を送ったのかが分からなくなる."""
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    an_upload(
        db,
        dest,
        media_id,
        state="complete",
        remote_asset_id="asset-1",
        remote_is_trashed=0,
        remote_checked_at=now_iso(),
        destination_revision_id=dest[1],
    )
    assert repo.deletion_blocker(media_id) == "Immich に入っている"


def test_a_derived_in_the_immich_trash_can_be_deleted(world, db, data_root):
    """**ゴミ箱は「無い」扱い**（利用者の判断）. Immich で捨てたのだから消せる."""
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    an_upload(
        db,
        dest,
        media_id,
        state="complete",
        remote_asset_id="asset-1",
        remote_is_trashed=1,
        remote_checked_at=now_iso(),
        destination_revision_id=dest[1],
    )
    assert repo.deletion_blocker(media_id) is None


def test_a_derived_that_vanished_from_immich_can_be_deleted(world, db, data_root):
    """再確認でサーバに無いと分かった記録（`remote_asset_id` が外れている）."""
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    an_upload(
        db,
        dest,
        media_id,
        state="complete",
        remote_asset_id=None,
        remote_checked_at=now_iso(),
        destination_revision_id=dest[1],
    )
    assert repo.deletion_blocker(media_id) is None


def test_an_unobserved_complete_is_kept(world, db, data_root):
    """**「無い」には観測を要求する.**

    `0007` を適用した DB には「向き先の記録が無い complete」が残っている。
    **Immich に在るのに識別子を捨てただけ**かもしれないので消さない
    （`POST /uploads/{id}/requeue` が同じ 2 列で選んでいるのと条件をそろえる）。
    """
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    an_upload(
        db,
        dest,
        media_id,
        state="complete",
        remote_asset_id=None,
        remote_checked_at=None,
        destination_revision_id=dest[1],
    )
    assert repo.deletion_blocker(media_id) == "Immich にあるかどうかを確かめていない"


def test_a_derived_being_sent_is_kept(world, db, data_root):
    """送信中は決着していない."""
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    an_upload(db, dest, media_id, state="pending")
    assert repo.deletion_blocker(media_id) == "送信中か、確認を待っている記録がある"


def test_an_invalidated_record_does_not_keep_a_derived(world, db, data_root):
    """**無効化された記録は数えない**（§10）."""
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    an_upload(
        db,
        dest,
        media_id,
        state="complete",
        remote_asset_id="asset-1",
        remote_is_trashed=0,
        remote_checked_at=now_iso(),
        destination_revision_id=dest[1],
        invalidated_at=now_iso(),
        invalidated_reason="試験",
    )
    assert repo.deletion_blocker(media_id) is None


def test_an_original_can_never_be_deleted(world, db, data_root):
    """カードから取り込んだ元ファイルは対象外."""
    repo, _, ref = world
    media_id = a_media_file(db, ref, role="original")
    assert repo.deletion_blocker(media_id) == "取り込んだ元ファイルは消せない"


def test_a_failed_record_does_not_keep_a_derived(world, db, data_root):
    """送れなかった記録は、リモートに何も残していない."""
    repo, _, ref = world
    media_id, _, _ = a_derived_with_group(db, data_root, ref)
    dest = a_destination(db)
    an_upload(db, dest, media_id, state="failed", last_error="つながらない")
    assert repo.deletion_blocker(media_id) is None


def test_a_derived_whose_current_groups_member_is_being_sent_is_kept(world, db, data_root):
    """出力自体は決着していても、元になった構成ファイルがまだ送信中なら消さない.

    削除の実装（Task 2）は既存の `MergeRepository._assert_editable` を通り、
    そこは構成ファイル（member）が送信中だと断る。ここで見ておかないと、
    画面が「消せます」と言った直後に 409 になる。
    """
    repo, _, ref = world
    media_id, group_id, _ = a_derived_with_group(db, data_root, ref)
    member_id = a_media_file(db, ref, role="original")
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group_id, member_id))
    dest = a_destination(db)
    an_upload(db, dest, member_id, state="pending")
    assert repo.deletion_blocker(media_id) == "元になったファイルを送信中か、確認を待っている"
