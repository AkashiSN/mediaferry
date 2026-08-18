"""生成した TypeScript の型が、いまの API と揃っているか（§11）.

**型は手書きしない。** `web/src/api/types.ts` は `openapi-typescript` の生成物で、
リポジトリに追跡している。API を足したのに再生成し忘れると、画面は古い形のまま
組み上がる —— それを **npm を使わずに**（Python のテストだけで）検出する。

    npm --prefix web run typegen
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mediaferry.api.app import create_app

TYPES = Path(__file__).resolve().parents[2] / "web" / "src" / "api" / "types.ts"


@pytest.mark.skipif(not TYPES.exists(), reason="web の型がまだ無い")
def test_every_route_appears_in_the_generated_types():
    paths = set(create_app().openapi()["paths"])
    generated = TYPES.read_text(encoding="utf-8")
    missing = sorted(path for path in paths if f'"{path}"' not in generated)
    assert not missing, f"型を再生成する（npm --prefix web run typegen）: {missing}"
