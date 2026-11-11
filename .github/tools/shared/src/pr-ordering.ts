/**
 * pr-ordering.ts — loop ordering + cheap "is there anything to do?" filter.
 *
 * This is deliberately NOT a decision engine. The per-PR agent makes every
 * substantive call (review verdict, merge, conflict strategy, nudge). These
 * helpers only:
 *   1. order the loop OLDEST-FIRST, so the most at-risk PRs are handled before
 *      any broad timeout can truncate the tail; and
 *   2. skip PRs that demonstrably have nothing actionable right now, so we
 *      don't spend an agent session to conclude "still being worked on".
 *
 * The skip filter is intentionally conservative: when in doubt it returns
 * true (actionable) and lets the agent decide. It must never cause a PR to be
 * silently dropped — anything it skips is logged with a reason by the caller.
 */

import type { PrSnapshot } from "./pr-snapshot.js";

/** Minutes since an ISO timestamp, relative to `nowMs`. Infinity when null. */
export function minutesSince(iso: string | null, nowMs: number): number {
  if (!iso) return Infinity;
  return (nowMs - new Date(iso).getTime()) / 60000;
}

/** Stable oldest-first ordering by creation time (ties broken by PR number). */
export function orderPrs(snapshots: PrSnapshot[]): PrSnapshot[] {
  return [...snapshots].sort((a, b) => {
    const t = new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime();
    return t !== 0 ? t : a.number - b.number;
  });
}

export interface ActionableDecision {
  actionable: boolean;
  /** Human-readable reason a PR was skipped (only set when !actionable). */
  reason?: string;
}

/** Settle window: a draft that committed within this many minutes is "still warm". */
export const SETTLE_MINUTES = 10;

/**
 * Cheap pre-filter. Returns actionable=false ONLY for the one safe case: a
 * fresh draft that is still actively being worked (committed within the settle
 * window). Everything else is actionable — including settled drafts (which may
 * need readying), conflicts, failing CI, and PRs awaiting review/merge.
 */
export function isActionable(snapshot: PrSnapshot, nowMs: number): ActionableDecision {
  if (snapshot.isDraft) {
    const sinceCommit = minutesSince(snapshot.lastCommitAt, nowMs);
    if (sinceCommit < SETTLE_MINUTES) {
      return {
        actionable: false,
        reason: `draft still warm (last commit ${sinceCommit.toFixed(0)}m ago, < ${SETTLE_MINUTES}m settle window)`,
      };
    }
  }
  return { actionable: true };
}

export interface OrderedPlan {
  /** PRs to hand to the agent, oldest-first. */
  actionable: PrSnapshot[];
  /** PRs skipped this pass, oldest-first, each with a reason. */
  skipped: { snapshot: PrSnapshot; reason: string }[];
}

/** Order oldest-first and split into actionable vs skipped (with reasons). */
export function planLoop(snapshots: PrSnapshot[], nowMs: number): OrderedPlan {
  const ordered = orderPrs(snapshots);
  const actionable: PrSnapshot[] = [];
  const skipped: { snapshot: PrSnapshot; reason: string }[] = [];
  for (const s of ordered) {
    const d = isActionable(s, nowMs);
    if (d.actionable) actionable.push(s);
    else skipped.push({ snapshot: s, reason: d.reason ?? "skipped" });
  }
  return { actionable, skipped };
}
