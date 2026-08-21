"""古くなった派生物の削除（やり直しの後片付け）.

**`design.md` は「孤立ファイルは削除しない」と決めている。** ここが例外なのは、
対象が「**もう誰も参照していない、我々が作った派生物**」に限られるから ——
出所の分からない孤立ファイルとは性質が違う。元ファイルは対象外。
"""

import pytest

from mediaferry.db.media import MediaRepository
from mediaferry.db.merges import GroupNotEditable
from mediaferry.db.profiles import ProfileRegistry

from .test_schema_artifacts import a_media_file, a_merge_group
from .test_schema_sources import a_volume


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
