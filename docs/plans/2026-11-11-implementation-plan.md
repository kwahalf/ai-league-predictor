# Match Predictor Implementation Plan

**Status:** Draft
**Owner:** @kwahalf
**Spec:** [`docs/specs/2026-11-11-match-predictor.md`](../specs/2026-11-11-match-predictor.md)
**Created:** 2026-11-11

## How we'll work

- **One pull request per step** below, merged into `main` in order. Each PR is small enough to review in one sitting and leaves `main` working.
- **`main` is protected; @kwahalf reviews and merges every PR.** Nothing is pushed to `main` directly, including changes to this plan and the spec. No automated reviewer or auto-merge.
- **Every PR passes** the worker test suite (pytest in the worker image), `supabase db reset` (migrations and seed apply cleanly), and `scripts/audit/run_audits.py`.
- **Measure before modelling.** The scoring metrics and the backtest harness land before any model, so every model is judged the day it arrives.
- **Data first, UI last.** Nothing user-facing is built until the numbers behind it have been backtested.

Sizes are relative: **S** about a day, **M** two to three days, **L** about a week.

## Milestone 0: Repository foundation

Goal: the template and spec on `main`, with PR validation passing and the stack running on the runner.

| # | PR | Size | Contents | Done when |
|---|---|---|---|---|
| 0.1 | Bring in the template | M | The Volaris template from branch `init/template-and-match-spec`, rebased onto `main`, with all its workflows kept (see Decisions). Bind the Supabase ports to localhost. Set the spec's owner. Rewrite `README.md` for this project. | `pr-validation` and `architecture-audit` pass on the PR; `make up` starts the stack on the runner |
| 0.2 | ADR-0001: football data gets its own tables | S | Records the decision in the spec's Data Model section, using `docs/adrs/TEMPLATE.md`. | ADR merged as Accepted |

## Milestone 1: Data foundation (spec phase 1)

Goal: 11 seasons of EPL results and odds in Postgres, refreshed daily, with checks that fail loudly.

| # | PR | Size | Contents | Done when |
|---|---|---|---|---|
| 1.1 | Football schema | M | Migration `…_football_core.sql`: `leagues`, `teams`, `matches`, `match_stats`, `bookmakers`, `odds`, plus RLS and indexes. Seed rows: EPL and the four odds sources (`B365`, `BW`, `AVG`, `PS`). | `supabase db reset` passes; audit's view checks pass |
| 1.2 | CSV parsing library | M | Pure Python, no I/O: parse a season file, map each season's odds columns (`BbAv` → `Avg`, pre-match vs closing) to the canonical shape, normalise team names through an alias table, convert kick-off times to UTC. Small fixture CSVs checked into `temporal/tests/fixtures/`, one pre-2019 and one post-2019. | Every team in all 11 real season files resolves; both fixture files parse to the expected rows |
| 1.3 | `IngestWorkflow` | M | Activities: download, parse (from 1.2), upsert idempotently. Also stores the raw CSVs in Supabase Storage. Backfill command for 2016/17–2026/27. Temporal Schedule: daily 06:00 UTC, including `fixtures.csv`. Move the stack to a long-lived checkout on the runner and start it at boot (see "Hosting on the runner"). | Backfill loads 10 × 380 matches plus the current season; a second run changes nothing |
| 1.4 | Data-quality checks | S | A check activity run after every ingest: 380 matches per completed season, no duplicate fixtures, scores only on finished matches, odds > 1.0, margins between 0 and 15%. A failure fails the workflow visibly in the Temporal UI. | A deliberately corrupted fixture file trips each check |

**Checkpoint:** spot-check one gameweek per season against the source files by hand before starting milestone 2.

## Milestone 2: Models and backtest (spec phase 2)

Goal: three model layers, honestly backtested, with live predictions being stored and scored.

| # | PR | Size | Contents | Done when |
|---|---|---|---|---|
| 2.1 | Model tables and metrics | M | Migration: `model_versions`, `team_ratings`, `predictions` (with the before-kick-off trigger), `prediction_scores`, `market_scores`. Python: RPS, log loss, Brier and calibration buckets, plus margin removal and the consensus. Scores the bookmakers on all history, giving the bar the models must reach. | Market RPS per season is in the database; metric functions are tested against hand-computed examples |
| 2.2 | Backtest harness and leakage tests | M | Walk-forward loop: for each week, the model sees only earlier matches and only pre-match odds. The no-leakage tests from the spec go in here, before any model exists, along with a trivial "home win 45%" model to exercise the harness. | Leakage tests pass; the trivial model produces a full 9-season backtest |
| 2.3 | Elo baseline | S | Elo ratings, and an ordered-logit mapping to 1X2. | Elo backtest stored; clearly beats the trivial model |
| 2.4 | Dixon–Coles fundamentals | L | Time-weighted maximum-likelihood fit, score matrix, derived markets (1X2, xG, O/U 2.5, BTTS). Promoted-team priors. | Recovers known parameters on synthetic data; fundamentals RPS beats Elo in the backtest |
| 2.5 | Headline blend and score solver | M | Multinomial logistic blend of fundamentals, consensus and Elo; the λ/μ solver that makes the score matrix agree with the headline 1X2; fallback to fundamentals when no odds exist. | Headline RPS at least matches the consensus over 2017/18–2025/26; solver within 0.5 points |
| 2.6 | Tuning and first model version | S | Tune decay half-life and blend weights via the backtest; record them as the first row in `model_versions`, with the backtest results and the promotion rule. | Spec's success-criteria table filled in with real numbers |
| 2.7 | `PredictWorkflow` and `ScoreWorkflow` | M | Daily predictions for fixtures in the next 14 days; scoring after full time; both on Temporal Schedules after ingest. Daily `pg_dump` backup of the database. | Predictions appear before kick-off and are scored after; one week runs end to end |

**Checkpoint (spec rollout plan):** run silently for at least 4 gameweeks, about a month. Compare live RPS with the backtest. Go on only if they're in line; if not, find out why first.

## Milestone 3: UI and agent (spec phase 3)

Steps 3.1–3.3 and 3.4 can be built in parallel once 3.1's views exist.

| # | PR | Size | Contents | Done when |
|---|---|---|---|---|
| 3.1 | Views and fixtures page | M | `v_upcoming_predictions`, `v_match_detail`, `v_model_performance` (with `security_invoker`); fixtures page as JSON forms under `frontend/forms/football/`. | Fixtures page shows the next 14 days with edge badges |
| 3.2 | Match detail page | M | Probability bars (headline and fundamentals), scoreline heat map, goal markets, odds table, form, strengths, head-to-head. | All match-page acceptance criteria in the spec pass |
| 3.3 | Performance page | M | RPS over time and calibration, for all layers and sources, filterable by season and model version. | Numbers match `v_model_performance` |
| 3.4 | Agent tools and `match_analyst` | M | The six read-only tools in `temporal/src/agent/tools.py`; `temporal/agents/match_analyst.yaml` on `claude-sonnet-5-5`. | Config loads under `AGENT_PROVIDER=fake`; a real run explains a fixture citing only tool figures |
| 3.5 | "Explain this prediction" | S | Button on the match page that starts a `match_analyst` run for that match. | 20-explanation faithfulness spot check passes |

## Dependencies between steps

```
0.1 ─┬─ 0.2
     └─ 1.1 ─ 1.2 ─ 1.3 ─ 1.4 ─ 2.1 ─ 2.2 ─ 2.3 ─ 2.4 ─ 2.5 ─ 2.6 ─ 2.7 ─ [4 gameweeks]
                                                                   │
                                                                   └─ 3.1 ─┬─ 3.2 ─ 3.5
                                                                           ├─ 3.3
                                                                           └─ 3.4 ─ 3.5
```

UI work can start right after 2.7, while the 4-gameweek silent run is still going. The checkpoint gates **trusting** the numbers, not building the screens.

## Decisions

- [x] **Owner:** @kwahalf (the spec's `Owner` field is updated in PR 0.1).
- [x] **Reviews:** @kwahalf reviews and merges every PR; `main` is protected. Protection requires a
      pull request with 0 required approvals, because GitHub doesn't let an author approve their own
      PR and these PRs are opened from the `kwahalf` account.
- [x] **Hosting:** the stack runs on the current self-hosted runner. See "Hosting on the runner" below.
- [x] **Workflows:** all 14 template workflows are kept for now; pruning is deferred. Until their
      secrets exist (Azure registry, Kubernetes, Copilot and Azure OpenAI tokens), the deploy,
      monitor, e2e, enrichment and `pipeline-*` workflows are expected to fail, and only
      `pr-validation` and `architecture-audit` are meaningful. A red run from one of the others is
      not a blocker for merging. Revisit if the failure noise or Actions minutes become a problem.

## Hosting on the runner

The runner is a shared organization machine, so a few things need care. They're folded into the
steps where they first matter:

- **Run from a stable checkout, not a session folder.** Session workspaces aren't guaranteed to
  last. Before the first schedule goes live (step 1.3), the stack moves to a long-lived clone of the
  repo outside the session directories, started with `make up`.
- **Start on reboot.** Add a systemd unit (or similar) that runs the stack at boot, in step 1.3, so
  the daily schedules survive a restart.
- **Keep ports private.** The Supabase CLI listens on all network interfaces by default (API,
  Postgres, Studio and others). Bind them to localhost in `supabase/config.toml` in PR 0.1, and reach
  the app through an SSH tunnel.
- **Back up the database.** Daily `pg_dump` from step 2.7 on. From then on the stored predictions are
  the model's live track record and can't be recreated after the fact.
- **The Temporal UI is on port 8081, not 8080,** because the runner's own process uses 8080
  (`TEMPORAL_UI_PORT=8081 make up`).

## Not in this plan

Phase 4 from the spec (second league, paid team-news API, richer models) gets its own plan once milestone 3 has shipped and the live track record shows whether the model is worth extending.
