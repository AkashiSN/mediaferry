# Phase 8 の実装計画 —— ホームが「いま何が起きているか」を言えるようにする

> **エージェントで回す人へ:** 必ず `superpowers:subagent-driven-development`
> （推奨）か `superpowers:executing-plans` を使い、タスクごとに実装する。
> 手順はチェックボックス（`- [ ]`）で追える形にしてある。

**目標:** ホームを開いたときに、**いま何が起きていて、次に何をすればよいか**が
分かるようにする（実機で見つかった 3 件＋その土台 1 件）。

**やり方:** 画面は一覧を持たず、`/devices`・`/jobs`・`/dashboard` から **1 つの
純粋関数**で 3 つの並び（いま動いていること／やること／いまの様子）を導く。
サーバ側は、その導出に要る 3 欄を `/devices` に足し、送信のジョブが進捗を書く
ようにし、watcher が `scan` を積むようにする。

**道具:** Python 3.14 ＋ FastAPI ＋ SQLite（`app/`）、React ＋ TypeScript ＋
Vitest ＋ Playwright（`web/`）。

**設計:** [`phase8-design.md`](phase8-design.md) —— **この計画は設計から
論じている。両方を読むこと。**

## 全体に掛かる決まり

`../../CLAUDE.md` と `../development.md` の全部が掛かる。とくに外しやすいもの:

- **実装より先に、失敗するテストを書き、失敗を確認してから最小実装する。**
- **コメントと docstring は日本語。** コード内コメントは**いま書かれているコードを
  現在形で**説明する。**過去の経緯はコメントに書かない**（`docs/` に残す）
- Python は `from __future__ import annotations` で始める。ruff は
  `line-length = 100`、`select = ["E","F","I","UP","B","SIM","ANN","S"]`
- **環境固有の値を書かない**（IP・ホスト名・データセットのパス・鍵・タイムゾーンの実値）
- **DB に絶対パスを保存しない。** 時刻は `mediaferry.clock` 経由の UTC ISO-8601
- **秘密をログ・`job.params_json`・`job_event`・API 応答・例外メッセージに出さない**
- **DB 接続はスコープごとに 1 本。** 別スレッドから DB に触らない
- 画面に**内部の名前をそのまま出さない**（§13 の語彙表: 結合 → つなぐ、
  ジョブ → 進行中の作業／作業の履歴、ボリューム → カード）
- **押せる領域は 44px 以上。** ライトとダークの両方で成立させる
- コミットは Conventional Commits ＋ 日本語の本文。**なぜそうしたかを本文に残す**。
  **セッション URL を本文に入れない**

実行するコマンド:

```bash
uv sync --all-packages          # --all-packages が必須
uv run pytest
uv run ruff check . && uv run ruff format --check .
npm --prefix web run test
npm --prefix web run lint && npm --prefix web run build
```

## 触るファイル

| ファイル | 役割 |
| --- | --- |
| `app/src/mediaferry/jobs/volumes.py` | `VolumeView` に `pending_count` / `scanned_at` / `busy`。取り込み対象の条件を定数に |
| `app/src/mediaferry/jobs/importer.py` | その定数を使う（条件を 2 か所に書かない） |
| `app/src/mediaferry/api/routes_devices.py` | 3 欄を返す |
| `app/src/mediaferry/api/routes_system.py` | `/jobs` に `volume_instance_id` |
| `app/src/mediaferry/core/lease_pulse.py` | 心拍に進捗を載せられるようにする |
| `app/src/mediaferry/adapters/immich.py` | 送るストリームの読み出し量を数える口 |
| `app/src/mediaferry/jobs/uploader.py` | 合計を数え、進捗を組み立てる |
| `app/src/mediaferry/jobs/watcher.py` | `scan` → `import` → `detect_groups` を積む |
| `app/src/mediaferry/db/migrations/0020_auto_scan.sql` | `volume_presence.auto_scan_at` |
| `web/src/hooks/homeSections.ts`（新規） | **導出規則。ホームの芯** |
| `web/src/screens/Home.tsx` | 新しい導出に載せ替える |
| `web/src/screens/work/CardDetail.tsx` | 「取り外す」を消し、抜ける／抜けないを出す |
| `web/src/components/JobProgress.tsx` | `upload` の phase の言葉 |
| `docs/design.md` §13 / `docs/decisions.md` | 仕様の正本と、決めた理由 |

---

### Task 1: `/devices` が「取り込む残り・数えた時刻・掴まれているか」を返す

**ファイル:**
- 変更: `app/src/mediaferry/jobs/volumes.py`（`VolumeView` と `refresh`）
- 変更: `app/src/mediaferry/jobs/importer.py:83-88`
- 変更: `app/src/mediaferry/api/routes_devices.py:20-35`
- テスト: `app/tests/test_volume_service.py`

**受け渡し:**
- 産出: `VolumeView.pending_count: int` / `.scanned_at: str | None` / `.busy: bool`、
  `mediaferry.jobs.volumes.PENDING_CLAUSE: str`
- 産出: `GET /devices` の各要素に `pending_count` / `scanned_at` / `busy`

- [ ] **手順 1: 失敗するテストを書く**

`app/tests/test_volume_service.py` の末尾に足す。

```python
def _seed_entries(db, volume_instance_id: str, states: list[str]) -> None:
    """`scan` が作る行を、状態だけ指定して並べる."""
    for index, state in enumerate(states):
        db.execute(
            "INSERT INTO source_entry (id, volume_instance_id, rel_path, size_bytes,"
            " mtime_ns, quick_fingerprint, fingerprint_version, state, observed_at)"
            " VALUES (?, ?, ?, 1, 1, 'x', 1, ?, '2026-08-24T00:00:00Z')",
            (f"entry-{index}", volume_instance_id, f"DCIM/{index}.MP4", state),
        )


def test_a_card_nobody_counted_yet_is_told_apart_from_an_empty_one(db, broker):
    """挿した直後は「0 件」ではなく「まだ数えていない」."""
    view = service(db, broker).refresh()[0]
    assert view.scanned_at is None
    assert view.pending_count == 0


def test_pending_counts_exactly_what_import_would_carry(db, broker):
    svc = service(db, broker)
    view = svc.refresh()[0]
    _seed_entries(db, view.volume_instance_id, ["seen", "seen", "published", "failed"])
    after = svc.refresh()[0]
    # `Importer.run` が拾うのは seen と failed だけ。
    assert after.pending_count == 3
    assert after.scanned_at == "2026-08-24T00:00:00Z"


def test_a_card_held_by_a_running_job_is_busy(db, broker):
    svc = service(db, broker)
    view = svc.refresh()[0]
    assert view.selection is not None
    svc.open(view.selection)
    try:
        assert svc.refresh()[0].busy is True
    finally:
        svc.release(view.selection)
    assert svc.refresh()[0].busy is False
```

- [ ] **手順 2: 失敗を確かめる**

実行: `uv run pytest app/tests/test_volume_service.py -k "counted or pending or busy" -v`
期待: `AttributeError: 'VolumeView' object has no attribute 'scanned_at'` で FAIL。

- [ ] **手順 3: 取り込み対象の条件を 1 か所に切り出す**

`app/src/mediaferry/jobs/volumes.py` の import の下に置く。

```python
# 取り込みの対象＝まだ運んでいない行。**`Importer.run` と同じ条件をここだけに持つ。**
# 2 か所に書くと、画面の「残り N 件」と実際に運ぶ件数がずれる。
PENDING_CLAUSE = "state IN ('seen', 'failed')"
```

`app/src/mediaferry/jobs/importer.py` の `run` を、その定数を使う形に変える。

```python
from .volumes import PENDING_CLAUSE

        pending = list(
            self._conn.execute(
                "SELECT * FROM source_entry WHERE volume_instance_id = ?"  # noqa: S608
                f" AND {PENDING_CLAUSE} ORDER BY rel_path",
                (volume_instance_id,),
            )
        )
```

**循環 import に注意。** `volumes.py` は `importer.py` を import していないので
一方向で済む。逆向きにしないこと。

- [ ] **手順 4: `VolumeView` に 3 欄を足す**

```python
@dataclass(frozen=True)
class VolumeView:
    ...
    reason: str
    # 取り込む残りの件数。**まだ数えていないカードは `scanned_at` が None**
    # ——「0 件」と区別できないと、挿した直後に「取り込むものはありません」と
    # 断定してしまう。
    pending_count: int
    scanned_at: str | None
    # このカードを掴んでいるジョブがあるか。**「いま抜いていいか」の答え。**
    busy: bool
    selection: VolumeSelection | None
```

`refresh()` が `VolumeView` を組み立てるところで、1 行につき 1 回だけ数える。

```python
    def _counts(self, volume_instance_id: str) -> tuple[int, str | None]:
        """取り込む残りと、最後に数えた時刻."""
        row = self._conn.execute(
            "SELECT sum(" + PENDING_CLAUSE + ") AS pending, max(observed_at) AS scanned_at"  # noqa: S608
            " FROM source_entry WHERE volume_instance_id = ?",
            (volume_instance_id,),
        ).fetchone()
        return (row["pending"] or 0), row["scanned_at"]
```

`busy` は `volume_instance_id in self._open`（`refresh` は既に `self._lock` の
内側にいる）。

- [ ] **手順 5: `/devices` が返す**

`app/src/mediaferry/api/routes_devices.py` の辞書に 3 行足す。

```python
                "reason": view.reason,
                "pending_count": view.pending_count,
                "scanned_at": view.scanned_at,
                "busy": view.busy,
```

- [ ] **手順 6: テストが通ることを確かめる**

実行: `uv run pytest app/tests/test_volume_service.py app/tests/test_importer.py -v`
期待: PASS。

- [ ] **手順 7: 静的検査**

実行: `uv run ruff check . && uv run ruff format --check .`

- [ ] **手順 8: コミット**

```bash
git add app/src/mediaferry/jobs/volumes.py app/src/mediaferry/jobs/importer.py \
        app/src/mediaferry/api/routes_devices.py app/tests/test_volume_service.py
git commit -m "feat(api): カードの残り件数・数えた時刻・掴まれているかを返す"
```

本文に**なぜ**を残す: 画面が「まだ数えていない」と「0 件」を区別できないと、
挿した直後に嘘をつくため。条件を定数にしたのは、残り件数と実際に運ぶ件数を
ずらさないため。

---

### Task 2: `/jobs` が「どのカードの作業か」を言う

**ファイル:**
- 変更: `app/src/mediaferry/api/routes_system.py:400-414`（`_job`）
- テスト: `app/tests/test_api.py`

**受け渡し:**
- 消費: なし
- 産出: `GET /jobs` と `GET /jobs/{id}` の各ジョブに
  `volume_instance_id: str | null`

**ラベルはサーバが作らない。** 画面が `/devices` と突き合わせ、既存の
`volumeLabel()` で名前を決める（ラベルの無いカードの連番まで含めて、名前付けの
実装を 2 つにしないため）。

- [ ] **手順 1: 失敗するテストを書く**

`app/tests/test_api.py` に足す。

```python
def test_a_job_says_which_card_it_belongs_to(client, db):
    """「いま動いていること」がどのカードの作業かを言えるようにする."""
    db.execute(
        "INSERT INTO job (id, type, status, params_json, created_at)"
        " VALUES ('j1', 'import', 'running', ?, '2026-08-24T00:00:00Z')",
        ('{"volume_instance_id": "vol-1"}',),
    )
    jobs = client.get("/api/jobs").json()["jobs"]
    assert jobs[0]["volume_instance_id"] == "vol-1"


def test_a_job_with_no_card_says_so(client, db):
    db.execute(
        "INSERT INTO job (id, type, status, params_json, created_at)"
        " VALUES ('j2', 'upload', 'running', '{}', '2026-08-24T00:00:00Z')",
    )
    jobs = client.get("/api/jobs").json()["jobs"]
    assert jobs[0]["volume_instance_id"] is None
```

- [ ] **手順 2: 失敗を確かめる**

実行: `uv run pytest app/tests/test_api.py -k which_card -v`
期待: `KeyError: 'volume_instance_id'` で FAIL。

- [ ] **手順 3: 最小の実装**

`routes_system.py` の `_job` に 1 行足す。

```python
def _job(row) -> dict[str, Any]:  # noqa: ANN001
    return {
        "id": row["id"],
        ...
        # どのカードの作業か。**`params_json` から取り出すのはこの 1 欄だけ**
        # —— params には秘密を入れない約束だが、丸ごと返す口は作らない。
        "volume_instance_id": json.loads(row["params_json"]).get("volume_instance_id"),
        "progress": _progress(row),
    }
```

`import json` が無ければ足す。

- [ ] **手順 4: テストが通ることを確かめる**

実行: `uv run pytest app/tests/test_api.py -v`
期待: PASS。

- [ ] **手順 5: コミット**

```bash
git add app/src/mediaferry/api/routes_system.py app/tests/test_api.py
git commit -m "feat(api): ジョブがどのカードの作業かを返す"
```

---

### Task 3: ホームの導出規則（純粋関数）

**ファイル:**
- 作成: `web/src/hooks/homeSections.ts`
- 作成: `web/src/hooks/homeSections.test.ts`
- 削除: `web/src/hooks/useTasks.ts` と `web/src/hooks/useTasks.test.ts`
  （`tasksFrom` はこの関数に吸収される）

**受け渡し:**
- 消費: Task 1 の `/devices` の 3 欄、Task 2 の `/jobs` の `volume_instance_id`
- 産出:

```ts
export type CardView = {
  volume_instance_id: string;
  label: string;
  profile_name: string;
  size_bytes: number;
  profile_slug: string | null;
  trusted: boolean;
  provisional: boolean;
  reason: string;
  pending_count: number;
  scanned_at: string | null;
  busy: boolean;
};

export type Doing = { job: Job; card: CardView | null };
export type Todo =
  | { kind: "import_card"; card: CardView }
  | { kind: "merge" | "merge_review" | "send" | "approve"; count: number };
export type StandingKind = "counting" | "not_target" | "no_contents" | "done";
export type Standing = { card: CardView; kind: StandingKind };
export type HomeSections = { doing: Doing[]; todo: Todo[]; standing: Standing[] };

export function homeSections(input: {
  cards: CardView[];
  jobs: Job[];
  counts: DashboardCounts | null;
}): HomeSections;

export type DashboardCounts = {
  merge_candidates: number;
  merge_review_total: number;
  unsent_total: number;
  awaiting_total: number;
};
```

- [ ] **手順 1: 失敗するテストを書く**

`web/src/hooks/homeSections.test.ts`。

```ts
import { describe, expect, it } from "vitest";

import { homeSections } from "./homeSections";
import type { CardView } from "./homeSections";
import type { Job } from "../components/JobProgress";

const NONE = {
  merge_candidates: 0,
  merge_review_total: 0,
  unsent_total: 0,
  awaiting_total: 0,
};

const CARD: CardView = {
  volume_instance_id: "vol-1",
  label: "SD_Card",
  profile_name: "DJI Osmo Pocket",
  size_bytes: 512_000_000_000,
  profile_slug: "dji-osmo",
  trusted: true,
  provisional: false,
  reason: "",
  pending_count: 38,
  scanned_at: "2026-08-24T00:00:00Z",
  busy: false,
};

function job(over: Partial<Job> = {}): Job {
  return {
    id: "j1",
    type: "import",
    status: "running",
    created_at: "2026-08-24T00:00:00Z",
    volume_instance_id: "vol-1",
    ...over,
  };
}

describe("ホームの導出", () => {
  it("取り込む残りがあるカードは、やることに出る", () => {
    const { todo } = homeSections({ cards: [CARD], jobs: [], counts: NONE });
    expect(todo).toEqual([{ kind: "import_card", card: CARD }]);
  });

  // **これがこの設計の芯。** 帯が出ているのに「やることはありません」に
  // なる場面を作れないことを、規則そのもので確かめる。
  it("カードが挿さっていれば、3 つの並びのどれかに必ず出る", () => {
    const cards: CardView[] = [
      CARD,
      { ...CARD, pending_count: 0 },
      { ...CARD, pending_count: 0, scanned_at: null },
      { ...CARD, profile_slug: null, reason: "対象の中身が無い" },
      { ...CARD, pending_count: 0, provisional: true },
      { ...CARD, trusted: false },
    ];
    for (const card of cards) {
      const sections = homeSections({ cards: [card], jobs: [], counts: NONE });
      const total = sections.doing.length + sections.todo.length + sections.standing.length;
      expect(total).toBeGreaterThan(0);
    }
  });

  it("走っているジョブを持つカードは、やることから消えていま動いていることへ移る", () => {
    const sections = homeSections({ cards: [CARD], jobs: [job()], counts: NONE });
    expect(sections.todo).toEqual([]);
    expect(sections.doing).toEqual([{ job: job(), card: CARD }]);
  });

  it("まだ数えていないカードは「数えています」で、空とは言わない", () => {
    const card = { ...CARD, pending_count: 0, scanned_at: null };
    const { standing } = homeSections({ cards: [card], jobs: [], counts: NONE });
    expect(standing).toEqual([{ card, kind: "counting" }]);
  });

  it("数えた上で残りが無いカードは、抜いていい側に出る", () => {
    const card = { ...CARD, pending_count: 0 };
    const { standing } = homeSections({ cards: [card], jobs: [], counts: NONE });
    expect(standing).toEqual([{ card, kind: "done" }]);
  });

  it("対象外のカードは、理由を持ったまま出る", () => {
    const card = { ...CARD, profile_slug: null, reason: "対象の中身が無い" };
    const { standing } = homeSections({ cards: [card], jobs: [], counts: NONE });
    expect(standing).toEqual([{ card, kind: "not_target" }]);
  });

  it("やることは、手を動かす順に並ぶ", () => {
    const { todo } = homeSections({
      cards: [CARD],
      jobs: [],
      counts: {
        merge_candidates: 3,
        merge_review_total: 1,
        unsent_total: 48,
        awaiting_total: 2,
      },
    });
    expect(todo.map((t) => t.kind)).toEqual([
      "import_card",
      "merge",
      "merge_review",
      "send",
      "approve",
    ]);
  });

  it("待機中の作業も出す。走っているものを先に、待っているものは古い順", () => {
    const running = job({ id: "run", status: "running", volume_instance_id: null });
    const older = job({ id: "old", status: "queued", created_at: "2026-08-24T00:00:01Z", volume_instance_id: null });
    const newer = job({ id: "new", status: "queued", created_at: "2026-08-24T00:00:02Z", volume_instance_id: null });
    const { doing } = homeSections({ cards: [], jobs: [newer, older, running], counts: NONE });
    expect(doing.map((d) => d.job.id)).toEqual(["run", "old", "new"]);
  });

  it("終わった作業は出さない", () => {
    const { doing } = homeSections({
      cards: [],
      jobs: [job({ status: "succeeded" })],
      counts: NONE,
    });
    expect(doing).toEqual([]);
  });

  it("集計がまだ読めていない間は、数から来るやることを出さない", () => {
    const { todo } = homeSections({ cards: [], jobs: [], counts: null });
    expect(todo).toEqual([]);
  });
});
```

- [ ] **手順 2: 失敗を確かめる**

実行: `npm --prefix web run test -- homeSections`
期待: `Failed to resolve import "./homeSections"` で FAIL。

- [ ] **手順 3: 最小の実装**

`web/src/hooks/homeSections.ts`。

```ts
// ホームの導出（§13）。**画面は一覧を持たない** —— カード・作業・集計から
// 毎回 3 つの並びを導く。
//
// **カードは「状態」ではなく「仕事」として扱う。** 取り込む残りがあるカードを
// やることに並べることで、「カードが挿さっているのに、やることはありません」
// という食い違いが、条件の直しではなく形の上で起こり得なくなる。

import { isLive } from "./useJobPulse";
import type { Job } from "../components/JobProgress";

export type CardView = {
  volume_instance_id: string;
  label: string;
  profile_name: string;
  size_bytes: number;
  profile_slug: string | null;
  trusted: boolean;
  provisional: boolean;
  reason: string;
  pending_count: number;
  scanned_at: string | null;
  busy: boolean;
};

export type DashboardCounts = {
  merge_candidates: number;
  merge_review_total: number;
  unsent_total: number;
  awaiting_total: number;
};

export type Doing = { job: Job; card: CardView | null };
export type Todo =
  | { kind: "import_card"; card: CardView }
  | { kind: "merge" | "merge_review" | "send" | "approve"; count: number };
export type StandingKind = "counting" | "not_target" | "no_contents" | "done";
export type Standing = { card: CardView; kind: StandingKind };
export type HomeSections = { doing: Doing[]; todo: Todo[]; standing: Standing[] };

// **つなぐ → 確かめる → 送る → 確認。** 手を動かす順（つないでから送る）。
// カードの取り込みはこれより前に来る。
export const COUNTED = [
  { kind: "merge", of: (c: DashboardCounts) => c.merge_candidates },
  { kind: "merge_review", of: (c: DashboardCounts) => c.merge_review_total },
  { kind: "send", of: (c: DashboardCounts) => c.unsent_total },
  { kind: "approve", of: (c: DashboardCounts) => c.awaiting_total },
] as const;

/** いま実際に動いているか（待機中は含まない）。並べる順を決めるのに使う。 */
function isRunning(job: Job): boolean {
  return job.status === "running" || job.status === "cancelling";
}

export function homeSections(input: {
  cards: CardView[];
  jobs: Job[];
  counts: DashboardCounts | null;
}): HomeSections {
  const byId = new Map(input.cards.map((card) => [card.volume_instance_id, card]));
  const live = input.jobs.filter(isLive);

  // **走っているものが先、待っているものは古い順。** 一覧の並びは API 側の
  // 都合で変わりうるので、順序をここで決める。
  const doing: Doing[] = [...live]
    .sort((a, b) =>
      isRunning(a) === isRunning(b)
        ? a.created_at.localeCompare(b.created_at)
        : isRunning(a)
          ? -1
          : 1,
    )
    .map((job) => ({
      job,
      card: job.volume_instance_id ? (byId.get(job.volume_instance_id) ?? null) : null,
    }));

  const held = new Set(
    live.map((job) => job.volume_instance_id).filter((id): id is string => Boolean(id)),
  );

  const todo: Todo[] = [];
  const standing: Standing[] = [];
  for (const card of input.cards) {
    // 動いているカードは、いま動いていることの側で見えている。
    if (held.has(card.volume_instance_id)) {
      continue;
    }
    if (card.profile_slug === null) {
      standing.push({ card, kind: "not_target" });
    } else if (card.scanned_at === null) {
      // **「取り込むものはありません」とは言わない。** 数える前の 0 件は
      // 「空」ではない。
      standing.push({ card, kind: "counting" });
    } else if (card.pending_count > 0) {
      todo.push({ kind: "import_card", card });
    } else if (card.provisional) {
      standing.push({ card, kind: "no_contents" });
    } else {
      standing.push({ card, kind: "done" });
    }
  }

  if (input.counts !== null) {
    const counts = input.counts;
    for (const row of COUNTED) {
      const count = row.of(counts);
      if (count > 0) {
        todo.push({ kind: row.kind, count });
      }
    }
  }

  return { doing, todo, standing };
}
```

`Job` に `volume_instance_id` を足す（`web/src/components/JobProgress.tsx`）。

```ts
export type Job = {
  id: string;
  type: string;
  status: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  // どのカードの作業か。カードに紐づかない作業（送信など）では null。
  volume_instance_id?: string | null;
  progress?: JobProgressValue | null;
};
```

- [ ] **手順 4: テストが通ることを確かめる**

実行: `npm --prefix web run test -- homeSections`
期待: PASS。

- [ ] **手順 5: 古い `useTasks` を消す**

```bash
git rm web/src/hooks/useTasks.ts web/src/hooks/useTasks.test.ts
```

`tasksFrom` を import している場所（`screens/Home.tsx`、`components/Layout.tsx`）は
Task 4 で載せ替える。**ここでは消すだけで、ビルドは通らない状態になる** ——
Task 4 と続けて行う。

- [ ] **手順 6: コミット**

```bash
git add web/src/hooks/homeSections.ts web/src/hooks/homeSections.test.ts \
        web/src/components/JobProgress.tsx
git commit -m "feat(web): ホームの導出規則を 1 つの純粋関数にする"
```

---

### Task 4: ホームを新しい導出に載せ替える

**ファイル:**
- 変更: `web/src/screens/Home.tsx`
- 変更: `web/src/screens/Home.test.tsx`
- 変更: `web/src/components/Layout.tsx`（`tasksFrom` を使っているナビのバッジ）

**受け渡し:**
- 消費: Task 3 の `homeSections()`、Task 1・2 の API の欄

- [ ] **手順 1: 失敗するテストを書く**

`web/src/screens/Home.test.tsx` に足す（既存の描画ヘルパの書き方に合わせる）。

```tsx
it("カードが挿さっている場面で「やることはありません」と書かない", async () => {
  // 実機で出た場面そのもの: カードが挿さっていて、集計はすべて 0。
  renderHome({
    devices: { volumes: [volume({ pending_count: 38, scanned_at: "2026-08-24T00:00:00Z" })] },
    dashboard: emptyDashboard(),
    jobs: { jobs: [] },
  });
  expect(await screen.findByText(/38 件を取り込む/)).toBeInTheDocument();
  expect(screen.queryByText("いま、やることはありません")).not.toBeInTheDocument();
});

it("取り込みが走っている間は、取り込むボタンを出さない", async () => {
  renderHome({
    devices: { volumes: [volume({ pending_count: 38, busy: true })] },
    dashboard: emptyDashboard(),
    jobs: { jobs: [{ id: "j1", type: "import", status: "running",
                     created_at: "2026-08-24T00:00:00Z", volume_instance_id: "vol-1" }] },
  });
  expect(await screen.findByText("取り込み")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "いま取り込む" })).not.toBeInTheDocument();
});

it("待機中の作業も残らず出す", async () => {
  renderHome({
    devices: { volumes: [] },
    dashboard: emptyDashboard(),
    jobs: { jobs: [
      { id: "a", type: "import", status: "running", created_at: "2026-08-24T00:00:00Z" },
      { id: "b", type: "detect_groups", status: "queued", created_at: "2026-08-24T00:00:01Z" },
    ] },
  });
  expect(await screen.findByText("取り込み")).toBeInTheDocument();
  expect(screen.getByText("候補の検出")).toBeInTheDocument();
});
```

- [ ] **手順 2: 失敗を確かめる**

実行: `npm --prefix web run test -- Home`
期待: FAIL（「いま、やることはありません」が出る／作業が 1 本しか出ない）。

- [ ] **手順 3: `Home.tsx` を載せ替える**

要点だけ挙げる（既存の描画部品はそのまま使う）。

1. `tasksFrom(dashboardData)` を `homeSections({ cards, jobs, counts })` にする。
   `cards` は `/devices` の各要素に `volumeLabel()` と `profileDisplayName()` を
   当てて `CardView` にしたもの
2. `pickLiveJob` を**使うのをやめ**、`sections.doing` を丸ごと `JobCard` で
   並べる（`useJobPulse(sections.doing.length > 0, jobs.reload)`）。カードに
   紐づく作業には見出しにラベルを添える
3. 「やること」は `sections.todo` を回す。`kind === "import_card"` の札は
   カードのラベル・容量・残り件数を出し、**未信頼なら同じ札に「このカードを
   信頼する」も置く**
4. 「いまの様子」は `sections.standing` を回す（文言は Task 5 の
   `CardStanding` を共用する）
5. 空表示は **3 つの並びがすべて空のときだけ**

```tsx
const sections = homeSections({ cards, jobs: jobs.data?.jobs ?? [], counts: dashboardData });
const nothing =
  sections.doing.length === 0 && sections.todo.length === 0 && sections.standing.length === 0;
```

「いま取り込む」の `disabled` は、**この画面の要求中**に加えて**そのカードが
掴まれているとき**も落とす。

```tsx
disabled={busy || card.busy}
```

- [ ] **手順 4: `Layout.tsx` のバッジを直す**

ナビの「やること」バッジは `tasksFrom` を使っている。`homeSections` は
`/devices` と `/jobs` を要るので、**バッジは集計だけから数える**形に留める
（枠で 3 本引くと、画面ごとに同じ集計が飛ぶ）。`COUNTED` を export して
バッジ側で使う。

- [ ] **手順 5: テストが通ることを確かめる**

実行: `npm --prefix web run test && npm --prefix web run lint && npm --prefix web run build`
期待: すべて PASS。

- [ ] **手順 6: コミット**

```bash
git add web/src/screens/Home.tsx web/src/screens/Home.test.tsx web/src/components/Layout.tsx
git commit -m "feat(web): ホームが、いま起きていることと食い違わないようにする"
```

---

### Task 5: 「抜いていいか」を、押さずに分かるようにする

**ファイル:**
- 作成: `web/src/components/CardStanding.tsx`
- 変更: `web/src/screens/work/CardDetail.tsx:318-330`（「取り外す」を消す）
- 変更: `web/src/screens/Home.tsx`（同じボタンを消し、この部品を使う）
- テスト: `web/src/screens/work/CardDetail.test.tsx`

**受け渡し:**
- 消費: Task 1 の `busy`
- 産出: `<CardStanding card={card} />` —— 抜けるか抜けないかの 1 行

- [ ] **手順 1: 失敗するテストを書く**

```tsx
it("掴まれていないカードは、抜いていいと言う", async () => {
  renderCardDetail({ volumes: [volume({ busy: false })] });
  expect(await screen.findByText("いま抜いて大丈夫です")).toBeInTheDocument();
});

it("作業中のカードは、抜かないでと言う", async () => {
  renderCardDetail({ volumes: [volume({ busy: true })] });
  expect(await screen.findByText(/抜かないでください/)).toBeInTheDocument();
});

it("何も起きないボタンを置かない", async () => {
  renderCardDetail({ volumes: [volume({ busy: false })] });
  await screen.findByText("いま抜いて大丈夫です");
  expect(screen.queryByRole("button", { name: /取り外す/ })).not.toBeInTheDocument();
});
```

- [ ] **手順 2: 失敗を確かめる**

実行: `npm --prefix web run test -- CardDetail`
期待: FAIL。

- [ ] **手順 3: 部品を作る**

```tsx
// カードを抜いていいか（§13）。**押して確かめるのではなく、常に出す。**
//
// 掴んでいる作業が無ければ、読み取り専用のマウントは作業の終わりに外れて
// いるので、その時点で既に安全である。画面は 2 秒ごとに取り直すので、
// 作業が終われば自分で切り替わる。

import type { CardView } from "../hooks/homeSections";

export function CardStanding({ card }: { card: CardView }) {
  return (
    <p role="status" className="small">
      {card.busy
        ? "作業中です。終わるまで抜かないでください。"
        : "いま抜いて大丈夫です。"}
    </p>
  );
}
```

- [ ] **手順 4: 「取り外す」を消す**

`work/CardDetail.tsx` の該当ボタンと、`act()` の `"close"` 分岐を消す。
`screens/Home.tsx` の同じボタンも消す（Task 4 で置き換わっている）。

**`POST /volumes/{id}/close` は API に残す。** サーバは触らない。

- [ ] **手順 5: テストが通ることを確かめる**

実行: `npm --prefix web run test && npm --prefix web run lint`

- [ ] **手順 6: コミット**

```bash
git add web/src/components/CardStanding.tsx web/src/screens/work/CardDetail.tsx \
        web/src/screens/work/CardDetail.test.tsx web/src/screens/Home.tsx
git commit -m "feat(web): 抜いていいかを、押さずに分かるようにする"
```

本文に**なぜ**を残す: 押しても何も起きないボタンで、値打ちは返ってくる答え
だけだったのに、その答えが画面に出ていなかったため。

---

### Task 6: 送信のジョブが進捗を書く

**ファイル:**
- 変更: `app/src/mediaferry/core/lease_pulse.py`
- 変更: `app/src/mediaferry/adapters/immich.py:172-205`（`upload_asset`）
- 変更: `app/src/mediaferry/jobs/uploader.py`
- テスト: `app/tests/test_uploader.py`、`app/tests/test_adapter_immich.py`

**受け渡し:**
- 産出: `with_lease_pulse(..., progress=lambda: dict | None)`
- 産出: `ImmichClient.upload_asset(..., on_bytes: Callable[[int], None] | None = None)`
- 産出: `upload` ジョブの `progress` が
  `{phase, rel_path, file_index, file_count, bytes_done, bytes_total,
  bytes_done_all, bytes_total_all}`

- [ ] **手順 1: 失敗するテストを書く**

`app/tests/test_uploader.py` に足す。既存の `world` フィクスチャ
（`server, uploader, ctx, uploads, destinations, destination_id, media_id` の
7 つ組）と `PAYLOAD` / `CAPTURED` / `API_KEY` をそのまま使う。**新しい枠組みを
持ち込まない。**

```python
import json

from mediaferry.jobs.uploader import _Reported


def test_the_upload_job_reports_how_far_it_got(world, db):
    """71 GB を 6 分かけている間、画面に何も出ないのを直す."""
    _server, uploader, ctx, _uploads, _destinations, destination_id, _media_id = world
    uploader.run(ctx, destination_id)
    # 進捗は走っている間だけ入る（終わらせるのは runner なので、ここでは残る）。
    row = db.execute("SELECT progress_json FROM job").fetchone()
    assert row["progress_json"] is not None, "送信のジョブが進捗を一度も書いていない"
    progress = json.loads(row["progress_json"])
    assert progress["phase"] == "upload"
    assert progress["file_index"] == 1
    assert progress["file_count"] == 1
    # 1 件の中を数えている証拠。ここが 0 なら、件数だけ出して中は見ていない。
    assert progress["bytes_done_all"] == len(PAYLOAD)
    assert progress["bytes_total_all"] == len(PAYLOAD)


def test_the_reported_total_grows_rather_than_lying():
    """走っている間に対象が増えても `12 / 10 件` とは書かない."""
    reported = _Reported(file_count=2, bytes_total_all=100)
    reported.file_index = 3
    reported.bytes_done_all = 150
    snapshot = reported.snapshot()
    assert snapshot["file_count"] == 3
    assert snapshot["bytes_total_all"] == 150


def test_bytes_are_counted_while_one_file_streams(immich, tmp_path):
    """大きい 1 件を送っている間も、その中で進む."""
    seen: list[int] = []
    payload = b"x" * 4096
    path = tmp_path / "big.mp4"
    path.write_bytes(payload)
    client = ImmichClient(immich.url, API_KEY)
    client.upload_asset(
        path,
        sha1_hex=hashlib.sha1(payload, usedforsecurity=False).hexdigest(),
        device_asset_id="mediaferry:big",
        file_created_at=CAPTURED,
        file_modified_at=CAPTURED,
        on_bytes=seen.append,
    )
    assert sum(seen) == 4096
```

- [ ] **手順 2: 失敗を確かめる**

実行: `uv run pytest app/tests/test_uploader.py -k "reports or reported or streams" -v`
期待: `assert seen, "送信のジョブが進捗を一度も書いていない"` で FAIL。

- [ ] **手順 3: 心拍が進捗を運べるようにする**

`core/lease_pulse.py`:

```python
def with_lease_pulse[T](
    ctx: JobContext,
    work: Callable[[], T],
    also: Callable[[], None] | None = None,
    progress: Callable[[], dict[str, Any] | None] | None = None,
    ownership_errors: tuple[type[BaseException], ...] = (LeaseLost,),
) -> T:
```

docstring に 1 段落足す。

```
    `progress` を渡すと、heartbeat のたびに呼んでその値を一緒に書く。
    **書き込みは増えない** —— 既に打っている UPDATE に相乗りする。走っている
    スレッドは値を数えるだけで、DB へ触るのは待つ側のまま。
```

心拍のところを変える。

```python
                ctx.assert_lease()
                ctx.heartbeat(progress() if progress is not None else None)
```

- [ ] **手順 4: 送るストリームの読み出し量を数える**

`adapters/immich.py`:

```python
class _CountingReader:
    """読んだ量を数えながら渡すだけのラッパ.

    **数えるのは送信スレッド、書くのは待つ側**（`with_lease_pulse`）。ここから
    DB へは触らない。
    """

    def __init__(self, stream: IO[bytes], on_bytes: Callable[[int], None]) -> None:
        self._stream = stream
        self._on_bytes = on_bytes

    def read(self, size: int = -1) -> bytes:
        chunk = self._stream.read(size)
        self._on_bytes(len(chunk))
        return chunk
```

`upload_asset` に引数を足し、`files=` へ渡す前に包む。

```python
        on_bytes: Callable[[int], None] | None = None,
    ) -> UploadOutcome:
        ...
        with path.open("rb") as stream:
            body = stream if on_bytes is None else _CountingReader(stream, on_bytes)
            response = self._request(
                ...
                files={"assetData": (path.name, body, "application/octet-stream")},
```

- [ ] **手順 5: `uploader.py` が合計を数え、進捗を組み立てる**

`run()` の先頭で 1 回だけ数える（条件は `claim_next` と同じ `SENDABLE_CLAUSE`）。
`_Progress` とは別の名前を使う（あれは解放先を決める内部フラグ）。

```python
@dataclass
class _Reported:
    """画面に出す進み具合. **`_Progress` とは別物**（あちらは解放先を決める内部フラグ）."""

    file_count: int
    bytes_total_all: int
    file_index: int = 0
    rel_path: str = ""
    bytes_total: int = 0
    bytes_done: int = 0
    bytes_done_all: int = 0

    def add(self, count: int) -> None:
        """送信スレッドから呼ばれる. **DB へは触らない。**"""
        self.bytes_done += count
        self.bytes_done_all += count

    def snapshot(self) -> dict[str, Any]:
        # **合計を追い越したら合計を伸ばす。** 走っている間に対象が増減しうる
        # ので、`12 / 10 件` のような嘘を画面に出さない。
        return {
            "phase": "upload",
            "rel_path": self.rel_path,
            "file_index": self.file_index,
            "file_count": max(self.file_count, self.file_index),
            "bytes_done": self.bytes_done,
            "bytes_total": max(self.bytes_total, self.bytes_done),
            "bytes_done_all": self.bytes_done_all,
            "bytes_total_all": max(self.bytes_total_all, self.bytes_done_all),
        }
```

- 1 件を claim したら `file_index += 1`、`rel_path` と `bytes_total` を入れ、
  `bytes_done = 0` に戻す
- ループの `ctx.heartbeat()`（`uploader.py:103`）を
  `ctx.heartbeat(reported.snapshot())` にする
- 送信の `with_lease_pulse`（`uploader.py:374`）に
  `progress=reported.snapshot` を渡し、`client.upload_asset(..., on_bytes=reported.add)`
  にする

- [ ] **手順 6: テストが通ることを確かめる**

実行: `uv run pytest app/tests/test_uploader.py app/tests/test_adapter_immich.py app/tests/test_upload_e2e.py -v`
期待: PASS。

- [ ] **手順 7: 静的検査とコミット**

```bash
uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/core/lease_pulse.py app/src/mediaferry/adapters/immich.py \
        app/src/mediaferry/jobs/uploader.py app/tests/
git commit -m "feat(app): 送信の進捗を、心拍に相乗りさせて書く"
```

本文に**なぜ**を残す: 71.2 GB を 6 分、35 件を 2 分半かける間、画面に動いて
いるかどうかが出なかったため。書き込みを増やさずに出せるのは、心拍が既に
同じ行を UPDATE しているから。

---

### Task 7: 画面が送信の進捗を読む

**ファイル:**
- 変更: `web/src/components/JobProgress.tsx:35`
- テスト: `web/src/components/JobProgress.test.tsx`

- [ ] **手順 1: 失敗するテストを書く**

```ts
it("送信の進捗を、内部の名前のまま出さない", () => {
  expect(
    progressLine(
      { phase: "upload", rel_path: "library/2026/DJI_0042.MP4", file_index: 12,
        file_count: 35, bytes_done: 1_000, bytes_total: 4_000 },
      null,
    ),
  ).toContain("送信中");
});
```

- [ ] **手順 2: 失敗を確かめる**

実行: `npm --prefix web run test -- JobProgress`
期待: `"upload"` がそのまま出て FAIL。

- [ ] **手順 3: 1 行足す**

```ts
const PHASES: Record<string, string> = {
  copy: "コピー中",
  merge: "つないでいます",
  upload: "送信中",
};
```

- [ ] **手順 4: 通ることを確かめてコミット**

```bash
npm --prefix web run test -- JobProgress
git add web/src/components/JobProgress.tsx web/src/components/JobProgress.test.tsx
git commit -m "feat(web): 送信の進捗を日本語で出す"
```

---

### Task 8: 挿すだけで取り込まれるようにする

**ファイル:**
- 作成: `app/src/mediaferry/db/migrations/0020_auto_scan.sql`
- 変更: `app/src/mediaferry/jobs/watcher.py:47-70`（`CANDIDATES`）と
  `:127-160`（`_enqueue_ready`）
- テスト: `app/tests/test_volume_watcher.py`、`app/tests/test_db_migrate.py`

**受け渡し:**
- 産出: `volume_presence.auto_scan_at`

- [ ] **手順 1: 失敗するテストを書く**

`app/tests/test_volume_watcher.py` に足す。既存の `watcher` フィクスチャと
`a_known_card()` / `trust_the_card()` / `reinsert()` をそのまま使う。

```python
def job_types(db) -> list[str]:
    return [row["type"] for row in db.execute("SELECT type FROM job ORDER BY created_at, id")]


def test_a_card_is_counted_as_soon_as_it_appears(watcher, db):
    """`scan` を積まないと `source_entry` が無く、自動取り込みは 0 件で成功する."""
    watcher.tick()
    assert job_types(db) == ["scan"]


def test_counting_happens_even_when_auto_import_is_off(database, db, broker, monkeypatch):
    """「取り込まない」は「数えない」ではない.

    **`watcher` フィクスチャは env で `trusted` に固定している**ので、
    ここだけ自前で組み立てる（env は DB より優先される）。
    """
    monkeypatch.setenv("MEDIAFERRY_DEFAULT_TIMEZONE", "Asia/Tokyo")
    ProfileRegistry(db).sync_builtins()
    off = VolumeWatcher(
        database,
        {"MEDIAFERRY_AUTO_IMPORT": "off", "MEDIAFERRY_DEFAULT_TIMEZONE": "Asia/Tokyo"},
        broker,
        poll_interval=0.01,
    )
    try:
        off.tick()
    finally:
        off.close()
    assert job_types(db) == ["scan"]


def test_the_automatic_path_also_looks_for_split_videos(watcher, db, volumes):
    """取り込んだあとに探さないと、ホームに「つなぐ」が出ない."""
    a_known_card(watcher, volumes)
    trust_the_card(db)
    watcher.tick()
    assert job_types(db)[-2:] == ["import", "detect_groups"]


def test_reinserting_the_card_counts_it_again(watcher, db, volumes):
    """前回のスキャン以降に撮ったものを拾う道.

    印は presence ごとなので、挿し直せば新しい行になり、もう一度数える。
    """
    watcher.tick()
    reinsert(watcher, volumes)
    scans = db.execute("SELECT count(*) FROM job WHERE type = 'scan'").fetchone()[0]
    assert scans == 2


def test_counting_is_marked_so_it_does_not_pile_up(watcher, db):
    """同じ接続に何度も積まない（印を付けるのと積むのは同じ排他区間）."""
    watcher.tick()
    watcher.tick()
    watcher.tick()
    scans = db.execute("SELECT count(*) FROM job WHERE type = 'scan'").fetchone()[0]
    assert scans == 1
```

- [ ] **手順 2: 失敗を確かめる**

実行: `uv run pytest app/tests/test_volume_watcher.py -k "counted or counting or split or reinserting" -v`
期待: `IndexError` か `assert types[0] == "scan"` で FAIL。

- [ ] **手順 3: マイグレーションを書く**

`app/src/mediaferry/db/migrations/0020_auto_scan.sql`:

```sql
-- カードを見つけたら数える（§12.1）。`auto_import_at` と同じ形の印で、
-- presence ごとに 1 回だけ `scan` を積むためのもの。
--
-- **信頼の有無にも AUTO_IMPORT にもよらない。** §12.1 の「スキャン結果を
-- 画面に出すところで止まり、ユーザの承認を待つ」は、数え終わっていることを
-- 前提にしている。
ALTER TABLE volume_presence ADD COLUMN auto_scan_at TEXT;
```

**チェックサムを更新する**（`app/tests/migration_checksums.txt`）。手順は
`app/tests/test_db_migrate.py` の失敗メッセージが教える。

- [ ] **手順 4: watcher が 3 本積む**

数える対象は取り込みより広い（信頼も確度も要らない）ので、**もう 1 つの
SELECT を足す**。

```python
# 数えてよい接続。**取り込みより広い** —— 信頼も確度も要らない。
# プロファイルが決まっていること（`scan.roots` を読むため）だけが条件。
TO_COUNT = """
    SELECT p.id AS presence_id, p.volume_instance_id, p.broker_epoch, p.generation,
           p.major, p.minor, v.fs_uuid, v.profile_id, v.profile_revision_id
      FROM volume_presence p
      JOIN volume_instance v ON v.id = p.volume_instance_id
      JOIN device_profile d ON d.id = v.profile_id
     WHERE p.detached_at IS NULL
       AND p.auto_scan_at IS NULL
       AND d.archived_at IS NULL
       AND v.profile_revision_id = d.current_revision_id
     ORDER BY p.attached_at, p.id
"""
```

`CANDIDATES` はそのままでよい（`detect_groups` の params に要る
`profile_id` と `profile_revision_id` を既に選んでいる）。

`_enqueue_ready` を、同じ排他区間で 3 種類を積む形にする。

```python
        with immediate(self._conn):
            # **数えるのは設定によらない。** 先に積むので、続けて積まれる
            # `import` は数え終わった行を読む（ジョブは 1 本ずつ直列に走る）。
            for row in self._conn.execute(TO_COUNT).fetchall():
                marked = self._conn.execute(
                    "UPDATE volume_presence SET auto_scan_at = ?"
                    " WHERE id = ? AND auto_scan_at IS NULL AND detached_at IS NULL",
                    (now_iso(), row["presence_id"]),
                ).rowcount
                if marked:
                    jobs.append(store.enqueue("scan", _params(row)))
            if SettingsService(self._conn, self._env).snapshot().auto_import != "trusted":
                return jobs
            for row in self._conn.execute(CANDIDATES).fetchall():
                marked = self._conn.execute(
                    "UPDATE volume_presence SET auto_import_at = ?"
                    " WHERE id = ? AND auto_import_at IS NULL AND detached_at IS NULL",
                    (now_iso(), row["presence_id"]),
                ).rowcount
                if marked:
                    jobs.append(store.enqueue("import", _params(row)))
                    # **探すところまでやる。** 取り込んだだけでは、ホームの
                    # 「つなぐ」は出ない（現行の結合候補の数から導くため）。
                    jobs.append(
                        store.enqueue(
                            "detect_groups",
                            {
                                "profile_id": row["profile_id"],
                                "profile_revision_id": row["profile_revision_id"],
                            },
                        )
                    )
```

**`return []` を `return jobs` に変えるのを忘れない** —— `AUTO_IMPORT=off` の
とき、積んだ `scan` を捨てて返すことになる。

- [ ] **手順 5: テストが通ることを確かめる**

実行: `uv run pytest app/tests/test_volume_watcher.py app/tests/test_db_migrate.py -v`
期待: PASS。

- [ ] **手順 6: コミット**

```bash
uv run ruff check . && uv run ruff format --check .
git add app/src/mediaferry/db/migrations/0020_auto_scan.sql \
        app/src/mediaferry/jobs/watcher.py app/tests/
git commit -m "feat(app): カードを見つけたら数え、挿すだけで取り込まれるようにする"
```

本文に**なぜ**を残す: `import` は `scan` が残した行を運ぶだけなので、数えずに
積むと 0 件で成功する（実機で確認）。挿し直しても `import` しか積まれない
現状では、前回のスキャン以降に撮ったファイルが永久に取り込まれない。

---

### Task 9: 巡回の錠と、仕様の正本の更新

**ファイル:**
- 変更: `web/e2e/` の既存の巡回 spec（**新しい spec ファイルを作らない**）
- 変更: `docs/design.md` §13
- 変更: `docs/decisions.md`

- [ ] **手順 1: E2E に錠を 1 本足す**

既存の巡回 spec に、**カードが挿さっている場面で「いま、やることはありません」が
出ない**ことを確かめる 1 本を足す。

**新しい spec ファイルを作らない。** E2E はサーバを回収しないので、spec を
1 本増やすと孤児のサーバが 1 つ増える（`../development.md` の持ち越し）。

- [ ] **手順 2: `docs/design.md` §13 を更新する**

- ホームの中身を **3 つの並び**（いま動いていること／やること／いまの様子）に
  書き換える
- 「やること」の表の先頭に**カードの取り込み**を足す
- 「どれも無いときは『いま、やることはありません』」の条件を**3 つの並びが
  すべて空のとき**に狭める
- 「カードの中身」ページから**「取り外す」を落とす**

- [ ] **手順 3: `docs/decisions.md` に理由を残す**

- **カードを「仕事」として扱うと決めた**こと（A-1 / A-3 を採らなかった理由）
- **「取り外す」を画面から外した**こと（押しても何も起きず、値打ちは返ってくる
  答えだけだった。API は残す）
- **送信の進捗を心拍に相乗りさせた**こと（書き込みを増やさずに出せる）

- [ ] **手順 4: 全部を通す**

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
npm --prefix web run test && npm --prefix web run lint && npm --prefix web run build
```

- [ ] **手順 5: コミット**

```bash
git add docs/design.md docs/decisions.md web/e2e/
git commit -m "docs: ホームの 3 つの並びを仕様の正本に反映する"
```

---

### Task 10: 変異試験

**ファイル:** テストのみ（実装は戻す）

**変異試験を省かない。** Phase 1 では「通ってはいるが実装の判断を検証して
いない」テストが 30 件以上見つかった。

- [ ] **手順 1: 控えを取る**

```bash
mkdir -p "$SCRATCH/phase8-mutation"
cp app/src/mediaferry/jobs/watcher.py app/src/mediaferry/jobs/uploader.py \
   app/src/mediaferry/jobs/volumes.py web/src/hooks/homeSections.ts \
   "$SCRATCH/phase8-mutation/"
```

**`git checkout` を使わない**（同時に別の変更を巻き戻す）。

- [ ] **手順 2: 判断を 1 つずつ壊す**

最低でもこれらを壊し、**対応するテストが落ちること**を確かめてから戻す。

| 壊すもの | 落ちるはずのテスト |
| --- | --- |
| `homeSections` の `scanned_at === null` の枝を消す | 「まだ数えていないカードは『数えています』」 |
| `homeSections` の `held.has(...)` の `continue` を消す | 「走っているジョブを持つカードは、やることから消える」 |
| `homeSections` の並べ替えの `isRunning` を反転 | 「走っているものを先に」 |
| `PENDING_CLAUSE` を `state IN ('seen')` に | 「取り込みが運ぶものとぴったり同じ」 |
| `_Reported.snapshot` の `max(...)` を外す | 「合計を追い越したら合計が伸びる」 |
| `with_lease_pulse` の `progress()` を `None` に | 「送信のジョブが進捗を書く」 |
| watcher の `TO_COUNT` の `auto_scan_at IS NULL` を外す | 「挿し直しで数え直す」（無限に積まれる） |
| watcher の `return jobs` を `return []` に | 「`AUTO_IMPORT=off` でも `scan` を積む」 |

**Python の変異は `PYTHONDONTWRITEBYTECODE=1` を付けて回す。**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest app/tests/test_volume_watcher.py -v
```

バイト数が変わらない書き換え（`>` を `<`、`a or b` を `b or a` 等）では
`.pyc` の無効化条件をすり抜け、古いバイトコードが使われる。

- [ ] **手順 3: 検出できなかったものを記録する**

落ちなかった変異は、**テストを足すか、落ちない理由を記録に残す**。記録先は
`docs/history/phase8-record.md`（このタスクで新規に作る）。

- [ ] **手順 4: 控えから戻っていることを確かめる**

```bash
git diff --stat   # 実装に差分が残っていないこと
uv run pytest && npm --prefix web run test
```

- [ ] **手順 5: コミット**

```bash
git add docs/history/phase8-record.md app/tests/ web/src/
git commit -m "test: 変異試験で網の穴を埋める"
```

---

## 実機で見ること

**自動テストが全部緑でも、実機でしか出ないものがある**（前回はそれで 9 件
出た）。`:sha-xxxxxxx` で固定したイメージに入れ替えてもらってから見る。

| 見るもの | 合格の目安 |
| --- | --- |
| **挿すだけで取り込まれるか** | カードを挿す → `scan` → `import` → `detect_groups` が順に走り、件数が 0 でない |
| ホームの食い違い | カードが挿さっている場面で「いま、やることはありません」が出ない |
| 取り込み中のボタン | 「いま取り込む」が出ない（札が上へ移っている） |
| **送信の進捗** | 71 GB 級で、件数・ファイル名・速度・残り時間が動く。**大きい 1 件の中でもバーが進む** |
| 抜いていい表示 | 取り込みの終わりで、押さずに「いま抜いて大丈夫です」へ切り替わる |

**画面の機能は画面から踏んでもらう。** API から叩くと題材が消える。
