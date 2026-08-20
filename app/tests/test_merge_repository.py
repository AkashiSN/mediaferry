import sqlite3
from datetime import UTC, datetime

import pytest

from mediaferry.core.merge.grouping import GIB, GroupCandidate, MergePart
from mediaferry.db.merges import GroupNotClaimable, GroupNotEditable, MergeRepository
from mediaferry.db.profiles import ProfileRegistry

from .test_schema_artifacts import a_media_file


@pytest.fixture
def profile(db):
    ProfileRegistry(db).sync_builtins()
    return ProfileRegistry(db).current("dji-osmo")


def a_candidate(db, profile, count=2, prefix="DJI"):
    members = []
    for index in range(count):
        rel_path = f"library/dji-osmo/DCIM/{prefix}_{index:04d}_D.MP4"
        sha1 = f"{prefix[0].lower() * 8}{index:032d}"
        media_id = a_media_file(
            db,
            (profile.profile_id, profile.revision_id),
            rel_path=rel_path,
            sha1=sha1,
        )
        members.append(
            MergePart(
                media_file_id=media_id,
                rel_path=rel_path,
                sha1=sha1,
                captured_at=datetime(2026, 8, 17, 14, 30, tzinfo=UTC),
                duration_seconds=1500.0,
                size_bytes=16 * GIB,
                probe_state="ok",
            )
        )
    return GroupCandidate(members=tuple(members), gaps=(2.0,) * (count - 1))


def test_a_detected_group_keeps_its_members_in_order(db, profile):
    repo = MergeRepository(db)
    candidate = a_candidate(db, profile)
    group_id = repo.save_detected(profile, candidate, "digest-1")
    rows = repo.members(group_id)
    assert [row["position"] for row in rows] == [0, 1]
    assert [row["media_file_id"] for row in rows] == [
        part.media_file_id for part in candidate.members
    ]
    assert repo.get(group_id)["status"] == "detected"
    assert repo.get(group_id)["detected_by"] == "auto"


def test_the_same_digest_is_not_stored_twice(db, profile):
    repo = MergeRepository(db)
    candidate = a_candidate(db, profile)
    assert repo.save_detected(profile, candidate, "digest-1") is not None
    assert repo.save_detected(profile, candidate, "digest-1") is None


def test_a_member_of_an_active_group_is_not_taken_again(db, profile):
    repo = MergeRepository(db)
    candidate = a_candidate(db, profile)
    repo.save_detected(profile, candidate, "digest-1")
    # 同じファイルを含む別の構成は作れない（1 ファイル 1 アクティブグループ）。
    assert repo.save_detected(profile, candidate, "digest-2") is None


def test_claiming_moves_the_group_to_merging(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    repo.claim_for_merge(group_id, "digest-1")
    assert repo.get(group_id)["status"] == "merging"


def test_claiming_twice_is_refused(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    repo.claim_for_merge(group_id, "digest-1")
    with pytest.raises(GroupNotClaimable):
        repo.claim_for_merge(group_id, "digest-1")


def test_a_changed_digest_cannot_be_claimed(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    with pytest.raises(GroupNotClaimable):
        repo.claim_for_merge(group_id, "digest-2")


def test_a_failed_group_can_be_claimed_again(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    repo.claim_for_merge(group_id, "digest-1")
    repo.mark_failed(group_id, "ffmpeg が落ちた")
    repo.claim_for_merge(group_id, "digest-1")
    assert repo.get(group_id)["status"] == "merging"
    # 再試行では前回の理由を残さない。
    assert repo.get(group_id)["error"] is None


def test_a_merged_group_cannot_be_claimed_again(db, profile):
    """再結合は旧 output_media_file_id を取り残す. supersede が要るので Phase 4."""
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    output_id = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        role="derived",
        rel_path="derived/dji-osmo/DCIM/MERGED.MP4",
    )
    repo.claim_for_merge(group_id, "digest-1")
    repo.record_verification(group_id, '{"passed": true}', "ffmpeg version X")
    db.execute(
        "UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output_id, group_id)
    )
    repo.mark_merged(group_id)
    with pytest.raises(GroupNotClaimable):
        repo.claim_for_merge(group_id, "digest-1")


def test_recording_a_verification_needs_a_merging_group(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    with pytest.raises(GroupNotClaimable):
        repo.record_verification(group_id, '{"passed": true}', "ffmpeg version X")


def test_marking_merged_needs_an_output_and_a_verification(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    repo.claim_for_merge(group_id, "digest-1")
    # 検証も出力も無い状態では倒せない。呼び出し順のバグで「merged なのに
    # 出力が無い」行を作らせない。
    with pytest.raises(GroupNotClaimable):
        repo.mark_merged(group_id)
    repo.record_verification(group_id, '{"passed": true}', "ffmpeg version X")
    with pytest.raises(GroupNotClaimable):
        repo.mark_merged(group_id)


def test_releasing_puts_the_group_back_to_detected(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    repo.claim_for_merge(group_id, "digest-1")
    repo.release(group_id)
    assert repo.get(group_id)["status"] == "detected"


def test_the_verification_is_recorded_before_the_group_is_merged(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    repo.claim_for_merge(group_id, "digest-1")
    repo.record_verification(group_id, '{"passed": true}', "ffmpeg version X")
    row = repo.get(group_id)
    assert row["verification_json"] == '{"passed": true}'
    assert row["tool_version"] == "ffmpeg version X"
    # まだ merged にはしない。公開が終わってから倒す。
    assert row["status"] == "merging"


def test_adopting_requires_a_merged_group_with_an_output(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    with pytest.raises(GroupNotClaimable):
        repo.adopt(group_id)


def test_adopting_is_idempotent(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    output_id = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        role="derived",
        rel_path="derived/dji-osmo/DCIM/MERGED.MP4",
    )
    repo.claim_for_merge(group_id, "digest-1")
    repo.record_verification(group_id, '{"passed": false}', "ffmpeg version X")
    db.execute(
        "UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output_id, group_id)
    )
    repo.mark_merged(group_id)
    repo.adopt(group_id)
    first = repo.get(group_id)["adopted_at"]
    repo.adopt(group_id)
    assert repo.get(group_id)["adopted_at"] == first


def test_groups_can_be_listed_by_status(db, profile):
    repo = MergeRepository(db)
    first = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    repo.claim_for_merge(first, "digest-1")
    assert [row["id"] for row in repo.list_groups(status="merging")] == [first]
    assert repo.list_groups(status="merged") == []


def test_a_digest_already_taken_by_another_group_is_refused(db, profile):
    # 構成ファイルが違っても、同じ digest なら作らない。作ろうとすると
    # 部分ユニーク索引が IntegrityError を投げ、None を返す契約が壊れる。
    repo = MergeRepository(db)
    assert repo.save_detected(profile, a_candidate(db, profile), "digest-1") is not None
    other = a_candidate(db, profile, prefix="OTHER")
    assert repo.save_detected(profile, other, "digest-1") is None


def test_marking_merged_needs_a_verification_even_with_an_output(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    output_id = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        role="derived",
        rel_path="derived/dji-osmo/DCIM/MERGED.MP4",
    )
    repo.claim_for_merge(group_id, "digest-1")
    db.execute(
        "UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output_id, group_id)
    )
    with pytest.raises(GroupNotClaimable):
        repo.mark_merged(group_id)


def test_adopting_a_merged_group_without_an_output_is_refused(db, profile):
    # mark_merged は出力を要求するが、DB が別経路で merged になった行でも
    # 採用させない（採用は「その出力を送る」という意思表示なので）。
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    db.execute("UPDATE merge_group SET status = 'merged' WHERE id = ?", (group_id,))
    with pytest.raises(GroupNotClaimable):
        repo.adopt(group_id)


def test_releasing_only_moves_a_merging_group(db, profile):
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    # detected からの release は何もしない遷移。黙って成功させない。
    with pytest.raises(GroupNotClaimable):
        repo.release(group_id)
    output_id = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        role="derived",
        rel_path="derived/dji-osmo/DCIM/MERGED.MP4",
    )
    repo.claim_for_merge(group_id, "digest-1")
    repo.record_verification(group_id, '{"passed": true}', "ffmpeg version X")
    db.execute(
        "UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output_id, group_id)
    )
    repo.mark_merged(group_id)
    # 公開済みのグループを detected へ戻すと、出力が宙に浮く。
    with pytest.raises(GroupNotClaimable):
        repo.release(group_id)


def test_regrouping_across_two_groups_is_refused_not_a_crash(db, profile):
    """**2 つのグループを 1 つにまとめる操作は、必ずこの経路を通る**（§13）.

    実機で結合判定を直した後、既に 2 つに割れて検出されていたものを組み直そうと
    して 500 になった。`create_manual` は同じ IntegrityError を捕まえて 409 で
    断っているのに、`supersede` にだけその処理が無かった。
    """
    repo = MergeRepository(db)
    first = repo.save_detected(profile, a_candidate(db, profile, prefix="AAA"), "digest-a")
    second = a_candidate(db, profile, prefix="BBB")
    repo.save_detected(profile, second, "digest-b")
    everyone = [row["media_file_id"] for row in repo.members(first)]
    everyone += [part.media_file_id for part in second.members]

    with pytest.raises(GroupNotEditable):
        repo.supersede(first, everyone, "digest-merged")


def test_discarding_a_group_frees_its_files(db, profile):
    """**破棄したグループはファイルを手放す。** さもないと組み直す経路が無い.

    `active` が `superseded_by_id` だけの写しだった頃は、破棄しても member が
    active のまま残り、再検出の境界になっていた。2 つのグループを 1 つにまとめる
    操作（§13 の supersede）も、相手側の member が active なので必ず 409 になる。
    実機で、割れて検出された 5 パートを組み直せずに詰まった。
    """
    repo = MergeRepository(db)
    candidate = a_candidate(db, profile)
    group_id = repo.save_detected(profile, candidate, "digest-1")
    repo.discard(group_id)
    taken = db.execute("SELECT count(*) FROM merge_member WHERE active = 1").fetchone()[0]
    assert taken == 0


def test_a_freed_file_can_join_a_different_group(db, profile):
    """解放したファイルは、別の構成なら組み直せる（同じ構成は digest が拒む）."""
    repo = MergeRepository(db)
    first = a_candidate(db, profile, count=2)
    repo.discard(repo.save_detected(profile, first, "digest-1"))
    extra = a_candidate(db, profile, count=1, prefix="EXT")
    everyone = [part.media_file_id for part in first.members + extra.members]
    group_id = repo.create_manual(everyone, "digest-2")
    assert group_id is not None
    assert len(repo.members(group_id)) == 3


def test_the_same_grouping_is_not_detected_again_after_a_discard(db, profile):
    """解放しても、捨てたのと同じ構成は作り直さない（`input_digest` が番人）."""
    repo = MergeRepository(db)
    candidate = a_candidate(db, profile)
    repo.discard(repo.save_detected(profile, candidate, "digest-1"))
    assert repo.save_detected(profile, candidate, "digest-1") is None


def test_a_discard_cannot_be_undone(db, profile):
    """戻せると、active と親の状態が乖離する（supersede と同じ理由）."""
    repo = MergeRepository(db)
    group_id = repo.save_detected(profile, a_candidate(db, profile), "digest-1")
    repo.discard(group_id)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE merge_group SET status = 'detected' WHERE id = ?", (group_id,))
