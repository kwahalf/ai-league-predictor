import { describe, it, expect } from "vitest";
import { runPrLoop, buildPrPrompt, type PrLoopResult } from "../run-pr-pipeline.js";
import type { PrSnapshot } from "../pr-snapshot.js";

function pr(n: number, title = `pr ${n}`): PrSnapshot {
  return {
    number: n,
    title,
    author: "copilot-swe-agent",
    createdAt: "2026-06-07T10:00:00Z",
    updatedAt: "2026-06-07T11:00:00Z",
    isDraft: false,
    mergeable: "MERGEABLE",
    reviewDecision: null,
    changedFiles: 1,
    labels: [],
    latestReview: null,
    approved: false,
    changesRequested: false,
    lastCommitAt: "2026-06-07T11:00:00Z",
    ciState: "SUCCESS",
    checks: [],
    linkedIssues: [],
  };
}

describe("runPrLoop", () => {
  it("returns no results for an empty PR list", async () => {
    const calls: number[] = [];
    const out = await runPrLoop([], async (p) => {
      calls.push(p.number);
      return { number: p.number, title: p.title, status: "ok" } as PrLoopResult;
    });
    expect(out).toEqual([]);
    expect(calls).toEqual([]);
  });

  it("processes PRs in the given order, one by one", async () => {
    const seen: number[] = [];
    await runPrLoop([pr(10), pr(20), pr(30)], async (p) => {
      seen.push(p.number);
      return { number: p.number, title: p.title, status: "ok" };
    });
    expect(seen).toEqual([10, 20, 30]);
  });

  it("continues past a handler that throws, recording it as an error", async () => {
    const seen: number[] = [];
    const out = await runPrLoop([pr(1), pr(2), pr(3)], async (p) => {
      seen.push(p.number);
      if (p.number === 2) throw new Error("boom");
      return { number: p.number, title: p.title, status: "ok" };
    });
    // All three were attempted despite #2 throwing — tail is never lost to one bad PR.
    expect(seen).toEqual([1, 2, 3]);
    expect(out.map((r) => [r.number, r.status])).toEqual([
      [1, "ok"],
      [2, "error"],
      [3, "ok"],
    ]);
    expect(out[1]!.detail).toContain("boom");
  });

  it("stops starting new PRs once shouldContinue() returns false (defers the tail)", async () => {
    const seen: number[] = [];
    let budget = 2; // allow exactly two PRs, then "run out of budget"
    const out = await runPrLoop(
      [pr(10), pr(20), pr(30), pr(40)],
      async (p) => {
        seen.push(p.number);
        return { number: p.number, title: p.title, status: "ok" };
      },
      () => budget-- > 0
    );
    // Oldest two handled; newer two deferred to the next pass.
    expect(seen).toEqual([10, 20]);
    expect(out.map((r) => r.number)).toEqual([10, 20]);
  });

  it("preserves a timeout status returned by the handler", async () => {
    const out = await runPrLoop([pr(7)], async (p) => ({
      number: p.number,
      title: p.title,
      status: "timeout",
      detail: "Timeout after 360000ms",
    }));
    expect(out[0]!.status).toBe("timeout");
  });
});

describe("buildPrPrompt", () => {
  it("embeds the PR number, title, and the snapshot JSON", () => {
    const prompt = buildPrPrompt(pr(42, "fix the thing"));
    expect(prompt).toContain("#42");
    expect(prompt).toContain("fix the thing");
    expect(prompt).toContain('"number": 42');
    expect(prompt).toContain("--pr 42");
  });
});
