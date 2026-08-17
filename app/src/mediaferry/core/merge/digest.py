"""結合グループの入力ダイジェスト.

構成ファイルの順序付きの id と sha1、結合設定、プロファイルリビジョンから
決定的に作る。**順序が変われば値も変わる。**

グループを手動で編集した後に旧派生物が選択肢へ戻る経路を、この値の一致
判定で塞ぐ（§10）。旧グループは `status = merged` のままなので、これが
無いと候補に残る。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict

from ..profiles.model import MergeRule

# 算出方法を変えたら上げる。上げると既存グループの digest が一致しなくなり、
# 派生物が既定の選択肢から外れる（安全側に倒れる）。
DIGEST_VERSION = 1


def input_digest(
    members: Sequence[tuple[str, str]], rule: MergeRule, profile_revision_id: str
) -> str:
    """`members` は `(media_file_id, sha1)` を position の順に並べたもの."""
    payload = {
        "version": DIGEST_VERSION,
        "members": [{"media_file_id": media_id, "sha1": sha1} for media_id, sha1 in members],
        "merge": asdict(rule),
        "profile_revision_id": profile_revision_id,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
