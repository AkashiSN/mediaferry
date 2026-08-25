"""`GET /media` の `collapse=stack` 絞り込み（§11）.

**組は束ねない。従を隠す。** 1 行 = 1 タイルのまま、ページングの意味を変えない。
"""

from dataclasses import dataclass

from mediaferry.core.listing import stack_extension_ranks
from mediaferry.core.profiles.model import StackRule


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
