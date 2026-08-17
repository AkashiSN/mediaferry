import hashlib
import io

from mediaferry.core.fingerprint import (
    FINGERPRINT_VERSION,
    WINDOW_BYTES,
    WINDOW_COUNT,
    quick_fingerprint,
    window_offsets,
)


def a_file(size, seed=b"\x01"):
    return io.BytesIO((seed * size)[:size])


def test_small_files_are_read_whole():
    assert window_offsets(1000) == [0]


def test_offsets_are_deterministic_and_ordered():
    first = window_offsets(10_000_000)
    assert first == window_offsets(10_000_000)
    assert first == sorted(first)
    assert len(first) == WINDOW_COUNT
    assert first[0] == 0
    assert first[-1] == 10_000_000 - WINDOW_BYTES


def test_windows_never_overlap_or_repeat():
    """窓が重なると、同じバイトを二重に読んで読み取り量だけが増える.

    現在の定数では閾値の直上でも間隔が窓幅を下回らないので、重複は起きない。
    window_offsets の set() はこの前提が崩れたときの保険。
    """
    for size in (
        WINDOW_BYTES * WINDOW_COUNT + 1,
        WINDOW_BYTES * WINDOW_COUNT + 10,
        3_000_000,
        16 * 1024**3,
    ):
        offsets = window_offsets(size)
        assert offsets == sorted(set(offsets))
        assert all(b - a >= WINDOW_BYTES for a, b in zip(offsets, offsets[1:], strict=False))
        assert offsets[-1] + WINDOW_BYTES <= size


def test_files_up_to_one_mib_are_read_whole():
    """窓の合計より小さいファイルを分割して読む意味は無い.

    閾値を下げると、64KiB を超えるだけのファイルが全体ではなく先頭だけの
    指紋になり、末尾を差し替えても同じ指紋になる。
    """
    assert window_offsets(WINDOW_BYTES + 1) == [0]
    assert window_offsets(WINDOW_BYTES * WINDOW_COUNT) == [0]

    size = 500_000
    data = bytearray(b"\x01" * size)
    changed = bytearray(data)
    changed[-1] = 0xFF
    assert quick_fingerprint(io.BytesIO(bytes(data)), size) != quick_fingerprint(
        io.BytesIO(bytes(changed)), size
    )


def test_size_is_part_of_the_digest():
    """サイズを含めないと、連結の曖昧さで別の内容が同じ指紋になりうる."""
    assert quick_fingerprint(a_file(100), 100) != quick_fingerprint(a_file(200), 200)


def test_same_bytes_give_the_same_digest():
    assert quick_fingerprint(a_file(5000), 5000) == quick_fingerprint(a_file(5000), 5000)


def test_a_change_inside_a_sampled_window_is_detected():
    size = 4 * 1024 * 1024
    base = bytearray(size)
    changed = bytearray(size)
    changed[0] = 0xFF
    assert quick_fingerprint(io.BytesIO(bytes(base)), size) != quick_fingerprint(
        io.BytesIO(bytes(changed)), size
    )


def test_a_change_in_a_later_window_is_detected():
    """窓ごとに seek しないと、先頭 1MiB を連続して読むだけになる.

    16GiB のカードでは、それ以降の差し替えを一切検出できなくなる。
    """
    size = 4 * 1024 * 1024
    base = bytearray(size)
    changed = bytearray(size)
    changed[size - 1] = 0xFF  # 最後の窓の中
    assert quick_fingerprint(io.BytesIO(bytes(base)), size) != quick_fingerprint(
        io.BytesIO(bytes(changed)), size
    )


def test_the_digest_has_the_documented_construction():
    """仕様の式 sha1(b"mfq" + u8(version) + u64le(size) + windows) と一致する."""
    data = bytes(range(256)) * 4  # 1024 バイト
    expected = hashlib.sha1(  # noqa: S324
        b"mfq" + bytes([FINGERPRINT_VERSION]) + len(data).to_bytes(8, "little") + data,
        usedforsecurity=False,
    ).hexdigest()
    assert quick_fingerprint(io.BytesIO(data), len(data)) == expected


def test_an_empty_file_has_a_digest():
    assert len(quick_fingerprint(io.BytesIO(b""), 0)) == 40
