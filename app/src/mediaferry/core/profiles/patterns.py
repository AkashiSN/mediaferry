"""プロファイルの正規表現を、実行時の上限つきで当てる.

**プロファイルはユーザが書き換える。長さの上限では防げない。** `(a+)+$` は
8 文字で、41 文字の入力に対して事実上停止しない。しかもマッチは
`VolumeService` の `RLock` の中で最大 2000 件のファイル名に当たるので、
1 本の悪い式で `GET /devices` も `VolumeWatcher` も固まる。

**保存時に敵対的な標本を試すだけでは足りない**（`(z+)+$` は `a` の標本を
素通りする）。固定標本は lint であって、固まらないことの保証にならない。

そこで `regex` を使い、必ず `timeout` を渡す。実測（2026-08-19）:

| 式 | `re` | `regex` | `regex` + `timeout=0.5` |
| --- | --- | --- | --- |
| `(a+)+$` に `"a"*40+"!"` | 10 秒でも終わらない | 0.000 秒 | 0.000 秒 |
| `(a*)*b` に同上 | — | 0.000 秒 | 0.000 秒 |
| `(a\\|a)+$` に同上 | — | 8 秒でも終わらない | `TimeoutError` を 0.500 秒で送出 |
| `^DJI_(\\d{14})_` に実ファイル名 | — | 0.0001 秒 | 0.0001 秒 |

`regex` は代表的な破綻パターンを自前の最適化で潰し、潰しきれないものには
`timeout` が壁時計の上限として効く。構文は `re` の上位互換なので、既存の
ビルトインの式はそのまま動く。

**`PatternTimeout` は「一致しなかった」ではなく失敗として扱う。** 黙って
不一致にすると、原因が画面から分からない。
"""

from __future__ import annotations

import regex

# 1 回のマッチに許す壁時計。正常な式は桁違いに速い（実測 0.0001 秒）ので、
# 余裕を持たせても悪性の式だけが当たる。
MATCH_TIMEOUT_SECONDS = 0.5


class PatternTimeout(RuntimeError):
    """正規表現の照合が上限を超えた."""


def compile_pattern(pattern: str):  # noqa: ANN201 - regex.Pattern
    return regex.compile(pattern)


def search(pattern: str, subject: str):  # noqa: ANN201 - regex.Match | None
    return _guard(lambda: regex.search(pattern, subject, timeout=MATCH_TIMEOUT_SECONDS), pattern)


def match(compiled, subject: str):  # noqa: ANN001, ANN201
    return _guard(lambda: compiled.match(subject, timeout=MATCH_TIMEOUT_SECONDS), compiled.pattern)


def _guard(call, pattern: str):  # noqa: ANN001, ANN202
    try:
        return call()
    except TimeoutError as exc:
        raise PatternTimeout(f"正規表現の照合を打ち切った: {pattern!r}") from exc
