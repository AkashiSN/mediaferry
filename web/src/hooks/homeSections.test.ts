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
