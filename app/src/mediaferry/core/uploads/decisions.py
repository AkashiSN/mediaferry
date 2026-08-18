"""アップロードの判断（§9.10）.

HTTP も DB も知らない。**「自分が作った資産か」を証明できるかどうかで、
既存資産を書き換えてよいかが決まる。**
"""

from __future__ import annotations

from dataclasses import dataclass

from ..profiles.model import ImmichRule


@dataclass(frozen=True)
class DatetimePlan:
    """撮影日時の補正案.

    `automatic` が偽なら `awaiting_datetime_approval` へ進み、ユーザの明示承認を
    待つ。`proposed` が None なら補正案そのものが無いので承認も要らない。
    """

    proposed: str | None
    automatic: bool
    reason: str


def tags_to_apply(rule: ImmichRule, origin: str) -> tuple[str, ...]:
    """付けるタグ. **追加操作だけ**で、既存タグは消さない.

    自分が作ったと証明できない資産に、ユーザが「既存には付けない」と決めた
    タグを付けてしまわないようにする。
    """
    if origin == "created_by_us" or rule.tag_pre_existing:
        return rule.tags
    return ()


def origin_after_upload(first_check_result: str | None, upload_status: str) -> str:
    """`POST /api/assets` の応答から origin を決める.

    `created` が返れば自分が作ったと確定する。`duplicate` は、初回の
    `checking` が `reject` だったときだけ「以前から存在した」と言える。
    **初回が `accept` だったことは自作の証明にならない**（チェックと
    アップロードの間に別のクライアントが割り込みうる）。
    """
    if upload_status == "created":
        return "created_by_us"
    if first_check_result == "reject":
        return "pre_existing"
    return "unknown"


def datetime_plan(rule: ImmichRule, policy: str, captured_at: str, origin: str) -> DatetimePlan:
    """撮影日時を書き戻すか、承認を待つか、何もしないかを決める."""
    if not rule.fix_datetime_after_upload:
        return DatetimePlan(None, False, "プロファイルが日時の補正を行わない設定")
    if policy == "none":
        return DatetimePlan(None, False, "タイムゾーンを解決していないので補正案が無い")
    if origin == "created_by_us":
        return DatetimePlan(captured_at, True, "自分がアップロードした資産")
    # 別経路で既に上がっていて、ユーザが手で直しているかもしれない。
    return DatetimePlan(captured_at, False, "自作と証明できない資産なので承認を待つ")
