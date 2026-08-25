"""一覧の絞り込みとページング（§11 / §13）.

**並びを固定する。** 撮影日時だけで並べると、同じ時刻の行がページの境目で
重複したり欠けたりする（`id` で tie-break する）。
"""

from __future__ import annotations

import pytest

from mediaferry.clock import now_iso
from mediaferry.core.listing import escape_like, page_bounds
from mediaferry.ids import new_id

from .test_schema_artifacts import a_media_file, a_merge_group
from .test_schema_sources import a_profile
from .test_schema_uploads import a_destination, an_upload


@pytest.fixture
def ref(db):
    """くわしくのテスト用に、使い回すプロファイル 1 つ."""
    return a_profile(db, slug="media-detail-test")


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


def test_media_can_be_filtered_to_merged_videos(client, db):
    """写真タブの「つないだ動画」の絞り込み."""
    ref = a_profile(db, slug="role-filter-test")
    a_media_file(db, ref, role="original", rel_path="library/dji-osmo/DCIM/A.MP4")
    a_media_file(db, ref, role="derived", rel_path="derived/dji-osmo/DCIM/OUT.MP4")

    body = client.get("/api/media?role=derived").json()

    assert body["total"] == 1
    assert [row["role"] for row in body["media"]] == ["derived"]


def test_an_unknown_role_matches_nothing(client, db):
    """**知らない値で全件を返さない.** 絞ったつもりが絞れていない、を作らない."""
    ref = a_profile(db, slug="unknown-role-test")
    a_media_file(db, ref, role="original")
    assert client.get("/api/media?role=nonsense").json()["total"] == 0


def test_a_role_value_cannot_break_out_of_the_literal(client, db):
    """`role` は既知の 2 値だけリテラルで埋める（`0023`）. **文字列連結の穴を作らない.**

    既知の語彙以外は SQL に触れさせず常に 0 件にする。壊れていれば、この文字列は
    `WHERE m.role = ''` の外へ抜け出して全件を返してしまう。
    """
    ref = a_profile(db, slug="role-injection-test")
    a_media_file(db, ref, role="original")
    a_media_file(db, ref, role="derived")
    body = client.get("/api/media", params={"role": "derived' OR '1'='1"}).json()
    assert body["total"] == 0


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


# ---------------------------------------------------------------- くわしく（`GET /media/{id}`）
def test_media_detail_lists_the_files_it_was_made_from(client, db, data_root, ref):
    """くわしく画面は、元になったファイルを **`position` 順** に出す."""
    first = a_media_file(db, ref, rel_path="library/dji-osmo/DCIM/A.MP4")
    second = a_media_file(db, ref, rel_path="library/dji-osmo/DCIM/B.MP4")
    output = a_media_file(db, ref, role="derived", rel_path="derived/dji-osmo/DCIM/OUT.MP4")
    group = a_merge_group(db, ref, "digest-1", status="merged")
    db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output, group))
    # **わざと逆順に入れる** —— 挿入順で通ってしまう試験にしない。
    db.execute("INSERT INTO merge_member VALUES (?, ?, 1, 1)", (group, second))
    db.execute("INSERT INTO merge_member VALUES (?, ?, 0, 1)", (group, first))

    body = client.get(f"/api/media/{output}").json()

    assert [s["rel_path"] for s in body["sources"]] == [
        "library/dji-osmo/DCIM/A.MP4",
        "library/dji-osmo/DCIM/B.MP4",
    ]


def test_media_detail_says_whether_it_can_be_deleted(client, db, data_root, ref):
    """**押しても 409 で断られるボタンを並べない.** 判定はサーバが返す."""
    output = a_media_file(db, ref, role="derived", rel_path="derived/dji-osmo/DCIM/OUT.MP4")
    group = a_merge_group(db, ref, "digest-1", status="merged")
    db.execute("UPDATE merge_group SET output_media_file_id = ? WHERE id = ?", (output, group))
    dest = a_destination(db)
    an_upload(
        db,
        dest,
        output,
        state="complete",
        destination_revision_id=dest[1],
        remote_asset_id="asset-1",
        remote_is_trashed=0,
        remote_checked_at=now_iso(),
    )

    body = client.get(f"/api/media/{output}").json()

    assert body["deletable"] is False
    assert body["delete_blocked_reason"] == "Immich に入っている"
    assert [d["presence"] for d in body["destinations"]] == ["present"]


def test_media_detail_marks_a_trashed_asset(client, db, ref):
    output = a_media_file(db, ref, role="derived")
    dest = a_destination(db)
    an_upload(
        db,
        dest,
        output,
        state="complete",
        destination_revision_id=dest[1],
        remote_asset_id="asset-1",
        remote_is_trashed=1,
        remote_checked_at=now_iso(),
    )

    body = client.get(f"/api/media/{output}").json()

    assert [d["presence"] for d in body["destinations"]] == ["trashed"]
    assert body["deletable"] is True


def test_media_detail_marks_a_record_still_in_flight(client, db, ref):
    """送信中・確認待ちの記録は `sending`（`pending` は既定値そのもの）."""
    output = a_media_file(db, ref, role="derived")
    an_upload(db, a_destination(db), output)

    body = client.get(f"/api/media/{output}").json()

    assert [d["presence"] for d in body["destinations"]] == ["sending"]


def test_media_detail_marks_a_failed_upload(client, db, ref):
    output = a_media_file(db, ref, role="derived")
    an_upload(db, a_destination(db), output, state="failed")

    body = client.get(f"/api/media/{output}").json()

    assert [d["presence"] for d in body["destinations"]] == ["failed"]


def test_media_detail_marks_an_unswept_complete_record_as_unknown(client, db, ref):
    """`0007` で洗った、識別子も確認時刻も無い `complete` は `unknown`."""
    output = a_media_file(db, ref, role="derived")
    dest = a_destination(db)
    an_upload(
        db,
        dest,
        output,
        state="complete",
        destination_revision_id=dest[1],
        remote_asset_id=None,
        remote_checked_at=None,
    )

    body = client.get(f"/api/media/{output}").json()

    assert [d["presence"] for d in body["destinations"]] == ["unknown"]


def test_media_detail_marks_a_reverified_absence_as_gone(client, db, ref):
    """再確認で識別子が外れ、確認時刻はある `complete` は `gone`."""
    output = a_media_file(db, ref, role="derived")
    dest = a_destination(db)
    an_upload(
        db,
        dest,
        output,
        state="complete",
        destination_revision_id=dest[1],
        remote_asset_id=None,
        remote_checked_at=now_iso(),
    )

    body = client.get(f"/api/media/{output}").json()

    assert [d["presence"] for d in body["destinations"]] == ["gone"]


def test_media_detail_of_an_original_has_no_sources(client, db, ref):
    """元ファイルは何からも作られていない."""
    body = client.get(f"/api/media/{a_media_file(db, ref)}").json()
    assert body["sources"] == []
    assert body["deletable"] is False


def test_media_detail_does_not_show_an_invalidated_record(client, db, ref):
    """無効化された記録は状況として出さない（§10）."""
    output = a_media_file(db, ref, role="derived")
    dest = a_destination(db)
    an_upload(
        db,
        dest,
        output,
        state="complete",
        destination_revision_id=dest[1],
        remote_asset_id="asset-1",
        remote_is_trashed=0,
        remote_checked_at=now_iso(),
        invalidated_at=now_iso(),
    )

    body = client.get(f"/api/media/{output}").json()

    assert [d["presence"] for d in body["destinations"]] == ["not_sent"]


def test_media_detail_hides_an_archived_destination_with_no_record(client, db, ref):
    """**退役した宛先は出さない** —— 押しようのない「まだ送っていません」を並べない."""
    output = a_media_file(db, ref, role="derived")
    dest = a_destination(db, name="retiring")
    db.execute("UPDATE upload_destination SET archived_at = ? WHERE id = ?", (now_iso(), dest[0]))

    body = client.get(f"/api/media/{output}").json()

    assert body["destinations"] == []


def test_media_detail_still_shows_an_archived_destination_with_a_record(client, db, ref):
    """**記録があるなら履歴として出す.** 退役したことと、送った事実は別."""
    output = a_media_file(db, ref, role="derived")
    dest = a_destination(db, name="retired-with-history")
    an_upload(
        db,
        dest,
        output,
        state="complete",
        destination_revision_id=dest[1],
        remote_asset_id="asset-1",
        remote_is_trashed=0,
        remote_checked_at=now_iso(),
    )
    db.execute("UPDATE upload_destination SET archived_at = ? WHERE id = ?", (now_iso(), dest[0]))

    body = client.get(f"/api/media/{output}").json()

    assert [d["presence"] for d in body["destinations"]] == ["present"]


def test_media_detail_folds_multiple_epochs_of_the_same_destination_into_one_row(client, db, ref):
    """向き先が変わっても、旧 epoch の `complete` は履歴として invalidate されない
    （`db/destinations.py` の `_invalidate_old_epoch_locked` は `state <> 'complete'`
    だけを破棄する）。**1 宛先 1 行に畳み、生きている状態を優先する** —— 画面に
    出る状況と `deletable` / `delete_blocked_reason` が食い違わないようにする。
    """
    output = a_media_file(db, ref, role="derived")
    dest_id, old_revision_id, credential_id = a_destination(db, name="moved", epoch=1)
    new_revision_id = new_id()
    db.execute(
        "INSERT INTO destination_revision (id, destination_id, revision, target_epoch,"
        " base_url, credential_id, created_at) VALUES (?, ?, 2, 2, 'http://immich.invalid', ?, ?)",
        (new_revision_id, dest_id, credential_id, now_iso()),
    )
    db.execute(
        "UPDATE upload_destination SET current_revision_id = ? WHERE id = ?",
        (new_revision_id, dest_id),
    )
    # 旧 epoch: Immich に入っている（invalidate されず残る）。
    an_upload(
        db,
        (dest_id, old_revision_id, credential_id),
        output,
        target_epoch=1,
        state="complete",
        destination_revision_id=old_revision_id,
        remote_asset_id="asset-old",
        remote_is_trashed=0,
        remote_checked_at=now_iso(),
    )
    # 新 epoch: 送信中（既定の `pending`）。
    an_upload(
        db,
        (dest_id, new_revision_id, credential_id),
        output,
        target_epoch=2,
    )

    body = client.get(f"/api/media/{output}").json()

    # (a) 同じ宛先が 2 行に分かれない。
    assert len(body["destinations"]) == 1
    # (b) 「生きている」状態（present）が優先される。
    assert body["destinations"][0]["presence"] == "present"
    # (c) 画面の状況（present）と削除の可否が食い違わない —— present な記録が
    # 残っている以上、必ず消せない。理由の文言は `deletion_blocker` が
    # 「当てはまりの強い順」（決着していない記録を最優先）に選ぶため、旧 epoch の
    # `present` ではなく新 epoch の `pending`（送信中）の方になる。理由の文言は
    # 畳んだ presence とは別に決まるが、**消せるか／消せないかの判定は必ず一致する**。
    assert body["deletable"] is False
    assert body["delete_blocked_reason"] == "送信中か、確認を待っている記録がある"
