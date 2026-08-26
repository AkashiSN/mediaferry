# Phase 13 の実装計画 —— 時刻の出所を器から取る

> **エージェントで回す場合:** `superpowers:subagent-driven-development`（推奨）か
> `superpowers:executing-plans` を使い、タスクごとに進める。手順は `- [ ]` で追える。

**目標:** 動画の撮影時刻を QuickTime の `creation_time` から読み、Canon の 4GB 分割を
検出できるようにし、Immich の 9 時間ずれを直し、一覧の同時刻の並びを決定的にする。

**作り:** `timestamp.source` を**出所の連鎖**（配列）にし、`container` を第 4 の出所として
足す。値は `ffprobe` が返した文字列のまま `media_file.container_wall` に持ち、解釈は
`core/timestamps.py` の 1 か所に置く。`media_file` の作り直しが要るので、移行 runner に
**外部キーを外して走らせる経路**を先に足す。

**道具:** Python 3.14 / SQLite / ffmpeg・ffprobe（配るのは bookworm の 5.1 系）/ pytest

**設計:** [`phase13-design.md`](phase13-design.md)（**必ず先に読む**。ここは設計を実行に
落としたものなので、なぜそうするかは設計側にしか無い）

## 全体の制約

- Python は **`>=3.14`**。全モジュールを `from __future__ import annotations` で始める
- ruff は `line-length = 100`、`select = ["E", "F", "I", "UP", "B", "SIM", "ANN", "S"]`。
  **`docs/` は対象外**
- **コメントと docstring は日本語。** いま書かれているコードを**現在形**で説明する。
  **過去の経緯は書かない**（「以前は〜だった」「〜へ移行した」はコメントから除き、
  `docs/` に置く）
- **環境固有の値をコードにもテストにも書かない**（IP、ホスト名、データセットのパス、
  API キー、タイムゾーンの実値）
- **DB に絶対パスを保存しない。** `DATA_ROOT` からの相対パスだけが正規形
- システム時刻は **UTC の ISO-8601 文字列**で DB に入れ、生成は `mediaferry.clock` の
  関数だけを使う。**例外は `media_file.captured_at`**
- 外部コマンドは必ず引数配列で起動する
- **DB 接続はスコープごとに 1 本**
- **実装より先に失敗するテストを書き、失敗を確認してから**最小実装する
- **変異試験は `PYTHONDONTWRITEBYTECODE=1` を付ける**
- **`git checkout` と `git add -A` を使わない**（Phase 9 で作業ディレクトリを飛ばした）
- コミットは Conventional Commits + 日本語の本文。**なぜそうしたか**を本文に残す

## 受け入れコマンド（毎タスクの最後に流す）

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

**Task 12 だけは、これに加えて画面側も流す。**

```bash
npm --prefix web run test && npm --prefix web run lint && npm --prefix web run build
npm --prefix web run test:e2e
```

## 名前の取り決め（タスクをまたいで一致させる）

| 名前 | 型・形 | 決めたタスク |
| --- | --- | --- |
| `ProbeResult.container_wall` | `str \| None`（`ffprobe` の生の文字列） | Task 2 |
| `TimestampRule.source` | `tuple[str, ...]`（出所の連鎖） | Task 3 |
| `TimestampRule.container_semantics` | `str`（既定 `"wall_clock"`） | Task 3 |
| `_TIMESTAMP_SOURCES` | `("filename", "exif", "container", "mtime")` | Task 3 |
| `container_wall_clock(raw, zone, semantics)` | `-> datetime \| None` | Task 4 |
| `resolve_captured_at(..., exif_wall=None, container_wall=None)` | `container_wall: str \| None` | Task 4 |
| `media_file.container_wall` | `TEXT`（nullable） | Task 5 |
| `ArtifactRequest.resolve_captured` | `Callable[[Path, ProbeResult], CapturedAt] \| None` | Task 6 |
| `ts_route_blockers(streams, keep)` | `-> tuple[dict[str, Any], ...]` | Task 9 |
| FK オフの目印 | `-- mediaferry:foreign-keys-off`（**先頭行のみ**） | Task 1 |

## 触るファイル

| ファイル | 役割 | タスク |
| --- | --- | --- |
| `app/src/mediaferry/db/migrate.py` | 移行 runner。FK オフの経路 | 1 |
| `app/src/mediaferry/adapters/ffprobe.py` | `creation_time` を表に出す | 2 |
| `app/src/mediaferry/core/profiles/model.py` | `source` の連鎖と `container_semantics` | 3 |
| `app/src/mediaferry/core/profiles/builtin/*.yaml` | 3 つとも新しい形へ | 3, 11 |
| `app/src/mediaferry/core/timestamps.py` | 連鎖の解決と `container` の解釈 | 4 |
| `app/src/mediaferry/db/migrations/0026_media_file_container.sql` | 作り直し | 5 |
| `app/src/mediaferry/adapters/publisher.py` | probe → captured の順、`container_wall` の保存 | 5, 6 |
| `app/src/mediaferry/jobs/importer.py` | 遅延解決に一本化 | 6 |
| `app/src/mediaferry/jobs/recompute.py` | `container_wall` を DB から読む | 7 |
| `app/src/mediaferry/core/merge/grouping.py` | `container` の分解能 | 8 |
| `app/src/mediaferry/core/merge/streams.py` | `ts_route_blockers` | 9 |
| `app/src/mediaferry/adapters/ffmpeg.py` | TS 経路を塞ぐ | 9 |
| `app/src/mediaferry/jobs/merger.py` | 進捗の拡張子 | 10 |
| `app/src/mediaferry/api/routes_media.py`, `db/selection.py`, `core/listing.py` | 並び | 12 |

---

## Task 1: 移行 runner が、外部キーを外して 1 本の版を走らせられるようにする

**Files:**
- Modify: `app/src/mediaferry/db/migrate.py`
- Test: `app/tests/test_db_migrate.py`

**Interfaces:**
- Consumes: なし（土台）
- Produces: 移行ファイルの**先頭行**が `-- mediaferry:foreign-keys-off` のとき、runner は
  トランザクションの外で `PRAGMA foreign_keys = OFF` にし、適用後に戻して
  `PRAGMA foreign_key_check` を確かめる。空でなければ `MigrationError`

- [ ] **Step 1: 失敗するテストを 3 本書く**

`app/tests/test_db_migrate.py` の末尾に足す。

```python
def _fk_fixture(folder):
    """親子 1 組を作る版。子が親を参照している."""
    (folder / "0001_base.sql").write_text(
        "CREATE TABLE parent (id TEXT PRIMARY KEY, tag TEXT NOT NULL"
        "   CHECK (tag IN ('a', 'b')));\n"
        "CREATE TABLE child (id TEXT PRIMARY KEY,"
        "   parent_id TEXT NOT NULL REFERENCES parent(id) ON DELETE RESTRICT);\n"
        "INSERT INTO parent VALUES ('p1', 'a');\n"
        "INSERT INTO child VALUES ('c1', 'p1');\n",
        encoding="utf-8",
    )


_REBUILD = (
    "CREATE TABLE parent_new (id TEXT PRIMARY KEY, tag TEXT NOT NULL"
    "   CHECK (tag IN ('a', 'b', 'c')));\n"
    "INSERT INTO parent_new SELECT id, tag FROM parent;\n"
    "DROP TABLE parent;\n"
    "ALTER TABLE parent_new RENAME TO parent;\n"
)


def test_rebuilding_a_referenced_table_fails_without_the_marker(tmp_path, monkeypatch):
    """**目印が無ければ、いままでどおり外部キーが効いている.**

    これが通らないと、次のテストが「目印のおかげ」なのか「もともと通る」のかが
    分からない。
    """
    from mediaferry.db import migrate

    folder = tmp_path / "m"
    folder.mkdir()
    _fk_fixture(folder)
    (folder / "0002_rebuild.sql").write_text(_REBUILD, encoding="utf-8")
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", folder)
    conn = Database(tmp_path / "db.sqlite3").connect()
    with pytest.raises(sqlite3.IntegrityError):
        apply_migrations(conn)
    conn.close()


def test_a_migration_can_declare_that_foreign_keys_must_be_off(tmp_path, monkeypatch):
    """**外部キーを持つ表は、FK を外さないと作り直せない.**

    `PRAGMA foreign_keys` はトランザクションの中では黙って無視されるので、
    移行ファイルの中からは外せない。runner が外側で切り替える。
    """
    from mediaferry.db import migrate

    folder = tmp_path / "m"
    folder.mkdir()
    _fk_fixture(folder)
    (folder / "0002_rebuild.sql").write_text(
        "-- mediaferry:foreign-keys-off\n" + _REBUILD, encoding="utf-8"
    )
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", folder)
    conn = Database(tmp_path / "db.sqlite3").connect()
    assert apply_migrations(conn) == [1, 2]
    # 子は残り、親は新しい CHECK を持ち、参照は壊れていない。
    assert conn.execute("SELECT parent_id FROM child").fetchone()[0] == "p1"
    conn.execute("INSERT INTO parent VALUES ('p2', 'c')")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # **適用のあと、外部キーは必ず戻っている。**
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_a_migration_that_leaves_dangling_references_is_refused(tmp_path, monkeypatch):
    """**外部キーを外すことを許す代わりに、適用後に必ず確かめる.**

    SQLite 公式の 12 手順が最後に `PRAGMA foreign_key_check` を求めているのと
    同じ手当て。これが無いと、目印を付けた版は壊れた参照を黙って残せる。
    """
    from mediaferry.db import migrate

    folder = tmp_path / "m"
    folder.mkdir()
    _fk_fixture(folder)
    (folder / "0002_rebuild.sql").write_text(
        "-- mediaferry:foreign-keys-off\n"
        "DELETE FROM parent;\n",  # 子が孤児になる
        encoding="utf-8",
    )
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", folder)
    conn = Database(tmp_path / "db.sqlite3").connect()
    with pytest.raises(MigrationError, match="参照が壊れている"):
        apply_migrations(conn)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_db_migrate.py -k "foreign_keys or dangling or referenced_table" -v
```

期待: `test_a_migration_can_declare_...` と `test_a_migration_that_leaves_dangling_...` が
FAIL（前者は `IntegrityError`、後者は `MigrationError` が上がらない）。
`test_rebuilding_a_referenced_table_fails_without_the_marker` は **最初から PASS**
（いまの挙動を固定するテスト）。

- [ ] **Step 3: 最小の実装**

`app/src/mediaferry/db/migrate.py` に足す。`_TRANSACTION_RE` の下に定数を置く。

```python
# 外部キーを外して走らせる版の目印。**先頭行だけを見る** —— 本文の途中に現れる
# 同じ文字列（コメントの引用など）で外れないようにする。
FK_OFF_MARKER = "-- mediaferry:foreign-keys-off"
```

`apply_migrations` の `_apply_one` 呼び出しを差し替える。

```python
        _apply_one(conn, version, path.name, body, checksum)
```

を

```python
        if body.split("\n", 1)[0].strip() == FK_OFF_MARKER:
            _apply_with_foreign_keys_off(conn, version, path.name, body, checksum)
        else:
            _apply_one(conn, version, path.name, body, checksum)
```

にし、`_apply_one` の下に足す。

```python
def _apply_with_foreign_keys_off(
    conn: sqlite3.Connection, version: int, name: str, body: str, checksum: str
) -> None:
    """外部キーを外して 1 本の版を適用する.

    **`PRAGMA foreign_keys` はトランザクションの中では黙って無視される**ので、
    移行ファイルの中からは外せない。`defer_foreign_keys` も `legacy_alter_table` も
    代わりにならない（前者は DROP の暗黙 DELETE で立った違反が COMMIT まで残り、
    後者はトランザクション内では効かず RENAME が子の参照先を書き換える）。

    外すことを許す代わりに、**適用後に `PRAGMA foreign_key_check` を必ず確かめる**。
    SQLite 公式の 12 手順が最後に求めている手当てで、これが無いと壊れた参照を
    黙って残せる。
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        _apply_one(conn, version, name, body, checksum)
        broken = conn.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise MigrationError(f"{name} の適用後に参照が壊れている（{len(broken)} 件）")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
```

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest app/tests/test_db_migrate.py -v
```

期待: 全部 PASS。

- [ ] **Step 5: 変異試験**

scratchpad に控えを取ってから、1 つずつ壊して対応するテストが落ちることを見る。
**`git checkout` を使わない。**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `body.split("\n", 1)[0].strip()` を `body` にする（目印を本文全体から探す） | 新しく 1 本足す —— 本文の途中に目印がある版で、外部キーが外れないこと |
| `if broken:` を `if False:` にする | `test_a_migration_that_leaves_dangling_references_is_refused` |
| `finally` を `else` にする | `test_a_migration_that_leaves_dangling_references_is_refused`（`foreign_keys` が 1 に戻らない） |
| `PRAGMA foreign_keys = OFF` を消す | `test_a_migration_can_declare_that_foreign_keys_must_be_off` |

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_db_migrate.py -v
```

- [ ] **Step 6: 受け入れとコミット**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/db/migrate.py app/tests/test_db_migrate.py
git commit -m "$(cat <<'EOF'
feat(db): 外部キーを外して走らせる版を、移行 runner が扱えるようにする

media_file のように子から参照されている表は、CHECK 制約を変えるために
作り直しが要る。ところが PRAGMA foreign_keys はトランザクションの中では
黙って無視されるので、移行ファイルの中からは外せない。defer_foreign_keys も
legacy_alter_table も代わりにならないことは実測した（docs/history/phase13-design.md）。

先頭行の目印で宣言させ、runner がトランザクションの外で切り替える。外すことを
許す代わりに、適用後に PRAGMA foreign_key_check を必ず確かめる。SQLite 公式の
12 手順が最後に求めている手当てで、これが無いと壊れた参照を黙って残せる。
EOF
)"
```

---

## Task 2: `ffprobe` の `creation_time` を `ProbeResult` に出す

**Files:**
- Modify: `app/src/mediaferry/adapters/ffprobe.py`
- Test: `app/tests/test_adapter_ffprobe.py`

**Interfaces:**
- Consumes: なし
- Produces: `ProbeResult.container_wall: str | None` —— `format.tags.creation_time` の
  **生の文字列**。写真と probe 失敗では `None`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_adapter_ffprobe.py` の末尾に足す。

```python
def test_the_container_creation_time_is_carried_verbatim(tmp_path, monkeypatch):
    """**器が申告した撮影時刻は、解釈せずそのまま運ぶ.**

    Canon は現地の壁時計を書きながら `Z` を付ける。ここで UTC として読むと
    9 時間ずれた値が固定されてしまう。意味の解釈は `core/timestamps.py` の
    1 か所に置く。
    """
    payload = {
        "format": {"duration": "12.5", "tags": {"creation_time": "2026-08-26T12:35:08.000000Z"}},
        "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}],
    }
    probe = _probe_returning(monkeypatch, payload)
    result = probe.describe(tmp_path / "MVI_0006.MOV", "MOV")
    assert result.container_wall == "2026-08-26T12:35:08.000000Z"


def test_a_container_without_a_creation_time_reports_none(tmp_path, monkeypatch):
    """タグを持たない器もある. 欠けていることと 0 を混ぜない."""
    payload = {"format": {"duration": "12.5"}, "streams": []}
    probe = _probe_returning(monkeypatch, payload)
    assert probe.describe(tmp_path / "a.MOV", "MOV").container_wall is None


def test_a_photo_reports_no_container_time(tmp_path):
    """写真では ffprobe を走らせない. 値も持たない."""
    from mediaferry.adapters.ffprobe import MediaProbe

    assert MediaProbe().describe(tmp_path / "IMG_0001.JPG", "JPG").container_wall is None
```

`_probe_returning` は同ファイルの既存の作法に合わせる。無ければこれを足す。

```python
def _probe_returning(monkeypatch, payload):
    """ffprobe を呼ばずに、決めた JSON を返させる."""
    import json
    import subprocess

    from mediaferry.adapters.ffprobe import MediaProbe

    class _Completed:
        stdout = json.dumps(payload)

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed())
    return MediaProbe()
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_adapter_ffprobe.py -k container -v
```

期待: FAIL（`ProbeResult` に `container_wall` が無い）。

- [ ] **Step 3: 最小の実装**

`app/src/mediaferry/adapters/ffprobe.py`:

```python
@dataclass(frozen=True)
class ProbeResult:
    kind: str  # photo / video
    duration_seconds: float | None
    probe_state: str  # ok / failed / not_applicable
    streams: list[dict[str, Any]] = field(default_factory=list)
    # 器が申告した撮影時刻（`format.tags.creation_time`）。**解釈しない。**
    # 現地の壁時計に `Z` を付ける機種があるので、ここで UTC として読むと
    # ずれが固定される。意味は `core/timestamps.py` が決める。
    container_wall: str | None = None
```

`describe` の戻り値を差し替える。

```python
        return ProbeResult(
            kind="video",
            duration_seconds=duration,
            probe_state="ok",
            streams=payload.get("streams", []),
            container_wall=_container_wall(payload),
        )
```

同ファイルの末尾に足す。

```python
def _container_wall(payload: dict[str, Any]) -> str | None:
    """`format.tags.creation_time` を文字列のまま返す. 無ければ `None`."""
    raw = payload.get("format", {}).get("tags", {}).get("creation_time")
    return raw if isinstance(raw, str) else None
```

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest app/tests/test_adapter_ffprobe.py -v
```

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `raw if isinstance(raw, str) else None` を `raw` にする | 新しく 1 本足す —— `creation_time` が数値で来た定義で `None` になること |
| `_container_wall(payload)` を `None` にする | `test_the_container_creation_time_is_carried_verbatim` |

- [ ] **Step 6: 受け入れとコミット**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/adapters/ffprobe.py app/tests/test_adapter_ffprobe.py
git commit -m "$(cat <<'EOF'
feat(ffprobe): 器が申告した撮影時刻を、解釈せずそのまま運ぶ

Canon の MOV は EXIF を持たないので、いまは mtime へ落ちる。ところが mtime は
録画の終了で、creation_time は開始を指す（実測で 70 秒差）。値を表に出して、
時刻の出所として使えるようにする。

-show_format は既に叩いているので ffprobe の呼び出しは増えない。文字列のまま
運ぶのは、Canon が現地の壁時計に Z を付けるため —— ここで UTC として読むと
ずれが固定される。意味の解釈は core/timestamps.py の 1 か所に置く。
EOF
)"
```

---

## Task 3: `timestamp.source` を出所の連鎖にする

**Files:**
- Modify: `app/src/mediaferry/core/profiles/model.py`
- Modify: `app/src/mediaferry/core/profiles/builtin/dji-osmo.yaml`
- Modify: `app/src/mediaferry/core/profiles/builtin/generic-dcim.yaml`
- Modify: `app/src/mediaferry/core/profiles/builtin/canon-eos.yaml`
- Test: `app/tests/test_profile_model.py`

**Interfaces:**
- Consumes: なし
- Produces: `TimestampRule.source: tuple[str, ...]`、`TimestampRule.container_semantics: str`
  （既定 `"wall_clock"`）。`fallback` は無くなる。`_TIMESTAMP_SOURCES` に `"container"` が入る

**この段では `canon-eos` の連鎖に `container` を入れない。** 解釈が Task 4 で入るまで、
連鎖は `[exif, mtime]` のまま（いまと同じ挙動）にしておく。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_profile_model.py` の末尾に足す。

```python
def test_the_timestamp_source_is_a_chain(a_definition):
    """出所は 1 つではなく順に試す列. 写真は EXIF、動画は器、どちらも無ければ mtime."""
    defn = parse_definition(a_definition(timestamp={
        "source": ["exif", "container", "mtime"],
        "timezone_policy": "none",
        "timezone": None,
    }))
    assert defn.timestamp.source == ("exif", "container", "mtime")


def test_a_chain_that_does_not_end_with_mtime_is_refused(a_definition):
    """**連鎖の終端は必ず mtime.**

    mtime だけが必ず値を返す。終端に置かないと、どの出所も当たらないファイルで
    撮影日時が決まらず、解決が全域関数でなくなる。
    """
    with pytest.raises(ProfileInvalid, match="mtime で終わる"):
        parse_definition(a_definition(timestamp={
            "source": ["exif", "container"],
            "timezone_policy": "none",
            "timezone": None,
        }))


def test_an_empty_chain_is_refused(a_definition):
    with pytest.raises(ProfileInvalid, match="mtime で終わる"):
        parse_definition(a_definition(timestamp={
            "source": [], "timezone_policy": "none", "timezone": None,
        }))


def test_an_unknown_source_is_refused(a_definition):
    with pytest.raises(ProfileInvalid, match="timestamp.source"):
        parse_definition(a_definition(timestamp={
            "source": ["gps", "mtime"], "timezone_policy": "none", "timezone": None,
        }))


def test_the_old_string_form_is_refused(a_definition):
    """**旧形は読まない**（リリース前なので、既存リビジョンを守らないと決めた）."""
    with pytest.raises(ProfileInvalid, match="timestamp.source は配列"):
        parse_definition(a_definition(timestamp={
            "source": "exif", "fallback": "mtime",
            "timezone_policy": "none", "timezone": None,
        }))


def test_the_container_semantics_defaults_to_wall_clock(a_definition):
    """**宣言の無い定義に「瞬間」を仮定しない.** 媒体の性質は形から見分けられない."""
    defn = parse_definition(a_definition(timestamp={
        "source": ["container", "mtime"], "timezone_policy": "none", "timezone": None,
    }))
    assert defn.timestamp.container_semantics == "wall_clock"


def test_the_container_semantics_can_be_declared_as_instant(a_definition):
    defn = parse_definition(a_definition(timestamp={
        "source": ["container", "mtime"], "container_semantics": "instant",
        "timezone_policy": "none", "timezone": None,
    }))
    assert defn.timestamp.container_semantics == "instant"


def test_filename_in_the_chain_still_requires_a_pattern(a_definition):
    """連鎖のどこにあっても、filename は pattern と format が要る."""
    with pytest.raises(ProfileInvalid, match="timestamp.pattern"):
        parse_definition(a_definition(timestamp={
            "source": ["filename", "mtime"], "timezone_policy": "none", "timezone": None,
        }))
```

`a_definition` は同ファイルの既存の fixture。無ければ、既存のテストが定義を組み立てて
いる形をそのまま使う（**新しい fixture を足すより、そのファイルの作法に合わせる**）。

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_profile_model.py -k "chain or container_semantics or old_string" -v
```

期待: FAIL。

- [ ] **Step 3: 最小の実装**

`app/src/mediaferry/core/profiles/model.py`:

```python
_TIMESTAMP_SOURCES = ("filename", "exif", "container", "mtime")
_CONTAINER_SEMANTICS = ("wall_clock", "instant")
```

`TimestampRule` を差し替える。

```python
@dataclass(frozen=True)
class TimestampRule:
    # 出所の連鎖。**先頭から順に試し、最後は必ず `mtime`**（唯一必ず値を返す）。
    source: tuple[str, ...]
    pattern: str | None
    format: str | None
    timezone_policy: str
    timezone: str | None
    # 既定は `wall_clock`。**宣言の無い定義に「瞬間」を仮定しない**（§6）。
    mtime_semantics: str = "wall_clock"
    container_semantics: str = "wall_clock"
```

`_parse_timestamp` を差し替える。

```python
def _parse_timestamp(data: Mapping[str, Any]) -> TimestampRule:
    _reject_unknown(
        data,
        {
            "source",
            "pattern",
            "format",
            "timezone_policy",
            "timezone",
            "mtime_semantics",
            "container_semantics",
        },
        "timestamp",
    )
    raw = data.get("source")
    if not isinstance(raw, list):
        raise ProfileInvalid("timestamp.source は配列")
    source = tuple(raw)
    for name in source:
        if name not in _TIMESTAMP_SOURCES:
            raise ProfileInvalid(f"timestamp.source は {_TIMESTAMP_SOURCES} のいずれか")
    # **mtime だけが必ず値を返す。** 終端に置かないと解決が全域関数でなくなる。
    if not source or source[-1] != "mtime":
        raise ProfileInvalid("timestamp.source は mtime で終わる")
    policy = _string(data, "timezone_policy")
    if policy not in _TIMEZONE_POLICIES:
        raise ProfileInvalid(f"timestamp.timezone_policy は {_TIMEZONE_POLICIES} のいずれか")
    pattern = data.get("pattern")
    fmt = data.get("format")
    if "filename" in source:
        if not isinstance(pattern, str):
            raise ProfileInvalid("source に filename があるなら timestamp.pattern が要る")
        _regex(data, "pattern")
        if "(?P<ts>" not in pattern:
            raise ProfileInvalid("timestamp.pattern は名前付きグループ ts を持つ必要がある")
        if not isinstance(fmt, str):
            raise ProfileInvalid("source に filename があるなら timestamp.format が要る")
    timezone = data.get("timezone")
    if timezone is not None and not isinstance(timezone, str):
        raise ProfileInvalid("timestamp.timezone は文字列か null")
    semantics = data.get("mtime_semantics", "wall_clock")
    if semantics not in _MTIME_SEMANTICS:
        raise ProfileInvalid(f"timestamp.mtime_semantics は {_MTIME_SEMANTICS} のいずれか")
    container = data.get("container_semantics", "wall_clock")
    if container not in _CONTAINER_SEMANTICS:
        raise ProfileInvalid(f"timestamp.container_semantics は {_CONTAINER_SEMANTICS} のいずれか")
    return TimestampRule(
        source=source,
        pattern=pattern if isinstance(pattern, str) else None,
        format=fmt if isinstance(fmt, str) else None,
        timezone_policy=policy,
        timezone=timezone,
        mtime_semantics=semantics,
        container_semantics=container,
    )
```

`model.py` の `_serialise`（定義を JSON へ戻す側）に `fallback` があれば外し、
`source` と `container_semantics` を含めるよう直す。**`grep -n "fallback" app/src` で
残りを全部潰す。**

3 つの YAML を直す。`dji-osmo.yaml`:

```yaml
  source: [filename, mtime]
```
（`fallback: mtime` の行を消す）

`generic-dcim.yaml` と `canon-eos.yaml` も同じ形にする（**この段では `container` を
入れない**）。`canon-eos.yaml`:

```yaml
  source: [exif, mtime]
```

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest app/tests/test_profile_model.py app/tests/test_profile_matching.py \
  app/tests/test_profile_registry.py app/tests/test_timestamps.py -v
```

**既存のテストが落ちたら、まず「挙動が正しく変わったのか」を見る。** `source="exif"` を
前提にしたテストは `source=("exif", "mtime")` へ書き換えるが、**守っているものを緩めない**。

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `source[-1] != "mtime"` を `source[0] != "mtime"` にする | `test_a_chain_that_does_not_end_with_mtime_is_refused` |
| `not source or` を消す | `test_an_empty_chain_is_refused` |
| `isinstance(raw, list)` を `isinstance(raw, (list, str))` にする | `test_the_old_string_form_is_refused` |
| `data.get("container_semantics", "wall_clock")` の既定を `"instant"` にする | `test_the_container_semantics_defaults_to_wall_clock` |
| `"filename" in source` を `source[0] == "filename"` にする | `test_filename_in_the_chain_still_requires_a_pattern`（連鎖の 2 番目に置いた定義を足す） |

- [ ] **Step 6: 受け入れとコミット**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/core/profiles/model.py \
        app/src/mediaferry/core/profiles/builtin/ app/tests/test_profile_model.py
git commit -m "$(cat <<'EOF'
feat(profiles): 撮影日時の出所を、1 つではなく連鎖で宣言する

写真は EXIF、動画は器、どちらも持たなければ mtime —— という 3 段が要る。
いまの source + fallback は 2 段しか書けず、しかも fallback は宣言されている
だけで実装は常に mtime へ落としていた。

終端を mtime に強制するのは、mtime だけが必ず値を返すため。終端に置かないと
どの出所も当たらないファイルで撮影日時が決まらず、解決が全域関数でなくなる。

旧形（source が文字列）は読まない。リリース前なので既存リビジョンを守る必要が
なく、受け口を残すと連鎖が定義ではなく実装に隠れる。
EOF
)"
```

---

## Task 4: `core/timestamps.py` が連鎖を解き、`container` を解釈する

**Files:**
- Modify: `app/src/mediaferry/core/timestamps.py`
- Test: `app/tests/test_timestamps.py`

**Interfaces:**
- Consumes: `TimestampRule.source`（Task 3）
- Produces:
  - `container_wall_clock(raw: str, zone: tzinfo, semantics: str) -> datetime | None`
  - `resolve_captured_at(defn, rel_path, mtime_ns, default_timezone, exif_wall=None,
    container_wall=None)` —— `container_wall` は `str | None`

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_timestamps.py` の末尾に足す。`_canon` はこのファイルの作法に合わせて
定義を組み立てるヘルパ。

```python
def _chain(source, **kwargs):
    """出所の連鎖だけを差し替えた定義を作る."""
    return a_definition(timestamp={
        "source": list(source),
        "timezone_policy": kwargs.pop("timezone_policy", "none"),
        "timezone": kwargs.pop("timezone", None),
        **kwargs,
    })


def test_the_container_time_is_read_as_a_wall_clock_by_default():
    """**`Z` を真に受けない.**

    Canon は現地の壁時計を書きながら `Z` を付ける（実測）。UTC として読むと
    9 時間ずれる。
    """
    defn = parse_definition(_chain(["container", "mtime"]))
    got = resolve_captured_at(
        defn, "DCIM/100CANON/MVI_0006.MOV", 0,
        None, container_wall="2026-08-26T12:35:08.000000Z",
    )
    assert got.source == "container"
    assert got.at.isoformat() == "2026-08-26T12:35:08+00:00"


def test_the_container_time_can_be_declared_as_an_instant():
    """真の UTC を書く器もある. 宣言されたときだけ瞬間として扱う."""
    defn = parse_definition(_chain(
        ["container", "mtime"], container_semantics="instant",
        timezone_policy="force_offset", timezone="Etc/GMT-9",
    ))
    got = resolve_captured_at(
        defn, "a.MOV", 0, None, container_wall="2026-08-26T12:35:08.000000Z"
    )
    assert got.source == "container"
    assert got.at.isoformat() == "2026-08-26T21:35:08+09:00"


def test_the_chain_falls_through_to_mtime_when_the_container_has_no_time():
    """器が時刻を持たないファイルは mtime へ落ちる."""
    defn = parse_definition(_chain(["container", "mtime"]))
    got = resolve_captured_at(defn, "a.MOV", 1_787_747_586_000_000_000, None)
    assert got.source == "mtime"


def test_a_container_time_is_ignored_when_the_chain_does_not_declare_it():
    """**宣言と実際の解釈をずらさない.** 連鎖に無い出所の値が来ても使わない."""
    defn = parse_definition(_chain(["exif", "mtime"]))
    got = resolve_captured_at(
        defn, "a.MOV", 1_787_747_586_000_000_000,
        None, container_wall="2026-08-26T12:35:08.000000Z",
    )
    assert got.source == "mtime"


def test_exif_wins_over_the_container_when_it_comes_first():
    """写真は EXIF、動画は器 —— 1 本の連鎖で両方をまかなう."""
    defn = parse_definition(_chain(["exif", "container", "mtime"]))
    got = resolve_captured_at(
        defn, "IMG_0001.CR2", 0,
        None,
        exif_wall=datetime(2026, 8, 26, 12, 33, 5),
        container_wall="2026-08-26T12:35:08.000000Z",
    )
    assert got.source == "exif"


def test_the_quicktime_epoch_is_treated_as_absent():
    """**日時を設定していない器は 1904-01-01 を書く.**

    そのまま採ると、撮影日時が 1904 年に飛んで一覧の先頭も末尾も壊れる。
    ちょうどこの値だけを「無い」として扱い、次の出所へ落とす。
    """
    defn = parse_definition(_chain(["container", "mtime"]))
    got = resolve_captured_at(
        defn, "a.MOV", 1_787_747_586_000_000_000,
        None, container_wall="1904-01-01T00:00:00.000000Z",
    )
    assert got.source == "mtime"


def test_an_unparsable_container_time_falls_through():
    """読めない値で取り込み全体を止めない."""
    defn = parse_definition(_chain(["container", "mtime"]))
    got = resolve_captured_at(
        defn, "a.MOV", 1_787_747_586_000_000_000, None, container_wall="なんだこれ"
    )
    assert got.source == "mtime"
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_timestamps.py -k container -v
```

期待: FAIL（`resolve_captured_at` に `container_wall` が無い）。

- [ ] **Step 3: 最小の実装**

`app/src/mediaferry/core/timestamps.py`。`resolve_captured_at` の引数を足す。

```python
def resolve_captured_at(
    defn: ProfileDefinition,
    rel_path: str,
    mtime_ns: int,
    default_timezone: str | None,
    exif_wall: datetime | None = None,
    container_wall: str | None = None,
) -> CapturedAt:
```

呼び出しを差し替える。

```python
    wall, source = _wall_clock(defn, rel_path, mtime_ns, exif_wall, container_wall, zone)
    if source == "mtime" and defn.timestamp.mtime_semantics == "instant":
        return CapturedAt(at=wall, source=source, tz=name, note=None)
    if source == "container" and defn.timestamp.container_semantics == "instant":
        # 瞬間から始めた値は fold まで決まっているので付け直さない（mtime と同じ）。
        return CapturedAt(at=wall, source=source, tz=name, note=None)
```

`_wall_clock` を連鎖で書き直す。

```python
def _wall_clock(
    defn: ProfileDefinition,
    rel_path: str,
    mtime_ns: int,
    exif_wall: datetime | None,
    container_wall: str | None,
    zone: tzinfo,
) -> tuple[datetime, str]:
    """宣言された出所を先頭から試す. `zone` は epoch を持つ出所に付ける TZ.

    **返す値が aware なのは `instant` を宣言した出所のときだけ。** 壁時計から
    始めた値へのオフセットの付与は呼び出し側が行う（`_attach_offset`）。
    """
    rule = defn.timestamp
    for name in rule.source:
        if name == "filename":
            found = _from_filename(rule, rel_path)
            if found is not None:
                return found, "filename"
        elif name == "exif":
            if exif_wall is not None:
                return exif_wall, "exif"
        elif name == "container":
            if container_wall is not None:
                found = container_wall_clock(container_wall, zone, rule.container_semantics)
                if found is not None:
                    return found, "container"
        else:
            return mtime_wall_clock(mtime_ns, zone, rule.mtime_semantics), "mtime"
    # 終端が mtime であることはパーサが保証する（`_parse_timestamp`）。
    raise AssertionError("連鎖が mtime で終わっていない")


def _from_filename(rule: TimestampRule, rel_path: str) -> datetime | None:
    if rule.pattern is None or rule.format is None:
        return None
    try:
        found = search(rule.pattern, PurePosixPath(rel_path).name)
    except PatternTimeout:
        # 悪性の式で取り込み全体を止めない。次の出所へ落とす。
        return None
    if found is None:
        return None
    try:
        return datetime.strptime(found.group("ts"), rule.format)  # noqa: DTZ007
    except ValueError:
        return None
```

`mtime_wall_clock` の下に足す。

```python
# 日時を設定していない器が書く値。QuickTime の epoch そのもの。
_QUICKTIME_EPOCH = "1904-01-01T00:00:00"


def container_wall_clock(raw: str, zone: tzinfo, semantics: str) -> datetime | None:
    """器が申告した時刻が指すカード上の時刻. **意味の解釈はここ 1 か所に置く.**

    - `wall_clock`: 桁がそのまま壁時計。`Z` が付いていても無視する（Canon は
      現地の壁時計に `Z` を付ける）。naive で返し、オフセットの付与は
      呼び出し側に任せる
    - `instant`: 真の瞬間なので `zone` へ直した aware な値を返す

    読めない値と、**日時未設定の器が書く QuickTime の epoch** は `None` を返し、
    次の出所へ落とす。1904 年を採ると一覧の並びが端から壊れる。
    """
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.replace(tzinfo=None).isoformat().startswith(_QUICKTIME_EPOCH):
        return None
    if semantics == "instant":
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(zone)
    return parsed.replace(tzinfo=None)
```

`TimestampRule` の import を足す（`from .profiles.model import ProfileDefinition,
TimestampRule`）。

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest app/tests/test_timestamps.py -v
```

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `parsed.replace(tzinfo=None)` を `parsed` にする（`wall_clock` 側） | `test_the_container_time_is_read_as_a_wall_clock_by_default` |
| `semantics == "instant"` を `semantics != "instant"` にする | `test_the_container_time_can_be_declared_as_an_instant` |
| QuickTime epoch の判定を消す | `test_the_quicktime_epoch_is_treated_as_absent` |
| `for name in rule.source` を `for name in reversed(rule.source)` にする | `test_exif_wins_over_the_container_when_it_comes_first` |
| `except ValueError: return None` を `raise` にする | `test_an_unparsable_container_time_falls_through` |
| `container` の枝を無条件に採る（`container_wall is not None` を消す） | `test_the_chain_falls_through_to_mtime_when_the_container_has_no_time` |

- [ ] **Step 6: 受け入れとコミット**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/core/timestamps.py app/tests/test_timestamps.py
git commit -m "$(cat <<'EOF'
feat(timestamps): 器が申告した時刻を出所として解き、連鎖で解決する

Canon は creation_time に現地の壁時計を書きながら Z を付ける（実測）。
真に受けると 9 時間ずれるので、既定では桁をそのまま壁時計として読む。真の UTC を
書く器のために instant も選べるが、宣言されたときだけ使う —— 媒体の性質は
値の形からは見分けられない（mtime_semantics と同じ理由）。

日時未設定の器が書く QuickTime の epoch（1904-01-01）は「無い」として扱う。
そのまま採ると撮影日時が 1904 年へ飛び、一覧の並びが端から壊れる。
EOF
)"
```

---

## Task 5: `media_file` を作り直し、`container_wall` を保存する

**Files:**
- Create: `app/src/mediaferry/db/migrations/0026_media_file_container.sql`
- Modify: `app/src/mediaferry/adapters/publisher.py`（INSERT に 1 列）
- Modify: `app/tests/migration_checksums.txt`（**行を足すだけ。既存の行は書き換えない**）
- Test: `app/tests/test_schema_artifacts.py`, `app/tests/test_db_migrate.py`

**Interfaces:**
- Consumes: Task 1 の FK オフの目印
- Produces: `media_file.container_wall`（TEXT, nullable）、`captured_at_source` が
  `container` を受け付ける、`media_file_listing` と `media_file_derived_listing` が
  `rel_path DESC` で終わる

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_schema_artifacts.py` の末尾に足す。

```python
def test_media_file_accepts_the_container_source(db):
    """`container` を出所として保存できる."""
    media_id = a_media_file(db, a_profile(db), captured_at_source="container",
                            container_wall="2026-08-26T12:35:08.000000Z")
    row = db.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
    assert row["captured_at_source"] == "container"
    assert row["container_wall"] == "2026-08-26T12:35:08.000000Z"


def test_media_file_still_refuses_an_unknown_source(db):
    """CHECK を広げても、知らない出所は弾く."""
    with pytest.raises(sqlite3.IntegrityError):
        a_media_file(db, a_profile(db), captured_at_source="gps")


def test_the_listing_indexes_break_ties_by_rel_path(db):
    """**同じ撮影日時の並びは、乱数ではなく名前で決まる.**

    索引が `id DESC` で終わっていると、`ORDER BY captured_at DESC, rel_path DESC`
    を索引で満たせず一時 B-tree のソートに落ちる。
    """
    indexes = {
        row["name"]: row["sql"]
        for row in db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'media_file'"
        )
        if row["sql"]
    }
    assert "rel_path DESC" in indexes["media_file_listing"]
    assert "rel_path DESC" in indexes["media_file_derived_listing"]
    assert "id DESC" not in indexes["media_file_listing"]
    assert "id DESC" not in indexes["media_file_derived_listing"]


def test_the_captured_revision_triggers_survive_the_rebuild(db):
    """**作り直すと trigger も消える.** `0011` が守っていたものを落とさない."""
    names = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }
    assert "media_file_captured_revision_insert" in names
    assert "media_file_captured_revision_update" in names
```

`app/tests/test_db_migrate.py` に足す。

```python
def test_every_shipped_migration_leaves_the_references_intact(tmp_path):
    """**外部キーを外して走る版があるので、全部流した後に必ず確かめる.**

    `0026` は media_file を作り直す。子から参照されている表なので、手順を
    1 つ間違えると参照が壊れたまま通る。
    """
    conn = Database(tmp_path / "db.sqlite3").connect()
    apply_migrations(conn)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_rows_survive_the_media_file_rebuild(tmp_path, monkeypatch):
    """**作り直しは中身を運ぶ.** 列を足すために行を捨てない.

    `missing_at` を持つ行を入れるのは、`INSERT ... SELECT` の列を 1 つでも
    落としたら落ちるようにするため。
    """
    import shutil

    from mediaferry.clock import now_iso
    from mediaferry.db import migrate

    from .test_schema_artifacts import a_media_file
    from .test_schema_sources import a_profile

    original = migrate.MIGRATIONS_DIR
    folder = tmp_path / "m"
    folder.mkdir()
    for path in sorted(original.glob("*.sql")):
        if not path.name.startswith("0026"):
            shutil.copy(path, folder / path.name)
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", folder)

    conn = Database(tmp_path / "db.sqlite3").connect()
    apply_migrations(conn)
    media_id = a_media_file(
        conn,
        a_profile(conn),
        rel_path="library/dji-osmo/DCIM/OLD.MP4",
        captured_at_source="mtime",
        missing_at=now_iso(),
    )

    # ここで初めて 0026 を持ち込み、当てる。
    shutil.copy(original / "0026_media_file_container.sql", folder)
    assert apply_migrations(conn) == [26]

    row = conn.execute("SELECT * FROM media_file WHERE id = ?", (media_id,)).fetchone()
    assert row["rel_path"] == "library/dji-osmo/DCIM/OLD.MP4"
    assert row["captured_at_source"] == "mtime"
    assert row["missing_at"] is not None
    # 新しい列は既存行では空。値を捏造しない。
    assert row["container_wall"] is None
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
```

`a_media_file` と `a_profile` は `test_schema_artifacts.py` / `test_schema_sources.py` の
既存のヘルパ。`a_media_file(db, profile, **over)` は `INSERT` の列を `over` から組み立てる
ので、`container_wall` を渡せばそのまま入る。

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_schema_artifacts.py -k "container or listing_indexes" -v
```

期待: FAIL。

- [ ] **Step 3: 移行を書く**

`app/src/mediaferry/db/migrations/0026_media_file_container.sql`:

```sql
-- mediaferry:foreign-keys-off
-- 器が申告した撮影時刻（`container_wall`）を持てるようにし、`captured_at_source` に
-- `container` を足す。あわせて一覧の索引を `rel_path` で tie-break する形に直す。
--
-- **作り直しが要るのは CHECK 制約を後から変えられないため。** media_file は
-- merge_group / merge_member / source_entry / upload_record から参照されているので、
-- 外部キーを外さないと入れ替えられない。runner が先頭行の目印を見て切り替え、
-- 適用後に PRAGMA foreign_key_check を確かめる。

CREATE TABLE media_file_new (
    id                  TEXT PRIMARY KEY,
    role                TEXT NOT NULL CHECK (role IN ('original', 'derived')),
    profile_id          TEXT NOT NULL REFERENCES device_profile(id) ON DELETE RESTRICT,
    profile_revision_id TEXT NOT NULL,
    -- DATA_ROOT からの相対パス。保存先の名前であり、カード上の原名ではない。
    rel_path            TEXT NOT NULL UNIQUE,
    size_bytes          INTEGER NOT NULL,
    mtime_ns            INTEGER NOT NULL,
    sha1                TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('photo', 'video')),
    captured_at         TEXT NOT NULL,
    captured_at_source  TEXT NOT NULL
        CHECK (captured_at_source IN ('filename', 'exif', 'container', 'mtime')),
    captured_at_tz      TEXT,
    captured_at_note    TEXT,
    -- ffprobe が返した creation_time をそのまま入れる。**解釈しない** ——
    -- 意味は core/timestamps.py が決めるので、再計算で読み直せる。
    container_wall      TEXT,
    duration_seconds    REAL,
    -- ffprobe を実行していない状態 (not_run) は公開済みレコードには無い。
    probe_state         TEXT NOT NULL CHECK (probe_state IN ('ok', 'failed', 'not_applicable')),
    missing_at          TEXT,
    captured_at_revision_id TEXT REFERENCES profile_revision(id) ON DELETE RESTRICT,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (profile_id, profile_revision_id)
        REFERENCES profile_revision(profile_id, id) ON DELETE RESTRICT,
    -- probe に成功した動画は必ず duration を持つ（§9.7 の境界判定が依存する）。
    CHECK (kind <> 'video' OR probe_state <> 'ok' OR duration_seconds IS NOT NULL)
);

INSERT INTO media_file_new (
    id, role, profile_id, profile_revision_id, rel_path, size_bytes, mtime_ns, sha1, kind,
    captured_at, captured_at_source, captured_at_tz, captured_at_note, duration_seconds,
    probe_state, missing_at, captured_at_revision_id, created_at)
SELECT
    id, role, profile_id, profile_revision_id, rel_path, size_bytes, mtime_ns, sha1, kind,
    captured_at, captured_at_source, captured_at_tz, captured_at_note, duration_seconds,
    probe_state, missing_at, captured_at_revision_id, created_at
FROM media_file;

DROP TABLE media_file;
ALTER TABLE media_file_new RENAME TO media_file;

-- 索引と trigger は DROP TABLE で一緒に消えるので、全部作り直す。
CREATE INDEX media_file_sha1 ON media_file (sha1);
CREATE INDEX media_file_captured_at ON media_file (captured_at);
CREATE INDEX media_file_by_profile ON media_file (profile_id, role, rel_path);

-- **tie-break は rel_path。** id は乱数なので、同じ撮影日時の並びに意味が無い。
-- rel_path は UNIQUE なので単独で足りる。
CREATE INDEX media_file_listing
    ON media_file (profile_id, captured_at DESC, rel_path DESC);
CREATE INDEX media_file_derived_listing
    ON media_file (captured_at DESC, rel_path DESC) WHERE role = 'derived';

-- `0011` の trigger をそのまま作り直す。単一の FK では「同じプロファイルの版で
-- あること」を守れないので、trigger で同じ強さを作っている。
CREATE TRIGGER media_file_captured_revision_insert
BEFORE INSERT ON media_file
WHEN NEW.captured_at_revision_id IS NULL
  OR NOT EXISTS (SELECT 1 FROM profile_revision
                  WHERE id = NEW.captured_at_revision_id
                    AND profile_id = NEW.profile_id)
BEGIN
    SELECT RAISE(ABORT, 'captured_at_revision_id must be a revision of the same profile');
END;

CREATE TRIGGER media_file_captured_revision_update
BEFORE UPDATE OF captured_at_revision_id, profile_id ON media_file
WHEN NEW.captured_at_revision_id IS NULL
  OR NOT EXISTS (SELECT 1 FROM profile_revision
                  WHERE id = NEW.captured_at_revision_id
                    AND profile_id = NEW.profile_id)
BEGIN
    SELECT RAISE(ABORT, 'captured_at_revision_id must be a revision of the same profile');
END;
```

`publisher.py` の INSERT に 1 列足す。

```python
                    "INSERT INTO media_file (id, role, profile_id, profile_revision_id, rel_path,"
                    " size_bytes, mtime_ns, sha1, kind, captured_at, captured_at_source,"
                    " captured_at_tz, captured_at_note, container_wall, duration_seconds,"
                    " probe_state, captured_at_revision_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
```

値の列にも `metadata["container_wall"]` を `captured_at_note` の次へ足す
（`metadata` に入れるのは Task 6）。**この段では `metadata.get("container_wall")` で
`None` を入れておく。**

- [ ] **Step 4: checksum を記録して通す**

```bash
uv run pytest app/tests/test_db_migrate.py -k "previous_release" -v
```

落ちたら、指示に従って `app/tests/migration_checksums.txt` に **1 行足す**。
**既存の行は書き換えない。**

```bash
printf '0026_media_file_container.sql %s\n' \
  "$(sha256sum app/src/mediaferry/db/migrations/0026_media_file_container.sql | cut -d' ' -f1)" \
  >> app/tests/migration_checksums.txt
uv run pytest app/tests/test_schema_artifacts.py app/tests/test_db_migrate.py -v
```

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| 先頭行の目印を消す | 全移行の適用が `IntegrityError` で落ちる |
| trigger の作り直しを 1 本消す | `test_the_captured_revision_triggers_survive_the_rebuild` |
| `media_file_listing` を `id DESC` のまま作る | `test_the_listing_indexes_break_ties_by_rel_path` |
| `INSERT ... SELECT` から `missing_at` を落とす | `test_rows_survive_the_media_file_rebuild`（`missing_at` を持つ行を用意する） |
| CHECK から `'gps'` 相当の締めを外す（`CHECK` ごと消す） | `test_media_file_still_refuses_an_unknown_source` |

- [ ] **Step 6: 受け入れとコミット**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/db/migrations/0026_media_file_container.sql \
        app/src/mediaferry/adapters/publisher.py app/tests/migration_checksums.txt \
        app/tests/test_schema_artifacts.py app/tests/test_db_migrate.py
git commit -m "$(cat <<'EOF'
feat(db): media_file が器の時刻を持ち、一覧の並びを名前で決められるようにする

captured_at_source に container を足すには CHECK を広げる必要があり、SQLite では
テーブルの作り直しになる。子から参照されている表なので、Task 1 で足した
「外部キーを外して走らせる」経路を使う。

作り直しで索引と trigger が消えるため全部作り直す。そのついでに、一覧の索引 2 本を
rel_path で tie-break する形にした。いまの tie-break は 32 桁の乱数 hex なので、
撮影日時が同じ行の並びに再現性が無い（実機で MVI_0007 が MVI_0008 より
左上に来た）。rel_path は UNIQUE なので単独で足りる。
EOF
)"
```

---

## Task 6: 公開の順序を「probe → captured」にし、取り込みを遅延解決へ一本化する

**Files:**
- Modify: `app/src/mediaferry/adapters/publisher.py`
- Modify: `app/src/mediaferry/jobs/importer.py`
- Test: `app/tests/test_publisher.py`, `app/tests/test_importer.py`

**Interfaces:**
- Consumes: `ProbeResult.container_wall`（Task 2）、`resolve_captured_at(..., container_wall=)`（Task 4）
- Produces: `ArtifactRequest.resolve_captured: Callable[[Path, ProbeResult], CapturedAt] | None`。
  `metadata["container_wall"]` が `media_file.container_wall` に入る

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_publisher.py` に足す。

```python
def _container_probe():
    """器の時刻を持つ probe の結果を返す stub."""
    return StubProbe(
        ProbeResult("video", 2.0, "ok", container_wall="2026-08-26T12:35:08.000000Z")
    )


def test_the_probe_runs_before_the_captured_time_is_resolved(setup, db, data_root):
    """**器の時刻は probe の結果からしか取れない.**

    captured を先に決める順序だと、`container` を宣言したプロファイルで値が
    間に合わず、黙って mtime へ落ちる。
    """
    _, ctx, profile, volume_id = setup
    entry_id = a_source_entry(db, volume_id)
    publisher = ArtifactPublisher(db, data_root, _container_probe())
    seen: list[str | None] = []

    def resolve(staging_abs, probe):
        seen.append(probe.container_wall)
        return CapturedAt(
            at=datetime.fromisoformat("2026-08-26T12:35:08+00:00"),
            source="container",
            tz=None,
            note=None,
        )

    publisher.publish(
        ctx,
        a_request(profile, entry_id, captured=None, resolve_captured=resolve),
        write_payload(b"payload"),
    )
    assert seen == ["2026-08-26T12:35:08.000000Z"]


def test_the_container_time_is_stored_verbatim(setup, db, data_root):
    """再計算が再 probe せずに読み直せるように、生の文字列を持つ."""
    _, ctx, profile, volume_id = setup
    entry_id = a_source_entry(db, volume_id)
    publisher = ArtifactPublisher(db, data_root, _container_probe())

    def resolve(staging_abs, probe):
        return CapturedAt(
            at=datetime.fromisoformat("2026-08-26T12:35:08+00:00"),
            source="container",
            tz=None,
            note=None,
        )

    got = publisher.publish(
        ctx,
        a_request(profile, entry_id, captured=None, resolve_captured=resolve),
        write_payload(b"payload"),
    )
    row = db.execute("SELECT * FROM media_file WHERE id = ?", (got.media_file_id,)).fetchone()
    assert row["container_wall"] == "2026-08-26T12:35:08.000000Z"
    assert row["captured_at_source"] == "container"
```

`a_request` は既定で `captured` を持つので、**`captured=None` を明示してから
`resolve_captured` を渡す**（`__post_init__` が「どちらか一方」を要求する）。

`app/tests/test_importer.py` に足す。`importing` fixture は `StubProbe()` を使うので、
器の時刻を持つ probe に差し替えた `Importer` を組み立てる。

```python
def test_a_video_takes_its_time_from_the_container(db, data_root, tmp_path):
    """**取り込みは常に遅延解決.** 器の時刻はステージ済みのファイルからしか読めない.

    `dji-osmo` は名前に時刻を持つので、器を見に行かないプロファイルでは差が出ない。
    ここでは `canon-eos`（`source: [exif, container, mtime]`）で見る。
    """
    from mediaferry.adapters.ffprobe import ProbeResult

    ProfileRegistry(db).sync_builtins()
    profile = ProfileRegistry(db).current("canon-eos")
    store = JobStore(db)
    store.enqueue("import", {})
    ctx = store.claim_next()

    card = tmp_path / "card"
    (card / "DCIM" / "100CANON").mkdir(parents=True)
    (card / "DCIM" / "100CANON" / "MVI_0006.MOV").write_bytes(b"m" * 100)
    fd = os.open(card, os.O_RDONLY | os.O_DIRECTORY)
    try:
        volume_id = a_volume(db, profile=(profile.profile_id, profile.revision_id))
        Scanner(db).scan(ctx, fd, volume_id, profile)
        probe = StubProbe(
            ProbeResult("video", 69.937, "ok", container_wall="2026-08-26T12:35:08.000000Z")
        )
        importer = Importer(
            db, ArtifactPublisher(db, data_root, probe), data_root,
            default_timezone="Asia/Tokyo",
        )
        importer.run(ctx, fd, volume_id, profile)
    finally:
        os.close(fd)

    row = db.execute(
        "SELECT * FROM media_file WHERE rel_path LIKE '%MVI_0006.MOV'"
    ).fetchone()
    assert row["captured_at_source"] == "container"
    assert row["captured_at"].startswith("2026-08-26T12:35:08")
```

**この 1 本は Task 11 で `canon-eos` の連鎖に `container` が入るまで赤のまま。**
Task 6 の時点では `@pytest.mark.xfail(reason="canon-eos の連鎖は Task 11 で入る")` を
付けて置き、**Task 11 で外す**。外し忘れないよう、Task 11 の Step 4 に入れてある。

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_publisher.py app/tests/test_importer.py -k "probe_runs or container" -v
```

- [ ] **Step 3: 最小の実装**

`publisher.py` の `ArtifactRequest`:

```python
    # **ステージ済みのファイルと、その probe の結果から決める遅延解決。**
    # 呼び出し側は publish の前に staging を持っていないので、ファイルを見て
    # 決める種類の値はここでしか解決できない（§9.3 手順 5）。
    resolve_captured: Callable[[Path, ProbeResult], CapturedAt] | None = None
```

`_read_metadata` を差し替える。

```python
            def _read_metadata() -> tuple[CapturedAt, Any]:
                # **probe が先。** 器が申告した撮影時刻は probe の結果にしか無い。
                probe = self._probe.describe(staging_abs, request.extension)
                captured = request.captured
                if captured is None:
                    # 例外を投げない契約（adapters/exif.py が握る）。投げると、
                    # 検証まで済んだファイルがここで落ちて staging に残る。
                    captured = request.resolve_captured(staging_abs, probe)
                return captured, probe
```

`metadata` に足す。

```python
                "container_wall": probe.container_wall,
```

INSERT の値の列を `metadata["container_wall"]` に直す（Task 5 で `.get` にしてある）。

`importer.py` の `_captured_for` を捨て、`_publish_one` を直す。

```python
    def _resolver(
        self, row: sqlite3.Row, profile: ProfileRef
    ) -> Callable[[Path, ProbeResult], CapturedAt]:
        """ステージ済みのファイルと probe から撮影日時を決める読み方を返す.

        **画像以外では EXIF を読まない。** `exifread` は認識できない入力に対して
        例外ではなく警告を出すので、Canon の MOV のように `exif` を宣言した
        プロファイルを通る動画で呼ぶと、1 本ごとに警告が並ぶ。振り分けは
        `MediaProbe` と同じ拡張子の規則で行う（判定が 2 箇所に散らない）。
        """
        extension = PurePosixPath(row["rel_path"]).suffix.lstrip(".").upper()
        reads_exif = "exif" in profile.definition.timestamp.source and (
            extension in PHOTO_EXTENSIONS
        )

        def resolve(staging_abs: Path, probe: ProbeResult) -> CapturedAt:
            return resolve_captured_at(
                profile.definition,
                row["rel_path"],
                row["mtime_ns"],
                self._default_timezone,
                exif_wall=read_datetime_original(staging_abs) if reads_exif else None,
                container_wall=probe.container_wall,
            )

        return resolve
```

`_publish_one` の `ArtifactRequest` を `captured=None, resolve_captured=self._resolver(row, profile)` にする。

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest app/tests/test_publisher.py app/tests/test_importer.py app/tests/test_merger.py -v
```

`merger.py` は `captured` を即値で渡すので**変えない**。落ちたら、`ArtifactRequest` の
`__post_init__`（どちらか一方）を壊していないか見る。

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| probe と captured の順序を元に戻す | `test_the_probe_runs_before_the_captured_time_is_resolved` |
| `container_wall=probe.container_wall` を `None` にする | `test_a_video_resolves_its_time_from_the_staged_file` |
| `metadata["container_wall"]` を `None` にする | `test_the_container_time_is_stored_verbatim` |
| `reads_exif` の拡張子の条件を外す | 既存の「動画で exifread の警告が出ない」テスト（無ければ 1 本足す） |

- [ ] **Step 6: 受け入れとコミット**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/adapters/publisher.py app/src/mediaferry/jobs/importer.py \
        app/tests/test_publisher.py app/tests/test_importer.py
git commit -m "$(cat <<'EOF'
feat(publish): 器の時刻を使えるように、probe を撮影日時の解決より先に走らせる

器が申告した撮影時刻は probe の結果にしか無い。captured を先に決める順序だと
値が間に合わず、container を宣言したプロファイルでも黙って mtime へ落ちる。

probe は公開時に必ず走るので、取り込み側の「EXIF のときだけ遅延解決、それ以外は
即値」という二股が要らなくなった。常に遅延解決へ一本化する。結合の出力は
即値のままで、「captured と resolve_captured はどちらか一方」の不変条件も変えない。
EOF
)"
```

---

## Task 7: 再計算が `container_wall` を DB から読む

**Files:**
- Modify: `app/src/mediaferry/jobs/recompute.py`
- Test: `app/tests/test_recompute.py`

**Interfaces:**
- Consumes: `media_file.container_wall`（Task 5）
- Produces: `POST /profiles/{slug}/recompute` が `container` を出所として選び直せる

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_recompute_reads_the_container_time_from_the_database(db, data_root):
    """**再 probe しない.** 取り込み時に生の文字列を持っているので読み直せる.

    16 GiB の動画を再計算のたびに ffprobe へ掛けると、リースの心拍では足りない
    時間がかかる。
    """
    profile = a_user_profile(
        db, "canon-eos", "canon-container",
        source=["container", "mtime"], container_semantics="wall_clock",
    )
    media_id = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/canon-container/DCIM/100CANON/MVI_0006.MOV",
        captured_at="2026-08-26T12:36:18+00:00",
        captured_at_source="mtime",
        container_wall="2026-08-26T12:35:08.000000Z",
        mtime_ns=ns("2026-08-26T12:36:18"),
    )

    recompute(db, data_root, profile)

    at, source = captured(db, media_id)
    assert source == "container"
    # **録画の終了ではなく開始。** mtime は 12:36:18、器は 12:35:08。
    assert at.startswith("2026-08-26T12:35:08")


def test_recompute_of_a_row_without_a_container_time_falls_through(db, data_root):
    """器の時刻を持たない行は mtime へ落ちる（値を捏造しない）."""
    profile = a_user_profile(
        db, "canon-eos", "canon-container-2", source=["container", "mtime"],
    )
    media_id = a_media_file(
        db,
        (profile.profile_id, profile.revision_id),
        rel_path="library/canon-container-2/DCIM/100CANON/MVI_0009.MOV",
        captured_at="2026-08-26T12:36:18+00:00",
        captured_at_source="mtime",
        mtime_ns=ns("2026-08-26T12:36:18"),
    )

    recompute(db, data_root, profile)

    _, source = captured(db, media_id)
    assert source == "mtime"
```

`a_user_profile` / `recompute` / `captured` / `ns` は `test_recompute.py` の既存のヘルパ
（`recompute` は `Recomputer(db, data_root, TOKYO).run(a_context(db), profile)` を包んだもの、
`captured` は `media_file` の `captured_at` と `captured_at_source` を返す）。
`a_user_profile` は `timestamp` の欄を `**over` で差し替える形なので、`source` と
`container_semantics` をそのまま渡せる。

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_recompute.py -k container -v
```

- [ ] **Step 3: 最小の実装**

`recompute.py` の `_recomputed_original` の `resolve_captured_at` 呼び出しに足す。

```python
        return resolve_captured_at(
            profile.definition,
            source_rel,
            row["mtime_ns"],
            self._default_timezone,
            exif_wall=self._exif_wall(ctx, row, source_rel, profile),
            # **再 probe しない。** 取り込みで生の文字列を持っている。
            container_wall=row["container_wall"],
        )
```

行を読む SELECT に `container_wall` を足す（**`grep -n "SELECT" app/src/mediaferry/jobs/recompute.py`
で対象を全部見る**）。`_exif_wall` の中の「写真のときだけ読む」判定を、Task 6 と同じく
`"exif" in profile.definition.timestamp.source` に直す。

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest app/tests/test_recompute.py -v
```

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `container_wall=row["container_wall"]` を `None` にする | `test_recompute_reads_the_container_time_from_the_database` |
| SELECT から `container_wall` を落とす | 同上（`KeyError` ではなくテストの表明で落ちること） |

- [ ] **Step 6: 受け入れとコミット**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/jobs/recompute.py app/tests/test_recompute.py
git commit -m "$(cat <<'EOF'
feat(recompute): 器の時刻を DB から読み、再 probe せずに計算し直す

container_semantics を後から変えたときに、16 GiB の動画を 1 本ずつ ffprobe へ
掛け直すのは高い。取り込みで生の文字列を保存してあるので読み直せる。

値を持たない行は次の出所へ落とす。捏造しない。
EOF
)"
```

---

## Task 8: 結合の検出が `container` の分解能を知る

**Files:**
- Modify: `app/src/mediaferry/core/merge/grouping.py`
- Test: `app/tests/test_merge_grouping.py`

**Interfaces:**
- Consumes: `captured_at_source == "container"`
- Produces: `_RESOLUTION_SECONDS["container"] == 1.0`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_a_container_sourced_seam_tolerates_one_second_of_rounding():
    """**`creation_time` は秒までしか持たない.**

    丸めで生じる符号のぶれを「重なり」と読むと、同じ録画の継ぎ目が割れる
    （DJI の 5 パートで踏んだのと同じ形）。
    """
    parts = [
        _part("MVI_0007.MOV", start="2026-08-26T12:37:13", duration=428.428,
              size_bytes=4_260_142_424, source="container"),
        _part("MVI_0008.MOV", start="2026-08-26T12:44:22", duration=23.023,
              size_bytes=218_782_864, source="container"),
    ]
    rule = _rule(tolerance_seconds=5, min_part_size_gib=3)
    groups = detect_groups(parts, rule)
    assert len(groups) == 1
    assert [p.rel_path for p in groups[0].members] == ["MVI_0007.MOV", "MVI_0008.MOV"]
    assert groups[0].gaps == pytest.approx([0.572], abs=0.001)


def test_a_separate_recording_is_not_joined():
    """55 秒空いた別録画は同じ組にしない."""
    parts = [
        _part("MVI_0006.MOV", start="2026-08-26T12:35:08", duration=69.937,
              size_bytes=618_422_312, source="container"),
        _part("MVI_0007.MOV", start="2026-08-26T12:37:13", duration=428.428,
              size_bytes=4_260_142_424, source="container"),
    ]
    assert len(detect_groups(parts, _rule(tolerance_seconds=5, min_part_size_gib=3))) == 0


def test_the_canon_split_is_below_four_gibibytes():
    """**実測の分割片は 3.9675 GiB。** 下限 4 では弾かれる."""
    parts = [
        _part("MVI_0007.MOV", start="2026-08-26T12:37:13", duration=428.428,
              size_bytes=4_260_142_424, source="container"),
        _part("MVI_0008.MOV", start="2026-08-26T12:44:22", duration=23.023,
              size_bytes=218_782_864, source="container"),
    ]
    assert detect_groups(parts, _rule(tolerance_seconds=5, min_part_size_gib=4)) == []
    assert len(detect_groups(parts, _rule(tolerance_seconds=5, min_part_size_gib=3))) == 1
```

`_part` と `_rule` は `test_merge_grouping.py` の既存のヘルパを使う（無ければ、そのファイルが
`MergePart` を組み立てている形をそのまま関数に切り出す）。

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_merge_grouping.py -k "container or canon_split or separate_recording" -v
```

期待: `test_a_container_sourced_seam_tolerates_one_second_of_rounding` が FAIL
（`container` の分解能が既定の 0.0 なので、`overlap` が 0 になり判定が変わる）。

- [ ] **Step 3: 最小の実装**

```python
_RESOLUTION_SECONDS = {
    "filename": 1.0,  # プロファイルの format は秒までしか持たない
    "exif": 1.0,  # DateTimeOriginal は秒
    "container": 1.0,  # creation_time は秒
}
```

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest app/tests/test_merge_grouping.py -v
```

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `"container": 1.0` を消す | `test_a_container_sourced_seam_tolerates_one_second_of_rounding`（**丸めで負に振れる筋書きで確かめる**。正の差だけだと素通りするので、`12:44:21` 始まりの片も 1 本用意する） |
| `"container": 1.0` を `2.0` にする | 新しく 1 本足す —— 1.5 秒重なる別録画が割れること |

**素通りしたら、まず「相方がマスクしていないか」を見る。**

- [ ] **Step 6: 受け入れとコミット**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/core/merge/grouping.py app/tests/test_merge_grouping.py
git commit -m "$(cat <<'EOF'
feat(merge): 器の時刻の分解能を、境界の判定に教える

creation_time は秒までしか持たない。分解能を 0 のままにすると、丸めで負に
振れた差を「重なり」と読み、同じ録画の継ぎ目が割れる。DJI の 5 パートが
+0.963 / +0.091 / -0.909 / +0.877 で 2 つに割れたのと同じ形。

実測（Canon の 4GB 分割）は継ぎ目が +0.572 秒、別録画との間が +55.063 秒で、
tolerance_seconds: 5 のまま正しく分かれる。
EOF
)"
```

---

## Task 9: TS 経路が運べないものを、走らせる前に見分ける

**Files:**
- Modify: `app/src/mediaferry/core/merge/streams.py`
- Modify: `app/src/mediaferry/adapters/ffmpeg.py`
- Test: `app/tests/test_merge_streams.py`, `app/tests/test_adapter_ffmpeg.py`

**Interfaces:**
- Consumes: `KeepStreams`
- Produces: `ts_route_blockers(streams: Sequence[dict[str, Any]], keep: KeepStreams)
  -> tuple[dict[str, Any], ...]` —— `stream_summary` の形で返す

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_merge_streams.py`:

```python
def test_pcm_audio_blocks_the_ts_route():
    """**mpegts は PCM を private data として詰め、警告だけ出して成功する.**

    実測: 読み直すと bin_data の data ストリームになり、音声が消える。
    ffmpeg が失敗しない以上、こちらで運べないと判断するしかない。
    """
    streams = [
        {"index": 0, "codec_type": "video", "codec_name": "h264"},
        {"index": 1, "codec_type": "audio", "codec_name": "pcm_s16le"},
    ]
    blockers = ts_route_blockers(streams, KeepStreams("primary", "all", False, False))
    assert [b["codec_name"] for b in blockers] == ["pcm_s16le"]


def test_aac_audio_does_not_block_the_ts_route():
    streams = [
        {"index": 0, "codec_type": "video", "codec_name": "h264"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac"},
    ]
    assert ts_route_blockers(streams, KeepStreams("primary", "all", False, False)) == ()


def test_a_dropped_pcm_stream_does_not_block():
    """**捨てるものは邪魔しない.** keep が落とすストリームは判定に入れない."""
    streams = [
        {"index": 0, "codec_type": "video", "codec_name": "h264"},
        {"index": 1, "codec_type": "audio", "codec_name": "pcm_s16le"},
    ]
    assert ts_route_blockers(streams, KeepStreams("primary", "none", False, False)) == ()
```

`app/tests/test_adapter_ffmpeg.py`:

```python
def make_pcm_clip(path, seconds=2, *, audio_first=False):
    """音声が PCM のクリップ. Canon の MOV と同じ形（器も QuickTime）."""
    command = [
        "ffmpeg", "-nostdin", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=64x64:rate=10",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
    ]
    command += ["-map", "1:a", "-map", "0:v"] if audio_first else ["-map", "0:v", "-map", "1:a"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le"]
    command += ["-y", str(path)]
    subprocess.run(command, check=True, capture_output=True)  # noqa: S603
    return path


def test_the_merge_refuses_the_ts_route_when_it_would_lose_audio(tmp_path):
    """**4 GB を再 mux してから駄目だと分かる経路を残さない.**

    concat が失敗しても、運べないと分かっているなら TS を試さない。
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    probe = MediaProbe()
    parts = [make_pcm_clip(tmp_path / f"{i}.mov") for i in range(2)]
    streams = [probe.describe(path, "MOV").streams for path in parts]
    # concat を必ず失敗させる（存在しないパートを混ぜる）。
    runner = MergeRunner()
    with pytest.raises(MergeFailed, match="pcm_s16le"):
        runner.merge(
            [*parts, tmp_path / "missing.mov"],
            [*streams, streams[0]],
            KEEP,
            tmp_path,
            "out.mov",
            lambda: None,
            lambda: False,
        )


def test_a_topology_mismatch_with_pcm_also_refuses(tmp_path):
    """並びが違うときも同じ. **TS へ落ちる道は 2 つある.**"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    probe = MediaProbe()
    first = make_pcm_clip(tmp_path / "a.mov")
    second = make_pcm_clip(tmp_path / "b.mov", audio_first=True)
    streams = [probe.describe(path, "MOV").streams for path in (first, second)]
    with pytest.raises(MergeFailed, match="pcm_s16le"):
        MergeRunner().merge(
            [first, second], streams, KEEP, tmp_path, "out.mov",
            lambda: None, lambda: False,
        )


def test_aac_still_falls_back_to_the_ts_route(tmp_path):
    """**塞ぐのは PCM だけ.** 運べるものまで諦めない."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    probe = MediaProbe()
    first = make_clip(tmp_path / "a.mp4")
    second = make_clip(tmp_path / "b.mp4", audio_first=True)
    streams = [probe.describe(path, "MP4").streams for path in (first, second)]
    outcome = MergeRunner().merge(
        [first, second], streams, KEEP, tmp_path, "out.mp4", lambda: None, lambda: False
    )
    assert outcome.route == "ts"
```

`MergeRunner` / `make_clip` / `KEEP` は `test_adapter_ffmpeg.py` の既存のもの。
**クラス名は `MergeRunner`**（`FfmpegMerger` ではない）。

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_merge_streams.py app/tests/test_adapter_ffmpeg.py -k "pcm or ts_route" -v
```

- [ ] **Step 3: 最小の実装**

`core/merge/streams.py` に足す。

```python
# mpegts が無損失で運べない codec の接頭辞。**種別ではなく codec の軸**なので、
# `UNSUPPORTED_BY_TS`（data を落とす）とは混ぜない。
_TS_LOSSY_CODEC_PREFIXES = ("pcm_",)


def ts_route_blockers(
    streams: Sequence[dict[str, Any]], keep: KeepStreams
) -> tuple[dict[str, Any], ...]:
    """TS 経路が無損失で運べない、保持対象のストリームを返す.

    mpegts は PCM を private data として詰め、**警告だけ出して終了コード 0 で
    成功する**。読み直すと `bin_data` の data ストリームになり、音声が消える。
    ffmpeg が失敗しない以上、こちらで運べないと判断するしかない。

    **捨てるストリームは数えない。** `keep` が落とすものは出力に影響しない。
    """
    return tuple(
        stream_summary(stream)
        for stream in selected_streams(streams, keep)
        if str(stream.get("codec_name", "")).startswith(_TS_LOSSY_CODEC_PREFIXES)
    )
```

`adapters/ffmpeg.py` の `MergeRunner.merge` を直す。

```python
        selections = [selected_streams(streams, keep) for streams in part_streams]
        output = work_dir / output_name
        if _topology_matches(part_streams, selections):
            carried, dropped = _split_unsupported(selections[0], UNSUPPORTED_BY_CONCAT)
            try:
                self._run(
                    self._concat_command(parts, map_arguments(carried), work_dir, output),
                    work_dir / "concat.log",
                    on_progress,
                    cancelled,
                )
                return MergeOutcome("concat", output, self.tool_version(), dropped)
            except MergeFailed as exc:
                _refuse_ts_if_lossy(part_streams[0], keep)
                note(f"concat demuxer に失敗した。TS 経由へ落とす: {exc}")
        else:
            _refuse_ts_if_lossy(part_streams[0], keep)
            note("パート間でストリームの並びが違うので concat demuxer を使わない")
```

同ファイルの末尾に足す。

```python
def _refuse_ts_if_lossy(streams: Sequence[dict[str, Any]], keep: KeepStreams) -> None:
    """TS 経路が音を捨てるなら、走らせる前に諦める.

    **運べないと分かっているものを、運べるか試してから諦める理由が無い。**
    4 GB のパートを mpegts へ書き直すだけで数分かかる。
    """
    blockers = ts_route_blockers(streams, keep)
    if blockers:
        names = "・".join(str(b["codec_name"]) for b in blockers)
        raise MergeFailed(f"TS 経路は {names} を運べないので結合できない")
```

`ts_route_blockers` を import に足す。

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest app/tests/test_merge_streams.py app/tests/test_adapter_ffmpeg.py \
  app/tests/test_merger.py app/tests/test_merge_e2e.py -v
```

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `startswith(_TS_LOSSY_CODEC_PREFIXES)` を `== "pcm_s16le"` にする | 新しく 1 本足す —— `pcm_s24le` も塞がること |
| `selected_streams(streams, keep)` を `streams` にする | `test_a_dropped_pcm_stream_does_not_block` |
| 並びが違う枝の `_refuse_ts_if_lossy` を消す | `test_a_topology_mismatch_with_pcm_also_refuses` |
| concat 失敗の枝の `_refuse_ts_if_lossy` を消す | `test_the_merge_refuses_the_ts_route_when_it_would_lose_audio` |

- [ ] **Step 6: 受け入れとコミット**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/core/merge/streams.py app/src/mediaferry/adapters/ffmpeg.py \
        app/tests/test_merge_streams.py app/tests/test_adapter_ffmpeg.py
git commit -m "$(cat <<'EOF'
feat(merge): TS 経路が音を捨てるときは、走らせる前に諦める

Canon の MOV は音声が pcm_s16le。mpegts は PCM を private data として詰め、
警告だけ出して終了コード 0 で成功する（実測: 読み直すと bin_data の data
ストリームになり、音声が消える）。ffmpeg が失敗しない以上、こちらで運べないと
判断するしかない。

4 GB のパートを mpegts へ書き直すだけで数分かかる。運べないと分かっているものを
運べるか試してから諦める理由が無いので、経路を選ぶ前に見分ける。判定は codec の
軸なので、種別で data を落とす UNSUPPORTED_BY_TS とは混ぜない。
EOF
)"
```

---

## Task 10: 結合の進捗が `.MOV` の出力を数える

**Files:**
- Modify: `app/src/mediaferry/jobs/merger.py`
- Test: `app/tests/test_merger.py`

**Interfaces:**
- Consumes: なし
- Produces: `work/` を舐める進捗が `.mov` も数える

- [ ] **Step 1: 失敗するテストを書く**

`beat()` は `Merger._merge` の中の閉包なので、**外から呼べる形に切り出してから測る**。
拡張子の集合を定数にするので、そこを直接見るテストで足りる。

```python
def test_the_progress_counts_a_quicktime_output(tmp_path):
    """**出力の器はプロファイルが決める.**

    `.mp4` と `.ts` しか数えないと、Canon の結合中は 4 GB のあいだ
    進捗が 0 のままになる。
    """
    from mediaferry.jobs.merger import MERGE_ARTIFACT_SUFFIXES, merge_bytes_written

    (tmp_path / "MVI_20260826123713_0007-0008_MERGED.MOV").write_bytes(b"x" * 300)
    (tmp_path / "concat.log").write_bytes(b"y" * 999)
    assert ".mov" in MERGE_ARTIFACT_SUFFIXES
    assert merge_bytes_written(tmp_path) == 300


def test_the_progress_ignores_case_in_the_suffix(tmp_path):
    """カメラは大文字の拡張子を書く. `.MOV` も数える."""
    from mediaferry.jobs.merger import merge_bytes_written

    (tmp_path / "A.MOV").write_bytes(b"x" * 10)
    assert merge_bytes_written(tmp_path) == 10


def test_the_progress_counts_the_ts_parts_too(tmp_path):
    """TS 経路は各パートの .ts と出力を両方置く. 分母が倍になる前提を壊さない."""
    from mediaferry.jobs.merger import merge_bytes_written

    (tmp_path / "0.ts").write_bytes(b"x" * 5)
    (tmp_path / "out.mp4").write_bytes(b"y" * 7)
    assert merge_bytes_written(tmp_path) == 12
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_merger.py -k quicktime -v
```

- [ ] **Step 3: 最小の実装**

```python
# 結合の途中に work/ へ置かれる器。出力の拡張子はプロファイルが決めるので、
# ここに無い器を選ぶと進捗が 0 のままになる。
MERGE_ARTIFACT_SUFFIXES = (".mp4", ".mov", ".ts")
```

`beat()` の中身を、モジュールの関数へ切り出す。

```python
def merge_bytes_written(work: Path) -> int:
    """`work/` に書けた量. **ffmpeg は別プロセスなので育ち方でしか測れない.**

    TS 経路は「各パートの `.ts`」と「結合後の出力」を両方置くので、両方数える。
    """
    return sum(
        path.stat().st_size
        for path in work.glob("*")
        if path.suffix.lower() in MERGE_ARTIFACT_SUFFIXES
    )
```

`beat()` はこれを呼ぶだけにする。

```python
        def beat() -> None:
            ctx.heartbeat(
                {
                    "phase": "merge",
                    "rel_path": desired,
                    "route": "ts" if fell_back else "concat",
                    "parts": len(parts),
                    "bytes_done": merge_bytes_written(work),
                    ...
                }
            )
```

**`...` の欄は既存のものをそのまま残す。** ここで変えるのは `bytes_done` の出どころだけ。

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest app/tests/test_merger.py -v
```

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `".mov"` を消す | `test_the_progress_counts_a_quicktime_output` |
| `path.suffix.lower()` を `path.suffix` にする | 新しく 1 本足す —— `.MOV`（大文字）を数えること |

- [ ] **Step 6: 受け入れとコミット**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/jobs/merger.py app/tests/test_merger.py
git commit -m "$(cat <<'EOF'
fix(merge): QuickTime の出力でも結合の進捗が出るようにする

進捗は work/ の育ち方でしか測れない（ffmpeg は別プロセス）。数える拡張子が
.mp4 と .ts だけだったので、出力を .MOV にすると 4 GB の結合中ずっと 0 の
ままになる。Phase 8 で「送信の進捗が出ない」を直したのと同じ穴を、こちらから
作り込むところだった。
EOF
)"
```

---

## Task 11: `canon-eos` を仕上げる

**Files:**
- Modify: `app/src/mediaferry/core/profiles/builtin/canon-eos.yaml`
- Test: `app/tests/test_profile_matching.py`, `app/tests/test_merge_output.py`

**Interfaces:**
- Consumes: Task 3〜10 の全部
- Produces: Canon の動画が `container` の時刻を持ち、4GB 分割が組まれ、Immich へ日時が
  書き戻る

**これが「点火」のタスク。** ここまでは挙動を変えていない。

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_profile_matching.py`:

```python
def test_canon_reads_the_time_from_exif_then_the_container():
    """写真は EXIF、動画は器、どちらも無ければ mtime."""
    defn = ProfileRegistry(db).current("canon-eos").definition
    assert defn.timestamp.source == ("exif", "container", "mtime")
    assert defn.timestamp.container_semantics == "wall_clock"


def test_canon_writes_the_datetime_back_to_immich():
    """**Immich は creation_time の Z を素直に UTC と読む.**

    書き戻さないと動画だけが 9 時間ずれる（実機で確認）。
    `fix_datetime_after_upload` は `timezone_policy: force_offset` と
    セットでないと効かない（`datetime_plan` が policy == "none" で降りる）。
    """
    defn = ProfileRegistry(db).current("canon-eos").definition
    assert defn.timestamp.timezone_policy == "force_offset"
    assert defn.immich.fix_datetime_after_upload is True


def test_canon_merges_four_gibibyte_splits():
    defn = ProfileRegistry(db).current("canon-eos").definition
    assert defn.merge.enabled is True
    assert defn.merge.min_part_size_gib == 3
```

`app/tests/test_merge_output.py`:

```python
def test_the_canon_output_name_carries_the_sequence_range():
    """`MVI_0007` と `MVI_0008` から `0007-0008` を組む."""
    rule = ProfileRegistry(db).current("canon-eos").definition.merge
    name = merged_rel_path("canon-eos", rule, [
        _part("DCIM/100CANON/MVI_0007.MOV", start="2026-08-26T12:37:13"),
        _part("DCIM/100CANON/MVI_0008.MOV", start="2026-08-26T12:44:22"),
    ])
    assert name.endswith("MVI_20260826123713_0007-0008_MERGED.MOV")
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_profile_matching.py app/tests/test_merge_output.py -k canon -v
```

- [ ] **Step 3: `canon-eos.yaml` を仕上げる**

```yaml
timestamp:
  # 写真は EXIF、動画は器（QuickTime の creation_time）、どちらも無ければ mtime。
  source: [exif, container, mtime]
  pattern: null
  format: null
  # **Canon は creation_time に現地の壁時計を書きながら Z を付ける。**
  # 真に受けると 9 時間ずれる（実測）。
  container_semantics: wall_clock
  # Immich は creation_time の Z を素直に UTC と読むので、撮影地の TZ を
  # 判定できない。壁時計にオフセットを付けて書き戻す。
  timezone_policy: force_offset
  # 地域固定の値は持たない。MEDIAFERRY_DEFAULT_TIMEZONE か画面で与える。
  timezone: null
merge:
  enabled: true
  tolerance_seconds: 5
  # **4GB 分割の実測は 3.9675 GiB。** 4 では弾かれる。別録画は 3 GiB にも
  # 届かないので、区別は保たれる。
  min_part_size_gib: 3
  sequence_pattern: '^MVI_(?P<seq>\d{4})$'
  # **器は QuickTime のまま。** 音声が PCM なので、MP4 は ffmpeg の版によって
  # 弾かれる（ipcm を持つのは新しい版だけ）。
  output_name: "MVI_{ts}_{first_seq}-{last_seq}_MERGED.MOV"
  keep_streams:
    video: primary
    audio: all
    timecode: false
    data: false
immich:
  tags: ["Canon EOS"]
  tag_pre_existing: true
  fix_datetime_after_upload: true
```

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest -v
```

**Task 6 で付けた `xfail` を外す。** `test_importer.py::test_a_video_takes_its_time_from_the_container`
の `@pytest.mark.xfail` を消し、緑になることを確かめる。**残すと、壊れても気づけない。**

```bash
uv run pytest app/tests/test_importer.py::test_a_video_takes_its_time_from_the_container -v
```

**既存のテストが落ちたら、まず「挙動が正しく変わったのか」を見る。** `canon-eos` を
`timezone_policy: none` の前提で書いたテストは、**新しい挙動をより直接的に書く形へ
変える**（テストを通すために直さない）。

- [ ] **Step 5: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `min_part_size_gib` を 4 に戻す | `test_the_canon_split_is_below_four_gibibytes`（Task 8） |
| `timezone_policy` を `none` に戻す | `test_canon_writes_the_datetime_back_to_immich` |
| `source` から `container` を抜く | `test_canon_reads_the_time_from_exif_then_the_container` |
| `output_name` の器を `.MP4` にする | `test_the_canon_output_name_carries_the_sequence_range` |
| `sequence_pattern` の錨（`^`）を外す | 新しく 1 本足す —— `IMG_0007.JPG` のような別の接頭辞に当たらないこと |

- [ ] **Step 6: 受け入れとコミット**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/core/profiles/builtin/canon-eos.yaml \
        app/tests/test_profile_matching.py app/tests/test_merge_output.py
git commit -m "$(cat <<'EOF'
feat(canon): 器の時刻を使い、4GB 分割を組み、Immich へ日時を書き戻す

実カードで測った 3 つを一度に入れる。

creation_time を出所に足すと、動画の撮影日時が録画の終了ではなく開始になり
（70 秒差）、4GB 分割の継ぎ目が +0.572 秒・別録画との間が +55.063 秒で
tolerance_seconds: 5 のまま正しく分かれる。min_part_size_gib は実測の
3.9675 GiB を通すため 3 へ下げた。

timezone_policy を force_offset にしたのは、fix_datetime_after_upload が
単独では効かないため（datetime_plan が policy == "none" を先に見て降りる）。
Immich は creation_time の Z を素直に UTC と読むので、書き戻さないと動画だけが
9 時間ずれる。写真は EXIF にオフセットが無く現地時刻として扱われるので元から
正しい。

出力を .MOV にしたのは音声が PCM だから。MP4 は ffmpeg の版によって弾かれる。
EOF
)"
```

---

## Task 12: 一覧の並びを名前で決め、索引の効きを測る

**Files:**
- Modify: `app/src/mediaferry/api/routes_media.py:202`
- Modify: `app/src/mediaferry/db/selection.py`（`_ORIGINALS` / `_DERIVED` / `_MEMBERS_OF_UNMERGED`）
- Modify: `app/src/mediaferry/core/listing.py`（契約のコメント）
- Test: `app/tests/test_api_listing.py`, `app/tests/test_api_media.py`, `app/tests/test_selection.py`

**Interfaces:**
- Consumes: Task 5 の索引
- Produces: `ORDER BY m.captured_at DESC, m.rel_path DESC`

- [ ] **Step 1: 失敗するテストを書く**

```python
SAME = "2026-08-26T12:44:45+00:00"


def _rows(db, names, captured_at=SAME):
    """同じ撮影日時の行を、名前だけ変えて入れる."""
    profile = a_profile(db)
    for name in names:
        a_media_file(db, profile, rel_path=f"library/canon-eos/{name}", captured_at=captured_at)


def test_rows_with_the_same_time_are_ordered_by_name(client, db):
    """**同じ撮影日時の並びは、乱数ではなく名前で決まる.**

    実機で `MVI_0007` が `MVI_0008` より左上に来た。tie-break が 32 桁の乱数 hex
    だったので、id の出方が逆なら順序も逆になっていた。
    """
    _rows(db, ["MVI_0007.MOV", "MVI_0008.MOV"])
    got = client.get("/api/media?page_size=10").json()["media"]
    assert [m["rel_path"].rsplit("/", 1)[-1] for m in got] == ["MVI_0008.MOV", "MVI_0007.MOV"]


def test_the_primary_of_a_stack_comes_before_its_secondary(client, db):
    """副産物: RAW+JPEG の並びも決定的になる（`"JPG" > "CR2"`）."""
    _rows(db, ["IMG_0001.CR2", "IMG_0001.JPG"], captured_at="2026-08-26T12:33:05+00:00")
    got = client.get("/api/media?page_size=10").json()["media"]
    assert [m["rel_path"][-3:] for m in got] == ["JPG", "CR2"]


def test_the_page_boundary_does_not_drop_or_repeat_a_row(client, db):
    """**切るクエリは順序が決まらないと境界が揺れる.**

    30 行すべてを同じ撮影日時にして、3 ページで舐める。tie-break が無いと、
    ページごとに並びが変わって重複と欠落が出る。
    """
    names = [f"IMG_{index:04d}.JPG" for index in range(30)]
    _rows(db, names)
    seen = []
    for page in (1, 2, 3):
        got = client.get(f"/api/media?page={page}&page_size=10").json()["media"]
        seen.extend(m["rel_path"].rsplit("/", 1)[-1] for m in got)
    assert seen == sorted(names, reverse=True)


def test_selectable_orders_by_name_within_the_same_time(client, db):
    """`GET /uploads/selectable` は `limit` で切るので、境界が揺れると候補が
    出たり出なかったりする."""
    _rows(db, ["MVI_0007.MOV", "MVI_0008.MOV"])
    got = client.get("/api/uploads/selectable?limit=1").json()["selectable"]
    assert [item["rel_path"].rsplit("/", 1)[-1] for item in got] == ["MVI_0008.MOV"]
```

`a_media_file` と `a_profile` は `test_schema_artifacts.py` / `test_schema_sources.py` の
既存のヘルパ。**`client` fixture の DB は `db` fixture と同じ**（`conftest.py` の
`data_root` を共有する）ので、直接入れた行が API から見える。

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_api_listing.py app/tests/test_selection.py -k "same_time or primary_of_a_stack or boundary" -v
```

- [ ] **Step 3: 最小の実装**

`routes_media.py:202`:

```python
        " ORDER BY m.captured_at DESC, m.rel_path DESC LIMIT ? OFFSET ?",
```

`db/selection.py` の 3 つに `, m.rel_path DESC` を足す。

```python
    " ORDER BY m.captured_at DESC, m.rel_path DESC"
```

`core/listing.py` の契約コメントを直す。

```python
ここに閉じる。並びの tie-break は SQL 側（`captured_at DESC, rel_path DESC`）。
```

`routes_media.py` の docstring を直す。

```python
    **並びは `captured_at DESC, rel_path DESC` で固定する。** 同じ撮影日時の行が
    あるので、tie-break を入れないとページの境目で重複・欠落する。**`rel_path` は
    `UNIQUE` なので単独で足りる** —— `id` は乱数なので、同じ撮影日時の並びに
    意味が出ない。
```

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest -v
npm --prefix web run test && npm --prefix web run lint && npm --prefix web run build
npm --prefix web run test:e2e
```

**E2E を必ず流す。** Phase 8 では受け入れに入っていなかったので 8 タスクぶん赤のまま
気づかなかった。

- [ ] **Step 5: 索引の効きを測る**

**張り替えの前後で同じ計測を取る。** Phase 9 は「測る対象が足りていなかった」ために
`0022` の退行を見落とした。対象は 3 経路。

```bash
# 60,000 行の original と 200 行の derived を入れた DB を作り、
# 各経路を 20 回ずつ叩いて中央値を出す。手順は phase9-record.md の索引の節に倣う。
```

| 経路 | 測るもの |
| --- | --- |
| `GET /media?page_size=50` | 既定の一覧（`media_file_listing`） |
| `GET /media?collapse=stack` と `?stack=members` | 組の 2 経路 |
| `GET /media?role=derived` | 部分索引（`media_file_derived_listing`） |
| `GET /uploads/selectable` | `limit` で切る経路（**専用の索引は無い**） |

**`EXPLAIN QUERY PLAN` を必ず添える。** `USE TEMP B-TREE FOR ORDER BY` が出ていないことを
確かめる。出ていたら索引が効いていない。

測った値を `docs/history/phase13-record.md` に書く（**記録はここにしか残らない**）。

- [ ] **Step 6: 変異試験**

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `rel_path DESC` を `rel_path ASC` にする | `test_rows_with_the_same_time_are_ordered_by_name` |
| `rel_path DESC` を消して `id DESC` に戻す | 同上（**id の出方に依存しない筋書き**にする。落ちない場合は、rel_path と id の大小が逆になる行を用意する） |
| `selection.py` の 1 つだけ元に戻す | `test_selectable_orders_by_name_within_the_same_time` |

- [ ] **Step 7: 受け入れとコミット**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
npm --prefix web run test && npm --prefix web run lint && npm --prefix web run build
npm --prefix web run test:e2e
git add app/src/mediaferry/api/routes_media.py app/src/mediaferry/db/selection.py \
        app/src/mediaferry/core/listing.py app/tests/
git commit -m "$(cat <<'EOF'
fix(api): 同じ撮影日時の並びを、乱数ではなく名前で決める

tie-break が id DESC で、id は 32 桁の乱数 hex だった。実機で MVI_0007 が
MVI_0008 より左上に来たのは id の出方がそうだっただけで、逆なら順序も逆に
なっていた。カメラの時計が止まっていた 68 件のように、同じ撮影日時の行は
現実に発生する。

rel_path は UNIQUE なので単独で tie-break になる。副産物として RAW+JPEG の
並びも決定的になった（"JPG" > "CR2" なので主が先。いままでは id の偶然）。

db/selection.py の 3 つも直した。limit で切るクエリなので、順序が決まらないと
どれが候補に入るかが実行ごとに変わる。
EOF
)"
```

---

## 実装が終わったら

1. **実機で確かめる**（設計 §6 の 5 つ）。イメージは `:sha-xxxxxxx` で固定して
   入れ替えてもらう。**画面の機能は画面から踏んでもらう** —— API から叩くと題材が消える
2. **`docs/design.md` を現在形へ**（`timestamp.source` の連鎖、`container`、
   `canon-eos` の `merge`、一覧の並びの契約）
3. **`docs/decisions.md` に足す** —— 「器が申告した時刻は解釈せず生で持ち、意味は
   プロファイルが宣言する」「TS 経路は codec の軸で塞ぐ」
4. **`docs/history/phase13-record.md` を書く** —— 変異試験の記録（**検出できないものは
   検出できないことを記録に残す**）、計画の誤り、測った値、レビューで覆ったもの
5. **`docs/development.md` の持ち越しから 4 件を落とす**（Immich の 9 時間ずれ、
   `container` が無い、`min_part_size_gib`、一覧の tie-break）
6. **codex にレビューを頼む**（`docs/development.md` の「codex への経路」）。
   **実装差分を見せる。** レビュアーには**変異を自分で当てさせる** —— 実装者の
   「テストが見たいものは変えていない」を鵜呑みにさせない
