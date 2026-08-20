"""2 枚同時に挿してある状態を、実プロセスで確かめる（Phase 5 §20）.

**Canon の実カードは手元に無い**（§1 の「残っていること」3）。ここで通せるのは
「仕様と `require` から組み立てた合成カードが `canon-eos` に確定する」ところまでで、
実カードの構成とラベルは `docs/hardware-checklist.md` で見る。
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


def test_two_cards_opened_at_once_get_their_own_directories(tmp_path):
    """**土台そのものの競合を先に潰す**（§8「土台を直してから契約を試す」）.

    `BrokerServer` は接続ごとにスレッドを作り、アプリは API 用と watcher 用の
    2 接続を持つ。マウント先を共有の属性で受け渡すと、片方の書き換えがもう片方の
    `os.open` に効いて、**別のカードの dirfd が返る**。そうなると「2 枚を独立に
    判定する」の受け入れが、土台の側で成立しなくなる。
    """
    import os
    import threading

    from .harness import _a_canon_card, _a_canon_volume, _a_card, _a_volume, _Cards

    cards = _Cards({"8:160": _a_card(tmp_path), "8:176": _a_canon_card(tmp_path)})
    opened: dict[str, int] = {}
    ready = threading.Barrier(2)

    def open_one(volume, key):
        # **verify の最中に相手が割り込む**筋書きを決定的に作る。
        _, dirfd = cards.mount(volume, None, ready.wait)
        opened[key] = os.stat(dirfd).st_ino

    threads = [
        threading.Thread(target=open_one, args=(_a_volume(), "dji")),
        threading.Thread(target=open_one, args=(_a_canon_volume(), "canon")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    cards.release_all()
    assert len(opened) == 2
    assert opened["dji"] != opened["canon"], "同じディレクトリを開いている"
