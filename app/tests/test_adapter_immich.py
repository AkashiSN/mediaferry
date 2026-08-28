import base64
import hashlib

import pytest

from mediaferry.adapters.immich import (
    ImmichAuthFailed,
    ImmichClient,
    ImmichProtocolError,
    ImmichRedirected,
    ImmichRejected,
    ImmichUnavailable,
    to_base64_checksum,
)

from .fake_immich import API_KEY


@pytest.fixture
def client(immich):
    with ImmichClient(immich.url, API_KEY) as client:
        yield client


def a_file(tmp_path, payload=b"movie-bytes"):
    path = tmp_path / "DJI_0001.MP4"
    path.write_bytes(payload)
    return path, hashlib.sha1(payload, usedforsecurity=False).hexdigest()


def an_upload(client, path, sha1):
    return client.upload_asset(
        path,
        sha1_hex=sha1,
        device_asset_id="mediaferry:m1",
        file_created_at="2026-08-17T14:30:00+00:00",
        file_modified_at="2026-08-17T14:30:00+00:00",
    )


def test_the_identity_of_the_target_is_read(client, immich):
    assert client.users_me()["id"] == immich.user_id


def test_a_wrong_api_key_is_an_auth_failure(immich):
    with ImmichClient(immich.url, "wrong") as client, pytest.raises(ImmichAuthFailed):
        client.users_me()


def test_an_unknown_checksum_is_accepted(client, tmp_path):
    _, sha1 = a_file(tmp_path)
    outcome = client.bulk_upload_check([("k1", sha1)])["k1"]
    assert outcome.action == "accept"
    assert outcome.asset_id is None


def test_a_known_checksum_comes_back_with_its_asset_id(client, tmp_path):
    path, sha1 = a_file(tmp_path)
    uploaded = an_upload(client, path, sha1)
    outcome = client.bulk_upload_check([("k1", sha1)])["k1"]
    assert outcome.action == "reject"
    assert outcome.asset_id == uploaded.asset_id
    assert outcome.is_trashed is False


def test_a_trashed_asset_is_reported_as_trashed(client, immich, tmp_path):
    path, sha1 = a_file(tmp_path)
    uploaded = an_upload(client, path, sha1)
    immich.trashed.add(uploaded.asset_id)
    assert client.bulk_upload_check([("k1", sha1)])["k1"].is_trashed is True


def test_the_checksum_is_sent_as_base64_in_both_places(client, immich, tmp_path):
    path, sha1 = a_file(tmp_path)
    an_upload(client, path, sha1)
    # fake は base64 のヘッダしか受理しない（400 なら upload_asset が送出する）。
    expected = base64.b64encode(bytes.fromhex(sha1)).decode()
    assert to_base64_checksum(sha1) == expected
    assert list(immich.assets) == [expected]


def test_the_upload_carries_the_device_asset_id(client, immich, tmp_path):
    path, sha1 = a_file(tmp_path)
    an_upload(client, path, sha1)
    assert immich.uploads[0]["deviceAssetId"] == "mediaferry:m1"
    assert immich.uploads[0]["fileCreatedAt"] == "2026-08-17T14:30:00+00:00"


def test_a_second_upload_of_the_same_bytes_is_a_duplicate(client, tmp_path):
    path, sha1 = a_file(tmp_path)
    first = an_upload(client, path, sha1)
    second = an_upload(client, path, sha1)
    assert first.status == "created"
    assert second.status == "duplicate"
    assert second.asset_id == first.asset_id


def test_a_large_file_goes_through_in_one_piece(client, immich, tmp_path):
    """8 MiB を送っても、途中で切れずに届く（ストリーミング送信の経路）."""
    path, sha1 = a_file(tmp_path, b"x" * (8 * 1024 * 1024))
    an_upload(client, path, sha1)
    assert immich.uploads[0]["size"] == 8 * 1024 * 1024


def test_a_tag_is_created_once_and_reused(client, immich):
    first = client.ensure_tag("mediaferry")
    second = client.ensure_tag("mediaferry")
    assert first == second
    assert immich.requests.count(("POST", "/api/tags")) == 1


def test_assets_are_added_to_a_tag(client, immich):
    tag_id = client.ensure_tag("mediaferry")
    client.tag_assets(tag_id, ["asset-1", "asset-2"])
    assert immich.tagged[tag_id] == ["asset-1", "asset-2"]


def test_the_capture_time_can_be_written_back(client, immich):
    client.set_date_time_original("asset-1", "2026-08-17T14:30:00+09:00")
    assert immich.datetimes["asset-1"] == "2026-08-17T14:30:00+09:00"


def test_a_redirect_to_another_host_never_gets_the_key(client, immich):
    immich.redirect_to = "http://immich-evil.invalid/api/users/me"
    with pytest.raises(ImmichRedirected):
        client.users_me()


def test_an_upload_is_never_redirected_even_within_the_same_origin(client, immich, tmp_path):
    """本文を伴う要求は追わない. ファイルは 1 回目で EOF に達している."""
    path, sha1 = a_file(tmp_path)
    immich.redirect_to = f"{immich.url}/api/assets"
    with pytest.raises(ImmichRedirected):
        an_upload(client, path, sha1)


def test_a_server_error_is_unavailable_not_rejected(client, immich):
    immich.fail_next = 1
    with pytest.raises(ImmichUnavailable):
        client.users_me()


def test_a_large_check_is_split_into_batches(client, immich):
    from mediaferry.adapters.immich import BULK_CHECK_BATCH

    items = [(f"k{i}", f"{i:040x}") for i in range(BULK_CHECK_BATCH + 5)]
    outcomes = client.bulk_upload_check(items)
    assert len(outcomes) == len(items)
    assert immich.requests.count(("POST", "/api/assets/bulk-upload-check")) == 2


def test_a_missing_result_is_a_protocol_error(client, immich, monkeypatch):
    """件数が合わない応答を黙って読み飛ばさない."""
    real = immich._bulk_check  # noqa: SLF001

    def drop_one(payload):
        body = real(payload)
        body["results"] = body["results"][:-1]
        return body

    monkeypatch.setattr(immich, "_bulk_check", drop_one)
    with pytest.raises(ImmichProtocolError):
        client.bulk_upload_check([("k1", "0" * 40), ("k2", "1" * 40)])


def test_an_unknown_action_is_a_protocol_error(client, immich, monkeypatch):
    monkeypatch.setattr(
        immich, "_bulk_check", lambda payload: {"results": [{"id": "k1", "action": "maybe"}]}
    )
    with pytest.raises(ImmichProtocolError):
        client.bulk_upload_check([("k1", "0" * 40)])


def test_a_reject_without_an_asset_id_is_a_protocol_error(client, immich, monkeypatch):
    monkeypatch.setattr(
        immich,
        "_bulk_check",
        lambda payload: {
            "results": [
                # isTrashed は入れておく（入れないと、そちらの検査が先に効いて
                # assetId の検査を通らない）。
                {"id": "k1", "action": "reject", "reason": "duplicate", "isTrashed": False}
            ]
        },
    )
    with pytest.raises(ImmichProtocolError):
        client.bulk_upload_check([("k1", "0" * 40)])


def test_a_malformed_response_is_a_protocol_error(client, immich, monkeypatch):
    """scalar や配列を返す相手でも、プロトコル不一致として分類できる."""
    monkeypatch.setattr(immich, "_bulk_check", lambda payload: ["not", "an", "object"])
    with pytest.raises(ImmichProtocolError):
        client.bulk_upload_check([("k1", "0" * 40)])


def test_a_scalar_result_item_is_a_protocol_error(client, immich, monkeypatch):
    """要素ごとの型も見る（`.get()` を呼んで AttributeError にしない）."""
    monkeypatch.setattr(immich, "_bulk_check", lambda payload: {"results": ["nope"]})
    with pytest.raises(ImmichProtocolError):
        client.bulk_upload_check([("k1", "0" * 40)])


def test_an_unknown_upload_status_is_a_protocol_error(client, immich, tmp_path, monkeypatch):
    path, sha1 = a_file(tmp_path)
    monkeypatch.setattr(
        immich, "_upload", lambda body, headers: (201, {"id": "asset-x", "status": "queued"})
    )
    with pytest.raises(ImmichProtocolError):
        an_upload(client, path, sha1)


def test_the_error_text_never_carries_the_response_body(client, immich):
    """相手の応答本文を例外に載せない. 受け取った API キーを返す相手がいる."""
    immich.echo_key_in_error = True
    with pytest.raises(ImmichRejected) as caught:
        client.users_me()
    assert API_KEY not in str(caught.value)
    assert "400" in str(caught.value)


def test_a_reject_without_is_trashed_is_a_protocol_error(client, immich, monkeypatch):
    """`isTrashed` の欠落を False に丸めない. 消された資産を送信済みにしてしまう."""
    monkeypatch.setattr(
        immich,
        "_bulk_check",
        lambda payload: {
            "results": [
                {"id": "k1", "action": "reject", "reason": "duplicate", "assetId": "asset-1"}
            ]
        },
    )
    with pytest.raises(ImmichProtocolError):
        client.bulk_upload_check([("k1", "0" * 40)])


# --- スタック（Phase 6 / §9.11） ----------------------------------------


def test_an_asset_reports_its_stack(client, immich):
    immich.stacks["stack-1"] = {"primary": "asset-1", "assets": ["asset-1", "asset-2"]}
    asset = client.asset("asset-2")
    assert asset.stack_id == "stack-1"
    assert asset.stack_primary_asset_id == "asset-1"


def test_an_asset_without_a_stack_reports_none(client):
    asset = client.asset("asset-1")
    assert asset.stack_id is None
    assert asset.stack_primary_asset_id is None


def test_a_malformed_stack_field_is_a_protocol_error(client, immich):
    """**キーが無いのは旧版で None、あるのに形が違うのは protocol error。**"""
    immich.malformed_stack_field = True
    with pytest.raises(ImmichProtocolError):
        client.asset("asset-1")


def test_creating_a_stack_returns_its_members(client):
    stack = client.create_stack(["asset-1", "asset-2"])
    assert set(stack.asset_ids) == {"asset-1", "asset-2"}
    assert stack.primary_asset_id in stack.asset_ids


def test_a_stack_can_be_read_back_by_its_primary(client):
    created = client.create_stack(["asset-1", "asset-2"])
    found = client.stack_by_primary(created.primary_asset_id)
    assert found is not None
    assert found.stack_id == created.stack_id
    assert set(found.asset_ids) == set(created.asset_ids)


def test_an_unknown_primary_has_no_stack(client):
    assert client.stack_by_primary("asset-9") is None


def test_the_primary_can_be_moved(client):
    created = client.create_stack(["asset-1", "asset-2"])
    other = next(a for a in created.asset_ids if a != created.primary_asset_id)
    client.set_stack_primary(created.stack_id, other)
    moved = client.stack_by_primary(other)
    assert moved is not None and moved.stack_id == created.stack_id


def test_identifiers_from_the_peer_are_validated(client, immich):
    """**相手が選べる値を経路へ組み立てない**（§14。既存の `_identifier` と同じ扱い）."""
    immich.echo_key_as_ids = True
    with pytest.raises(ImmichProtocolError):
        client.create_stack(["asset-1", "asset-2"])


def test_a_response_without_assets_is_a_protocol_error(client, immich):
    immich.stack_response_without_assets = True
    with pytest.raises(ImmichProtocolError):
        client.create_stack(["asset-1", "asset-2"])


def test_a_response_with_a_different_set_is_a_protocol_error(client, immich):
    """**吸収の仕様がある以上、違う集合が返ったら「別のものを作った」。**"""
    immich.drop_one_asset_from_the_stack_response = True
    with pytest.raises(ImmichProtocolError):
        client.create_stack(["asset-1", "asset-2"])


def test_a_primary_outside_the_members_is_a_protocol_error(client, immich):
    immich.primary_outside_the_stack = True
    with pytest.raises(ImmichProtocolError):
        client.create_stack(["asset-1", "asset-2"])


def test_duplicate_inputs_are_refused_before_sending(client, immich):
    """入力の重複を先に閉じる（[A, A, B] を送って [A, B] が返ると集合比較が通る）."""
    with pytest.raises(ValueError):
        client.create_stack(["asset-1", "asset-1"])
    assert immich.requests == []


def test_the_create_does_not_follow_a_redirect(client, immich):
    """**非冪等で吸収する要求を自動 replay させない**（303 でも method は変わらない）."""
    immich.redirect_to = "/api/stacks"
    with pytest.raises(ImmichRedirected):
        client.create_stack(["asset-1", "asset-2"])


def test_a_broken_list_response_is_a_protocol_error(client, immich):
    """`response.json()` を直に呼ぶと `ValueError` が漏れて分岐に入らない."""
    immich.stack_list_is_not_json = True
    with pytest.raises(ImmichProtocolError):
        client.stack_by_primary("asset-1")


def test_a_non_object_element_is_not_skipped_silently(client, immich):
    immich.stack_list_has_a_scalar = True
    with pytest.raises(ImmichProtocolError):
        client.stack_by_primary("asset-1")


def test_an_empty_assets_list_is_a_protocol_error(client, immich):
    immich.empty_assets_in_the_stack_response = True
    with pytest.raises(ImmichProtocolError):
        client.create_stack(["asset-1", "asset-2"])


def test_a_duplicated_member_in_the_response_is_a_protocol_error(client, immich):
    """**件数が違うのに集合は同じ**（[A, B] を送って [A, A, B] が返る）。"""
    immich.duplicate_asset_in_the_stack_response = True
    with pytest.raises(ImmichProtocolError):
        client.create_stack(["asset-1", "asset-2"])


def test_a_stack_of_another_primary_is_not_taken(client, immich):
    """絞り込みを無視して返す相手から、無関係なスタックを掴まない."""
    client.create_stack(["asset-1", "asset-2"])
    immich.stack_list_ignores_the_primary_filter = True
    assert client.stack_by_primary("asset-9") is None


def test_a_body_that_is_not_json_at_all_is_a_protocol_error(client, immich):
    """`response.json()` を直に呼ぶと `ValueError` が漏れて分岐に入らない."""
    immich.stack_list_is_not_even_json = True
    with pytest.raises(ImmichProtocolError):
        client.stack_by_primary("asset-1")


def test_the_stack_id_itself_is_validated(client, immich):
    """**id は DB に入り、次の要求の URL にも入る**（`remote_stack_id`）。

    資産の集合が正しくても、id だけに鍵を混ぜられる。全単射の検査は
    この経路を守らない。
    """
    immich.key_as_stack_id = True
    with pytest.raises(ImmichProtocolError, match="識別子"):
        client.create_stack(["asset-1", "asset-2"])


def test_an_asset_response_for_another_id_is_a_protocol_error(client, immich):
    """**要求した資産と応答が対応することまで見る。**

    A を読んで C が返ると、こちらは C のスタックを自分の組と取り違える。
    """
    immich.swap_asset_ids = True
    with pytest.raises(ImmichProtocolError, match="要求"):
        client.asset("asset-1")


def test_an_explicit_null_stack_is_not_a_stack(client, immich):
    """実 Immich はスタックに入っていない資産へ `"stack": null` を返す."""
    immich.null_stack_field = True
    assert client.asset("asset-1").stack_id is None


def test_stacks_lists_every_stack(immich):
    immich.stacks["stack-1"] = {"primary": "asset-a", "assets": ["asset-a", "asset-b"]}
    immich.stacks["stack-2"] = {"primary": "asset-c", "assets": ["asset-c", "asset-d"]}
    with ImmichClient(immich.url, API_KEY) as client:
        found = client.stacks()

    assert {stack.stack_id for stack in found} == {"stack-1", "stack-2"}
    assert {stack.stack_id: set(stack.asset_ids) for stack in found}["stack-1"] == {
        "asset-a",
        "asset-b",
    }


def test_a_broken_stack_list_is_a_protocol_error(immich):
    """**壊れた応答を DB へ確定させない。** 黙って「スタックが無い」と読むと、
    組み直しの判断が全部ひっくり返る（在る組を解けていると読む）。"""
    immich.stacks["stack-1"] = {"primary": "asset-a", "assets": ["asset-a", "asset-b"]}
    immich.stack_response_without_assets = True
    with ImmichClient(immich.url, API_KEY) as client, pytest.raises(ImmichProtocolError):
        client.stacks()
