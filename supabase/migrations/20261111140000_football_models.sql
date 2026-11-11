-- Football models (plan step 2.1b): model versions, team ratings, predictions, their scores, and
-- the bookmakers' own scores. See docs/specs/2026-11-11-match-predictor.md ("Data Model",
-- "Prediction model", "Metrics").
--
-- Access follows 20261111120000_football_core.sql: RLS on, no anon/authenticated policies,
-- privileges revoked from anon/authenticated; the worker writes with the service role. The
-- frontend will read through security_invoker views (step 3.1).
--
-- Probabilities are double precision in [0, 1]; every 1X2 triple must sum to 1 within 1e-5, the
-- tolerance the metrics library uses (temporal/src/football/metrics/_validation.py).
--
-- Backtest results are NOT stored as predictions: a backtest "predicts" matches that were played
-- long ago, which the before-kick-off trigger on predictions rightly rejects. They live in
-- model_versions.backtest instead. predictions holds only live predictions, made before kick-off.

-- A trained model configuration. Exactly one is current (the one live predictions use): the
-- partial unique index allows at most one row with is_current, and the worker switches versions
-- in one transaction (clear the old flag, then set the new one).
create table if not exists model_versions (
  id uuid primary key default gen_random_uuid(),
  name text not null unique check (btrim(name) <> ''),
  -- Hyper-parameters: decay half-life, Elo K, home advantage, ...
  params jsonb not null default '{}'::jsonb check (jsonb_typeof(params) = 'object'),
  -- Headline blend coefficients (step 2.5); null until a blend is trained.
  blend_weights jsonb check (blend_weights is null or jsonb_typeof(blend_weights) = 'object'),
  -- Date of the last match the version was fitted on.
  trained_through date,
  -- Walk-forward backtest summary (per season and overall, per layer).
  backtest jsonb check (backtest is null or jsonb_typeof(backtest) = 'object'),
  is_current boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index if not exists model_versions_one_current
  on model_versions ((true)) where is_current;

-- Team strengths as a model version saw them at as_of: one snapshot per prediction run.
create table if not exists team_ratings (
  id uuid primary key default gen_random_uuid(),
  team_id uuid not null references teams(id) on delete restrict,
  model_version_id uuid not null references model_versions(id) on delete cascade,
  as_of timestamptz not null,
  -- Dixon-Coles log-strengths (step 2.4) and the Elo rating (step 2.3).
  attack double precision check (attack is null or (attack > '-infinity' and attack < 'infinity')),
  defence double precision check (defence is null or (defence > '-infinity' and defence < 'infinity')),
  elo double precision check (elo is null or (elo > '-infinity' and elo < 'infinity')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (model_version_id, as_of, team_id),
  check (attack is not null or defence is not null or elo is not null)
);
create index if not exists idx_team_ratings_team on team_ratings(team_id, as_of desc);

-- Live predictions. Append-only: a prediction is a record of what the model said at the time, so
-- it is never changed or removed (statement-level triggers below block UPDATE, DELETE and
-- TRUNCATE). A newer prediction for the same match is a new row; the latest one counts.
create table if not exists predictions (
  id uuid primary key default gen_random_uuid(),
  match_id uuid not null references matches(id) on delete restrict,
  model_version_id uuid not null references model_versions(id) on delete restrict,
  -- Set by the database (the trigger below overwrites any value sent), so it can't be backdated.
  created_at timestamptz not null default now(),
  -- captured_at of the odds the headline used; null when there were none (used_odds = false).
  odds_captured_at timestamptz,
  -- Headline (blend of fundamentals and market; falls back to fundamentals without odds).
  p_home double precision not null check (p_home between 0 and 1),
  p_draw double precision not null check (p_draw between 0 and 1),
  p_away double precision not null check (p_away between 0 and 1),
  -- Fundamentals (Dixon-Coles, no odds).
  f_home double precision not null check (f_home between 0 and 1),
  f_draw double precision not null check (f_draw between 0 and 1),
  f_away double precision not null check (f_away between 0 and 1),
  -- Elo baseline.
  e_home double precision not null check (e_home between 0 and 1),
  e_draw double precision not null check (e_draw between 0 and 1),
  e_away double precision not null check (e_away between 0 and 1),
  -- From the score matrix that agrees with the headline 1X2.
  xg_home double precision not null check (xg_home >= 0 and xg_home < 'infinity'),
  xg_away double precision not null check (xg_away >= 0 and xg_away < 'infinity'),
  p_over_2_5 double precision not null check (p_over_2_5 between 0 and 1),
  p_btts double precision not null check (p_btts between 0 and 1),
  -- Most likely scores, e.g. [{"home": 1, "away": 0, "p": 0.12}, ...].
  top_scores jsonb check (top_scores is null or jsonb_typeof(top_scores) = 'array'),
  -- Score probabilities, rows = home goals, columns = away goals.
  score_matrix jsonb check (score_matrix is null or jsonb_typeof(score_matrix) = 'array'),
  used_odds boolean not null,
  check (abs(p_home + p_draw + p_away - 1) <= 1e-5),
  check (abs(f_home + f_draw + f_away - 1) <= 1e-5),
  check (abs(e_home + e_draw + e_away - 1) <= 1e-5),
  check (not used_odds or odds_captured_at is not null),
  check (odds_captured_at is null or odds_captured_at <= created_at)
);
create index if not exists idx_predictions_match on predictions(match_id, created_at desc);
create index if not exists idx_predictions_model_version on predictions(model_version_id);

-- The latest moment a prediction for a match may be made.
--
-- Normally that is kick-off. Season files before 2019/20 have no kick-off time, only the UK date;
-- the ingest then stores 00:00 UTC on that date (temporal/src/football/ingest/payload.py,
-- kickoff_iso). No Premier League match kicks off at 00:00 UTC (01:00 UK in summer, midnight in
-- winter), so a kick-off at exactly 00:00:00 UTC means "date only", and the deadline is the start
-- of that date in the UK (Europe/London): 23:00 UTC the day before in summer, 00:00 UTC in winter.
create or replace function public.football_prediction_deadline(p_kickoff_at timestamptz)
returns timestamptz
language sql
-- stable, not immutable: the result depends on the time zone database.
stable
strict
set search_path = public, pg_temp
as $$
  select case
    when (p_kickoff_at at time zone 'UTC')::time = time '00:00'
      then ((p_kickoff_at at time zone 'UTC')::date)::timestamp at time zone 'Europe/London'
    else p_kickoff_at
  end
$$;

-- Before insert: stamp created_at with the database clock and reject a prediction made at or after
-- the deadline above, or for a match that is not scheduled (finished or postponed).
create or replace function public.football_predictions_before_kickoff()
returns trigger
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_kickoff_at timestamptz;
  v_status text;
  v_deadline timestamptz;
begin
  new.created_at := now();
  select kickoff_at, status into v_kickoff_at, v_status from matches where id = new.match_id;
  if not found then
    return new; -- the foreign key reports the missing match
  end if;
  v_deadline := football_prediction_deadline(v_kickoff_at);
  if new.created_at >= v_deadline then
    raise exception using
      errcode = 'check_violation',
      message = format('prediction for match %s is too late: created_at %s is not before %s',
                       new.match_id, new.created_at, v_deadline);
  end if;
  if v_status <> 'scheduled' then
    raise exception using
      errcode = 'check_violation',
      message = format('prediction for match %s rejected: match is %s, not scheduled', new.match_id, v_status);
  end if;
  return new;
end;
$$;

create or replace function public.football_predictions_append_only()
returns trigger
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
begin
  raise exception using
    errcode = 'insufficient_privilege',
    message = format('predictions are append-only: %s is not allowed', tg_op);
end;
$$;

drop trigger if exists predictions_before_kickoff on predictions;
create trigger predictions_before_kickoff
  before insert on predictions
  for each row execute function football_predictions_before_kickoff();

-- Statement-level, so they fire even when no row matches.
drop trigger if exists predictions_append_only on predictions;
create trigger predictions_append_only
  before update or delete on predictions
  for each statement execute function football_predictions_append_only();

drop trigger if exists predictions_no_truncate on predictions;
create trigger predictions_no_truncate
  before truncate on predictions
  for each statement execute function football_predictions_append_only();

-- Scores of a prediction once the match is finished: one row per layer. Written by ScoreWorkflow
-- (step 2.7). RPS in [0, 1], log loss >= 0 (the metrics clip at 1e-15, so at most ~34.54),
-- Brier (summed over outcomes) in [0, 2].
create table if not exists prediction_scores (
  prediction_id uuid not null references predictions(id) on delete restrict,
  layer text not null check (layer in ('headline', 'fundamentals', 'elo')),
  rps double precision not null check (rps between 0 and 1),
  log_loss double precision not null check (log_loss >= 0 and log_loss < 'infinity'),
  brier double precision not null check (brier between 0 and 2),
  scored_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (prediction_id, layer)
);

-- The bookmakers' own track record: each source's margin-free 1X2 from one odds snapshot, scored
-- against the result. bookmaker_id null is the consensus of the model-input sources (B365, BW,
-- AVG), defined as in the backtest (temporal/src/football/backtest/market.py). NULLS NOT DISTINCT
-- makes (match, null, snapshot) unique too, so a consensus row can't be duplicated.
create table if not exists market_scores (
  id uuid primary key default gen_random_uuid(),
  match_id uuid not null references matches(id) on delete cascade,
  bookmaker_id uuid references bookmakers(id) on delete restrict,
  snapshot text not null check (snapshot in ('pre_match', 'closing', 'live')),
  -- The margin-free probabilities that were scored.
  p_home double precision not null check (p_home between 0 and 1),
  p_draw double precision not null check (p_draw between 0 and 1),
  p_away double precision not null check (p_away between 0 and 1),
  rps double precision not null check (rps between 0 and 1),
  log_loss double precision not null check (log_loss >= 0 and log_loss < 'infinity'),
  brier double precision not null check (brier between 0 and 2),
  -- When these values were computed; moves only when a value changes.
  scored_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint market_scores_match_bookmaker_snapshot_key
    unique nulls not distinct (match_id, bookmaker_id, snapshot),
  check (abs(p_home + p_draw + p_away - 1) <= 1e-5)
);
create index if not exists idx_market_scores_bookmaker on market_scores(bookmaker_id);

drop trigger if exists set_timestamp_model_versions on model_versions;
create trigger set_timestamp_model_versions
  before update on model_versions
  for each row execute function update_updated_at();

drop trigger if exists set_timestamp_team_ratings on team_ratings;
create trigger set_timestamp_team_ratings
  before update on team_ratings
  for each row execute function update_updated_at();

drop trigger if exists set_timestamp_prediction_scores on prediction_scores;
create trigger set_timestamp_prediction_scores
  before update on prediction_scores
  for each row execute function update_updated_at();

drop trigger if exists set_timestamp_market_scores on market_scores;
create trigger set_timestamp_market_scores
  before update on market_scores
  for each row execute function update_updated_at();

alter table model_versions enable row level security;
alter table team_ratings enable row level security;
alter table predictions enable row level security;
alter table prediction_scores enable row level security;
alter table market_scores enable row level security;

revoke all on model_versions, team_ratings, predictions, prediction_scores, market_scores
  from anon, authenticated;
grant all on model_versions, team_ratings, prediction_scores, market_scores to service_role;
-- Append-only for the worker too (the triggers enforce it for everyone, owners included).
grant select, insert on predictions to service_role;

-- Write one batch of market scores.
--
--   p_match_ids  the matches this batch covers (e.g. every match of a season)
--   p_scores     [{"match_id", "bookmaker" (code, or null for the consensus), "snapshot",
--                  "p_home", "p_draw", "p_away", "rps", "log_loss", "brier"}]
--
-- The batch is the truth for the matches it covers: a stored score of a covered match that the
-- batch no longer has (its odds were removed, or the match lost its result) is deleted. Updates
-- have an IS DISTINCT FROM guard, so re-sending the same scores changes no row and no updated_at
-- or scored_at. Batches are serialised (advisory lock). Returns rows inserted, updated, deleted.
create or replace function public.football_upsert_market_scores(
  p_match_ids jsonb,
  p_scores jsonb
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_inserted int := 0;
  v_updated int := 0;
  v_deleted int := 0;
  v_problem text;
begin
  if jsonb_typeof(p_match_ids) is distinct from 'array' or jsonb_typeof(p_scores) is distinct from 'array' then
    raise exception using errcode = '22023', message = 'p_match_ids and p_scores must be JSON arrays';
  end if;

  perform pg_advisory_xact_lock(hashtext('football_upsert_market_scores'));

  select format('match %s bookmaker %s', s.match_id, coalesce(s.bookmaker, 'consensus'))
  into v_problem
  from jsonb_to_recordset(p_scores) as s(match_id uuid, bookmaker text)
  left join bookmakers b on b.code = s.bookmaker
  where (s.bookmaker is not null and b.id is null)
     or not (p_match_ids @> jsonb_build_array(s.match_id::text))
  limit 1;
  if v_problem is not null then
    raise exception using errcode = '22023',
      message = format('market score for %s has an unknown bookmaker or a match outside p_match_ids', v_problem);
  end if;

  with covered as (
    select (value #>> '{}')::uuid as match_id from jsonb_array_elements(p_match_ids)
  ), incoming as (
    select s.match_id, b.id as bookmaker_id, s.snapshot
    from jsonb_to_recordset(p_scores) as s(match_id uuid, bookmaker text, snapshot text)
    left join bookmakers b on b.code = s.bookmaker
  ), gone as (
    delete from market_scores ms
    using covered c
    where ms.match_id = c.match_id
      and not exists (
        select 1 from incoming i
        where i.match_id = ms.match_id
          and i.bookmaker_id is not distinct from ms.bookmaker_id
          and i.snapshot = ms.snapshot)
    returning 1
  )
  select count(*) into v_deleted from gone;

  with incoming as (
    select s.match_id, b.id as bookmaker_id, s.snapshot,
           s.p_home, s.p_draw, s.p_away, s.rps, s.log_loss, s.brier
    from jsonb_to_recordset(p_scores) as s(
      match_id uuid, bookmaker text, snapshot text,
      p_home double precision, p_draw double precision, p_away double precision,
      rps double precision, log_loss double precision, brier double precision)
    left join bookmakers b on b.code = s.bookmaker
  ), up as (
    insert into market_scores as ms (
      match_id, bookmaker_id, snapshot, p_home, p_draw, p_away, rps, log_loss, brier)
    select match_id, bookmaker_id, snapshot, p_home, p_draw, p_away, rps, log_loss, brier
    from incoming
    on conflict on constraint market_scores_match_bookmaker_snapshot_key do update
      set p_home = excluded.p_home,
          p_draw = excluded.p_draw,
          p_away = excluded.p_away,
          rps = excluded.rps,
          log_loss = excluded.log_loss,
          brier = excluded.brier,
          scored_at = now()
      where (ms.p_home, ms.p_draw, ms.p_away, ms.rps, ms.log_loss, ms.brier)
        is distinct from
            (excluded.p_home, excluded.p_draw, excluded.p_away, excluded.rps, excluded.log_loss, excluded.brier)
    returning (xmax = 0) as inserted
  )
  select count(*) filter (where inserted), count(*) filter (where not inserted)
  into v_inserted, v_updated
  from up;

  return jsonb_build_object('inserted', v_inserted, 'updated', v_updated, 'deleted', v_deleted);
end;
$$;

-- The inputs for scoring the market on one season: every match of the season (so stale scores of
-- matches that lost their result can be removed), with its result and its pre-match and closing
-- 1X2 prices per source.
create or replace function public.football_market_score_inputs(p_division text, p_season text)
returns jsonb
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
  select coalesce(jsonb_agg(m.row order by m.kickoff_at, m.id), '[]'::jsonb)
  from (
    select mt.id, mt.kickoff_at,
           jsonb_build_object(
             'match_id', mt.id,
             'status', mt.status,
             'home_goals', mt.home_goals,
             'away_goals', mt.away_goals,
             'odds', coalesce((
               select jsonb_agg(jsonb_build_object(
                        'source', b.code, 'snapshot', o.snapshot,
                        'home', o.home, 'draw', o.draw, 'away', o.away)
                      order by b.code, o.snapshot)
               from odds o
               join bookmakers b on b.id = o.bookmaker_id
               where o.match_id = mt.id and o.snapshot in ('pre_match', 'closing')
             ), '[]'::jsonb)
           ) as row
    from matches mt
    join leagues l on l.id = mt.league_id
    where l.code = p_division and mt.season = p_season
  ) m
$$;

-- Only the worker (service role) may call these. Supabase's default privileges grant EXECUTE on
-- new public functions to anon and authenticated, so revoke explicitly.
revoke all on function public.football_prediction_deadline(timestamptz) from public, anon, authenticated;
revoke all on function public.football_predictions_before_kickoff() from public, anon, authenticated;
revoke all on function public.football_predictions_append_only() from public, anon, authenticated;
revoke all on function public.football_upsert_market_scores(jsonb, jsonb) from public, anon, authenticated;
revoke all on function public.football_market_score_inputs(text, text) from public, anon, authenticated;
grant execute on function public.football_prediction_deadline(timestamptz) to service_role;
grant execute on function public.football_upsert_market_scores(jsonb, jsonb) to service_role;
grant execute on function public.football_market_score_inputs(text, text) to service_role;

notify pgrst, 'reload schema';
