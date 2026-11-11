-- Football ingest (plan step 1.3): raw-file bucket and the idempotent upsert the worker calls.
-- See docs/specs/2026-11-11-match-predictor.md ("Architecture", "Odds data", "Data Model").
--
-- The worker (IngestWorkflow, temporal/src/football/ingest/) downloads each football-data.co.uk
-- file, keeps the raw bytes in Storage, parses it, and sends one season (or fixtures.csv) to
-- football_ingest_matches in a single call. The function writes only what actually changed, so a
-- second run with the same files leaves every row, and every updated_at, as it was.

-- Raw CSVs, private. Objects are named by content hash (E0/2324/<sha256>.csv,
-- fixtures/<sha256>.csv), so downloading identical content again stores nothing new, and the
-- database can be rebuilt without the source site. Only the service role reads or writes it:
-- there are no storage.objects policies for this bucket.
insert into storage.buckets (id, name, public)
values ('football-raw', 'football-raw', false)
on conflict (id) do nothing;

-- The latest download loaded per source file ('E0/2324', 'fixtures.csv'). Manual and scheduled
-- runs can overlap; a load whose download is older than the last one loaded for the same file is
-- skipped, so an older file can never overwrite newer prices or results. Bookkeeping only: it moves
-- on every run, unlike the data tables.
create table if not exists football_ingest_state (
  division text not null,
  source_key text not null check (btrim(source_key) <> ''),
  last_captured_at timestamptz not null,
  primary key (division, source_key)
);
alter table football_ingest_state enable row level security;
revoke all on football_ingest_state from anon, authenticated;
grant all on football_ingest_state to service_role;

-- Upsert one file's teams, matches, match stats and odds.
--
--   p_division     league code, e.g. 'E0' (must exist in leagues)
--   p_kind         'season' (a season file) or 'fixtures' (fixtures.csv)
--   p_source_key   stored in matches.source_key, e.g. 'E0/2324' or 'fixtures.csv'
--   p_captured_at  when the file was downloaded; captured_at of new or changed odds
--   p_teams        [{"name": canonical name, "aliases": [spellings]}]
--   p_matches      [{"season", "home_team", "away_team", "kickoff_at", "home_goals", "away_goals",
--                    "status", "stats": {...} | null,
--                    "odds": [{"source", "snapshot", "home", "draw", "away", "over_2_5", "under_2_5"}]}]
--
-- Rules:
--   * Teams resolve by canonical name; aliases are merged in (never removed).
--   * Matches upsert on their natural key (league, season, home, away). A season file sets
--     kick-off, score, status and source_key. fixtures.csv only creates scheduled matches or moves
--     the kick-off of one that is still scheduled: it never touches a finished match, so a fixture
--     that later appears with a result becomes 'finished' on the same row.
--   * Live odds from fixtures.csv are stored only for matches still scheduled and not yet kicked
--     off at p_captured_at, so a live price is never captured after kick-off.
--   * The file is the truth for the odds of each match it covers: a stored quote of the file's
--     snapshots (pre_match/closing for a season file, live for fixtures.csv) that the file no longer
--     has (absent, or nothing left after cleaning) is deleted.
--   * Every update has a WHERE ... IS DISTINCT FROM ... guard: unchanged rows aren't written, so
--     their updated_at (set by the update_updated_at trigger) doesn't move. odds.captured_at moves
--     only when a price changes.
--   * Loads are serialised per league (advisory lock), and a download older than the last one
--     loaded for the same file is skipped (football_ingest_state), returning stale_skipped = 1.
--
-- Returns counts of rows inserted, updated and deleted per table, fixtures_skipped and stale_skipped.
create or replace function public.football_ingest_matches(
  p_division text,
  p_kind text,
  p_source_key text,
  p_captured_at timestamptz,
  p_teams jsonb,
  p_matches jsonb
) returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  v_league uuid;
  v_fixtures boolean;
  v_teams_inserted int := 0;
  v_teams_updated int := 0;
  v_matches_inserted int := 0;
  v_matches_updated int := 0;
  v_stats_inserted int := 0;
  v_stats_updated int := 0;
  v_odds_inserted int := 0;
  v_odds_updated int := 0;
  v_odds_deleted int := 0;
  v_fixtures_skipped int := 0;
  v_problem text;
  v_last_captured_at timestamptz;
begin
  if p_kind is null or p_kind not in ('season', 'fixtures') then
    raise exception using errcode = '22023', message = format('p_kind must be season or fixtures, got %L', p_kind);
  end if;
  v_fixtures := p_kind = 'fixtures';
  if p_captured_at is null then
    raise exception using errcode = '22023', message = 'p_captured_at is required';
  end if;
  if jsonb_typeof(p_teams) is distinct from 'array' or jsonb_typeof(p_matches) is distinct from 'array' then
    raise exception using errcode = '22023', message = 'p_teams and p_matches must be JSON arrays';
  end if;

  select id into v_league from leagues where code = p_division;
  if v_league is null then
    raise exception using errcode = '22023', message = format('unknown league %L', p_division);
  end if;
  if p_source_key is null or btrim(p_source_key) = '' then
    raise exception using errcode = '22023', message = 'p_source_key is required';
  end if;

  -- One load per league at a time; then refuse a download older than the last one loaded.
  perform pg_advisory_xact_lock(hashtext('football_ingest_matches'), hashtext(p_division));
  select last_captured_at into v_last_captured_at
  from football_ingest_state
  where division = p_division and source_key = p_source_key;
  if v_last_captured_at is not null and p_captured_at < v_last_captured_at then
    return jsonb_build_object('stale_skipped', 1);
  end if;
  insert into football_ingest_state as st (division, source_key, last_captured_at)
  values (p_division, p_source_key, p_captured_at)
  on conflict (division, source_key) do update
    set last_captured_at = excluded.last_captured_at
    where st.last_captured_at < excluded.last_captured_at;

  -- Teams.
  with incoming as (
    select t.name, array(select distinct a from unnest(coalesce(t.aliases, '{}')) as a order by a) as aliases
    from jsonb_to_recordset(p_teams) as t(name text, aliases text[])
  ), up as (
    insert into teams as tm (league_id, name, aliases)
    select v_league, i.name, i.aliases from incoming i
    on conflict (league_id, name) do update
      set aliases = array(select distinct a from unnest(tm.aliases || excluded.aliases) as a order by a)
      where not (excluded.aliases <@ tm.aliases)
    returning (xmax = 0) as inserted
  )
  select count(*) filter (where inserted), count(*) filter (where not inserted)
  into v_teams_inserted, v_teams_updated
  from up;

  select format('%s v %s', m.home_team, m.away_team)
  into v_problem
  from jsonb_to_recordset(p_matches) as m(home_team text, away_team text)
  left join teams th on th.league_id = v_league and th.name = m.home_team
  left join teams ta on ta.league_id = v_league and ta.name = m.away_team
  where th.id is null or ta.id is null
  limit 1;
  if v_problem is not null then
    raise exception using errcode = '22023', message = format('match %s names a team missing from p_teams', v_problem);
  end if;

  -- Matches.
  with incoming as (
    select m.season, th.id as home_team_id, ta.id as away_team_id, m.kickoff_at,
           m.home_goals, m.away_goals, m.status
    from jsonb_to_recordset(p_matches) as m(
      season text, home_team text, away_team text, kickoff_at timestamptz,
      home_goals smallint, away_goals smallint, status text)
    join teams th on th.league_id = v_league and th.name = m.home_team
    join teams ta on ta.league_id = v_league and ta.name = m.away_team
  ), up as (
    insert into matches as mt (
      league_id, season, kickoff_at, home_team_id, away_team_id,
      home_goals, away_goals, status, source_key)
    select v_league, i.season, i.kickoff_at, i.home_team_id, i.away_team_id,
           case when v_fixtures then null else i.home_goals end,
           case when v_fixtures then null else i.away_goals end,
           case when v_fixtures then 'scheduled' else i.status end,
           p_source_key
    from incoming i
    on conflict (league_id, season, home_team_id, away_team_id) do update
      set kickoff_at = excluded.kickoff_at,
          home_goals = case when v_fixtures then mt.home_goals else excluded.home_goals end,
          away_goals = case when v_fixtures then mt.away_goals else excluded.away_goals end,
          status = case when v_fixtures then mt.status else excluded.status end,
          source_key = case when v_fixtures then mt.source_key else excluded.source_key end
      where case
        when v_fixtures then
          mt.status = 'scheduled' and mt.kickoff_at is distinct from excluded.kickoff_at
        else
          (mt.kickoff_at, mt.home_goals, mt.away_goals, mt.status, mt.source_key)
            is distinct from
          (excluded.kickoff_at, excluded.home_goals, excluded.away_goals, excluded.status, excluded.source_key)
      end
    returning (xmax = 0) as inserted
  )
  select count(*) filter (where inserted), count(*) filter (where not inserted)
  into v_matches_inserted, v_matches_updated
  from up;

  -- Match stats (season files only; fixtures.csv has none).
  if not v_fixtures then
    with incoming as (
      select mt.id as match_id, s.*
      from jsonb_to_recordset(p_matches) as m(season text, home_team text, away_team text, stats jsonb)
      join teams th on th.league_id = v_league and th.name = m.home_team
      join teams ta on ta.league_id = v_league and ta.name = m.away_team
      join matches mt on mt.league_id = v_league and mt.season = m.season
                     and mt.home_team_id = th.id and mt.away_team_id = ta.id
      cross join lateral jsonb_to_record(m.stats) as s(
        home_shots smallint, away_shots smallint,
        home_shots_on_target smallint, away_shots_on_target smallint,
        home_corners smallint, away_corners smallint,
        home_yellow_cards smallint, away_yellow_cards smallint,
        home_red_cards smallint, away_red_cards smallint)
      where jsonb_typeof(m.stats) = 'object'
    ), up as (
      insert into match_stats as ms (
        match_id, home_shots, away_shots, home_shots_on_target, away_shots_on_target,
        home_corners, away_corners, home_yellow_cards, away_yellow_cards,
        home_red_cards, away_red_cards)
      select match_id, home_shots, away_shots, home_shots_on_target, away_shots_on_target,
             home_corners, away_corners, home_yellow_cards, away_yellow_cards,
             home_red_cards, away_red_cards
      from incoming
      on conflict (match_id) do update
        set home_shots = excluded.home_shots,
            away_shots = excluded.away_shots,
            home_shots_on_target = excluded.home_shots_on_target,
            away_shots_on_target = excluded.away_shots_on_target,
            home_corners = excluded.home_corners,
            away_corners = excluded.away_corners,
            home_yellow_cards = excluded.home_yellow_cards,
            away_yellow_cards = excluded.away_yellow_cards,
            home_red_cards = excluded.home_red_cards,
            away_red_cards = excluded.away_red_cards
        where (ms.home_shots, ms.away_shots, ms.home_shots_on_target, ms.away_shots_on_target,
               ms.home_corners, ms.away_corners, ms.home_yellow_cards, ms.away_yellow_cards,
               ms.home_red_cards, ms.away_red_cards)
          is distinct from
              (excluded.home_shots, excluded.away_shots, excluded.home_shots_on_target,
               excluded.away_shots_on_target, excluded.home_corners, excluded.away_corners,
               excluded.home_yellow_cards, excluded.away_yellow_cards,
               excluded.home_red_cards, excluded.away_red_cards)
      returning (xmax = 0) as inserted
    )
    select count(*) filter (where inserted), count(*) filter (where not inserted)
    into v_stats_inserted, v_stats_updated
    from up;
  end if;

  -- Odds: check sources and snapshots first, so bad input fails loudly instead of being dropped.
  select format('source %L snapshot %L', o.source, o.snapshot)
  into v_problem
  from jsonb_to_recordset(p_matches) as m(odds jsonb)
  cross join lateral jsonb_to_recordset(coalesce(m.odds, '[]'::jsonb)) as o(source text, snapshot text)
  left join bookmakers b on b.code = o.source
  where b.id is null
     or (v_fixtures and o.snapshot is distinct from 'live')
     or (not v_fixtures and o.snapshot not in ('pre_match', 'closing'))
  limit 1;
  if v_problem is not null then
    raise exception using errcode = '22023', message = format('unexpected odds %s for p_kind %L', v_problem, p_kind);
  end if;

  if v_fixtures then
    select count(*)
    into v_fixtures_skipped
    from jsonb_to_recordset(p_matches) as m(season text, home_team text, away_team text)
    join teams th on th.league_id = v_league and th.name = m.home_team
    join teams ta on ta.league_id = v_league and ta.name = m.away_team
    join matches mt on mt.league_id = v_league and mt.season = m.season
                   and mt.home_team_id = th.id and mt.away_team_id = ta.id
    where mt.status <> 'scheduled' or mt.kickoff_at <= p_captured_at;
  end if;

  -- Quotes the file no longer has, for the matches whose odds it covers.
  with covered as (
    select mt.id as match_id, coalesce(m.odds, '[]'::jsonb) as odds
    from jsonb_to_recordset(p_matches) as m(season text, home_team text, away_team text, odds jsonb)
    join teams th on th.league_id = v_league and th.name = m.home_team
    join teams ta on ta.league_id = v_league and ta.name = m.away_team
    join matches mt on mt.league_id = v_league and mt.season = m.season
                   and mt.home_team_id = th.id and mt.away_team_id = ta.id
    where not v_fixtures or (mt.status = 'scheduled' and mt.kickoff_at > p_captured_at)
  ), gone as (
    delete from odds od
    using covered c
    where od.match_id = c.match_id
      and od.snapshot = any (case when v_fixtures then array['live'] else array['pre_match', 'closing'] end)
      and not exists (
        select 1
        from jsonb_to_recordset(c.odds) as o(source text, snapshot text)
        join bookmakers b on b.code = o.source
        where b.id = od.bookmaker_id and o.snapshot = od.snapshot)
    returning 1
  )
  select count(*) into v_odds_deleted from gone;

  with incoming as (
    select mt.id as match_id, b.id as bookmaker_id, o.snapshot,
           o.home, o.draw, o.away, o.over_2_5, o.under_2_5
    from jsonb_to_recordset(p_matches) as m(season text, home_team text, away_team text, odds jsonb)
    join teams th on th.league_id = v_league and th.name = m.home_team
    join teams ta on ta.league_id = v_league and ta.name = m.away_team
    join matches mt on mt.league_id = v_league and mt.season = m.season
                   and mt.home_team_id = th.id and mt.away_team_id = ta.id
    cross join lateral jsonb_to_recordset(coalesce(m.odds, '[]'::jsonb)) as o(
      source text, snapshot text, home numeric, draw numeric, away numeric,
      over_2_5 numeric, under_2_5 numeric)
    join bookmakers b on b.code = o.source
    where not v_fixtures or (mt.status = 'scheduled' and mt.kickoff_at > p_captured_at)
  ), up as (
    insert into odds as od (
      match_id, bookmaker_id, snapshot, home, draw, away, over_2_5, under_2_5, captured_at)
    select match_id, bookmaker_id, snapshot, home, draw, away, over_2_5, under_2_5, p_captured_at
    from incoming
    on conflict (match_id, bookmaker_id, snapshot) do update
      set home = excluded.home,
          draw = excluded.draw,
          away = excluded.away,
          over_2_5 = excluded.over_2_5,
          under_2_5 = excluded.under_2_5,
          captured_at = excluded.captured_at
      -- excluded.* are already rounded to numeric(8, 3), so equal prices compare equal.
      where (od.home, od.draw, od.away, od.over_2_5, od.under_2_5)
        is distinct from
            (excluded.home, excluded.draw, excluded.away, excluded.over_2_5, excluded.under_2_5)
    returning (xmax = 0) as inserted
  )
  select count(*) filter (where inserted), count(*) filter (where not inserted)
  into v_odds_inserted, v_odds_updated
  from up;

  return jsonb_build_object(
    'teams_inserted', v_teams_inserted,
    'teams_updated', v_teams_updated,
    'matches_inserted', v_matches_inserted,
    'matches_updated', v_matches_updated,
    'stats_inserted', v_stats_inserted,
    'stats_updated', v_stats_updated,
    'odds_inserted', v_odds_inserted,
    'odds_updated', v_odds_updated,
    'odds_deleted', v_odds_deleted,
    'fixtures_skipped', v_fixtures_skipped,
    'stale_skipped', 0
  );
end;
$$;

-- Only the worker (service role) may call it. Supabase's default privileges grant EXECUTE on new
-- public functions to anon and authenticated, so revoke explicitly.
revoke all on function public.football_ingest_matches(text, text, text, timestamptz, jsonb, jsonb)
  from public, anon, authenticated;
grant execute on function public.football_ingest_matches(text, text, text, timestamptz, jsonb, jsonb)
  to service_role;

notify pgrst, 'reload schema';
