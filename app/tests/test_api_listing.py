"""一覧の絞り込みとページング（§11 / §13）.

**並びを固定する。** 撮影日時だけで並べると、同じ時刻の行がページの境目で
重複したり欠けたりする（`id` で tie-break する）。
"""

from __future__ import annotations

import pytest

from mediaferry.core.listing import escape_like, page_bounds

from .test_schema_artifacts import a_media_file, a_merge_group
from .test_schema_sources import a_profile
from .test_schema_uploads import a_destination, an_upload


@pytest.fixture
def library(db):
    """撮影日時が同じ行を含むライブラリ."""
    profile = a_profile(db, slug="listing-test")
    ids = []
    for index in range(5):
        ids.append(
            a_media_file(
                db,
                profile,
                rel_path=f"library/listing/CLIP_{index}.MP4",
                captured_at=(
                    "2026-08-17T14:30:00+09:00" if index < 3 else "2026-08-18T09:00:00+09:00"
                ),
                kind="video" if index % 2 == 0 else "photo",
                duration_seconds=1.5,
            )
        )
    return ids


# ---------------------------------------------------------------- 純粋な判断
@pytest.mark.parametrize(
    ("page", "size", "expected"),
    [(1, 50, (50, 0)), (2, 50, (50, 50)), (3, 10, (10, 20))],
)
def test_pages_are_translated_to_limit_and_offset(page, size, expected):
    assert page_bounds(page, size) == expected


def test_a_page_size_is_capped():
    """**上限を置く。** 1 回の要求で全件を引かせない."""
    limit, _ = page_bounds(1, 100_000)
    assert limit == 200


def test_a_page_below_one_is_the_first_page():
    assert page_bounds(0, 50) == (50, 0)
    assert page_bounds(-3, 50) == (50, 0)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("clip", "clip"), ("50%", "50\\%"), ("a_b", "a\\_b"), ("100\\%", "100\\\\\\%")],
)
def test_search_wildcards_are_escaped(raw, expected):
    """`%` を打った利用者に全件を返さない（LIKE の記号として効かせない）."""
    assert escape_like(raw) == expected


# ---------------------------------------------------------------- 一覧
def test_the_order_is_stable_across_pages(client, library):
    first = client.get("/api/media?page=1&page_size=2").json()
    second = client.get("/api/media?page=2&page_size=2").json()

    assert first["total"] == 5
    assert len(first["media"]) == 2
    ids = [row["id"] for row in first["media"] + second["media"]]
    # **同じ時刻の行があってもページの境目で重複・欠落しない。**
    assert len(set(ids)) == 4


def test_rows_with_the_same_time_have_a_defined_order(client, library):
    """**同じ撮影日時の中の並びも決める。** 決めないと、実行計画が変わった日に
    ページの境目で行が重複・欠落する（`id` の降順を約束する）."""
    body = client.get(
        "/api/media?page=1&page_size=5&captured_to=2026-08-17T23:59:59%2B09:00"
    ).json()

    ids = [row["id"] for row in body["media"]]
    assert ids == sorted(ids, reverse=True)


def test_an_invalidated_record_does_not_count_as_sent(client, db, library):
    """無効化された記録は「送った」ではない（§10）."""
    destination = a_destination(db, name="invalidated-dest")
    destination_id, revision_id, _ = destination
    an_upload(
        db,
        destination,
        library[0],
        state="complete",
        destination_revision_id=revision_id,
        remote_asset_id="asset-1",
        invalidated_at="2026-08-18T00:00:00+00:00",
        invalidated_reason="宛先を編集した",
    )

    sent = client.get(f"/api/media?destination_id={destination_id}&status=sent").json()
    unsent = client.get(f"/api/media?destination_id={destination_id}&status=unsent").json()

    assert sent["total"] == 0
    assert unsent["total"] == 5


def test_the_total_is_reported_for_the_progress_line(client, library):
    """画面は「12 / 87 件」と出す（§13）."""
    body = client.get("/api/media?page=1&page_size=2").json()
    assert body["total"] == 5
    assert body["page"] == 1
    assert body["page_size"] == 2


def test_media_can_be_filtered_by_kind(client, library):
    body = client.get("/api/media?kind=photo").json()
    assert body["total"] == 2
    assert all(row["kind"] == "photo" for row in body["media"])


def test_media_can_be_filtered_by_capture_date(client, library):
    body = client.get("/api/media?captured_from=2026-08-18").json()
    assert body["total"] == 2


def test_media_can_be_searched_by_name(client, library):
    body = client.get("/api/media?q=CLIP_1").json()
    assert body["total"] == 1
    assert body["media"][0]["rel_path"].endswith("CLIP_1.MP4")


def test_a_search_for_a_wildcard_finds_nothing(client, library):
    """`%` は文字として扱う（打った人に全件を返さない）."""
    assert client.get("/api/media?q=%").json()["total"] == 0


def test_media_can_be_filtered_by_what_a_destination_has(client, db, library):
    """**「宛先 D に未送信」**（§13）."""
    destination = a_destination(db, name="dashboard-dest")
    destination_id, revision_id, _ = destination
    an_upload(
        db,
        destination,
        library[0],
        state="complete",
        destination_revision_id=revision_id,
        remote_asset_id="asset-1",
    )

    sent = client.get(f"/api/media?destination_id={destination_id}&status=sent").json()
    unsent = client.get(f"/api/media?destination_id={destination_id}&status=unsent").json()

    assert [row["id"] for row in sent["media"]] == [library[0]]
    assert sent["total"] == 1
    assert unsent["total"] == 4
    assert library[0] not in [row["id"] for row in unsent["media"]]


def test_a_status_filter_needs_a_destination(client, library):
    """どの宛先での状態かが決まらない要求は断る（黙って全件を返さない）."""
    response = client.get("/api/media?status=sent")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


# ---------------------------------------------------------------- ダッシュボード
def test_the_dashboard_summarises_each_destination(client, db, library):
    destination = a_destination(db, name="summary-dest")
    destination_id, revision_id, _ = destination
    an_upload(
        db,
        destination,
        library[0],
        state="complete",
        destination_revision_id=revision_id,
        remote_asset_id="asset-1",
    )
    an_upload(
        db,
        destination,
        library[1],
        state="failed",
        destination_revision_id=revision_id,
        last_error="だめだった",
    )
    an_upload(
        db,
        destination,
        library[2],
        state="awaiting_datetime_approval",
        destination_revision_id=revision_id,
        remote_asset_id="asset-2",
    )

    body = client.get("/api/dashboard").json()

    [summary] = [row for row in body["destinations"] if row["destination_id"] == destination_id]
    assert summary["complete"] == 1
    assert summary["failed"] == 1
    assert summary["awaiting_approval"] == 1
    # 送っていないもの（記録が無い）も数える —— 画面の「未送信」バッジの元になる。
    assert summary["unsent"] == 2
    assert body["media_total"] == 5


def test_the_dashboard_reports_what_needs_attention(client, db, library):
    body = client.get("/api/dashboard").json()
    assert body["warnings"] == []
    assert body["running_jobs"] == 0
    assert "orphans" in body


def test_media_can_be_filtered_by_profile(client, db, library):
    """プロファイルでの絞り込み.

    **`IN (SELECT ...)` ではなく `= (SELECT ...)` で書く。** `IN` だと SQLite は
    複数の値を取りうると見て、索引があっても並べ替えを外せない（一覧は
    `captured_at DESC, id DESC` 固定なので、`0014` の索引が効かなくなる）。
    slug は UNIQUE なので値は高々 1 つで、意味は変わらない。
    """
    other = a_profile(db, slug="another-profile")
    a_media_file(db, other, rel_path="library/another/CLIP_9.MP4")

    got = client.get("/api/media?profile=listing-test").json()
    assert got["total"] == len(library)
    assert all("library/listing/" in row["rel_path"] for row in got["media"])


def test_an_unknown_profile_matches_nothing(client, library):
    """知らない slug は 0 件（`IN` から `=` へ変えても意味が変わらない）."""
    got = client.get("/api/media?profile=nope").json()
    assert got["total"] == 0
    assert got["media"] == []


def test_stale_derived_outputs_are_listed_for_the_screen(client, db, data_root):
    """置き換えられたグループは `GET /merge-groups` に出ない（`list_groups`）.

    そのグループの「できたファイル」だけを別の経路で出す。**出せなければ
    削除ボタンにも到達できない** —— 実機で 66 GiB がそこに残っていた。
    """
    from .test_schema_artifacts import a_merge_group

    ref = a_profile(db, slug="stale-listing")
    rel = "derived/dji-osmo/DCIM/SUPERSEDED.MP4"
    (data_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (data_root / rel).write_bytes(b"old output")
    derived = a_media_file(db, ref, rel_path=rel, role="derived")
    old_group = a_merge_group(db, ref, "digest-old", status="merged")
    newer = a_merge_group(db, ref, "digest-new")
    db.execute(
        "UPDATE merge_group SET output_media_file_id = ?, superseded_by_id = ? WHERE id = ?",
        (derived, newer, old_group),
    )

    assert all(g["id"] != old_group for g in client.get("/api/merge-groups").json()["groups"])
    got = client.get("/api/media/stale-derived").json()["stale"]
    assert [row["id"] for row in got] == [derived]
    assert got[0]["reason"] == "superseded"
    assert got[0]["rel_path"] == rel


def test_the_stale_listing_is_not_swallowed_by_the_media_route(client, db):
    """**並びの順で API が飲まれる。** `/media/{id}` が先だと id 扱いになる."""
    got = client.get("/api/media/stale-derived")
    assert got.status_code == 200, got.json()
    assert "stale" in got.json()


def test_a_stale_derived_can_be_deleted_and_an_original_cannot(client, db, data_root):
    """やり直しの後片付け（§13）. **元ファイルは対象外.**"""
    from .test_schema_artifacts import a_merge_group

    ref = a_profile(db, slug="delete-test")
    api_db = db
    rel = "derived/dji-osmo/DCIM/OLD.MP4"
    (data_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (data_root / rel).write_bytes(b"old output")
    derived = a_media_file(api_db, ref, rel_path=rel, role="derived")
    group_id = a_merge_group(api_db, ref, "digest-old", status="skipped")
    api_db.execute(
        "UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (derived, group_id)
    )
    original = a_media_file(api_db, ref, rel_path="library/dji-osmo/DCIM/KEEP.MP4")

    assert client.delete(f"/api/media/{derived}").status_code == 200
    assert not (data_root / rel).exists()
    refused = client.delete(f"/api/media/{original}")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "conflict"


# ------------------------------------------------------------- 「まだ送っていない」（§10）
def test_unsent_means_no_record_at_all(client, db):
    """**`failed` は「まだ送っていない」ではない**（再試行は別の操作、§10）."""
    profile = a_profile(db, slug="unsent-test")
    media = a_media_file(db, profile, rel_path="library/unsent/A.JPG")
    destination = a_destination(db, name="unsent-test")
    an_upload(db, destination, media, state="failed")

    body = client.get(f"/api/media?destination_id={destination[0]}&status=unsent").json()
    assert body["total"] == 0


def test_unsent_excludes_members_of_a_live_merge_group(client, db):
    """**構成ファイルは送る候補に出ない**（§10）. 出すと POST /uploads が断る."""
    profile = a_profile(db, slug="unsent-members")
    part = a_media_file(db, profile, rel_path="library/unsent/PART1.MP4")
    group = a_merge_group(db, profile, "digest-1", status="detected")
    db.execute(
        "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
        " VALUES (?, ?, 0, 1)",
        (group, part),
    )
    destination = a_destination(db, name="unsent-members")

    body = client.get(f"/api/media?destination_id={destination[0]}&status=unsent").json()
    assert body["total"] == 0


def test_unsent_includes_the_output_of_a_merged_group(client, db):
    profile = a_profile(db, slug="unsent-output")
    output = a_media_file(db, profile, rel_path="derived/unsent/OUT.MP4", role="derived")
    group = a_merge_group(db, profile, "digest-2", status="merged")
    db.execute(
        "UPDATE merge_group SET output_media_file_id = ?, verification_json = ? WHERE id = ?",
        (output, '{"passed": true}', group),
    )
    destination = a_destination(db, name="unsent-output")

    body = client.get(f"/api/media?destination_id={destination[0]}&status=unsent").json()
    assert [row["id"] for row in body["media"]] == [output]


def test_unsent_excludes_a_derived_that_failed_verification(client, db):
    """**検証に落ちた結合結果は、採用するまで候補に出ない**（§10）."""
    profile = a_profile(db, slug="unsent-failed-verify")
    output = a_media_file(db, profile, rel_path="derived/unsent/BAD.MP4", role="derived")
    group = a_merge_group(db, profile, "digest-3", status="merged")
    db.execute(
        "UPDATE merge_group SET output_media_file_id = ?, verification_json = ? WHERE id = ?",
        (output, '{"passed": false}', group),
    )
    destination = a_destination(db, name="unsent-failed-verify")

    body = client.get(f"/api/media?destination_id={destination[0]}&status=unsent").json()
    assert body["total"] == 0


def test_the_dashboard_counts_unsent_the_same_way(client, db):
    """**2 箇所で意味を変えない**（ホームと写真で数が食い違う）."""
    profile = a_profile(db, slug="unsent-dashboard")
    part = a_media_file(db, profile, rel_path="library/unsent/PART2.MP4")
    group = a_merge_group(db, profile, "digest-4", status="detected")
    db.execute(
        "INSERT INTO merge_member (merge_group_id, media_file_id, position, active)"
        " VALUES (?, ?, 0, 1)",
        (group, part),
    )
    destination = a_destination(db, name="unsent-dashboard")

    listed = client.get(f"/api/media?destination_id={destination[0]}&status=unsent").json()["total"]
    summary = client.get("/api/dashboard").json()["destinations"][0]["unsent"]
    assert summary == listed == 0


def test_unsent_excludes_a_superseded_groups_derived_output(client, db):
    """supersede されたグループの派生物は候補に出ない（§10）.

    supersede した旧グループはまだ `status = merged` のままなので、
    `superseded_by_id IS NULL` を見ないと候補に戻ってしまう。
    """
    profile = a_profile(db, slug="unsent-superseded")
    output = a_media_file(db, profile, rel_path="derived/unsent/SUPERSEDED.MP4", role="derived")
    old_group = a_merge_group(db, profile, "digest-old-superseded", status="merged")
    db.execute(
        "UPDATE merge_group SET output_media_file_id = ?, verification_json = ? WHERE id = ?",
        (output, '{"passed": true}', old_group),
    )
    newer_group = a_merge_group(db, profile, "digest-new-superseded")
    db.execute("UPDATE merge_group SET superseded_by_id = ? WHERE id = ?", (newer_group, old_group))
    destination = a_destination(db, name="unsent-superseded")

    body = client.get(f"/api/media?destination_id={destination[0]}&status=unsent").json()
    assert body["total"] == 0


def test_unsent_includes_an_adopted_derived_that_failed_verification(client, db):
    """採用済みなら検証不合格でも候補に出る（§10）."""
    profile = a_profile(db, slug="unsent-adopted")
    output = a_media_file(db, profile, rel_path="derived/unsent/ADOPTED.MP4", role="derived")
    group = a_merge_group(db, profile, "digest-adopted", status="merged")
    db.execute(
        "UPDATE merge_group SET output_media_file_id = ?, verification_json = ?,"
        " adopted_at = ? WHERE id = ?",
        (output, '{"passed": false}', "2026-08-17T00:00:00+00:00", group),
    )
    destination = a_destination(db, name="unsent-adopted")

    body = client.get(f"/api/media?destination_id={destination[0]}&status=unsent").json()
    assert [row["id"] for row in body["media"]] == [output]


def test_unsent_excludes_a_missing_original(client, db):
    """`missing_at` が入ったファイルは候補に出ない（§10）."""
    profile = a_profile(db, slug="unsent-missing")
    a_media_file(
        db,
        profile,
        rel_path="library/unsent/MISSING.MP4",
        missing_at="2026-08-17T00:00:00+00:00",
    )
    destination = a_destination(db, name="unsent-missing")

    body = client.get(f"/api/media?destination_id={destination[0]}&status=unsent").json()
    assert body["total"] == 0
