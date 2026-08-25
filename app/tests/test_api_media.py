"""`GET /media` の `collapse=stack` 絞り込み（§11）.

**組は束ねない。従を隠す。** 1 行 = 1 タイルのまま、ページングの意味を変えない。
"""

from dataclasses import dataclass

import pytest

from mediaferry.clock import now_iso
from mediaferry.core.listing import stack_extension_ranks
from mediaferry.core.profiles.model import StackRule
from mediaferry.core.uploads.stacking import Candidate, Group, resolve_group
from mediaferry.db.profiles import ProfileRegistry

from .test_schema_uploads import a_destination, an_upload


@dataclass
class _FakeDefinition:
    stack: StackRule


@dataclass
class _FakeProfile:
    profile_id: str
    definition: _FakeDefinition


def test_stack_extension_ranks_skips_a_disabled_profile_even_with_extensions():
    """`stack.enabled = false` のプロファイルは順位表に 1 行も出さない.

    実物の `_parse_stack` は enabled=false のとき `extensions` を必ず空にするが
    （`STACK_DISABLED`）、`stack_extension_ranks` 自身もこの門を持つ —— 呼び出し側
    の正規化に頼らない。ここでは `extensions` を残したまま `enabled=False` を渡し、
    関数自身の判断だけを見る。
    """
    enabled = _FakeProfile(
        "p1", _FakeDefinition(StackRule(enabled=True, extensions=("JPG", "CR2")))
    )
    disabled = _FakeProfile(
        "p2", _FakeDefinition(StackRule(enabled=False, extensions=("JPG", "CR2")))
    )

    assert stack_extension_ranks([enabled, disabled]) == [("p1", "JPG", 0), ("p1", "CR2", 1)]


def test_collapsing_hides_the_raw_and_names_it_on_the_jpeg(client, canon_pair):
    """**1 行 = 1 タイル。** 従を隠し、主に組の中身を付ける."""
    body = client.get("/api/media?collapse=stack").json()

    paths = [m["rel_path"] for m in body["media"]]
    assert [p for p in paths if p.endswith(".CR2")] == []
    jpeg = next(m for m in body["media"] if m["rel_path"].endswith(".JPG"))
    assert [m["rel_path"].split("/")[-1] for m in jpeg["stack"]["members"]] == [
        "IMG_0001.JPG",
        "IMG_0001.CR2",
    ]


def test_collapsing_counts_only_what_it_shows(client, canon_pair):
    """`total` が見える件数と食い違うと、ページ送りが空のページを作る.

    1 ページに全件収まる規模だと、`total` が実体より大きくても
    `len(body["media"])` がページ内で頭打ちになるだけで気づけない。
    `page_size=1` で全ページを実際にたどり、返した件数の合計が `total` と
    一致することを確かめる —— 食い違えば、最後に必ず「空のページ」が現れる。
    """
    total = client.get("/api/media?collapse=stack&page_size=1&page=1").json()["total"]

    seen = 0
    page = 1
    while True:
        body = client.get(f"/api/media?collapse=stack&page_size=1&page={page}").json()
        if not body["media"]:
            break
        seen += len(body["media"])
        page += 1
        assert page <= total + 1, "ページ送りが終わらない"

    assert seen == total


def test_without_collapsing_both_files_are_listed(client, canon_pair):
    """**既定は畳まない。** 選んで送る画面とホームの契約を変えない."""
    paths = [m["rel_path"] for m in client.get("/api/media").json()["media"]]

    assert len([p for p in paths if p.endswith(".CR2")]) == 1


def test_a_profile_without_stacking_is_never_hidden(client, canon_pair, dji_media):
    """`stack.enabled` が false のプロファイルの行は 1 つも外れない."""
    paths = [m["rel_path"] for m in client.get("/api/media?collapse=stack").json()["media"]]

    assert any("dji-osmo" in p for p in paths)


def test_a_file_without_proof_is_not_collapsed(client, canon_pair_without_proof):
    """同席の証拠が無ければ組ではない。**2 タイルのまま出す。**"""
    paths = [m["rel_path"] for m in client.get("/api/media?collapse=stack").json()["media"]]

    assert len([p for p in paths if p.endswith(".CR2")]) == 1


def test_members_follow_the_current_rule(client, canon_pair, narrowed_stack_rule):
    """`extensions` から CR2 を外した後は、CR2 は組の中身に出ない.

    `copresent_key` は残り続けるので、絞らないと `identity_partners`（現行規則で
    CR2 を外す）と食い違い、「同じ関数が決める」という設計の要が崩れる。
    JPG も CR2 も一覧には出るが（隠すほどの根拠がもう無い）、どちらにも
    `stack` は付かない（`all(...)` / `not any(...)` は一覧が空でも通ってしまうので、
    実際に両方の行が出ることを先に固定する）。
    """
    body = client.get("/api/media?collapse=stack").json()

    by_path = {m["rel_path"]: m for m in body["media"]}
    jpeg = next(m for path, m in by_path.items() if path.endswith(".JPG"))
    cr2 = next(m for path, m in by_path.items() if path.endswith(".CR2"))
    assert "stack" not in jpeg
    assert "stack" not in cr2


def test_an_unknown_collapse_value_is_a_bad_request(client):
    """`collapse` は `stack` だけ受け付ける."""
    response = client.get("/api/media?collapse=bogus")

    assert response.status_code == 400


def test_the_ambiguous_pair_is_not_collapsed(client, canon_pair, ambiguous_sibling):
    """同じ順位の兄弟が 2 つあると、どちらが主か決まらない. **CR2 も隠さない。**

    `IMG_0001.JPG` と `IMG_0001.jpg`（大小違い）が同じ同席の証拠を持つとき、
    「自分より順位が上の兄弟が居る」から CR2 を単純に隠すと、本当は実在する
    ファイルが一覧から黙って消える。曖昧なときは従の隠しごと止める。
    """
    body = client.get("/api/media?collapse=stack").json()

    paths = [m["rel_path"] for m in body["media"]]
    assert len([p for p in paths if p.endswith(".CR2")]) == 1
    assert body["total"] == len(body["media"])


def test_the_ambiguous_pair_gets_no_stack_members(client, canon_pair, ambiguous_sibling):
    """曖昧なグループの 3 行は、どれも `stack` を持たない.

    `identity_partners` は同じ状況を `ambiguous=True` と判定し、`resolve_group`
    は組を作らない（`Refusal("同じ拡張子の相方が複数ある。自動では決められない")`）。
    一覧が `stack.members` を宣言すると、Immich には作られない組を画面が語ることに
    なる（`docs/history/phase10-design.md` の「画面に出す組と Immich が作る組は、
    同じ関数が決める」）。3 行とも見えたまま、誰も `stack` を持たないのが正しい。
    """
    body = client.get("/api/media?collapse=stack").json()

    assert len(body["media"]) == 3
    assert not any("stack" in m for m in body["media"])


def test_a_sibling_under_a_different_profile_is_not_wrongly_hidden(
    client, canon_pair, cross_profile_rank_collision
):
    """`theirs.rank < mine.rank` は**厳密**でなければならない.

    順位の単射性（同じ順位 ⟺ 同じ拡張子）が効くのは 1 つのプロファイルの中だけ。
    同じ同席グループに、順位表の異なる別プロファイルの `media_file` が混ざると、
    **拡張子が違うのに順位の値が一致しうる**。ここでは CR2 を「逆順」プロファイル
    （`extensions=["CR2","JPG"]`）へ付け替えたので、JPG（canon-eos, 順位 0）と
    CR2（逆順, 順位 0）が同じ順位になる。拡張子が違うので `_AMBIGUOUS_EXISTS` は
    反応せず守ってくれない。`<=` に緩めると JPG・CR2 の両方が一覧から消える。
    """
    body = client.get("/api/media?collapse=stack").json()

    paths = [m["rel_path"] for m in body["media"]]
    assert any(p.endswith(".JPG") for p in paths)
    assert any(p.endswith(".CR2") for p in paths)


def test_a_failed_raw_is_listed_when_its_jpeg_succeeded(client, db, canon_pair):
    """**絞り込みを通らない主は、従を隠す根拠にならない。**

    写真タブは全部の絞り込みに `collapse=stack` を付ける。JPG が `complete`・
    CR2 が `failed` のとき、主（JPG）が同じ絞り込みを通るかを見ずに従を隠すと、
    **失敗した CR2 が画面から消えて再試行に辿り着けない**。
    """
    destination = a_destination(db, name="stack-retry")
    an_upload(
        db,
        destination,
        canon_pair.media_ids["JPG"],
        state="complete",
        destination_revision_id=destination[1],
    )
    an_upload(db, destination, canon_pair.media_ids["CR2"], state="failed")
    db.commit()

    body = client.get(
        f"/api/media?collapse=stack&destination_id={destination[0]}&status=failed"
    ).json()

    assert [m["rel_path"].split("/")[-1] for m in body["media"]] == ["IMG_0001.CR2"]
    assert body["total"] == 1


def test_an_unsendable_jpeg_does_not_hide_its_raw(client, db, canon_pair):
    """`status=unsent` の判断（`sendable_clause`）も、兄弟の行に当てる.

    元のファイルが行方不明になった JPG は送る候補に出ない（§10）。一覧にも出ない
    のに従を隠す根拠として数えると、**送れる CR2 が「まだ送っていない」から消える**。
    """
    db.execute(
        "UPDATE media_file SET missing_at = ? WHERE id = ?",
        (now_iso(), canon_pair.media_ids["JPG"]),
    )
    destination = a_destination(db, name="stack-unsent")
    db.commit()

    body = client.get(
        f"/api/media?collapse=stack&destination_id={destination[0]}&status=unsent"
    ).json()

    assert [m["rel_path"].split("/")[-1] for m in body["media"]] == ["IMG_0001.CR2"]


def test_searching_for_the_raw_by_name_finds_it(client, canon_pair):
    """名前で探した従は出す. **主（JPG）はこの `q` を通らない。**"""
    body = client.get("/api/media?collapse=stack&q=IMG_0001.CR2").json()

    assert [m["rel_path"].split("/")[-1] for m in body["media"]] == ["IMG_0001.CR2"]
    assert body["total"] == 1


def test_the_raw_is_still_hidden_when_the_filter_keeps_the_jpeg(client, canon_pair):
    """**主も同じ絞り込みを通るなら、従はこれまでどおり隠す。**

    絞り込みが付いたら畳むのをやめる、のではない —— 「主が絞り込みを通るか」だけを
    足す。両方が通る `kind=photo` では、畳みの結果が絞り込み無しと同じになる。
    """
    body = client.get("/api/media?collapse=stack&kind=photo").json()

    paths = [m["rel_path"] for m in body["media"]]
    assert [p for p in paths if p.endswith(".CR2")] == []
    jpeg = next(m for m in body["media"] if m["rel_path"].endswith(".JPG"))
    assert len(jpeg["stack"]["members"]) == 2  # noqa: PLR2004 - JPG と CR2


def test_the_same_jpeg_on_two_cards_with_two_raws_is_ambiguous(
    client, canon_pair, second_card_with_another_raw
):
    """**曖昧さは、主の観測 1 つの中では数え切れない。**

    同じ JPG が 2 枚のカードに在り（中身が同じなので 1 つの `media_file`）、
    各カードに別々の CR2 が在る。`identity_partners` は主の**複数の鍵にまたがって**
    `by_extension` を数えるので CR2 が 2 つ＝曖昧で、`resolve_group` は
    「同じ拡張子の相方が複数ある」と断る。同席の鍵 1 つの中だけを見ると曖昧に
    ならず、**画面が Immich の作らない 3 枚組を宣言し、実在する 2 つの RAW が
    一覧から消える。**

    JPG の側だけが曖昧で、**CR2 の側から見た相方は 1 枚に決まる**（`resolve_group`
    もそれぞれ 2 枚の組を返す）ので、CR2 のタイルは自分と JPG の組を名乗る。
    どの行も消えず、3 枚組はどこにも現れないのが正しい。
    """
    body = client.get("/api/media?collapse=stack").json()

    by_name = {m["rel_path"].split("/")[-1]: m for m in body["media"]}
    assert set(by_name) == {"IMG_0001.JPG", "IMG_0001.CR2", "IMG_0001_2.CR2"}
    assert "stack" not in by_name["IMG_0001.JPG"]
    for name in ("IMG_0001.CR2", "IMG_0001_2.CR2"):
        members = {m["id"] for m in by_name[name]["stack"]["members"]}
        assert members == {canon_pair.media_ids["JPG"], by_name[name]["id"]}
    assert body["total"] == len(body["media"])


def test_a_raw_shared_by_two_cards_is_not_hidden(client, canon_pair, second_card_with_another_jpeg):
    """**身元が曖昧な行は、誰の従としても隠さない。**

    同じ CR2 が 2 枚のカードに在り、各カードに別々の JPG が在る。どちらの JPG から
    見ても相方は 1 枚に決まる（主は曖昧でない）ので、主の側の判断だけでは CR2 が
    隠れる。**CR2 自身から見ると相方の JPG が 2 枚**で `resolve_group` は断るので、
    隠すと「どの組にも決まらないファイル」が画面から消える。3 行とも出す。
    """
    body = client.get("/api/media?collapse=stack").json()

    by_name = {m["rel_path"].split("/")[-1]: m for m in body["media"]}
    assert set(by_name) == {"IMG_0001.JPG", "IMG_0001.CR2", "IMG_0001_2.JPG"}
    assert "stack" not in by_name["IMG_0001.CR2"]
    assert body["total"] == len(body["media"])


def test_an_unpublished_observation_does_not_make_the_pair_ambiguous(
    client, canon_pair, second_card_still_importing
):
    """**公開されていない観測は、身元の材料に数えない。**

    2 枚目のカードの CR2 はもう公開済みだが、同じカードの JPG はまだ取り込み中。
    `identity_partners` が読む観測（`sources_of` / `siblings_on_card`）はどちらも
    `state = 'published'` で絞るので、この CR2 は 1 枚目の組の相方候補にならない。
    数えてしまうと「CR2 が 2 つある」に見えて、1 枚目の組が畳まれなくなる。
    """
    body = client.get("/api/media?collapse=stack").json()

    by_name = {m["rel_path"].split("/")[-1]: m for m in body["media"]}
    assert set(by_name) == {"IMG_0001.JPG", "IMG_0001_2.CR2"}
    assert {m["id"] for m in by_name["IMG_0001.JPG"]["stack"]["members"]} == {
        canon_pair.media_ids["JPG"],
        canon_pair.media_ids["CR2"],
    }


def _candidates_of(db) -> list[Candidate]:
    """DB の `source_entry` を、`identity_partners` が読む観測の並びにほどく.

    宛先まわり（`origin` / `state` / `remote_asset_id`）は、**組める側に倒した
    値**を入れる —— ここで見たいのは身元の判断だけなので、資格で断られると
    「畳むか」との一致を確かめられない。
    """
    rows = db.execute(
        "SELECT se.media_file_id AS media_file_id, se.volume_instance_id AS volume_instance_id,"
        "       se.rel_path AS rel_path, se.copresent_key AS copresent_key,"
        "       m.profile_id AS profile_id, m.captured_at AS captured_at,"
        "       m.captured_at_source AS captured_at_source"
        "  FROM source_entry se JOIN media_file m ON m.id = se.media_file_id"
        " WHERE se.state = 'published'"
    ).fetchall()
    return [
        Candidate(
            record_id=row["media_file_id"],
            media_file_id=row["media_file_id"],
            profile_id=row["profile_id"],
            volume_instance_id=row["volume_instance_id"],
            rel_path=row["rel_path"],
            copresent_key=row["copresent_key"],
            captured_at=row["captured_at"],
            captured_at_source=row["captured_at_source"],
            origin="created_by_us",
            state="complete",
            remote_asset_id=row["media_file_id"],
            invalidated=False,
        )
        for row in rows
    ]


@pytest.mark.parametrize(
    "extra",
    [
        None,
        "second_card_with_another_raw",
        "second_card_with_another_jpeg",
        "second_card_still_importing",
    ],
)
def test_the_listing_collapses_exactly_when_a_group_can_be_made(
    request, client, db, canon_pair, extra
):
    """**「畳むか」と「組むか」は同じ結論でなければならない。**

    一覧の SQL と `identity_partners` / `resolve_group` は別々に書かれている。
    食い違うと、画面は Immich が作らない組を宣言するか（畳んだのに組めない）、
    隠した行がどのタイルからも辿れなくなる。同じ筋書きを両方へ流し、**行ごとに**
    突き合わせる（`docs/history/phase10-design.md` の「画面に出す組と Immich が
    作る組は、同じ関数が決める」）。
    """
    if extra is not None:
        request.getfixturevalue(extra)
    rule = ProfileRegistry(db).current("canon-eos").definition.stack
    candidates = _candidates_of(db)

    body = client.get("/api/media?collapse=stack").json()

    declared: set[str] = set()
    for item in body["media"]:
        primary = next(c for c in candidates if c.media_file_id == item["id"])
        decision = resolve_group(primary, candidates, rule)
        assert ("stack" in item) is isinstance(decision, Group), item["rel_path"]
        if isinstance(decision, Group):
            members = {member.media_file_id for member in decision.members}
            assert {m["id"] for m in item["stack"]["members"]} == members
            declared |= members
    # **隠した行は、必ずどれかのタイルの組の中に居る。** 名乗り手の無い行を隠すと、
    # 実在するファイルが画面から黙って消える。
    every = {row["id"] for row in db.execute("SELECT id FROM media_file")}
    assert every - {m["id"] for m in body["media"]} <= declared


def test_the_detail_names_the_members_of_the_pair(client, canon_pair):
    """**詳細も組を知る。** 一覧でだけ組が見えると、押した先で消える."""
    jpeg = canon_pair.media_ids["JPG"]

    body = client.get(f"/api/media/{jpeg}").json()

    assert [m["rel_path"].split("/")[-1] for m in body["stack"]["members"]] == [
        "IMG_0001.JPG",
        "IMG_0001.CR2",
    ]
    assert [m["size_bytes"] for m in body["stack"]["members"]] == [
        body["size_bytes"],
        client.get(f"/api/media/{canon_pair.media_ids['CR2']}").json()["size_bytes"],
    ]


def test_the_secondary_sees_the_same_pair_with_the_primary_first(client, canon_pair):
    """**従から開いても並びは同じ**（主が先頭）. どちらから見ても同じ組."""
    raw = canon_pair.media_ids["CR2"]

    body = client.get(f"/api/media/{raw}").json()

    assert [m["rel_path"].split("/")[-1] for m in body["stack"]["members"]] == [
        "IMG_0001.JPG",
        "IMG_0001.CR2",
    ]


def test_the_order_is_by_rank_not_by_insertion(client, canon_pair_inserted_in_reverse):
    """**並びは `rank` が決める. 挿入順ではない.**

    CR2 を JPG より先に `media_file` へ入れても、`stack.extensions` の順位は
    変わらない（JPG が primary）ので、`members` は依然として JPG が先頭。
    `ORDER BY r.rank` を消しても、挿入順とたまたま同じ順で返ると気付けない
    （`canon_pair` は常に JPG を先に入れるので、そちらだけでは見抜けない）。
    """
    jpeg = canon_pair_inserted_in_reverse.media_ids["JPG"]

    body = client.get(f"/api/media/{jpeg}").json()

    assert [m["rel_path"].split("/")[-1] for m in body["stack"]["members"]] == [
        "IMG_0001.JPG",
        "IMG_0001.CR2",
    ]


def test_a_lone_file_has_no_stack(client, canon_pair_without_proof):
    """同席の証拠が無ければ組ではない. **`None` を返す**（空の組を作らない）."""
    jpeg = canon_pair_without_proof.media_ids["JPG"]

    assert client.get(f"/api/media/{jpeg}").json()["stack"] is None


def test_an_ambiguous_pair_has_no_stack_in_the_detail(client, canon_pair, ambiguous_sibling):
    """**曖昧な組は詳細でも組にしない.**

    `identity_partners` は同じ状況を `ambiguous=True` と判定し、`resolve_group` は
    組を作らない。一覧と同じ `_members_of` を通すので、判断は自動でそろう ——
    ここが割れると、画面が Immich には作らない組を宣言する。
    """
    jpeg = canon_pair.media_ids["JPG"]

    assert client.get(f"/api/media/{jpeg}").json()["stack"] is None
