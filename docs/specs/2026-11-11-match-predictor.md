# Match Predictor Specification

**Status:** Draft
**Owner:** @kwahalf
**Created:** 2026-11-11
**Last Updated:** 2026-11-11

## Overview

An internal tool that predicts the outcome of upcoming English Premier League matches. For every
fixture it produces home/draw/away probabilities, a scoreline distribution and goal markets, compares
them with three sources of bookmaker odds, and lets a user ask the agent why the model thinks what it
thinks.

It runs on this template's stack without changing the framework: Temporal schedules fetch data and
run the models, Supabase stores matches, odds and predictions, the JSON-driven frontend shows them,
and the existing agent loop explains them through new read-only tools.

## Decisions

| Question | Decision |
|---|---|
| League | English Premier League (football-data.co.uk code `E0`) |
| Bookmakers | Bet365 (`B365`), Bwin (`BW`) and the market average (`BbAv` before 2019/20, `Avg` from 2019/20), plus their consensus. These are the only sources present in all 11 seasons **and** in the live `fixtures.csv`, so the model sees the same inputs in training and in production. Pinnacle (`PS`) and William Hill (`WH`) were considered and rejected: Pinnacle is missing from 2026/27 and `fixtures.csv`, and William Hill stops after 2024/25. Pinnacle is kept as an evaluation benchmark only (2016/17 to 2025/26) |
| Odds as a model input | Yes. Pre-match odds feed the headline prediction (see "Prediction model") |
| History | 10 completed seasons (2016/17 to 2025/26), plus the current season 2026/27 |

## Goals

- Predict every upcoming EPL fixture with calibrated probabilities:
  - 1X2: home win / draw / away win
  - Expected goals for each side and the most likely scorelines
  - Over/under 2.5 goals and both teams to score (BTTS)
- Show the implied probabilities from each of the three odds sources next to the model's, and flag where they
  disagree.
- Measure the models honestly: every prediction is stored before kick-off and scored after full time.
- Explain any prediction in plain language through the agent, using only data the tools return.
- Need no paid data: everything comes from the free football-data.co.uk CSVs.

## Non-Goals

- **Live or in-play predictions.** The CSVs update about twice a week, not during matches.
- **Lineups, injuries and suspensions.** The free data doesn't have them, so the agent may not claim
  knowledge of them. The odds input carries some of this information indirectly, because bookmakers
  price it in. A paid API for team news is a candidate for phase 4.
- **Placing bets, staking advice or bankroll management.** The tool reports probabilities and
  disagreements with the market. It does not tell anyone to bet.
- **Public users, sign-up or billing.** This is for us and our team only.
- **Other leagues or cups in v1.** The schema has a `leagues` table so more can be added later,
  but only the EPL is loaded and validated.

## User Stories

### As a user, I want to see predictions for the next round of fixtures so that I know what the model expects

**Acceptance Criteria:**
- [ ] The fixtures page lists every upcoming EPL match in the next 14 days, by kick-off time.
- [ ] Each row shows home/draw/away %, expected goals for each side and the single most likely score.
- [ ] Each row shows when the prediction was made, which model version made it, and when the odds it
      used were captured.

### As a user, I want to open a match and see the full picture so that I understand the prediction

**Acceptance Criteria:**
- [ ] The match page shows a scoreline heat map (0–5 goals each side) with probabilities.
- [ ] It shows over/under 2.5 and BTTS probabilities.
- [ ] It shows each team's last 5 results, current Elo rating and model attack/defence strengths.
- [ ] It shows the last 5 head-to-head results.
- [ ] It shows the odds-free "fundamentals" 1X2 next to the headline prediction, so the user can see
      how much the market moved the forecast.

### As a user, I want to compare the model with the bookmakers so that I can see where they disagree

**Acceptance Criteria:**
- [ ] For each fixture, the page shows the margin-free probabilities from Bet365, Bwin and the
      market average separately, and their consensus (the average of the three).
- [ ] Where the **fundamentals** model is at least 5 percentage points away from the consensus on an
      outcome, that outcome is highlighted, together with the model's implied "fair" odds. The
      headline prediction isn't used here because it already contains the market's view.
- [ ] If a source has no odds for a match, it is shown as missing and the consensus uses the
      sources that do.

### As a user, I want to ask the agent about a match so that I get a plain-language explanation

**Acceptance Criteria:**
- [ ] From a match page, "Explain this prediction" starts an agent run for that match.
- [ ] The answer cites only figures returned by tools (form, ratings, head-to-head, odds, market comparison).
- [ ] The answer separates what the team data says (fundamentals) from what the market adds.
- [ ] If the user asks about something the data doesn't cover (injuries, weather), the agent says it
      doesn't know instead of guessing.

### As a user, I want to know whether the model is any good so that I know how much to trust it

**Acceptance Criteria:**
- [ ] A performance page shows, for completed matches: ranked probability score (RPS), log loss,
      Brier score and calibration (predicted vs actual frequency in 10% buckets).
- [ ] The same metrics are shown side by side for: the headline model, the fundamentals model, each
      of the three odds sources, the consensus, Pinnacle closing odds (seasons where available), and
      the Elo baseline.
- [ ] Metrics can be filtered by season and by model version.

## Technical Design

### Architecture

```
football-data.co.uk ──CSV──▶ Temporal worker (Python)
                              │  IngestWorkflow   (schedule: daily 06:00 UTC)
                              │  PredictWorkflow  (after ingest, and on demand)
                              │  ScoreWorkflow    (after ingest: grade finished matches)
                              ▼
                         Supabase (Postgres)
                              │  matches, odds, team_ratings, predictions, prediction_scores
                              ▼
       Frontend (JSON-driven UI)  ◀──── PostgREST, read-only views
                              │
                              └──▶ Agent API ──▶ AgentWorkflow ──▶ read-only football tools ──▶ Supabase
```

- **Ingestion**: one activity downloads the season CSVs
  (`https://www.football-data.co.uk/mmz4281/{season}/E0.csv`, seasons `1617` to `2627`) and the
  upcoming fixtures file (`fixtures.csv`, filtered to `Div = E0`). It normalises team names and
  upserts matches and odds. It's idempotent: re-running it changes nothing if the source hasn't changed.
- **Modelling** runs in the worker, which is already Python. New dependencies: `numpy`, `scipy`,
  `pandas`, `scikit-learn`.
- **Scheduling** uses Temporal Schedules, not cron. That gives retries, history and a manual
  "run now" from the Temporal UI for free.
- The **frontend** reads from views through PostgREST with the anon key. Only the worker writes,
  using the service-role key, as the template already does.

### Odds data

The CSVs carry, per match and odds source, 1X2 odds and sometimes over/under 2.5 odds, in two
snapshots:
- **Pre-match odds** (for example `B365H`, `B365D`, `B365A`), collected a day or two before kick-off.
- **Closing odds** (for example `B365CH`, `B365CD`, `B365CA`), the last price before kick-off.

Coverage, checked against the E0 files on 2026-11-11:

| Source | 1X2 pre-match | 1X2 closing | O/U 2.5 pre-match | O/U 2.5 closing | In `fixtures.csv` |
|---|---|---|---|---|---|
| Bet365 `B365` | 2016/17 → now | 2019/20 → now | 2019/20 → now | 2019/20 → now | Yes |
| Bwin `BW` | 2016/17 → now | 2019/20 → now | Never | Never | Yes |
| Market average `BbAv` / `Avg` | 2016/17 → now (`BbAv` to 2018/19, `Avg` after) | 2019/20 → now | 2016/17 → now (`BbAv>2.5`, then `Avg>2.5`) | 2019/20 → now | Yes |
| Pinnacle `PS` (benchmark only) | 2016/17 → 2025/26 | 2016/17 → 2025/26 | 2019/20 → 2025/26 | 2019/20 → 2025/26 | No |

Consequences:
- The **over/under input** to the scoreline solver comes from the market average, the only source
  with over/under in every season.
- Source column names change between seasons (`BbAv` → `Avg`). The ingester maps each season's
  columns to the canonical `odds` table, stores missing values as null and doesn't fail. An unknown
  file layout (no recognisable 1X2 columns at all) does fail loudly.

The rule that keeps the backtest honest: **models may only use odds that existed when the prediction
was made.**
- Live predictions use the latest odds from `fixtures.csv`, stored with `captured_at`.
- The backtest uses **pre-match** odds only, never closing odds.
- Closing odds are stored but used only for evaluation: they're the toughest benchmark, and checking
  whether the model agrees with where the price moved after the prediction is a useful signal.

**Removing the margin:** a bookmaker's odds turn into probabilities `1/odds` that add up to more than
1. They're normalised to sum to 1 (proportional method). The **consensus** is the average of the
margin-free probabilities from the bookmakers that priced the match.

### Prediction model

The pipeline has three layers. Every layer is stored, so the agent and the performance page can show
each one.

**1. Elo baseline.** K ≈ 20 with home advantage, mapped to 1X2 with an ordered-logit fit. It's cheap,
easy to explain, gives the agent its "team strength" numbers, and is the floor every other model must
beat.

**2. Fundamentals model (no odds): Dixon–Coles bivariate Poisson.**
- Each team gets an attack strength and a defence strength, plus a league-wide home advantage.
  Expected goals are `λ_home = exp(home_adv + attack_home − defence_away)` and
  `μ_away = exp(attack_away − defence_home)`.
- The Dixon–Coles correction adjusts the 0-0, 1-0, 0-1 and 1-1 probabilities, which plain
  independent Poisson gets wrong.
- Matches are time-weighted (exponential decay, half-life about 180 days, tuned in the backtest).
- Fit by maximum likelihood (`scipy.optimize`) over the last 3 seasons available at prediction time.
- This is the model used to find disagreements with the market, because it doesn't see the odds.

**3. Headline model (with odds): blend of fundamentals and market.**
- A multinomial logistic regression combines the log-probabilities of the fundamentals model, the
  consensus market probabilities, and Elo difference into final 1X2 probabilities.
- It's trained walk-forward on the same history (see "Backtest"), so its weights reflect how much the
  market and the fundamentals each deserve to be trusted.
- If a match has no odds yet, the headline falls back to the fundamentals model and is marked that way.

**Scorelines that agree with the headline 1X2.** The blend outputs 1X2 only. To get scores, goals and
BTTS that are consistent with it, the worker solves for the pair of expected-goals values (λ, μ) whose
Dixon–Coles score matrix best matches the headline 1X2 and, when available, the consensus over/under
2.5 price (least squares). That matrix then gives xG, most likely scores, over/under and BTTS, so no
output can contradict another.

**Backtest (walk-forward).** For each week of the 10 seasons after a warm-up season: refit using only
matches before that week, predict that week using pre-match odds, then move on. This is how all three
layers' weights and the decay half-life are tuned.

**Promotion rule:** a new model version replaces the current one only if its headline RPS beats the
current version's over the last 2 seasons of the backtest, and its fundamentals RPS doesn't get worse.

### Data Model

The template's generic `entities` / `entity_facts` model suits flexible business records. Matches,
odds and predictions are fixed-shape, high-volume and joined constantly, so they get their own tables in
a new migration, as `AGENTS.md` prescribes for domain tables
(`supabase/migrations/2026111109xxxx_football_core.sql`). This choice deserves an ADR.

| Table | Key columns | Notes |
|---|---|---|
| `leagues` | `id`, `code` (`E0`), `name`, `country` | One row in v1 |
| `teams` | `id`, `league_id`, `name`, `aliases text[]` | Aliases map spellings across sources |
| `matches` | `id`, `league_id`, `season` (`2627`), `kickoff_at`, `home_team_id`, `away_team_id`, `home_goals`, `away_goals`, `status` (`scheduled`/`finished`/`postponed`), `source_key` | Unique `(league_id, season, home_team_id, away_team_id)` |
| `match_stats` | `match_id`, shots, shots on target, corners, cards for each side | From the CSV, for the agent and future features |
| `bookmakers` | `id`, `code` (`B365`, `BW`, `AVG`, `PS`), `name`, `is_model_input boolean` | Four rows in v1. `AVG` is the market average; `PS` (Pinnacle) is a benchmark with `is_model_input = false` |
| `odds` | `match_id`, `bookmaker_id`, `snapshot` (`pre_match`/`closing`/`live`), `home`, `draw`, `away`, `over_2_5`, `under_2_5`, `captured_at` | Unique `(match_id, bookmaker_id, snapshot)`. Prices must be > 1.0 |
| `model_versions` | `id`, `name`, `params jsonb`, `blend_weights jsonb`, `trained_through`, `backtest jsonb`, `is_current` | Exactly one current version, enforced by a partial unique index |
| `team_ratings` | `team_id`, `model_version_id`, `as_of`, `attack`, `defence`, `elo` | Snapshot per prediction run |
| `predictions` | `id`, `match_id`, `model_version_id`, `created_at`, `odds_captured_at`, headline `p_home`/`p_draw`/`p_away`, fundamentals `f_home`/`f_draw`/`f_away`, Elo `e_home`/`e_draw`/`e_away`, `xg_home`, `xg_away`, `p_over_2_5`, `p_btts`, `top_scores jsonb`, `score_matrix jsonb`, `used_odds boolean` | Append-only. Must be created before `kickoff_at`, enforced by a check trigger |
| `prediction_scores` | `prediction_id`, `layer` (`headline`/`fundamentals`/`elo`), `rps`, `log_loss`, `brier`, `scored_at` | Written by `ScoreWorkflow` after full time |
| `market_scores` | `match_id`, `bookmaker_id` (null for consensus), `snapshot`, `rps`, `log_loss`, `brier` | The bookmakers' own track record, for comparison |

**Views for the frontend:** `v_upcoming_predictions` (latest prediction per scheduled match, with each
bookmaker's and the consensus probabilities, and the fundamentals edge), `v_match_detail`,
`v_model_performance`.

**Row-level security (RLS):** the anon role may `select` the views only. All writes go through the
service role from the worker. Views use `security_invoker = true`, which the repo's
`scripts/audit/check_view_security_invoker.py` already checks.

### API Design

There's no new HTTP service. The frontend reads views through Supabase's existing API (PostgREST).
The agent reuses the existing agent API, and only new tools and a new agent config are added.

**Agent: `temporal/agents/match_analyst.yaml`**, model `claude-sonnet-5-5`. All tools are read-only,
so nothing needs approval.

| Tool | Returns |
|---|---|
| `get_match_prediction(match_id)` | Headline, fundamentals and Elo 1X2; xG, top scores, O/U, BTTS; model version; whether odds were used |
| `get_team_form(team_id, last_n=5)` | Results, goals for/against, Elo trend |
| `get_head_to_head(home_id, away_id, last_n=5)` | Past meetings and scores |
| `get_team_strengths(team_id)` | Attack/defence ratings and league rank of each |
| `compare_to_market(match_id)` | Each bookmaker's and the consensus margin-free 1X2, fundamentals edge per outcome |
| `get_model_performance(season?)` | RPS/log loss/Brier for every layer, each bookmaker and the consensus |

**Workflows (new, registered in the worker):** `IngestWorkflow(season)`, `PredictWorkflow(horizon_days=14)`,
`ScoreWorkflow()`, `BacktestWorkflow(model_params, seasons)`.

### UI/UX Design

Built as JSON form definitions under `frontend/forms/football/`, rendered by the existing UI engine:

1. **Fixtures**: table of upcoming matches. Columns: kick-off, home, away, headline H/D/A % as a
   stacked bar, xG, most likely score, and an edge badge if the fundamentals model disagrees with the
   consensus by 5 points or more.
2. **Match detail**: headline probability bar with the fundamentals bar beneath it, scoreline heat map,
   goal markets, a table of Bet365 / Bwin / market average / consensus vs fundamentals vs headline,
   both teams' form and strengths, head-to-head, and an "Explain this prediction" button that opens the
   agent panel with the match pre-filled.
3. **Performance**: RPS over time for headline, fundamentals, each bookmaker, consensus and Elo, plus a
   calibration chart, filterable by season and model version.

## Implementation Plan

### Phase 1: Data foundation
- [ ] Migration: football tables, indexes, RLS, views, seed rows for the EPL and the four odds sources
- [ ] Team-name normaliser with alias table, plus a test that every team in all 11 season files resolves
- [ ] `IngestWorkflow`: backfill 2016/17 to 2026/27, then a daily schedule that also reads `fixtures.csv`
- [ ] Odds column mapper that handles each season's available columns and the pre-match/closing split
- [ ] Data-quality checks: 380 matches per completed season, no duplicate fixtures, scores only on
      finished matches, odds > 1.0, margin between 0% and 15%

### Phase 2: Models and backtest
- [ ] Elo baseline
- [ ] Dixon–Coles fundamentals model, score matrix, derived markets
- [ ] Margin removal and consensus
- [ ] Headline blend and the λ/μ solver that makes the score matrix match it
- [ ] Walk-forward `BacktestWorkflow` (2016/17 as warm-up, then weekly over 2017/18 to 2025/26)
- [ ] Tune decay half-life and blend weights, and record the backtest in `model_versions.backtest`
- [ ] `PredictWorkflow` and `ScoreWorkflow` on schedule, including `market_scores`

### Phase 3: UI and agent
- [ ] Fixtures, match detail and performance pages
- [ ] Six read-only agent tools plus `match_analyst.yaml`
- [ ] "Explain this prediction" entry point from the match page

### Phase 4 (later, optional)
- [ ] Second league
- [ ] Paid API for lineups and injuries, fed to the agent as context and later as model features
- [ ] Richer fundamentals: shot-based xG, or gradient boosting over rating differences

## Testing Strategy

- **Unit tests** (pytest, run in the worker image like the existing suite):
  - Every score matrix sums to 1 (±1e-9).
  - 1X2 and O/U derived from the matrix agree with each other.
  - Margin-free probabilities from each bookmaker sum to 1, and the consensus ignores a missing bookmaker.
  - The λ/μ solver reproduces a target 1X2 within 0.5 percentage points.
  - The name normaliser handles known variants ("Man United" / "Manchester Utd").
- **Model tests:** fitting Dixon–Coles on synthetic data generated from known parameters recovers them
  within tolerance. Home advantage comes out positive on real E0 data.
- **No-leakage tests** (the most important tests in the suite):
  - The backtest never uses a match result on or after the date it's predicting.
  - The backtest never reads closing odds. A test feeds it odds where only the closing columns
    disagree, and checks the predictions don't change.
  - A live prediction's `odds_captured_at` is never after its `created_at`.
- **Integration tests:** ingest fixed CSV fixtures for a pre-2019 season and a post-2019 season (with
  closing odds) into a fresh database (`supabase db reset`) and check row counts. Run predict, then
  score, end to end on a past gameweek.
- **Agent tests:** with `AGENT_PROVIDER=fake`, the `match_analyst` config loads and only names tools
  that exist (the template's registry already enforces this).

## Rollout Plan

Internal only. Merge phase by phase behind no flags, because nothing user-facing exists until phase 3.
After phase 2, run the scheduled predictions "silently" for at least 4 gameweeks and look at live
RPS against the backtest before anyone relies on the numbers.

## Metrics & Success Criteria

Because the headline model sees the odds, it should at least match the market. The fundamentals model
doesn't, so its bar is lower.

| Metric | Target |
|---|---|
| Headline RPS vs consensus pre-match odds (walk-forward, 2017/18 to 2025/26) | Equal or better |
| Headline RPS vs Pinnacle closing odds (2016/17 to 2025/26) | Within about 0.003. Closing Pinnacle is the hardest benchmark there is |
| Fundamentals RPS vs Elo baseline | Clearly better |
| Fundamentals RPS vs consensus pre-match odds | Within about 0.01 |
| Calibration (headline) | Each 10% bucket within ±5 points of observed frequency, given enough matches |
| Freshness | Predictions for all fixtures in the next 14 days exist by 08:00 UTC daily |
| Agent faithfulness | In a spot check of 20 explanations, every figure matches a tool result |

## Dependencies

- football-data.co.uk CSV availability and format (free, unofficial, no service-level agreement)
- Python packages `numpy`, `scipy`, `pandas`, `scikit-learn`, added to `temporal/pyproject.toml`
- The existing LiteLLM proxy and key for the agent (already working with `claude-sonnet-5-5`)

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Closing odds or future results leak into the backtest and make it look better than reality | High | Medium | The no-leakage tests. Backtest reads pre-match odds only. Live-only scoring of predictions stored before kick-off |
| The headline model just copies the market and adds nothing | Medium | High | Expected and acceptable for the headline. The fundamentals model and its edge flags are where independent value shows up, and both are tracked separately |
| Odds in `fixtures.csv` are stale (it updates about twice a week) | Medium | Medium | Store `odds_captured_at` and show it. Fall back to fundamentals when no odds exist |
| A bookmaker drops out of the CSVs or a column is renamed | Medium | Medium | Consensus works with any subset of the three. Schema validation on ingest that fails loudly on unknown formats |
| CSV team names change | Medium | Medium | Alias table and a resolve-every-team test. Ingest is idempotent and re-runnable |
| Source site goes down or blocks the box | Medium | Low | Daily schedule with retries. Keep the raw CSVs in Supabase Storage so a rebuild doesn't need the site |
| Promoted teams have little or no top-flight history | Medium | High (3 a season) | Start promoted teams at a prior below the league average. Their odds carry the market's view from day one |
| Agent invents injuries or news | Medium | Medium | Tools return no such data. The system prompt forbids unsourced facts. Spot-check faithfulness |
| People read the edge flags as betting advice | Medium | Medium | Wording says "fundamentals disagree with market", not "bet". Show the track record next to it |

## Open Questions

- [ ] Margin removal: proportional (proposed) or Shin's method? Shin handles favourite–longshot bias
      better but is more work. Decide in phase 2 from the backtest.
- [ ] Should the consensus weight the three sources equally? The market average already contains
      Bet365 and Bwin among many others. Proposed: equal weights first, then let the backtest decide.
- [ ] Pinnacle's absence from 2026/27 means live predictions can't be benchmarked against it. Is the
      market's closing average a good enough live benchmark, or is a paid Pinnacle feed worth it later?
- [ ] Is a 5-point disagreement the right threshold for edge flags?

## References

- Dixon, M. & Coles, S. (1997), *Modelling Association Football Scores and Inefficiencies in the Football Betting Market*
- Constantinou, A. & Fenton, N. (2012), *Solving the problem of inadequate scoring rules for assessing probabilistic football forecast models* (the RPS)
- Shin, H. S. (1993), *Measuring the Incidence of Insider Trading in a Market for State-Contingent Claims* (margin removal)
- football-data.co.uk: data and `notes.txt` column definitions
- `docs/specs/2026-09-08-agentic-feature.md`: the agent loop the explanations run on
- `Generalisable_schema.md`: why football data gets its own tables rather than `entity_facts`
