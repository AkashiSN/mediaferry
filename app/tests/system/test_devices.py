"""2 枚同時に挿してある状態を、実プロセスで確かめる（Phase 5 §20）.

**Canon の実カードは手元に無い**（§1 の「残っていること」3）。ここで通せるのは
「仕様と `require` から組み立てた合成カードが `canon-eos` に確定する」ところまでで、
実カードの構成とラベルは `phase1-manual-checklist.md` で見る。
"""

from __future__ import annotations

import httpx
import pytest

from .harness import system_app

pytestmark = pytest.mark.needs_system


def test_two_cards_are_listed_and_matched_independently(tmp_path):
    with system_app(tmp_path) as app:
        volumes = httpx.get(f"{app.url}/api/devices", timeout=30.0).json()["volumes"]

    by_label = {volume["fs_label"]: volume for volume in volumes}
    assert set(by_label) == {"SD_Card", "EOS_DIGITAL"}
    assert by_label["SD_Card"]["profile_slug"] == "dji-osmo"
    # **USB ID はリーダーのものなので手がかりにならない。** ラベルと中身で決まる。
    assert by_label["EOS_DIGITAL"]["profile_slug"] == "canon-eos"
    # 判定の理由は画面に出す（§13）。空にしない。
    assert by_label["EOS_DIGITAL"]["reason"]
    # 初めて見るカードは必ず承認を待つ（§12.1）。**確度は承認とは別の問い**で、
    # 指紋を憶えた後の 2 度目の観測では high になりうる（watcher が先に 1 度
    # 観測してから API の refresh が走るため、ここでの値は tick 数に依存する）。
    assert by_label["EOS_DIGITAL"]["trusted"] is False
    assert by_label["SD_Card"]["trusted"] is False
    assert by_label["EOS_DIGITAL"]["identity_confidence"] in {"low", "high"}


def test_the_default_timezone_can_be_left_to_the_database(tmp_path):
    """env に無ければ画面から変えられる（`locked` にならない）.

    再計算の受け入れは「設定を変えてから直す」筋書きなので、env で固定された
    ままでは経路そのものが試せない（§12.2）。
    """
    with system_app(tmp_path, default_timezone=None) as app:
        settings = httpx.get(f"{app.url}/api/settings", timeout=30.0).json()["settings"]

    row = next(item for item in settings if item["key"] == "DEFAULT_TIMEZONE")
    assert row["locked"] is False
    assert row["writable"] is True
    assert row["value"] is None
