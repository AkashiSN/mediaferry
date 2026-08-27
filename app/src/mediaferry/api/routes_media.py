"""ライブラリの一覧と、reconciliation が見つけた齟齬."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from ..adapters.thumbnails import ThumbnailFailed, quantise
from ..core.listing import DEFAULT_PAGE_SIZE, escape_like, page_bounds, stack_extension_ranks
from ..db.media import IN_FLIGHT_STATES, MediaRepository, owner_group
from ..db.merges import GroupNotEditable, MergeRepository
from ..db.profiles import ProfileRegistry
from ..db.selection import sendable_clause
from .deps import conn as get_conn
from .deps import state as get_state
from .errors import ApiError, ErrorCode
from .routes_merges import _current_revisions, group_view

router = APIRouter()

# `media_file.role` の CHECK 制約が許す値そのもの（`db/migrations/0003_*.sql`）。
# `_filters` の role 節がこの外の値をリテラルで埋めないために使う。
_KNOWN_ROLES = frozenset({"original", "derived"})


# **曖昧な組は畳まない**（`identity_partners` の `ambiguous` と同じ判断）。
# 同じ順位の兄弟が 2 つあると、どちらが主か決まらない。畳むと片方が消える。
#
# **数える範囲は主の観測すべて。** `identity_partners` の `by_extension` は、主が
# 持つ**複数の鍵にまたがって**相方を集めてから拡張子ごとに数える。ここも `a` と
# `b` が同じ鍵を共有することは求めず、**それぞれが主のいずれかの観測（`mea` /
# `meb`）の鍵を共有していれば**曖昧に数える —— 同じ JPG が 2 枚のカードに在り、
# 各カードに別々の CR2 が在る形は、鍵 1 つの中だけを見ると曖昧にならないので、
# 画面が Immich の作らない 3 枚組を宣言し、実在する 2 つの RAW が一覧から消える。
#
# **主語（`m.id` / `sm.id` / `?`）だけを差し替えられる形にする。** 一覧の除外節
# （相関）と `_members_of`（束縛変数、`?`）の両方が、同じ判断を同じ SQL から
# 作る —— 曖昧さの定義を 2 か所に書くと、`_members_of` だけが曖昧な組にも
# `stack.members` を付けてしまう（Task 6 レビューで実際に見つかった穴）。
def _ambiguous_exists_sql(media_ref: str) -> str:
    sql = f"""
EXISTS (
  SELECT 1
    FROM source_entry mea
    JOIN source_entry a
      ON a.volume_instance_id = mea.volume_instance_id
     AND a.copresent_key = mea.copresent_key
     AND a.media_file_id IS NOT NULL AND a.state = 'published'
    JOIN source_entry meb
      ON meb.media_file_id = mea.media_file_id
     AND meb.state = 'published'
     AND meb.copresent_key IS NOT NULL
    JOIN source_entry b
      ON b.volume_instance_id = meb.volume_instance_id
     AND b.copresent_key = meb.copresent_key
     AND b.media_file_id IS NOT NULL AND b.state = 'published'
     AND b.media_file_id <> a.media_file_id
     AND b.extension = a.extension
   WHERE mea.media_file_id = {media_ref}
     AND mea.state = 'published'
     AND mea.copresent_key IS NOT NULL
)
"""  # noqa: S608 - media_ref は呼び出し側の定数（"m.id" / "sm.id" / "?"）のみ
    return sql


# **従を外す節。** 「同じカードで、同じ同席の印を持ち、自分より順位が上の
# 拡張子の兄弟が居る」行は主ではないので一覧に出さない。組がページの境目を
# またがないよう、束ねずに隠す（`docs/history/phase10-design.md` の 4）。
#
# **隠すのは、その主（`sm`）が組を宣言するときだけ。** 主が曖昧なら
# `_members_of` は `stack.members` を返さないので、隠した従はどのタイルからも
# 辿れなくなる。曖昧さは主の観測すべてにまたがるため（`_ambiguous_exists_sql`）、
# 隠される側の `m` から見た曖昧さでは主の判断を代弁できない。
#
# **主も同じ絞り込みを通るときだけ隠す。** 写真タブは全部の絞り込みに
# `collapse=stack` を付けるので、絞り込みを見ずに隠すと「JPG は `complete`・
# CR2 は `failed`」の CR2 が `status=failed` の一覧から消え、再試行に辿り着け
# なくなる（`q` や `profile` でも同じ）。`sibling_where` は `_filters` が別名
# `sm` に対して組み立てた同じ節で、束縛変数は主の分の後ろにもう一度積む。
def _secondary_exists_sql(sibling_where: str) -> str:
    sql = f"""
EXISTS (
  SELECT 1
    FROM source_entry me
    JOIN rank mine ON mine.profile_id = m.profile_id AND mine.extension = me.extension
    JOIN source_entry sib
      ON sib.volume_instance_id = me.volume_instance_id
     AND sib.copresent_key = me.copresent_key
     AND sib.media_file_id IS NOT NULL
     AND sib.media_file_id <> m.id
     AND sib.state = 'published'
    JOIN media_file sm ON sm.id = sib.media_file_id
    JOIN rank theirs ON theirs.profile_id = sm.profile_id AND theirs.extension = sib.extension
   WHERE me.media_file_id = m.id
     AND me.state = 'published'
     AND me.copresent_key IS NOT NULL
     AND theirs.rank < mine.rank
     AND NOT ({_ambiguous_exists_sql("sm.id")})
     AND ({sibling_where})
)
"""  # noqa: S608 - sibling_where は `_filters` が組む節（値は束縛変数）
    return sql


_AMBIGUOUS_EXISTS = _ambiguous_exists_sql("m.id")

_KNOWN_COLLAPSE_VALUES = frozenset({"stack"})

_KNOWN_STACK_VALUES = frozenset({"members"})


@router.get("/media")
def list_media(  # noqa: PLR0913
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    kind: str | None = None,
    role: str | None = None,
    profile: str | None = None,
    captured_from: str | None = None,
    captured_to: str | None = None,
    q: str | None = None,
    destination_id: str | None = None,
    status: str | None = None,
    collapse: str | None = None,
    stack: str | None = None,
    conn=Depends(get_conn),  # noqa: ANN001, B008
) -> dict[str, Any]:
    """ライブラリの一覧（§11）.

    **並びは `captured_at DESC, rel_path DESC` で固定する。** 同じ撮影日時の行が
    あるので、tie-break を入れないとページの境目で重複・欠落する。**`rel_path` は
    `UNIQUE` なので単独で足りる** —— `id` は乱数なので、同じ撮影日時の並びに
    意味が出ない。

    `status` は**宛先ごとの状態**なので、`destination_id` と併せて指定する。
    `role=derived` で写真タブの「つないだ動画」だけに絞れる。

    `collapse=stack` で、組の従（`stack.extensions` で後ろの拡張子）を一覧から
    外す。**組は束ねない。** 束ねるとページの境目を組がまたぐので、行は増減
    させず、従の行だけを隠す。主の行には `stack.members` が付く。**既定は
    畳まない** —— ホームの「さっき取り込んだもの」と選んで送る画面が同じ
    この API を使うので、`collapse` を指定しない限り契約を変えない。

    `stack=members` は**従を隠さずに**、組に属する行すべてへ `stack` を付ける。
    送る画面が使う —— 送る対象は絞り込みが返した行そのもので、畳むと未送信の
    RAW が送られなくなる（`docs/history/phase12-design.md` の 2）。**`collapse=stack`
    と併せて来たら `collapse` が勝つ**（隠す）。片方だけを 400 にすると、既存の
    呼び出し元が壊れる。
    """
    if collapse is not None and collapse not in _KNOWN_COLLAPSE_VALUES:
        raise ApiError(400, ErrorCode.BAD_REQUEST, "collapse は stack だけ", {"collapse": collapse})
    if stack is not None and stack not in _KNOWN_STACK_VALUES:
        raise ApiError(400, ErrorCode.BAD_REQUEST, "stack は members だけ", {"stack": stack})
    clause, params = _filters(
        "m", kind, role, profile, captured_from, captured_to, q, destination_id, status
    )
    where = f"WHERE {clause}" if clause else ""
    limit, offset = page_bounds(page, page_size)

    # **組を教えるのと、従を隠すのは別。** 隠すのは写真タブ（`collapse=stack`）
    # だけで、送る画面は隠されると未送信の RAW を送れなくなる。順位表はどちらでも
    # 要るので、先に 1 度だけ作る。
    ranks_sql = ""
    ranks_params: tuple[Any, ...] = ()
    prefix = ""
    prefix_params: tuple[Any, ...] = ()
    if collapse == "stack" or stack == "members":
        ranks_sql, ranks_params = _ranks(conn)
    # `and ranks_sql` を落とさない。**`ranks_sql` が空だと `VALUES` は 0 行を
    # 書けない**（`WITH rank(...) AS (VALUES )` は構文エラー）。stack が有効な
    # プロファイルが 1 つも無い環境では、`collapse=stack` の要求ごとに毎回ここを通る。
    if collapse == "stack" and ranks_sql:
        prefix = f"WITH rank(profile_id, extension, rank) AS (VALUES {ranks_sql}) "
        prefix_params = ranks_params
        # **`_AMBIGUOUS_EXISTS` は従外しを打ち消す保護であって、独立の除外
        # 条件ではない。** 「従に見える行」でも、その身元が曖昧（同じ順位の
        # 別候補がいる）なら、どちらが本当の主か決まらないので隠さない ——
        # `identity_partners` が曖昧なら組まないのと同じ判断
        # （`docs/history/phase10-plan.md` の「曖昧なら組まない。…一覧も
        # 畳まない」）。**隠される側（`m`）と主（`sm`。`_secondary_exists_sql`
        # の中）の両方で見る** —— 曖昧さは観測ごとに向きが変わるので、片側
        # からしか見ないと、実在するファイルが名乗り手の無いまま一覧から消える。
        #
        # **同じ絞り込みを兄弟（`sm`）にも当てる。** 引数は主の分の後ろに
        # もう一度、同じ順で積む。
        sibling_clause, sibling_params = _filters(
            "sm", kind, role, profile, captured_from, captured_to, q, destination_id, status
        )
        secondary = _secondary_exists_sql(sibling_clause or "1")
        hide_as_secondary = f"({secondary}) AND NOT ({_AMBIGUOUS_EXISTS})"
        exclude = f"NOT ({hide_as_secondary})"
        where = f"{where} AND {exclude}" if where else f"WHERE {exclude}"
        params = (*params, *sibling_params)

    total = conn.execute(
        f"{prefix}SELECT count(*) AS n FROM media_file m {where}",  # noqa: S608
        (*prefix_params, *params),
    ).fetchone()["n"]
    rows = conn.execute(
        f"{prefix}SELECT m.* FROM media_file m {where}"  # noqa: S608
        " ORDER BY m.captured_at DESC, m.rel_path DESC LIMIT ? OFFSET ?",
        (*prefix_params, *params, limit, offset),
    )
    media = []
    for row in rows:
        item = _media(row)
        if ranks_sql:
            # 組の中身は、主の行 1 つにつき 1 回だけ引く。
            member_rows = _members_of(conn, row["id"], ranks_sql, ranks_params)
            if member_rows is not None:
                item["stack"] = _stack_json(member_rows)
        media.append(item)
    return {
        "media": media,
        "total": total,
        "page": max(1, page),
        "page_size": limit,
    }


def _members_of(
    conn,  # noqa: ANN001
    media_id: str,
    ranks_sql: str,
    ranks_params: tuple[Any, ...],
) -> list[Any] | None:
    """主から見た組の中身（**主を先頭に**、順位の順）. 組でなければ None.

    **現行の `stack` 規則で絞る。** `copresent_key` は残り続けるのに順位は現行版
    なので、絞らないと `extensions` を変えた後に `identity_partners` と食い違う
    （「同じ関数が決める」が崩れ、順位の dict 引きも KeyError になる）。

    **曖昧なら None を返す。** `identity_partners` は同じ状況を `ambiguous=True`
    と判定し、`resolve_group` は組を作らない（`Refusal`）。ここで揃えないと、
    画面が Immich には作らない組を宣言してしまう（`docs/history/phase10-design.md`
    の「画面に出す組と Immich が作る組は、同じ関数が決める」）。
    """
    ambiguous = _ambiguous_exists_sql("?")
    rows = conn.execute(
        f"WITH rank(profile_id, extension, rank) AS (VALUES {ranks_sql})"  # noqa: S608
        " SELECT DISTINCT sm.id AS id, sm.rel_path AS rel_path,"
        "        sm.size_bytes AS size_bytes, r.rank AS rank"
        "   FROM source_entry me"
        "   JOIN source_entry sib"
        "     ON sib.volume_instance_id = me.volume_instance_id"
        "    AND sib.copresent_key = me.copresent_key"
        "    AND sib.media_file_id IS NOT NULL AND sib.state = 'published'"
        "   JOIN media_file sm ON sm.id = sib.media_file_id"
        "   JOIN rank r ON r.profile_id = sm.profile_id AND r.extension = sib.extension"
        "  WHERE me.media_file_id = ? AND me.state = 'published'"
        "    AND me.copresent_key IS NOT NULL"
        f"    AND NOT ({ambiguous})"  # noqa: S608 - `ambiguous` は定数から組む static な文字列
        "  ORDER BY r.rank",
        (*ranks_params, media_id, media_id),
    ).fetchall()
    if len(rows) < 2:  # noqa: PLR2004 - 1 つでは組にならない
        return None
    return rows


def _ranks(conn) -> tuple[str, tuple[Any, ...]]:  # noqa: ANN001
    """順位表を `VALUES` の並びにする. **空なら `("", ())`**（`VALUES` は 0 行を書けない）.

    一覧と詳細が同じ表を作る。**2 か所で組み立てると、片方だけが古い規則を読む。**
    """
    ranks = stack_extension_ranks(ProfileRegistry(conn).all())
    if not ranks:
        return "", ()
    return ", ".join(["(?, ?, ?)"] * len(ranks)), tuple(value for row in ranks for value in row)


def _stack_json(member_rows: list[Any]) -> dict[str, Any]:
    """組を API の形にする（一覧と詳細で同じ形）."""
    return {
        "members": [
            {
                "id": member["id"],
                "rel_path": member["rel_path"],
                "size_bytes": member["size_bytes"],
            }
            for member in member_rows
        ]
    }


def _stack_of(conn, media_id: str) -> dict[str, Any] | None:  # noqa: ANN001
    """1 件から見た組（**主が先頭**）. 組でなければ None.

    **一覧と同じ `_members_of` を通す。** 曖昧な組を組にしない判断を 2 か所に
    書かない（`docs/history/phase10-design.md` の「画面に出す組と Immich が作る組は、
    同じ関数が決める」）。
    """
    ranks_sql, ranks_params = _ranks(conn)
    if not ranks_sql:
        return None
    member_rows = _members_of(conn, media_id, ranks_sql, ranks_params)
    if member_rows is None:
        return None
    return _stack_json(member_rows)


def _filters(  # noqa: PLR0913
    alias: str,
    kind: str | None,
    role: str | None,
    profile: str | None,
    captured_from: str | None,
    captured_to: str | None,
    q: str | None,
    destination_id: str | None,
    status: str | None,
) -> tuple[str, tuple[Any, ...]]:
    """絞り込みの節（`WHERE` は付けない）と引数. **文字列を連結して値を埋めない。**

    **`alias` は `media_file` の別名**（呼び出し側の定数）。一覧は主の行に `m` で
    当て、`collapse=stack` の従外しは同じ節を兄弟の行に `sm` で当てる。
    """
    clauses: list[str] = []
    params: list[Any] = []
    if kind is not None:
        clauses.append(f"{alias}.kind = ?")  # noqa: S608 - alias は呼び出し側の定数
        params.append(kind)
    if role is not None:
        # **既知の 2 値だけリテラルで埋める。** `_KNOWN_ROLES` に無い値（利用者が
        # 送った任意の文字列を含む）は SQL へ触れさせず、常に 0 件になる節にする
        # —— `f"m.role = '{role}'"` へそのまま渡すと文字列連結になってしまう。
        # 既知の 2 値はバインド変数ではなくリテラルで埋める（`_status_clause` の
        # `known[status]` と同じ作法）。バインド変数のままだと SQLite が prepare
        # 時に `role = 'derived'` を証明できず、`0023` の部分索引
        # （`WHERE role = 'derived'`）が選ばれる保証が無い。
        if role in _KNOWN_ROLES:
            clauses.append(f"{alias}.role = '{role}'")  # noqa: S608 - 語彙は上で固定
        else:
            # 知らない値（＝ CHECK 制約の外）は、そもそも 1 行も一致しない。
            clauses.append("0")
    if profile is not None:
        # **`IN` ではなく `=` で書く。** `IN` だと SQLite は複数の値を取りうると
        # 見なして、索引があっても並べ替えを外せない（`0014` が効かず、その
        # プロファイルの全行を拾ってから並べ替える）。slug は UNIQUE なので
        # 値は高々 1 つで、意味は変わらない（無ければ NULL 比較で 0 件）。
        clauses.append(
            f"{alias}.profile_id = (SELECT id FROM device_profile WHERE slug = ?)"  # noqa: S608
        )
        params.append(profile)
    if captured_from is not None:
        clauses.append(f"{alias}.captured_at >= ?")  # noqa: S608 - alias は呼び出し側の定数
        params.append(captured_from)
    if captured_to is not None:
        clauses.append(f"{alias}.captured_at <= ?")  # noqa: S608 - alias は呼び出し側の定数
        params.append(captured_to)
    if q is not None:
        # 保存先の名前で探す（カード上の原名は列に持っていない）。
        clauses.append(  # noqa: S608 - alias は呼び出し側の定数
            f"{alias}.rel_path LIKE ? ESCAPE '\\'"
        )
        params.append(f"%{escape_like(q)}%")
    if status is not None:
        if destination_id is None:
            raise ApiError(400, ErrorCode.BAD_REQUEST, "status は destination_id と一緒に指定する")
        clauses.append(_status_clause(status, alias))
        params.append(destination_id)
    elif destination_id is not None:
        clauses.append("1 = 1 AND ? IS NOT NULL")
        params.append(destination_id)
    return " AND ".join(clauses), tuple(params)


def _status_clause(status: str, alias: str) -> str:
    """宛先ごとの状態。**無効化された記録は数えない**（§10）.

    **`alias` は `media_file` の別名**（`_filters` から渡る呼び出し側の定数）。
    """
    existing = (
        f"SELECT 1 FROM upload_record u WHERE u.media_file_id = {alias}.id"  # noqa: S608
        " AND u.destination_id = ? AND u.invalidated_at IS NULL"
    )
    if status == "unsent":
        # **「まだ送っていない」＝ この宛先の有効な記録がまだ無く、いま送れるもの。**
        # `failed` は再試行という別の操作、`pending` は既に積んである。
        # **積んだまま claim されない `pending` はここに出てこない。** `/dashboard` の
        # 宛先ごとの `pending` 件数で別に見せる（`status=pending` でも個別に絞れる）。
        return f"NOT EXISTS ({existing}) AND {sendable_clause(alias)}"  # noqa: S608 - 定数のみ
    known = {
        "sent": "complete",
        "failed": "failed",
        "awaiting": "awaiting_datetime_approval",
        "pending": "pending",
    }
    if status not in known:
        raise ApiError(400, ErrorCode.BAD_REQUEST, "知らない status", {"status": status})
    return f"EXISTS ({existing} AND u.state = '{known[status]}')"  # noqa: S608 - 語彙は上で固定


# **`/media/{media_id}` より前に置く。** 後ろだと `media_id = "stale-derived"` として
# 飲まれ、404 になる（この案件で何度も出た「並びの順で API が飲まれる」）。
@router.get("/media/stale-derived")
def list_stale_derived(
    conn=Depends(get_conn),  # noqa: ANN001, B008
    state=Depends(get_state),  # noqa: ANN001, B008
) -> dict[str, Any]:
    """もう使われていない派生物（やり直しの後片付けの対象）.

    置き換えられたグループは `GET /merge-groups` に出ないので、その「できた
    ファイル」はここからしか辿れない。
    """
    repo = MediaRepository(conn, state.settings.data_root)
    return {"stale": repo.list_stale_derived()}


@router.get("/media/{media_id}")
def get_media(  # noqa: ANN201
    media_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    state=Depends(get_state),  # noqa: ANN001, B008
):
    """1 件のくわしく（§13 の「くわしく」画面）.

    **画面が要るものを 1 本で返す。** 複数の API を継ぎ足すと、片方だけ古い状態が出る。
    """
    row = conn.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
    if row is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのメディアは無い")
    repo = MediaRepository(conn, state.settings.data_root)
    blocker = repo.deletion_blocker(media_id)
    return {
        **_media(row),
        # **この 1 件が属する組**（RAW+JPEG。組でなければ `None`）。画面は
        # 送るものをここから選ぶので、**一覧と同じ判断で返す**。
        "stack": _stack_of(conn, media_id),
        "sources": _sources(conn, media_id),
        "destinations": _destinations(conn, media_id),
        "deletable": blocker is None,
        "delete_blocked_reason": blocker,
        # **消すと元になったファイルが「まだ送っていない」に戻るか.** 現行グループの
        # 出力を消すときだけ真。確認ダイアログの文言をこれで出し分ける
        # （画面はグループの現行性を知らないので、ここで返す）。
        "delete_frees_sources": repo.delete_frees_sources(media_id),
        # **この出力を持っているグループ**（元のファイルなら `None`）。つなぐ画面は
        # 「まだつないでいないもの」だけを出すので、採用・やり直し・構成の変更・
        # 別々にするの入口はこの画面にある。**判断に要る欄を同じ 1 本で返す** ——
        # 別の API を継ぎ足すと、検証結果と「消せるか」が別々の時点の状態になる。
        "group": _merge_group(conn, media_id),
    }


def _merge_group(conn, media_id: str) -> dict[str, Any] | None:  # noqa: ANN001
    """持ち主のグループを、つなぐ画面と同じ形で返す.

    **表現を書き写さない。** `routes_merges.group_view` を呼ぶ —— `profile_changed`
    の導き方が 2 か所にあると、採用の入口だけが古い条件を持つことになる。
    """
    owner = owner_group(conn, media_id)
    if owner is None:
        return None
    repo = MergeRepository(conn)
    row = repo.get(owner["id"])
    if row is None:
        return None
    return group_view(repo, row, _current_revisions(conn))


def _sources(conn, media_id: str) -> list[dict[str, Any]]:  # noqa: ANN001
    """この 1 件の元になったファイル. **`position` 順**（つないだ順）.

    **持ち主のグループ 1 つの member だけを出す。** 同じ出力を複数のグループが
    指しうる（`db.media.owner_group`）ので、`output_media_file_id` で直に join すると
    元ファイルが 2 重に並び、確認ダイアログの「元になった N 件」も 2 倍になる。
    """
    group = owner_group(conn, media_id)
    if group is None:
        return []
    rows = conn.execute(
        "SELECT mm.position, m.id, m.rel_path, m.missing_at"
        " FROM merge_member mm JOIN media_file m ON m.id = mm.media_file_id"
        " WHERE mm.merge_group_id = ? ORDER BY mm.position",
        (group["id"],),
    )
    return [
        {
            "media_file_id": row["id"],
            "rel_path": row["rel_path"],
            "position": row["position"],
            "missing": row["missing_at"] is not None,
        }
        for row in rows
    ]


# **「生きている」順に並べた presence の優先度.** 同じ宛先に複数の有効な記録が
# 残ることがある —— 向き先が変わって `target_epoch` が進んでも、`complete` は
# 履歴として invalidate されない（`db/destinations.py` の
# `_invalidate_old_epoch_locked`）。`_destinations` はこの並びで 1 宛先 1 行に畳む。
# **`target_epoch` だけに絞って現行分だけを出さない。** `deletion_blocker` は
# epoch を区別せず有効な記録を全部見るので、現行 epoch だけを出す画面は
# 「旧 epoch に `present` な資産が残っていて消せない」を説明できなくなる。
# 優先度で畳めば、画面に出る状況と削除の可否が必ず一致する。
#
# **削除を断る 3 状態（`sending` / `present` / `unknown`）は、断らない 4 状態
# （`trashed` / `gone` / `failed` / `not_sent`）より必ず上に置く。** 下にあると、
# 画面が「消せそうな状況」を出しているのに `deletion_blocker` が断る組み合わせが
# できる。3 状態の中の順は `deletion_blocker` が理由を選ぶ順（決着していない
# 記録を最優先）にそろえる。
_PRESENCE_PRIORITY = ("sending", "present", "unknown", "trashed", "gone", "failed", "not_sent")


def _destinations(conn, media_id: str) -> list[dict[str, Any]]:  # noqa: ANN001
    """宛先ごとの状況. **日本語にはしない** —— 画面が §13 の語彙で訳す.

    **1 宛先 1 行に畳む。** `target_epoch` は API に出さない内部の概念。
    `_PRESENCE_PRIORITY` の優先度で、同じ宛先の有効な記録から最良の 1 件を選ぶ。

    **退役した宛先（`archived_at` あり）は、記録が無ければ出さない。** もう
    送り先ではないので「まだ送っていません」を並べても押しようが無い。ただし
    過去に送った（無効化されていない）記録が残っているなら、履歴として出す。
    """
    rows = conn.execute(
        "SELECT d.id, d.name, d.archived_at, u.id AS upload_id, u.state,"
        "       u.remote_asset_id, u.remote_is_trashed, u.remote_checked_at"
        " FROM upload_destination d"
        " LEFT JOIN upload_record u ON u.destination_id = d.id"
        "   AND u.media_file_id = ? AND u.invalidated_at IS NULL"
        " ORDER BY d.name",
        (media_id,),
    )
    destinations: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        destination_id = row["id"]
        if destination_id not in destinations:
            destinations[destination_id] = {
                "name": row["name"],
                "archived_at": row["archived_at"],
                "records": [],
            }
            order.append(destination_id)
        if row["upload_id"] is not None:
            destinations[destination_id]["records"].append(row)
    result: list[dict[str, Any]] = []
    for destination_id in order:
        info = destinations[destination_id]
        records = info["records"]
        if not records:
            if info["archived_at"] is not None:
                # 記録の無い退役済みの宛先は、押しようの無い行を並べない。
                continue
            result.append(
                {
                    "destination_id": destination_id,
                    "name": info["name"],
                    "state": None,
                    "presence": "not_sent",
                    "upload_id": None,
                }
            )
            continue
        best = min(records, key=lambda r: _PRESENCE_PRIORITY.index(_presence(r)))
        result.append(
            {
                "destination_id": destination_id,
                "name": info["name"],
                "state": best["state"],
                "presence": _presence(best),
                "upload_id": best["upload_id"],
            }
        )
    return result


def _presence(row) -> str:  # noqa: ANN001
    """`deletion_blocker` と同じ判断を、1 行ぶんの語彙にほどく.

    片方を変えたらもう片方も変える（`deletion_blocker` が正）。
    """
    if row["state"] is None:
        return "not_sent"
    if row["state"] in IN_FLIGHT_STATES:
        return "sending"
    if row["remote_asset_id"] is not None:
        return "trashed" if row["remote_is_trashed"] else "present"
    if row["state"] == "complete":
        return "gone" if row["remote_checked_at"] is not None else "unknown"
    return "failed"


@router.delete("/media/{media_id}")
def delete_media(  # noqa: ANN201
    media_id: str,
    conn=Depends(get_conn),  # noqa: ANN001, B008
    state=Depends(get_state),  # noqa: ANN001, B008
):
    """**Immich に生きていない `derived` だけ**消す（写真タブの「消す」）.

    元ファイルは消せない。現行のグループの出力なら、グループごと「別々にした」
    にしてから消す。消せない理由は 409 で返す（規則は `deletion_blocker`）。
    """
    repo = MediaRepository(conn, state.settings.data_root)
    try:
        rel_path = repo.delete_derived(media_id)
    except GroupNotEditable as exc:
        raise ApiError(409, ErrorCode.CONFLICT, str(exc)) from exc
    return {"status": "ok", "rel_path": rel_path}


@router.get("/media/{media_id}/thumbnail")
def get_thumbnail(  # noqa: ANN201
    media_id: str,
    request: Request,
    at: int = 0,
    state=Depends(get_state),  # noqa: ANN001, B008
    conn=Depends(get_conn),  # noqa: ANN001, B008
):
    """サムネイルを返す（`at` は秒。刻みに丸める）.

    **同じ絵には同じ札を付ける。** 丸めた後の位置で `ETag` を作るので、
    `at=13` と `at=17` は同じ応答になる。
    """
    row = conn.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
    if row is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, "そのメディアは無い")
    position = quantise(at, row["duration_seconds"])
    etag = f'"{row["sha1"]}-{position}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    try:
        path = state.thumbnails.get_or_create(
            media_id, state.settings.data_root / row["rel_path"], position
        )
    except ThumbnailFailed as exc:
        # 元のファイルが消えている・壊れている。**理由の分かる形で返す。**
        raise ApiError(
            422, ErrorCode.THUMBNAIL_FAILED, "サムネイルを作れなかった", {"at": position}
        ) from exc
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"ETag": etag, "Cache-Control": "private, max-age=604800"},
    )


@router.get("/orphans")
def list_orphans(state=Depends(get_state), conn=Depends(get_conn)) -> dict[str, Any]:  # noqa: ANN001, B008
    report = state.last_reconcile
    missing = conn.execute(
        "SELECT id, rel_path, missing_at FROM media_file WHERE missing_at IS NOT NULL"
    )
    return {
        "orphans": [
            {"rel_path": o.rel_path, "size_bytes": o.size_bytes, "sha1": o.sha1}
            for o in report.orphans
        ],
        "unrecoverable": report.unrecoverable,
        "missing": [
            {"id": row["id"], "rel_path": row["rel_path"], "missing_at": row["missing_at"]}
            for row in missing
        ],
    }


def _media(row) -> dict[str, Any]:  # noqa: ANN001
    return {
        "id": row["id"],
        "role": row["role"],
        "rel_path": row["rel_path"],
        "size_bytes": row["size_bytes"],
        "kind": row["kind"],
        "captured_at": row["captured_at"],
        "captured_at_source": row["captured_at_source"],
        "duration_seconds": row["duration_seconds"],
        "probe_state": row["probe_state"],
        "missing_at": row["missing_at"],
    }
