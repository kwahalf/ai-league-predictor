-- Football data-quality checks (plan step 1.4): the facts the worker's check activity judges.
-- See docs/specs/2026-11-11-match-predictor.md ("Odds data", "Data Model").
--
-- IngestWorkflow (temporal/src/football/ingest/) calls football_quality_check as its last step,
-- after every ingest. The function only reads. It returns, for one league:
--
--   seasons            one row per completed season and the current season: match, team and
--                      unfinished-match counts, the most home and away matches of any team, and,
--                      for completed seasons only, the teams whose home/away counts are not
--                      teams-1 each and the ordered home/away pairs that have no match. The
--                      worker decides what passes (exactly 380 / 20 / 38 per team for a completed
--                      season, at most that for the current one), so the rule lives in one place.
--   duplicate_fixtures matches sharing (season, home, away), and pairs of matches on the same UK
--                      date that share a team, across all seasons (a match stored under two
--                      seasons, or the same team booked twice in one day).
--   scores             matches whose scores don't fit their status: a score on a match that
--                      isn't finished, a finished match without both scores, or only one score.
--   odds_prices        odds rows with any price <= 1.0.
--   odds_margins       complete 1X2 and over/under 2.5 sets whose margin (sum of 1/price - 1) is
--                      outside the limits passed in p_margin_limits, per market.
--   totals             matches and odds rows checked.
--
-- Every list except "seasons" is {"count": n, "rows": [...]}, with rows capped at p_limit in a
-- stable order. Several of these can't happen while the schema's constraints hold (the unique
-- (league, season, home, away) key, prices > 1.0, complete sets, finished => scored); the check is
-- the backstop that confirms it after every ingest, and the one place a constraint change would
-- be noticed.
--
--   p_division          league code, e.g. 'E0' (must exist in leagues)
--   p_current_season    the season running now, e.g. '2627'
--   p_expected_seasons  seasons that must be complete even if they have no rows (1617 up to the
--                       season before the current one). Any other stored season that starts
--                       before the current one is treated as completed too.
--   p_teams_per_season  20 for the Premier League
--   p_margin_limits     {"1x2": {"min": 0, "max": 0.15}, "over_under_2_5": {"min": 0, "max": 0.15}}
--   p_limit             cap on rows returned per list
--
-- Dates are UK dates (Europe/London), the source's own; kick-offs before 2019/20 are stored at
-- 00:00 UTC on the match date, which is the same UK date.
create or replace function public.football_quality_check(
  p_division text,
  p_current_season text,
  p_expected_seasons text[],
  p_teams_per_season int,
  p_margin_limits jsonb,
  p_limit int
) returns jsonb
language plpgsql
stable
security invoker
set search_path = public, pg_temp
as $$
declare
  v_league uuid;
  v_current_start int;
  v_min_1x2 numeric;
  v_max_1x2 numeric;
  v_min_ou numeric;
  v_max_ou numeric;
  v_result jsonb;
begin
  select id into v_league from leagues where code = p_division;
  if v_league is null then
    raise exception using errcode = '22023', message = format('unknown league %L', p_division);
  end if;
  if p_current_season is null or p_current_season !~ '^[0-9]{4}$' then
    raise exception using errcode = '22023', message = format('bad current season %L', p_current_season);
  end if;
  if p_expected_seasons is null
     or exists (select 1 from unnest(p_expected_seasons) s where s is null or s !~ '^[0-9]{4}$') then
    raise exception using errcode = '22023', message = 'p_expected_seasons must be season codes like 2324';
  end if;
  if p_teams_per_season is null or p_teams_per_season < 2 then
    raise exception using errcode = '22023', message = 'p_teams_per_season must be at least 2';
  end if;
  if p_limit is null or p_limit < 1 then
    raise exception using errcode = '22023', message = 'p_limit must be at least 1';
  end if;
  begin
    v_min_1x2 := (p_margin_limits -> '1x2' ->> 'min')::numeric;
    v_max_1x2 := (p_margin_limits -> '1x2' ->> 'max')::numeric;
    v_min_ou := (p_margin_limits -> 'over_under_2_5' ->> 'min')::numeric;
    v_max_ou := (p_margin_limits -> 'over_under_2_5' ->> 'max')::numeric;
  exception when others then
    raise exception using errcode = '22023', message = 'p_margin_limits must hold numeric min and max per market';
  end;
  if v_min_1x2 is null or v_max_1x2 is null or v_min_ou is null or v_max_ou is null then
    raise exception using errcode = '22023',
      message = 'p_margin_limits needs min and max for 1x2 and over_under_2_5';
  end if;

  -- Two-digit years: 00-69 are 2000s, 70-99 are 1900s (as the worker's season arithmetic).
  v_current_start := (case when left(p_current_season, 2)::int < 70 then 2000 else 1900 end)
                     + left(p_current_season, 2)::int;

  with mt as (
    select m.id, m.season, m.home_team_id, m.away_team_id, m.status, m.home_goals, m.away_goals,
           (m.kickoff_at at time zone 'Europe/London')::date as match_date,
           th.name as home, ta.name as away,
           (case when left(m.season, 2)::int < 70 then 2000 else 1900 end) + left(m.season, 2)::int
             as start_year
    from matches m
    join teams th on th.id = m.home_team_id
    join teams ta on ta.id = m.away_team_id
    where m.league_id = v_league
  ),
  checked_seasons as (
    select s as season, true as completed from unnest(p_expected_seasons) s
    union
    select distinct season, true from mt where start_year < v_current_start
    union
    select p_current_season, false
  ),
  appearances as (
    select season, home_team_id as team_id, 1 as home, 0 as away from mt
    union all
    select season, away_team_id, 0, 1 from mt
  ),
  team_counts as (
    select a.season, a.team_id, t.name, sum(a.home)::int as home, sum(a.away)::int as away
    from appearances a join teams t on t.id = a.team_id
    group by a.season, a.team_id, t.name
  ),
  missing_pairs as (
    select h.season, h.name as home, a.name as away
    from team_counts h
    join team_counts a on a.season = h.season and a.team_id <> h.team_id
    where not exists (
      select 1 from mt
      where mt.season = h.season and mt.home_team_id = h.team_id and mt.away_team_id = a.team_id
    )
  ),
  seasons as (
    select coalesce(jsonb_agg(jsonb_build_object(
      'season', c.season,
      'completed', c.completed,
      'matches', (select count(*) from mt where mt.season = c.season),
      'teams', (select count(*) from team_counts tc where tc.season = c.season),
      'max_home', (select coalesce(max(tc.home), 0) from team_counts tc where tc.season = c.season),
      'max_away', (select coalesce(max(tc.away), 0) from team_counts tc where tc.season = c.season),
      'unfinished', (select count(*) from mt where mt.season = c.season and mt.status <> 'finished'),
      'unfinished_rows', (
        select coalesce(jsonb_agg(r order by r ->> 'date', r ->> 'home'), '[]'::jsonb) from (
          select jsonb_build_object('date', mt.match_date, 'home', mt.home, 'away', mt.away,
                                    'status', mt.status) as r
          from mt where mt.season = c.season and mt.status <> 'finished'
          order by mt.match_date, mt.home limit p_limit) x),
      'teams_off', (
        select coalesce(jsonb_agg(r order by r ->> 'team'), '[]'::jsonb) from (
          select jsonb_build_object('team', tc.name, 'home', tc.home, 'away', tc.away) as r
          from team_counts tc
          where tc.season = c.season and c.completed
            and (tc.home <> p_teams_per_season - 1 or tc.away <> p_teams_per_season - 1)
          order by tc.name limit p_limit) x),
      'teams_off_count', (
        select count(*) from team_counts tc
        where tc.season = c.season and c.completed
          and (tc.home <> p_teams_per_season - 1 or tc.away <> p_teams_per_season - 1)),
      'missing_pairs', (
        select count(*) from missing_pairs mp where mp.season = c.season and c.completed),
      'missing_pair_rows', (
        select coalesce(jsonb_agg(r order by r ->> 'home', r ->> 'away'), '[]'::jsonb) from (
          select jsonb_build_object('home', mp.home, 'away', mp.away) as r
          from missing_pairs mp where mp.season = c.season and c.completed
          order by mp.home, mp.away limit p_limit) x)
    ) order by c.season), '[]'::jsonb) as v
    from checked_seasons c
    -- Only the current season's own row matters for it; if it is also listed as expected
    -- (shouldn't happen), the completed rule wins.
    where c.completed or not exists (
      select 1 from checked_seasons c2 where c2.season = c.season and c2.completed)
  ),
  same_key as (
    select season, home, away, count(*) as n
    from mt group by season, home_team_id, away_team_id, home, away having count(*) > 1
  ),
  same_day as (
    select a.match_date, a.season as season_a, a.home as home_a, a.away as away_a,
           b.season as season_b, b.home as home_b, b.away as away_b
    from mt a
    -- Each pair once, the earlier season (then home team) first, so messages are stable.
    join mt b on b.match_date = a.match_date
             and (a.season, a.home, a.away, a.id) < (b.season, b.home, b.away, b.id)
    where a.home_team_id in (b.home_team_id, b.away_team_id)
       or a.away_team_id in (b.home_team_id, b.away_team_id)
  ),
  duplicate_fixtures as (
    select jsonb_build_object(
      'count', (select count(*) from same_key) + (select count(*) from same_day),
      'rows', (
        select coalesce(jsonb_agg(r), '[]'::jsonb) from (
          select r from (
            select 0 as k, season as s, null::date as d, home as h,
                   jsonb_build_object('kind', 'same_season', 'season', season, 'home', home,
                                      'away', away, 'matches', n) as r
            from same_key
            union all
            select 1, season_a, match_date, home_a,
                   jsonb_build_object('kind', 'same_day', 'date', match_date,
                                      'a', jsonb_build_object('season', season_a, 'home', home_a, 'away', away_a),
                                      'b', jsonb_build_object('season', season_b, 'home', home_b, 'away', away_b))
            from same_day
          ) u order by k, d, s, h limit p_limit) x)
    ) as v
  ),
  bad_scores as (
    select * from mt
    where not (
      (status = 'finished' and home_goals is not null and away_goals is not null)
      or (status <> 'finished' and home_goals is null and away_goals is null)
    )
  ),
  scores as (
    select jsonb_build_object(
      'count', (select count(*) from bad_scores),
      'rows', (
        select coalesce(jsonb_agg(r), '[]'::jsonb) from (
          select jsonb_build_object('season', season, 'date', match_date, 'home', home, 'away', away,
                                    'status', status, 'home_goals', home_goals,
                                    'away_goals', away_goals) as r
          from bad_scores order by match_date, season, home limit p_limit) x)
    ) as v
  ),
  quotes as (
    select mt.season, mt.match_date, mt.home as home_team, mt.away as away_team, b.code as bookmaker,
           o.snapshot, o.home, o.draw, o.away, o.over_2_5, o.under_2_5
    from odds o
    join mt on mt.id = o.match_id
    join bookmakers b on b.id = o.bookmaker_id
  ),
  bad_prices as (
    -- least() ignores nulls: any present price <= 1.0.
    select * from quotes where least(home, draw, away, over_2_5, under_2_5) <= 1.0
  ),
  odds_prices as (
    select jsonb_build_object(
      'count', (select count(*) from bad_prices),
      'rows', (
        select coalesce(jsonb_agg(r), '[]'::jsonb) from (
          select jsonb_build_object('season', season, 'date', match_date, 'home', home_team,
                                    'away', away_team, 'bookmaker', bookmaker, 'snapshot', snapshot,
                                    'prices', jsonb_build_object('home', home, 'draw', draw, 'away', away,
                                                                 'over_2_5', over_2_5,
                                                                 'under_2_5', under_2_5)) as r
          from bad_prices order by match_date, season, home_team, bookmaker, snapshot
          limit p_limit) x)
    ) as v
  ),
  margins as (
    -- Complete sets only; a price <= 0 can't be turned into a margin (and is caught above).
    select '1x2' as market, season, match_date, home_team, away_team, bookmaker, snapshot,
           array[home, draw, away] as prices, 1 / home + 1 / draw + 1 / away - 1 as margin
    from quotes where home > 0 and draw > 0 and away > 0
    union all
    select 'over_under_2_5', season, match_date, home_team, away_team, bookmaker, snapshot,
           array[over_2_5, under_2_5], 1 / over_2_5 + 1 / under_2_5 - 1
    from quotes where over_2_5 > 0 and under_2_5 > 0
  ),
  bad_margins as (
    select * from margins
    where (market = '1x2' and (margin < v_min_1x2 or margin > v_max_1x2))
       or (market = 'over_under_2_5' and (margin < v_min_ou or margin > v_max_ou))
  ),
  odds_margins as (
    select jsonb_build_object(
      'count', (select count(*) from bad_margins),
      'rows', (
        select coalesce(jsonb_agg(r), '[]'::jsonb) from (
          select jsonb_build_object('market', market, 'season', season, 'date', match_date,
                                    'home', home_team, 'away', away_team, 'bookmaker', bookmaker,
                                    'snapshot', snapshot, 'prices', to_jsonb(prices),
                                    'margin', round(margin, 6)) as r
          from bad_margins order by match_date, season, home_team, bookmaker, snapshot, market
          limit p_limit) x)
    ) as v
  )
  select jsonb_build_object(
    'seasons', (select v from seasons),
    'duplicate_fixtures', (select v from duplicate_fixtures),
    'scores', (select v from scores),
    'odds_prices', (select v from odds_prices),
    'odds_margins', (select v from odds_margins),
    'totals', jsonb_build_object('matches', (select count(*) from mt), 'odds', (select count(*) from quotes))
  ) into v_result;

  return v_result;
end;
$$;

-- Only the worker (service role) may call it. Supabase's default privileges grant EXECUTE on new
-- public functions to anon and authenticated, so revoke explicitly.
revoke all on function public.football_quality_check(text, text, text[], int, jsonb, int)
  from public, anon, authenticated;
grant execute on function public.football_quality_check(text, text, text[], int, jsonb, int)
  to service_role;

notify pgrst, 'reload schema';
