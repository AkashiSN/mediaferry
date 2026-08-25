"""RAW/JPEG の組を決める規則（§6・§9.11）.

**判断だけを持つ。** DB も相手も触らないので、条件を 1 つずつ壊す試験が書ける。

組の同一性は**カード上の原名**（`source_entry.rel_path`）で取る。公開名
（`media_file.rel_path`）は衝突時に改名されるので、連番が一周した別カードの
ファイルと束ねうる。

**撮影時刻は見ない。** 時刻は同じ 1 枚であることを弱めこそすれ強めない ——
一括で日時を入れ直すと JPG だけ書き換わって CR2 が元のままになりうる
（RAW に書き込める道具の方が少ない）ため、時刻の食い違いは誤って組を
拒む理由にしかならない。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from posixpath import splitext

from ..profiles.model import StackRule

# 範囲引きの上端（`source_entry.rel_path` を prefix で引くときに使う）。
# UTF-8 で最も大きい符号位置。
HIGH_SENTINEL = "\U0010ffff"


@dataclass(frozen=True)
class Candidate:
    """**観測 1 つぶん**の候補.

    同じ `media_file` が複数の `source_entry` を持つときは、この値が複数できる。
    **1 つに絞らない**（`observed_at` は再スキャンで動くので、順序で選ぶと同じ組が
    実行のたびに変わる）。**平坦化もしない** —— 「ボリュームの集合」と「1 つの
    stem」に潰すと、*別々の観測*でそれぞれが一致するだけの組が通る。
    """

    record_id: str
    media_file_id: str
    profile_id: str
    volume_instance_id: str
    rel_path: str  # **カード上の原名**
    # **同席の証拠**（`source_entry.copresent_key`）。同じ鍵で 2 つ以上の
    # 拡張子が同時に見えたときだけ、スキャンがここへ `<job_id>:<stem prefix>`
    # を書く。`None` は「まだ分からない」であって「同席していない」ではないが、
    # 組の判定では確かめられない相手として扱う（見つからない分は組まない）。
    copresent_key: str | None
    # `captured_at` / `captured_at_source` は組の判定には使わない（撮影時刻は
    # 同じ 1 枚であることを弱めこそすれ強めない）。値そのものは呼び出し側が
    # 日時の突き合わせに使うので、観測として持ち続ける。
    captured_at: str
    captured_at_source: str
    origin: str
    state: str
    remote_asset_id: str | None
    invalidated: bool

    @property
    def source_key(self) -> tuple[str, str]:
        """組の鍵。**ボリュームと stem は必ず組で持つ。**"""
        return (self.volume_instance_id, stem_prefix(self.rel_path))


@dataclass(frozen=True)
class Identity:
    """身元だけで決まる組。**曖昧さを潰さずに返す。**

    一覧（送る前）と第 2 パス（送った後）の両方が `identity_partners` を通して
    これを得る。**予測と事実を別の関数で決めない**（`docs/history/phase10-design.md`）。
    """

    partners: tuple[Candidate, ...]
    # 同じ鍵に**同じ正規化拡張子**が 2 つ以上ある。`iter_media_files` は
    # `{ext.upper()}` で突き合わせるので、case-sensitive な FS では
    # `IMG_0001.JPG` と `IMG_0001.jpg` がこれになる。**自動では決められない。**
    ambiguous: bool


@dataclass(frozen=True)
class Group:
    """成立した組。**先頭が primary**（`extensions` の順）."""

    members: tuple[Candidate, ...]


@dataclass(frozen=True)
class Refusal:
    """組めなかった理由（画面に出す）."""

    reason: str


def stem_prefix(rel_path: str) -> str:
    """`DCIM/100CANON/IMG_1234.JPG` → `DCIM/100CANON/IMG_1234.`"""
    return splitext(rel_path)[0] + "."


def extension_of(rel_path: str) -> str:
    """ドット無しの大文字（`scan.extensions` の突き合わせと同じ形）."""
    return splitext(rel_path)[1].lstrip(".").upper()


def identity_partners(
    primary: Candidate, candidates: Sequence[Candidate], rule: StackRule
) -> Identity:
    """**身元だけ**で相方を返す（カード上の事実。宛先を見ない）.

    一覧（送る前）と第 2 パス（送った後）の両方がこれを呼ぶ。**予測と事実を
    別の関数で決めない**（`docs/history/phase10-design.md`）。
    """
    if not rule.enabled:
        return Identity(partners=(), ambiguous=False)
    if extension_of(primary.rel_path) not in rule.extensions:
        return Identity(partners=(), ambiguous=False)
    keys = {c.source_key for c in candidates if c.media_file_id == primary.media_file_id}
    # **鍵ごとの同席の証拠。** 1 つの鍵の下に自分の行は 1 つしか無い
    # （UNIQUE (volume_instance_id, rel_path)）ので、対応は一意に決まる。
    # 集合に潰すと、別々の観測でそれぞれが一致するだけの組が通ってしまう。
    proofs = {
        c.source_key: c.copresent_key
        for c in candidates
        if c.media_file_id == primary.media_file_id
    }
    partners: list[Candidate] = []
    seen: set[str] = set()
    by_extension: dict[str, set[str]] = {}
    for candidate in candidates:
        if candidate.media_file_id == primary.media_file_id:
            continue
        # **鍵は組で比べる**（同じカードの、同じディレクトリの、同じ stem）。
        if candidate.source_key not in keys:
            continue
        extension = extension_of(candidate.rel_path)
        if extension not in rule.extensions:
            continue
        proof = proofs.get(candidate.source_key)
        # **「同じ時点でカードに在った」を要求する。** 鍵だけでは、片方だけ
        # 撮り直したときに古い published な行と組む。**曖昧さの数え上げより
        # 前で落とす** —— 証拠の無い相手は相方候補ですらない。
        if proof is None or candidate.copresent_key != proof:
            continue
        # **同じ資産を 2 回送らない。** 1 つの media_file が複数の観測で候補に入る。
        if candidate.media_file_id in seen:
            continue
        seen.add(candidate.media_file_id)
        by_extension.setdefault(extension, set()).add(candidate.media_file_id)
        partners.append(candidate)
    # **1 つの拡張子に 2 つ以上の media_file が来たら曖昧。** 自分の拡張子も数える
    # （自分と同じ拡張子の別ファイルが相方に来る場合がある）。
    by_extension.setdefault(extension_of(primary.rel_path), set()).add(primary.media_file_id)
    ambiguous = any(len(ids) > 1 for ids in by_extension.values())
    return Identity(partners=tuple(partners), ambiguous=ambiguous)


def resolve_group(
    primary: Candidate, candidates: Sequence[Candidate], rule: StackRule
) -> Group | Refusal:
    """身元で相方を決め、**資格**を確かめる. 同じ組はどの member から呼んでも同じ."""
    if not rule.enabled:
        return Refusal("カメラの種類がスタックを使わない")
    if extension_of(primary.rel_path) not in rule.extensions:
        return Refusal("この拡張子は組の対象ではない")
    identity = identity_partners(primary, candidates, rule)
    if identity.ambiguous:
        # **「どちらかを選ぶ」を機械にやらせない。** 送信は止めず、理由を出す。
        return Refusal("同じ拡張子の相方が複数ある。自動では決められない")
    partners = list(identity.partners)
    if not partners:
        return Refusal("相方が見つからない")
    refusal = _refused(primary, partners)
    if refusal is not None:
        return refusal
    members = sorted(
        [primary, *partners], key=lambda c: rule.extensions.index(extension_of(c.rel_path))
    )
    return Group(members=tuple(members))


def _refused(primary: Candidate, partners: Sequence[Candidate]) -> Refusal | None:
    """組めない理由を探す. **見つからなければ None。**"""
    for member in (primary, *partners):
        if member.origin != "created_by_us":
            # `POST /stacks` は既存スタックを吸収する。タグ（追加のみ）と違って
            # 利用者が手で作った組を作り直しうるので、**証明できない相手には触らない**。
            return Refusal("自分が上げたと証明できない資産が含まれる")
    identifiers = [member.remote_asset_id for member in (primary, *partners)]
    if any(identifier is None for identifier in identifiers):
        # **「分からない」を「重なっている」と言わない。** 再確認で資産が消えると
        # `None` が並ぶが、それは相手が同じ ID を返した事象ではない。
        return Refusal("組の資産 ID が分からない")
    if len(set(identifiers)) != len(identifiers):
        # **相手が両方へ同じ資産 ID を返すことがある。** そのまま進むと、
        # 作成では「同じ id が複数ある」で落ち、回収では 1 資産のスタックを
        # 2 行へ `stacked` と記録してしまう。
        return Refusal("相方と資産 ID が重なっている")
    for partner in partners:
        if partner.profile_id != primary.profile_id:
            # 規則が 1 つに決まらない（§9.11）。
            return Refusal("相方が別のカメラの種類に属している")
        if partner.invalidated or partner.state != "complete":
            return Refusal("相方はまだ送信が終わっていない")
    return None
