# Live stack on the runner

How the **live** stack runs on the self-hosted runner, how to set it up the first time, how to move it
to a new release, and how to run the football ingest (plan step 1.3). The rules behind this are in
the plan, "Hosting on the runner": live runs **tagged releases** from a **long-lived checkout**, is
**started at boot**, and is **never reset**.

| What | Where |
|---|---|
| Live checkout | `/home/ubuntu/ai-league-predictor-live` (outside Claude session folders), detached at a release tag |
| Start at boot | `ai-league-predictor-live.service`: `make up STACK=live`, then the ingest schedule |
| Firewall at boot | `ai-league-predictor-firewall.service`, before Docker: `restrict-supabase-ports.sh apply` (root-owned copy in `/usr/local/sbin`) |
| Daily ingest | Temporal Schedule `football-ingest-daily`, 06:00 UTC, in the live Temporal (UI on port 8181) |
| Raw CSVs | Supabase Storage bucket `football-raw` (private), `E0/<season>/<sha256>.csv` and `fixtures/<sha256>.csv` |
| Ports | `stacks/live.env` (API 55321, Postgres 55322, Studio 55323, Temporal UI 8181, frontend 3100) |

Scripts, all in `scripts/runner/`:

| Script | Runs as | Does |
|---|---|---|
| `live-deploy.sh <tag>` | ubuntu, docker group | Clones the live checkout if missing, checks out `<tag>`, rebuilds the live images. Starts nothing |
| `install-live-units.sh` | root (`sudo`) | Installs and enables the two systemd units. Starts nothing |
| `ingest-schedule.sh [dev\|live]` | ubuntu, docker group | Creates the daily schedule, or updates it in place. Safe to re-run |
| `restrict-supabase-ports.sh` | root | Blocks outside access to the Supabase ports (from PR 0.1) |

In this session the docker group isn't active in the shell, so docker commands are wrapped in
`sg docker -c '...'`.

## First-time setup (after PR 1.3 merges)

Until now live ran from a session folder (`.../_sessions/.../ai-league-predictor-main`). These
steps move it to the long-lived checkout without losing data: both checkouts use the same Supabase
project ID and Compose project name (`stacks/live.env`), so the new checkout reuses the same Docker
volumes.

1. **Tag the release** (@kwahalf's call). On `main` after the merge, for example:
   `git tag -a v0.2 -m "Ingest workflow (1.3)" <merge-commit> && git push origin v0.2`
2. **Prepare the checkout and images** (any checkout that has this script):
   ```bash
   sg docker -c 'scripts/runner/live-deploy.sh v0.2'
   ```
   A private repo needs git credentials for `ubuntu` first (for example `gh auth setup-git`).
3. **Copy the secrets file** from the current live checkout. Keep it owner-only:
   ```bash
   install -m 600 <old-live-checkout>/.env /home/ubuntu/ai-league-predictor-live/.env
   ```
4. **Back up live**, from the old checkout: `sg docker -c 'make backup STACK=live'`.
5. **Stop live in the old checkout** (data is kept; `down` never deletes volumes):
   ```bash
   cd <old-live-checkout> && sg docker -c 'make down STACK=live'
   ```
6. **Start live from the new checkout and apply the new migrations** (`make migrate` backs up
   first, then applies only migrations live doesn't have yet: `…_football_core.sql` and
   `…_football_ingest.sql` if they're new to live):
   ```bash
   cd /home/ubuntu/ai-league-predictor-live
   sg docker -c 'make up STACK=live'
   sg docker -c 'make migrate STACK=live'
   ```
   Never run `make reset` here: it refuses on live anyway.
7. **Install the boot units and start them** (`start` on the live unit is a no-op `make up` plus
   the schedule):
   ```bash
   sudo /home/ubuntu/ai-league-predictor-live/scripts/runner/install-live-units.sh
   sudo systemctl start ai-league-predictor-firewall.service ai-league-predictor-live.service
   systemctl status ai-league-predictor-firewall.service ai-league-predictor-live.service --no-pager
   sudo /usr/local/sbin/ai-league-predictor-restrict-ports status
   ```
8. **Backfill** 2016/17 to the current season, then `fixtures.csv` (takes under a minute):
   ```bash
   cd /home/ubuntu/ai-league-predictor-live
   sg docker -c 'exports="$(./scripts/stack-env.sh live)" && eval "$exports" && docker compose exec -T temporal-worker python -m src.football.ingest.cli backfill'
   ```
   It prints one line per file and a totals line. Expect 380 matches for each season 1617 to 2526,
   the current season's played matches, and `prices_dropped: 2` (2021/22, see below).
9. **Check** (see "Checking the data" for the SQL): match counts per season, the schedule in the
   Temporal UI (http://localhost:8181, Schedules, `football-ingest-daily`, next run 06:00 UTC).
10. Optionally reboot the runner at a quiet time and check that live comes back and
    `football-ingest-daily` is still there.

The old session checkout can be left alone or deleted later; nothing points at it any more.

## Moving live to a new release

```bash
cd /home/ubuntu/ai-league-predictor-live
sg docker -c 'make backup STACK=live'
sg docker -c 'scripts/runner/live-deploy.sh v0.3'              # checkout + rebuild images
sg docker -c 'make migrate STACK=live'                         # only if the release adds migrations
sg docker -c 'make up STACK=live'                              # recreates containers whose image changed
sg docker -c 'scripts/runner/ingest-schedule.sh live'          # picks up schedule changes
```

If a release changes `scripts/runner/systemd/*` or `restrict-supabase-ports.sh`, re-run
`sudo scripts/runner/install-live-units.sh` from the live checkout. To roll back code, run
`live-deploy.sh` with the previous tag and `make up STACK=live`; migrations are not rolled back, so a
release whose migration must be undone needs its own fix-forward migration.

## Running the ingest

All commands run inside the worker container of the stack you mean. Load that stack's settings first
(`exports="$(./scripts/stack-env.sh dev)" && eval "$exports"`, or `live`) so `docker compose` picks
the right containers. Keep the separate `exports=` assignment: a bare `eval "$(…)"` succeeds on empty
output, and compose would then act on whatever stack the shell already had.

| Command | Does |
|---|---|
| `python -m src.football.ingest.cli backfill` | Every season from 1617 to the current one, then `fixtures.csv`. Options: `--from-season`, `--to-season`, `--no-fixtures` |
| `python -m src.football.ingest.cli run [--season 2627]` | One season (default: current), then `fixtures.csv` |
| `python -m src.football.ingest.cli schedule` | Creates `football-ingest-daily`, or updates it in place. Never creates a second one |

For example, on dev: `sg docker -c 'exports="$(./scripts/stack-env.sh dev)" && eval "$exports" && docker compose exec -T temporal-worker python -m src.football.ingest.cli backfill'`.

The scheduled run can also be started by hand from the Temporal UI: Schedules,
`football-ingest-daily`, **Trigger**. Each run is an `IngestWorkflow` with its own history.

What a run does, per file: download from football-data.co.uk, store the raw bytes in the
`football-raw` bucket (named by SHA-256, so an unchanged file stores nothing new), parse, then one
database call that writes only rows whose values changed. Running it again on unchanged files writes
nothing to the data tables: row counts and every `updated_at` stay the same (only the bookkeeping
row in `football_ingest_state` records the newer download time).

- Season files go first, then `fixtures.csv`, so a match that has a result never turns back into a
  fixture. A fixture that later appears with a result becomes `finished` on the same row.
- Live odds (`snapshot = 'live'`) are stored only for matches that are still scheduled and haven't
  kicked off at download time. `captured_at` is the download time, and moves only when a price changes.
- The file is the truth for the odds of the matches it covers: a stored quote the file no longer has
  (absent, or nothing left after cleaning) is deleted (`odds_deleted`), and so is a match's
  `match_stats` row when a season file leaves all its statistics blank (`stats_deleted`).
- Overlapping runs (a manual backfill during the scheduled run) are safe: loads are serialised per
  league, and a download older than the last one loaded for the same file is skipped
  (`stale_skipped`, recorded in `football_ingest_state`), so older prices never overwrite newer ones.
- Matches before 2019/20 have no kick-off time in the source; they're stored at 00:00 UTC on the
  match date.
- **Prices of 1.0 or less are stored as missing** (owner decision, 2026-11-11), and if that leaves a
  1X2 or over/under set incomplete, the whole set is stored as missing. Each run logs how many
  (`prices_dropped`, `price_sets_nulled`). In the 2026-11-11 data only 2021/22 has any: Bet365's closing
  over/under prices of `0` for Newcastle v Man City on 19/12/2021.

Failures: a download or database error is retried with back-off (the source site sometimes answers
503 for a while). Bad data (an unknown team name, an unrecognised file layout, a fixture listed
twice) fails the workflow at once, visibly in the Temporal UI, and nothing from that file is written.
An unknown team name means a new spelling: add it to `TEAM_ALIASES` in
`temporal/src/football/data/teams.py`.

Logs: `make logs-temporal STACK=live`; each file logs one `football ingest loaded …` line, plus a
`football ingest dropped prices<=1.0 …` line when it dropped any.

## When the quality check fails

Every `IngestWorkflow` run ends with the data-quality check (plan step 1.4, activity
`football_quality_check`), which looks at the whole league, not just the files of that run. The
**current season** is the one running on the run's date, rolling over on 1 July (on 2026-11-11 it is
2627); every earlier season is **completed**.

| Check | Passes when |
|---|---|
| `completed_seasons` | Every completed season (1617 up to last season, plus any other stored season before the current one) has 380 matches, 20 teams, 19 home and 19 away matches per team, every home/away pair once, and every match finished |
| `current_season` | The current season has at most 20 teams and no team with more than 19 home or 19 away matches |
| `duplicate_fixtures` | No two matches share season, home and away team, and no team plays twice on the same UK date, across all seasons |
| `scores_match_status` | Only finished matches have scores, and every finished match has both |
| `odds_above_one` | No stored price is 1.0 or less |
| `odds_margins` | Every complete 1X2 set and every complete over/under 2.5 set has a margin (Σ 1/price − 1) between 0% and 15%, apart from the known source errors listed in `KNOWN_MARGIN_EXCEPTIONS` (`temporal/src/football/quality/checks.py`) |

When a check fails:

- The run fails in the Temporal UI (Workflows, `football-ingest-daily` or `football-ingest-<season>`)
  with a `DataQualityError` whose message names each failed check and its first 10 offending rows.
  It is not retried: bad data doesn't improve on retry. The worker log has the same message on a
  `football quality FAILED` line (`make logs-temporal STACK=live`).
- **The data that run loaded stays loaded.** The check runs after the loads and only reads.
- **The schedule carries on.** `football-ingest-daily` is not paused by a failed run, so the next
  day's run loads and checks again; it keeps failing until the data is fixed.

What to do:

1. Read the message and look at the rows it names (SQL in "Checking the data" below).
2. Compare with the source file, kept in the `football-raw` bucket and on football-data.co.uk.
   - **Our bug** (a wrong team alias, a parsing or loading mistake): fix it in a PR, release, and
     re-run the ingest for the affected seasons (`cli run --season <season>`).
   - **The source is wrong and later corrected**: re-run that season once the file is fixed.
   - **The source is wrong and stays wrong** (for example one bookmaker's price in an old season):
     the fix is a decision for the owner. Either add it to `KNOWN_MARGIN_EXCEPTIONS` in a PR
     (matched on its exact prices, so a later change is checked again), or correct the stored row
     with a fix-forward migration. Don't widen the limits to make one row pass.
   - **A completed season is short or has unplayed matches** (an abandoned match, a file not yet
     final): check the source; if it's genuinely incomplete, it's the owner's call.
3. Once fixed, start a run by hand (Temporal UI, Schedules, `football-ingest-daily`, **Trigger**) and
   check that it completes.

Known source error, acknowledged (2026-11-11): Bet365's pre-match 1X2 prices for Aston Villa v
Arsenal on 18/02/2023 are 4.50/3.60/1.50, a 16.67% margin; every other book had Arsenal at 1.78-1.95.
The worker logs it on each run as `football quality acknowledged known source error`.

## Checking the data

```bash
psql "postgresql://postgres:postgres@127.0.0.1:55322/postgres"     # live; dev is 54322
```

```sql
-- Matches per season and status: 380 finished for each of 1617..2526.
select season, status, count(*) from matches group by 1, 2 order by 1, 2;
-- Upcoming fixtures with live odds.
select m.kickoff_at, th.name, ta.name, b.code, o.home, o.draw, o.away, o.captured_at
from odds o join matches m on m.id = o.match_id
join teams th on th.id = m.home_team_id join teams ta on ta.id = m.away_team_id
join bookmakers b on b.id = o.bookmaker_id
where o.snapshot = 'live' and m.status = 'scheduled' order by m.kickoff_at;
-- Raw files kept.
select name, created_at from storage.objects where bucket_id = 'football-raw' order by created_at;
```

## Notes

- Docker restarts the stacks' containers itself when it starts (`restart: unless-stopped`), so the
  firewall unit runs **before** Docker: it creates Docker's `DOCKER-USER` chain and fills it, and
  Docker keeps an existing chain's rules. If the firewall unit fails, Docker still starts (other
  workloads on the runner need it) but the live unit doesn't; check
  `systemctl status ai-league-predictor-firewall.service` after a reboot.
- The firewall rules cover both stacks' Supabase ports, so they also protect dev. Stopping the unit
  leaves them in place on purpose; remove them only deliberately, with
  `sudo /usr/local/sbin/ai-league-predictor-restrict-ports remove`.
- Live is never reset. Back it up before every migration (`make migrate` does this itself).
