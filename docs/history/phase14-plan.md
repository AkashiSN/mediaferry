# Phase 14 実装計画 — リセットを通し、押したことを伝え、送り直せるようにする

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 実機で見つかった 4 件を直す —— リセットが 4 段すべて内部エラーで落ちる、
ジョブを積むボタンが押したことを言わない、Immich で消した資産を送り直す手段が画面に無い、
一覧に送り先の絞り込みが常設されていない。

**Architecture:** リセットは `db/reset.py` の削除順と検査を直す（スキーマも公開手順も変えない）。
動線はホームを「進捗と結果の唯一の置き場」にし、ジョブを積む 3 つの操作が押したジョブの id を
持ってホームへ遷移する。`/sending` は廃止。送り直しは既にある API にボタンを付ける。

**Tech Stack:** Python 3.14 / SQLite / FastAPI / React 19 + React Router 7 / vitest / Playwright

**Spec:** [`phase14-design.md`](phase14-design.md)

## Global Constraints

- Python は `>=3.14`。**すべてのモジュールは `from __future__ import annotations` で始まる**
- ruff: `line-length = 100`、`select = ["E","F","I","UP","B","SIM","ANN","S"]`。**`docs/` は対象外**
- **コメントと docstring は日本語で、いま書かれているコードを現在形で説明する。過去の経緯は書かない**
- **環境固有の値（IP・ホスト名・データセットのパス・API キー・タイムゾーンの実値）を書かない**
- **DB に絶対パスを保存しない。** 外部コマンドは引数配列で起動する
- **秘密をログ・`job.params_json`・`job_event`・API 応答・例外メッセージに出さない**
- **DB 接続はスコープごとに 1 本**
- **実装より先に失敗するテストを書き、失敗を確認してから最小実装する**
- **変異試験を省かない。`PYTHONDONTWRITEBYTECODE=1` を必ず付ける。各変異 5 回以上当てて回数を報告する**
- **`git checkout` と `git add -A` を絶対に使わない**（作業ディレクトリを破壊した事故がある）
- 受け入れ: `uv run pytest` / `uv run ruff check .` / `uv run ruff format --check .`。
  web を触るタスクは加えて `npm --prefix web run test && lint && build`、
  **動線を変えるタスクは `npm --prefix web run test:e2e` も**

---

## ファイル構成

| ファイル | 責務 | 触るタスク |
| --- | --- | --- |
| `app/src/mediaferry/db/reset.py` | 段ごとの削除。順序と前提の検査 | 1 |
| `app/src/mediaferry/api/errors.py` | `ErrorCode` の一覧 | 2 |
| `app/src/mediaferry/api/routes_system.py` | `/reset` の例外→ApiError 変換 | 2 |
| `app/src/mediaferry/api/routes_media.py` | `/media/{id}` の宛先ごとの payload | 5 |
| `web/src/api/errors.ts` | code → 日本語 | 2 |
| `web/src/hooks/useQueuedJobs.ts`（新） | 押したジョブを名指しで追い、30 秒で消す | 3 |
| `web/src/screens/Home.tsx` | 結果カードの表示 | 3 |
| `web/src/screens/work/Merge.tsx` | つなぐ → 通知 → ホーム | 4 |
| `web/src/screens/work/CardDetail.tsx` | 取り込む → 通知 → ホーム | 4 |
| `web/src/screens/work/Send.tsx` | 送る → 通知 → ホーム | 4 |
| `web/src/screens/work/Sending.tsx` | **削除** | 4 |
| `web/src/App.tsx` | `/sending` のルートを外す | 4 |
| `web/src/screens/PhotoDetail.tsx` | 送り直す・サーバを確かめる | 6 |
| `web/src/screens/Photos.tsx` | 送り先のプルダウンを常設 | 7 |

---

## Task 1: リセットの削除順と、回収待ちの検査

**Files:**
- Modify: `app/src/mediaferry/db/reset.py`
- Test: `app/tests/test_reset.py`

**Interfaces:**
- Produces: `ResetNotPossible` が 2 つの理由で上がる（走っている作業がある / 回収待ちの staging がある）。
  どちらも既存の例外型のまま。**新しい例外型は作らない**（Task 2 が `code` で分ける）

- [ ] **Step 1: 失敗するテストを書く**

`app/tests/test_reset.py` に足す。**既存の `a_job()` は `job` を参照する行を作らない**ので、
公開済みの staging を持つ土台を別に用意する。

```python
from .test_schema_artifacts import a_source_entry, a_staging
from .test_schema_sources import a_volume


def a_published_staging(db, job_id: str, ref) -> str:
    """**公開が完了した後の形。** `artifact_staging` の行は `published` のまま残り、
    `job_id NOT NULL REFERENCES job(id) ON DELETE RESTRICT` で `job` を掴んでいる.
    """
    entry = a_source_entry(db, a_volume(db, ref))
    return a_staging(
        db,
        job_id,
        state="published",
        source_entry_id=entry,
        final_rel_path="library/dji-osmo/DCIM/PUBLISHED.MP4",
        expected_size=1,
        content_sha1="0" * 40,
        metadata_json="{}",
    )


@pytest.mark.parametrize("scope", ["jobs", "uploads", "library", "all"])
def test_a_reset_works_after_something_was_published(client, api_db, ref, scope):
    """**一度でも公開した DB でリセットが通る.**

    `artifact_staging` は公開後も `published` のまま残り、`job` を
    `ON DELETE RESTRICT` で掴む。`job` を先に消すと 4 段すべてが外部キーで落ちる。
    """
    job_id = a_job(api_db)
    a_published_staging(api_db, job_id, ref)
    api_db.commit()

    assert client.post("/api/reset", json={"scope": scope}).status_code == 200
    assert count(api_db, "job") == 0
    assert count(api_db, "artifact_staging") == 0


def test_a_reset_is_refused_while_a_publish_waits_to_be_recovered(client, api_db, ref):
    """**回収待ちの取り込みは消さずに断る.**

    `writing` / `staged` の行は中断した公開の復旧に要る。黙って消すと戻せない。
    """
    job_id = a_job(api_db)
    entry = a_source_entry(api_db, a_volume(api_db, ref))
    a_staging(
        api_db,
        job_id,
        state="staged",
        source_entry_id=entry,
        final_rel_path="library/dji-osmo/DCIM/HALF.MP4",
        expected_size=1,
        content_sha1="0" * 40,
        metadata_json="{}",
    )
    api_db.commit()

    response = client.post("/api/reset", json={"scope": "jobs"})
    assert response.status_code == 409
    # **1 行も消えていない。** 断ったのに片付いていると、次の判断ができない。
    assert count(api_db, "job") == 1
    assert count(api_db, "artifact_staging") == 1
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_reset.py -k "after_something_was_published or waits_to_be_recovered" -v
```

期待: 前者は 4 つとも 500（`FOREIGN KEY constraint failed`）、後者は 200 が返って行が消える。

- [ ] **Step 3: 最小の実装**

`db/reset.py` の `reset()` の中、`live` の検査の直後に足す。

```python
        # **回収待ちの公開は消さない。** `writing` / `staged` の行は中断した
        # 公開の復旧に要る（起動時の reconciliation が拾う）。消すと戻せない。
        pending = conn.execute(
            "SELECT count(*) AS n FROM artifact_staging WHERE state <> 'published'"
        ).fetchone()["n"]
        if pending:
            raise ResetNotPossible(
                "回収待ちの取り込みがあるので、いまはリセットできない"
            )

        # 1. 作業の記録。**作り直せる**（再スキャン・再検出）。
        #    **`published` の staging を先に消す。** `job_id` を
        #    `ON DELETE RESTRICT` で掴んでいるので、`job` を先に消すと止まる。
        #    公開が完了した行は履歴であって、回収の対象ではない（上で確かめた）。
        removed["artifact_staging"] = conn.execute(
            "DELETE FROM artifact_staging WHERE state = 'published'"
        ).rowcount
        removed["job_event"] = conn.execute("DELETE FROM job_event").rowcount
        removed["job"] = conn.execute("DELETE FROM job").rowcount
```

`library` の段の `removed["artifact_staging"] = conn.execute("DELETE FROM artifact_staging").rowcount`
は、**上で消した数を上書きしてしまう**ので `+=` にする。

```python
            removed["artifact_staging"] += conn.execute("DELETE FROM artifact_staging").rowcount
```

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest app/tests/test_reset.py -v
uv run pytest
```

- [ ] **Step 5: 変異試験**（`PYTHONDONTWRITEBYTECODE=1`、各 5 回）

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `WHERE state = 'published'` を外して全件消す | `..._waits_to_be_recovered`（回収待ちまで消える） |
| 回収待ちの検査を消す | `..._waits_to_be_recovered`（409 が返らない） |
| `published` の削除を `job_event` / `job` の**後ろ**へ移す | `..._after_something_was_published` の 4 つ |
| `+=` を `=` に戻す | `library` / `all` で `removed["artifact_staging"]` の数が合わなくなる ——
  **数を断言するテストが無ければ、この変異は殺せない。** 殺せないなら報告に書く |

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/db/reset.py app/tests/test_reset.py
git commit -m "$(cat <<'EOF'
fix(reset): 公開済みの staging を job より先に消し、回収待ちは断る

artifact_staging の行は公開が成功した後も published のまま残り、
job_id NOT NULL REFERENCES job(id) ON DELETE RESTRICT で job を掴む。
reset() は手順 1 で DELETE FROM job を実行していたので、親を先に消して
外部キーが止めていた。手順 1 はどの段でも必ず走るため、一度でも取り込みか
結合をした DB では 4 段すべてが内部エラーで落ちていた。

published は完了した公開の履歴なので、作業の記録の段で job より先に消す。
writing / staged は中断した公開の復旧に要るので、消さずに断る。

既存のテストが通っていたのは、a_job() が job を参照する行を 1 つも作らない
ため。公開済みの staging を持つ土台を足した。
EOF
)"
```

---

## Task 2: 断りの理由を、画面が読める形にする

**Files:**
- Modify: `app/src/mediaferry/api/errors.py`（`ErrorCode` に 1 つ足す）
- Modify: `app/src/mediaferry/api/routes_system.py:52-57`
- Modify: `web/src/api/errors.ts`
- Test: `app/tests/test_reset.py`, `web/src/api/errors.test.ts`（あれば）

**Interfaces:**
- Consumes: Task 1 の `ResetNotPossible`（2 つの理由）
- Produces: `ErrorCode.STAGING_PENDING = "staging_pending"`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_a_refused_reset_says_which_thing_to_wait_for(client, api_db, ref):
    """**理由ごとに `code` を分ける.** 画面は `code` で文面を選ぶので、
    どちらも `job_in_flight` だと「何を待てばよいか」が読めない。
    """
    job_id = a_job(api_db)
    entry = a_source_entry(api_db, a_volume(api_db, ref))
    a_staging(
        api_db, job_id, state="staged", source_entry_id=entry,
        final_rel_path="library/dji-osmo/DCIM/HALF.MP4", expected_size=1,
        content_sha1="0" * 40, metadata_json="{}",
    )
    api_db.commit()
    body = client.post("/api/reset", json={"scope": "jobs"}).json()
    assert body["error"]["code"] == "staging_pending"


def test_a_running_job_still_reports_job_in_flight(client, api_db):
    """走っている作業の側は `job_in_flight` のまま（既存の文言を変えない）."""
    a_job(api_db, status="running")
    api_db.commit()
    body = client.post("/api/reset", json={"scope": "jobs"}).json()
    assert body["error"]["code"] == "job_in_flight"
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_reset.py -k "which_thing_to_wait_for or still_reports_job_in_flight" -v
```

期待: 1 本目が `job_in_flight` を返して落ちる。

- [ ] **Step 3: 最小の実装**

`db/reset.py` に理由を持たせる。**新しい例外型は作らない** —— 型を増やすと
呼び出し側の分岐が増える。理由を属性で持つ。

```python
class ResetNotPossible(RuntimeError):
    """いま走っている作業や、回収待ちの公開があるので足元を外せない.

    `reason` は API の `code` を決めるためのもので、利用者へ出す文ではない。
    """

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason
```

送出側を `raise ResetNotPossible("...", "job_in_flight")` と
`raise ResetNotPossible("...", "staging_pending")` にする。

`api/errors.py` の 409 の並びに足す。

```python
    STAGING_PENDING = "staging_pending"
```

`api/routes_system.py` の変換を直す。

```python
    except ResetNotPossible as exc:
        # **generic な conflict にしない。** 画面は code で文面を選ぶので、
        # conflict のままだと「いまの状態ではこの操作はできません」としか出ず、
        # 何を待てばよいのかが読めない。
        raise ApiError(409, ErrorCode(exc.reason), str(exc)) from exc
```

`web/src/api/errors.ts` の `MESSAGES` に足す。

```ts
  staging_pending:
    "取り込みの途中だったものが残っています。アプリを再起動すると片付くので、そのあとで試してください。",
```

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest app/tests/test_reset.py -v
npm --prefix web run test && npm --prefix web run lint && npm --prefix web run build
```

- [ ] **Step 5: 変異試験**（各 5 回）

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `ErrorCode(exc.reason)` を `ErrorCode.JOB_IN_FLIGHT` に戻す | `..._which_thing_to_wait_for` |
| 回収待ち側の `reason` を `"job_in_flight"` にする | 同上 |
| 走っている作業側の `reason` を `"staging_pending"` にする | `..._still_reports_job_in_flight` |

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/db/reset.py app/src/mediaferry/api/errors.py \
        app/src/mediaferry/api/routes_system.py web/src/api/errors.ts app/tests/test_reset.py
git commit -m "$(cat <<'EOF'
fix(reset): 断る理由を code で分け、何を待てばよいかを書く

リセットが断られる理由は 2 つある。走っている作業があるときと、回収待ちの
取り込みが残っているとき。どちらも job_in_flight だと「作業の履歴を見て
ください」としか出ず、後者では履歴を見ても何も無い。

理由は ResetNotPossible の属性で持つ。例外型を増やすと呼び出し側の分岐が
増えるだけで、決めたいのは API の code だけなので。
EOF
)"
```

---

## Task 3: ホームが、押したジョブを名指しで追う

**Files:**
- Create: `web/src/hooks/useQueuedJobs.ts`
- Modify: `web/src/screens/Home.tsx`
- Test: `web/src/hooks/useQueuedJobs.test.ts`, `web/src/screens/Home.test.tsx`

**Interfaces:**
- Produces: `useQueuedJobs(jobs: Job[]): { queued: Job[]; note: string | null; dismiss: (id: string) => void }`
  —— router の `location.state`（`{ jobIds?: string[]; note?: string | null }`）を読み、
  その id のジョブと `note` を返す。
  **成功したものは、ホームに着いてから 30 秒で自動的に落ちる。失敗は落ちない。**
  Task 4 がこの `jobIds` と `note` を渡す側になる。

**`note` を落とさない。** 「断られた写真と、開始に失敗した宛先を隠さない」は
`design.md` §13 の設計価値で、いまは `/sending` が出している。Task 4 でその画面が
消えるので、**ここでホームが引き取る**。

- [ ] **Step 1: 失敗するテストを書く**

```ts
// web/src/hooks/useQueuedJobs.test.ts
import { act, renderHook } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import { QUEUED_FADE_MS, useQueuedJobs } from "./useQueuedJobs";

function wrapper(jobIds: string[]) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <MemoryRouter initialEntries={[{ pathname: "/", state: { jobIds } }]}>
        {children}
      </MemoryRouter>
    );
  };
}

const succeeded = { id: "a", type: "merge", status: "succeeded", last_message: "できた" };
const failed = { id: "b", type: "merge", status: "failed", error: "だめ" };

describe("useQueuedJobs", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("押したジョブだけを出す", () => {
    const { result } = renderHook(() => useQueuedJobs([succeeded, failed]), {
      wrapper: wrapper(["a"]),
    });
    expect(result.current.queued.map((job) => job.id)).toEqual(["a"]);
  });

  it("**成功はホームに着いてから 30 秒で消える**", () => {
    const { result } = renderHook(() => useQueuedJobs([succeeded]), {
      wrapper: wrapper(["a"]),
    });
    expect(result.current.queued).toHaveLength(1);
    act(() => void vi.advanceTimersByTime(QUEUED_FADE_MS));
    expect(result.current.queued).toHaveLength(0);
  });

  it("**失敗は時間で消えない.** 見逃した人には「何も起きなかった」と区別が付かない", () => {
    const { result } = renderHook(() => useQueuedJobs([failed]), {
      wrapper: wrapper(["b"]),
    });
    act(() => void vi.advanceTimersByTime(QUEUED_FADE_MS * 3));
    expect(result.current.queued).toHaveLength(1);
  });

  it("**`note` を渡す.** 断られた写真と、開始に失敗した宛先を隠さない（§13）", () => {
    function withNote({ children }: { children: React.ReactNode }) {
      return (
        <MemoryRouter
          initialEntries={[{ pathname: "/", state: { jobIds: ["a"], note: "2 件は断られました" } }]}
        >
          {children}
        </MemoryRouter>
      );
    }
    const { result } = renderHook(() => useQueuedJobs([succeeded]), { wrapper: withNote });
    expect(result.current.note).toBe("2 件は断られました");
  });

  it("**`note` は、ジョブが消えても残る.** 1 本も始まらなかった送信でも知らせが要る", () => {
    function withNote({ children }: { children: React.ReactNode }) {
      return (
        <MemoryRouter
          initialEntries={[{ pathname: "/", state: { jobIds: [], note: "1 件も始まりませんでした" } }]}
        >
          {children}
        </MemoryRouter>
      );
    }
    const { result } = renderHook(() => useQueuedJobs([]), { wrapper: withNote });
    expect(result.current.queued).toHaveLength(0);
    expect(result.current.note).toBe("1 件も始まりませんでした");
  });

  it("× で消せる", () => {
    const { result } = renderHook(() => useQueuedJobs([failed]), {
      wrapper: wrapper(["b"]),
    });
    act(() => result.current.dismiss("b"));
    expect(result.current.queued).toHaveLength(0);
  });

  it("**走っている間は消えない.** 30 秒より長い作業でも結果まで見える", () => {
    const running = { id: "a", type: "merge", status: "running" };
    const { result, rerender } = renderHook(
      ({ jobs }) => useQueuedJobs(jobs),
      { wrapper: wrapper(["a"]), initialProps: { jobs: [running] } },
    );
    act(() => void vi.advanceTimersByTime(QUEUED_FADE_MS * 2));
    expect(result.current.queued).toHaveLength(1);
    rerender({ jobs: [succeeded] });
    act(() => void vi.advanceTimersByTime(QUEUED_FADE_MS));
    expect(result.current.queued).toHaveLength(0);
  });
});
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
npm --prefix web run test -- useQueuedJobs
```

期待: `useQueuedJobs` が無いので import で落ちる。

- [ ] **Step 3: 最小の実装**

```ts
// web/src/hooks/useQueuedJobs.ts
// 押したジョブを名指しで追う（§13）。**ホームが進捗と結果の唯一の置き場**で、
// ジョブを積む操作はここへ遷移してくる。
//
// **時計は「ホームに着いてから」クライアント側で計る。** `finished_at` との
// 引き算にすると、ブラウザとサーバの時計がずれていたとき、遅れていれば
// 「未来に終わったジョブ」が居座り、進んでいれば一度も出ない。
import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";

import type { Job } from "../components/JobProgress";
import { isLive } from "./useJobPulse";

/** 成功した結果を出しておく長さ。押してから目を上げ、読み返せる程度。 */
export const QUEUED_FADE_MS = 30_000;

export function useQueuedJobs(jobs: Job[]): {
  queued: Job[];
  note: string | null;
  dismiss: (id: string) => void;
} {
  const location = useLocation();
  const state = location.state as { jobIds?: string[]; note?: string | null } | null;
  const wanted = useMemo(() => new Set(state?.jobIds ?? []), [state]);
  const [dropped, setDropped] = useState<ReadonlySet<string>>(new Set());

  const queued = useMemo(
    () => jobs.filter((job) => wanted.has(job.id) && !dropped.has(job.id)),
    [jobs, wanted, dropped],
  );

  // **成功したものだけ時間で落とす。** 失敗は利用者が読むまで残す。
  const fading = queued
    .filter((job) => !isLive(job) && job.status === "succeeded")
    .map((job) => job.id)
    .join(",");

  useEffect(() => {
    if (!fading) {
      return;
    }
    const ids = fading.split(",");
    const timer = setTimeout(() => {
      setDropped((before) => new Set([...before, ...ids]));
    }, QUEUED_FADE_MS);
    return () => clearTimeout(timer);
  }, [fading]);

  const dismiss = useCallback((id: string) => {
    setDropped((before) => new Set([...before, id]));
  }, []);

  // **`note` はジョブと独立に返す。** 1 本も始まらなかった送信では `jobIds` が
  // 空になるが、そのときこそ知らせが要る。
  return { queued, note: state?.note ?? null, dismiss };
}
```

ホーム側は「いま動いていること」の上に出す。**`isLive` で絞った既存の枠とは別**に、
`queued` を出す（走っている間は両方に出るので、**既存の枠から `queued` の id を除く**）。

- [ ] **Step 4: 通ることを確かめる**

```bash
npm --prefix web run test && npm --prefix web run lint && npm --prefix web run build
```

- [ ] **Step 5: 変異試験**（各 5 回）

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `job.status === "succeeded"` を外して失敗も落とす | 「失敗は時間で消えない」 |
| `QUEUED_FADE_MS` を 0 にする | 「押したジョブだけを出す」（着いた瞬間に消える） |
| `!isLive(job)` を外す | 「走っている間は消えない」 |
| `wanted.has(job.id)` を外す | 「押したジョブだけを出す」 |
| `note` を `queued.length > 0` のときだけ返す | 「`note` は、ジョブが消えても残る」 |

- [ ] **Step 6: コミット**

```bash
git add web/src/hooks/useQueuedJobs.ts web/src/hooks/useQueuedJobs.test.ts \
        web/src/screens/Home.tsx web/src/screens/Home.test.tsx
git commit -m "$(cat <<'EOF'
feat(web): ホームが、押したジョブを名指しで追って結果を出す

検出ジョブは実測 47 ms で終わる。遷移した時点でもう succeeded なので、
isLive で絞ったホームには何も無く、「押しても何も起きない」がそのまま
再現する。押したジョブの id を router state で受け取り、走っていれば進捗、
終わっていれば結果を出す。

時計はホームに着いてからクライアント側で計る。finished_at との引き算に
すると、ブラウザとサーバの時計がずれていたとき、遅れていれば未来に終わった
ジョブが居座り、進んでいれば一度も出ない。

失敗は時間で消さない。時間で消える失敗は、見逃した人にとって「何も
起きなかった」と区別が付かない。
EOF
)"
```

---

## Task 4: ジョブを積む 3 つの操作を、通知してホームへ送る（`/sending` 廃止）

**Files:**
- Modify: `web/src/screens/work/Merge.tsx:243-261`（`act`）
- Modify: `web/src/screens/work/CardDetail.tsx:250`（`act`）
- Modify: `web/src/screens/work/Send.tsx:379`
- Delete: `web/src/screens/work/Sending.tsx`, `web/src/screens/work/Sending.test.tsx`
- Modify: `web/src/App.tsx:72`（`/sending` のルート）
- Test: 上記の各 `*.test.tsx`, `web/e2e/journey.spec.ts`

**Interfaces:**
- Consumes: Task 3 の `useQueuedJobs`（router state の `{ jobIds, note }` を読む）
- Produces: 3 つの操作が `navigate("/", { state: { jobIds, note } })` を呼ぶ

- [ ] **Step 1: 失敗するテストを書く**

```tsx
// web/src/screens/work/Merge.test.tsx に足す
it("**つなぐを押すと、積んだジョブを持ってホームへ行く**", async () => {
  // 押しても画面が変わらないと「失敗した」と読まれる。POST はジョブを積むだけで、
  // detected → merging はワーカーが拾ってからなので、reload しても
  // 押す前と同じカードが描き直される（実測で 465 ms の窓）。
  const user = userEvent.setup();
  renderMerge();
  await user.click(await screen.findByRole("button", { name: "つなぐ" }));
  expect(navigateSpy).toHaveBeenCalledWith("/", {
    state: { jobIds: ["job-merge"], note: null },
  });
});

it("**分かれた動画を探すは遷移しない.** 候補はこの画面に出る", async () => {
  const user = userEvent.setup();
  renderMerge();
  await user.click(await screen.findByRole("button", { name: "分かれた動画を探す" }));
  expect(navigateSpy).not.toHaveBeenCalled();
});
```

`Send.test.tsx` の `/sending` を期待している箇所を `"/"` に書き換える。
**`note` が渡り続けることを断言する** —— 断られた写真と開始に失敗した宛先は §13 の設計価値。

- [ ] **Step 2: 落ちることを確かめる**

```bash
npm --prefix web run test -- Merge Send CardDetail
```

- [ ] **Step 3: 最小の実装**

`Merge.tsx` の `act()` に、遷移するかどうかを引数で持たせる。

**`edit.run` は `Promise<boolean>` を返し、応答本体を捨てる**
（`web/src/api/hooks.ts:106-118`）。`job_id` は**閉包の中で受け取る** ——
`Send.tsx:370` が `jobIds.push(started.job_id)` としているのと同じ形。

```ts
  /** 成功したかを返す。**失敗したのに後片付けを進めない**ため。 */
  async function act(
    path: string,
    body?: unknown,
    method?: "POST" | "PATCH" | "DELETE",
    /** **ジョブを積む操作だけ true。** 進捗はホームにあるので、そこへ送る。
     *  検出は候補がこの画面に出るので連れ出さない。 */
    handOff?: boolean,
  ): Promise<boolean> {
    let queued: string | null = null;
    const done = await edit.run(async () => {
      const started = (await request(path, {
        method: method ?? (path.includes("?action=") ? "PATCH" : "POST"),
        body,
      })) as { job_id?: string } | null;
      queued = started?.job_id ?? null;
    });
    if (done) {
      groups.reload();
      refreshTasks();
    }
    setConfirmation(null);
    if (done && handOff && queued) {
      navigate("/", { state: { jobIds: [queued], note: null } });
    }
    return done;
  }
```

`CardDetail.tsx` の `act(volumeId, "import")` も同じ形にする（`import` のときだけ遷移）。

`Send.tsx:379` を `navigate("/", { state: { jobIds, note } })` にする。

`App.tsx` の `/sending` の `Route` を消し、`Sending.tsx` と `Sending.test.tsx` を消す。

- [ ] **Step 4: 通ることを確かめる**

```bash
npm --prefix web run test && npm --prefix web run lint && npm --prefix web run build
npm --prefix web run test:e2e
```

**e2e を必ず流す。** `/sending` を消すので、送信の筋を通している e2e が落ちる。
落ちたら**期待をホームへ書き換える**（テストを消して回避しない）。

- [ ] **Step 5: 変異試験**（各 5 回）

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `handOff` を無視して常に遷移する | 「分かれた動画を探すは遷移しない」 |
| `handOff` を無視して一度も遷移しない | 「つなぐを押すと…ホームへ行く」 |
| `note` を渡さない | `Send.test.tsx` の note の断言 |

- [ ] **Step 6: コミット**

```bash
git add web/src/screens/work web/src/App.tsx web/e2e
git commit -m "$(cat <<'EOF'
feat(web): ジョブを積む操作は、積んだと言ってからホームへ送る

つなぐボタンを押しても画面が変わらず、利用者が「失敗した」と読んだ。
POST はジョブを積むだけで、detected から merging への書き換えはワーカーが
拾ってからなので、直後に reload すると押す前と同じカードが描き直される
（実測で 465 ms の窓）。進捗バーはホームに出ているのに、押した人のいる場所
には無い。

進捗の置き場をホームに一本化し、時間のかかるジョブを積む 3 つの操作が
押したジョブの id を持って遷移する。分かれた動画を探すは候補がその画面に
出るので連れ出さない。

/sending は役目が無くなるのでルートごと消した。残すと誰も辿り着かない画面
が 1 枚増える。断られた写真と開始に失敗した宛先の知らせは、ホームへ移した。
EOF
)"
```

---

## Task 5: `/media/{id}` が、宛先ごとに `upload_id` を返す

**Files:**
- Modify: `app/src/mediaferry/api/routes_media.py:558-566`
- Test: `app/tests/test_api_media.py`

**Interfaces:**
- Produces: 宛先ごとの dict に `"upload_id": str | None` が入る。
  Task 6 が `POST /uploads/{upload_id}/requeue` を呼ぶのに使う

- [ ] **Step 1: 失敗するテストを書く**

`test_api_media.py` は `from .test_schema_uploads import a_destination, an_upload` を
すでに持っている。`a_media_file` / `a_profile` も既存のヘルパ。

```python
def a_sent_media(db, *, name: str, **over):
    """1 件だけ送った状態を作り、`(media_id, record_id, destination_id)` を返す."""
    from .test_schema_artifacts import a_media_file
    from .test_schema_sources import a_profile

    profile = a_profile(db, slug=name)
    media_id = a_media_file(db, profile, rel_path=f"library/{name}/DCIM/A.JPG")
    destination = a_destination(db, name=name)
    record_id = an_upload(db, destination, media_id, state="complete", **over)
    return media_id, record_id, destination[0]


def test_the_detail_carries_the_upload_id_for_each_destination(client, db):
    """**送り直すには記録の id が要る.** `presence` は状態を言うが、
    どのレコードを操作すればよいかは言わない。
    """
    media_id, record_id, _ = a_sent_media(db, name="upload-id-test")
    body = client.get(f"/api/media/{media_id}").json()
    assert body["destinations"][0]["upload_id"] == record_id


def test_a_destination_without_a_record_has_no_upload_id(client, db):
    """まだ送っていない宛先は `None`（**キーごと落とさない** —— 画面が
    「キーが無い」と「値が None」を区別できなくなる）."""
    from .test_schema_artifacts import a_media_file
    from .test_schema_sources import a_profile

    media_id = a_media_file(db, a_profile(db, slug="no-record-test"))
    a_destination(db, name="no-record-test")
    body = client.get(f"/api/media/{media_id}").json()
    assert body["destinations"][0]["upload_id"] is None
```

**`client` と `db` が同じ DB を指す**ことは既存のテストが使っている性質
（`conftest.py` の `data_root` を共有する）。

- [ ] **Step 2: 落ちることを確かめる**

```bash
uv run pytest app/tests/test_api_media.py -k upload_id -v
```

- [ ] **Step 3: 最小の実装**

`routes_media.py` の `_destinations()` の 2 か所に足す。記録が無い枝:

```python
                    "state": None,
                    "presence": "not_sent",
                    "upload_id": None,
```

記録がある枝:

```python
                "state": best["state"],
                "presence": _presence(best),
                "upload_id": best["upload_id"],
```

- [ ] **Step 4: 通ることを確かめる**

```bash
uv run pytest app/tests/test_api_media.py -v
uv run pytest
npm --prefix web run typegen   # 生成物を作り直す
```

**`typegen` の差分がこの 1 フィールドだけであることを確認する。**

- [ ] **Step 5: 変異試験**（各 5 回）

| 変異 | 落ちるはずのテスト |
| --- | --- |
| 記録がある枝の `upload_id` を `None` にする | `..._carries_the_upload_id_...` |
| 記録が無い枝の `"upload_id": None` を消す | `..._has_no_upload_id`（KeyError） |

- [ ] **Step 6: コミット**

```bash
git add app/src/mediaferry/api/routes_media.py app/tests/test_api_media.py web/src/api/types.ts
git commit -m "$(cat <<'EOF'
feat(api): くわしくが、宛先ごとに送信レコードの id を返す

presence は状態を言うが、どのレコードを操作すればよいかは言わない。
送り直す（POST /uploads/{id}/requeue）には記録の id が要る。

まだ送っていない宛先は None を返す。キーごと落とすと、画面が「キーが無い」
と「値が None」を区別できない。
EOF
)"
```

---

## Task 6: くわしくから、送り直せるようにする

**Files:**
- Modify: `web/src/screens/PhotoDetail.tsx:32-39, 264-290`
- Test: `web/src/screens/PhotoDetail.test.tsx`

**Interfaces:**
- Consumes: Task 5 の `upload_id`、既存の `presence`

- [ ] **Step 1: 失敗するテストを書く**

```tsx
it("**リモートから消えた宛先にだけ「送り直す」が出る**", async () => {
  // 条件は `presence === "gone"`（再確認でサーバに無いと分かった complete）。
  // API 側の条件（remote_asset_id IS NULL かつ remote_checked_at IS NOT NULL）と
  // 同じものを、画面は presence の語彙で見る。
  renderDetail({
    destinations: [
      { destination_id: "d1", name: "家", state: "complete", presence: "gone", upload_id: "u1" },
      { destination_id: "d2", name: "外", state: "complete", presence: "present", upload_id: "u2" },
    ],
  });
  const buttons = await screen.findAllByRole("button", { name: "送り直す" });
  expect(buttons).toHaveLength(1);
});

it("送り直すを押すと、その記録を pending へ戻す", async () => {
  const user = userEvent.setup();
  renderDetail({
    destinations: [
      { destination_id: "d1", name: "家", state: "complete", presence: "gone", upload_id: "u1" },
    ],
  });
  await user.click(await screen.findByRole("button", { name: "送り直す" }));
  expect(requestSpy).toHaveBeenCalledWith("/uploads/u1/requeue", { method: "POST" });
});

it("**サーバを確かめる動線がある.** 消したことに気づくには再確認が要る", async () => {
  const user = userEvent.setup();
  renderDetail({
    destinations: [
      { destination_id: "d1", name: "家", state: "complete", presence: "present", upload_id: "u1" },
    ],
  });
  await user.click(await screen.findByRole("button", { name: "サーバを確かめる" }));
  expect(requestSpy).toHaveBeenCalledWith("/destinations/d1/recheck", { method: "POST" });
});

it("**まだ送っていない宛先には、どちらのボタンも出さない**", async () => {
  renderDetail({
    destinations: [
      { destination_id: "d1", name: "家", state: null, presence: "not_sent", upload_id: null },
    ],
  });
  await screen.findByText("家");
  expect(screen.queryByRole("button", { name: "送り直す" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "サーバを確かめる" })).not.toBeInTheDocument();
});
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
npm --prefix web run test -- PhotoDetail
```

- [ ] **Step 3: 最小の実装**

`DestinationItem` に `upload_id: string | null` を足し、宛先の行に 2 つのボタンを出す。

```tsx
{dest.presence === "gone" && dest.upload_id && (
  <button type="button" className="btn sm" disabled={acting.busy}
          onClick={() => void requeue(dest.upload_id!)}>
    送り直す
  </button>
)}
{dest.state !== null && (
  <button type="button" className="btn sm quiet" disabled={acting.busy}
          onClick={() => void recheck(dest.destination_id)}>
    サーバを確かめる
  </button>
)}
```

**押せない状態のボタンを並べない**（`deletable` / `delete_blocked_reason` と同じ形）。

- [ ] **Step 4: 通ることを確かめる**

```bash
npm --prefix web run test && npm --prefix web run lint && npm --prefix web run build
```

- [ ] **Step 5: 変異試験**（各 5 回）

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `presence === "gone"` を外して常に出す | 「消えた宛先にだけ出る」 |
| `requeue` の path を `/uploads/{id}/retry` にする | 「押すと pending へ戻す」 |
| 「サーバを確かめる」を消す | 「動線がある」 |

- [ ] **Step 6: コミット**

```bash
git add web/src/screens/PhotoDetail.tsx web/src/screens/PhotoDetail.test.tsx
git commit -m "$(cat <<'EOF'
feat(web): くわしくから、リモートで消えた資産を送り直せるようにする

POST /uploads/{id}/requeue は前からあるのに、画面からの呼び出し元が 1 つも
無かった。errors.ts に「この記録は送り直せません。」という文言だけがあり、
それを出せるボタンが存在しない状態だった。

Immich 側で消しても状態が反映されないのは、再確認への動線が設定の奥にしか
無いため。宛先の行に「サーバを確かめる」も置いた。
EOF
)"
```

---

## Task 7: 一覧に、送り先のプルダウンを常設する

**Files:**
- Modify: `web/src/screens/Photos.tsx:140-160, 251-259, 430-450`
- Test: `web/src/screens/Photos.test.tsx`

**Interfaces:**
- Consumes: 既存の `selectDestination` と `buildMediaQuery`

- [ ] **Step 1: 失敗するテストを書く**

```tsx
it("**宛先が 2 つ以上なら、どの絞り込みでもプルダウンが出る**", async () => {
  renderPhotos({ destinations: TWO });
  expect(await screen.findByRole("combobox", { name: "送り先" })).toBeInTheDocument();
});

it("宛先が 1 つなら出さない（黙ってそれを使う）", async () => {
  renderPhotos({ destinations: [{ id: "d1", name: "家" }] });
  await screen.findByRole("list");
  expect(screen.queryByRole("combobox", { name: "送り先" })).not.toBeInTheDocument();
});

const TWO = [
  { id: "d1", name: "家" },
  { id: "d2", name: "外" },
];

it("**宛先を変えると 1 ページ目へ戻る**", async () => {
  // 3 ページ目のまま移ると、当てはまるものが 1 ページ分しか無いときに
  // 空の一覧だけが出る（既存の `selectDestination` が守っている性質）。
  const user = userEvent.setup();
  renderPhotos({ destinations: TWO, initialEntry: "/photos?page=3" });
  await user.selectOptions(await screen.findByRole("combobox", { name: "送り先" }), "d2");
  expect(currentSearch()).not.toContain("page=3");
});

it("**宛先が要らない絞り込みでは destination_id を API に渡さない**", async () => {
  // `status` を伴わない `destination_id` は API が 400 を返す。
  const user = userEvent.setup();
  renderPhotos({ destinations: TWO });
  await user.selectOptions(await screen.findByRole("combobox", { name: "送り先" }), "d2");
  expect(lastMediaQuery()).not.toContain("destination_id");
});
```

- [ ] **Step 2: 落ちることを確かめる**

```bash
npm --prefix web run test -- Photos
```

- [ ] **Step 3: 最小の実装**

いまの「宛先を選ばせる空の状態」（`needsDestination` の枝、`:444`）から、
**フィルタ行の右端の `<select>`** へ移す。`buildMediaQuery` は
**`DESTINATION_SCOPED.has(filter)` のときしか `destination_id` を載せない**ので変更不要。

```tsx
{destinationRows.length > 1 && (
  <label className="sel">
    <span className="vh">送り先</span>
    <select
      value={effectiveDestinationId ?? ""}
      onChange={(event) => selectDestination(event.target.value)}
    >
      <option value="">送り先を選ぶ</option>
      {destinationRows.map((destination) => (
        <option key={destination.id} value={destination.id}>{destination.name}</option>
      ))}
    </select>
  </label>
)}
```

**`needsDestination` の空の状態は残す** —— 宛先が要る絞り込みで未選択のときは、
一覧を出しても意味が無い。文言だけ「上の送り先を選んでください」へ寄せる。

- [ ] **Step 4: 通ることを確かめる**

```bash
npm --prefix web run test && npm --prefix web run lint && npm --prefix web run build
npm --prefix web run test:e2e
```

- [ ] **Step 5: 変異試験**（各 5 回）

| 変異 | 落ちるはずのテスト |
| --- | --- |
| `destinationRows.length > 1` を `> 0` にする | 「宛先が 1 つなら出さない」 |
| `selectDestination` から `nextParams.delete("page")` を消す | 「1 ページ目へ戻る」 |
| `buildMediaQuery` の `DESTINATION_SCOPED.has(filter)` を外す | 「渡さない」 |

- [ ] **Step 6: コミット**

```bash
git add web/src/screens/Photos.tsx web/src/screens/Photos.test.tsx
git commit -m "$(cat <<'EOF'
feat(web): 一覧のフィルタに、送り先のプルダウンを常設する

destination_id はもともと扱っていたが、宛先が要る絞り込みを選んだときだけ
選ばせる形だった。どの絞り込みからでも送り先を切り替えられるようにする。

宛先が 1 つのときは出さない（黙ってそれを使う既存の性質）。宛先が要らない
絞り込みでは API に destination_id を渡さない。status を伴わない
destination_id は 400 になる。
EOF
)"
```

---

## Task 8: 仕様と記録を現在形へ

**Files:**
- Modify: `docs/design.md`（§13 の動線、リセットの段の定義）
- Modify: `docs/decisions.md`
- Create: `docs/history/phase14-record.md`
- Modify: `docs/development.md`（持ち越しから 2 件を落とす）
- Modify: `docs/history/README.md`

**このタスクは統括（進行役）が行う。実装者へは出さない。**

---

## 実装が終わったら

**実機で確かめる**（設計の §「実機で確かめること」の 6 つ）。イメージは `:sha-xxxxxxx` で
固定して入れ替えてもらう。**画面の機能は画面から踏んでもらう** —— API から叩くと題材が消える。
