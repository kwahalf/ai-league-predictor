-- Football core: leagues, teams, matches, match stats, odds sources and odds.
-- See docs/specs/2026-11-11-match-predictor.md ("Odds data", "Data Model").
--
-- These are fixed-shape, high-volume tables joined constantly, so they get their
-- own tables instead of the template's generic entities model (AGENTS.md).
--
-- Access: RLS is on for every table and there are no anon/authenticated
-- policies. The frontend will read through security_invoker views (step 3.1);
-- the worker writes with the service role, which bypasses RLS.
--
-- Reference rows (the EPL and the four odds sources) live here rather than in
-- seed.sql, because the live stack never loads the seed.

create table if not exists leagues (
  id uuid primary key default gen_random_uuid(),
  -- football-data.co.uk division code, e.g. E0 for the Premier League.
  code text not null unique check (btrim(code) <> ''),
  name text not null check (btrim(name) <> ''),
  country text not null check (btrim(country) <> ''),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists teams (
  id uuid primary key default gen_random_uuid(),
  league_id uuid not null references leagues(id) on delete restrict,
  name text not null check (btrim(name) <> ''),
  -- Other spellings of the name across sources, e.g. 'Man United'.
  aliases text[] not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (league_id, name)
);

create table if not exists matches (
  id uuid primary key default gen_random_uuid(),
  league_id uuid not null references leagues(id) on delete restrict,
  -- Season in football-data.co.uk form: '2627' is 2026/27.
  season text not null check (season ~ '^[0-9]{4}$'),
  kickoff_at timestamptz not null,
  home_team_id uuid not null references teams(id) on delete restrict,
  away_team_id uuid not null references teams(id) on delete restrict,
  home_goals smallint check (home_goals >= 0),
  away_goals smallint check (away_goals >= 0),
  status text not null default 'scheduled'
    check (status in ('scheduled', 'finished', 'postponed')),
  -- Where the row came from, e.g. 'E0/2627' or 'fixtures.csv'.
  source_key text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (league_id, season, home_team_id, away_team_id),
  check (home_team_id <> away_team_id),
  -- Both scores are set together, and a finished match has them.
  check ((home_goals is null) = (away_goals is null)),
  check (status <> 'finished' or home_goals is not null)
);

create table if not exists match_stats (
  match_id uuid primary key references matches(id) on delete cascade,
  home_shots smallint check (home_shots >= 0),
  away_shots smallint check (away_shots >= 0),
  home_shots_on_target smallint check (home_shots_on_target >= 0),
  away_shots_on_target smallint check (away_shots_on_target >= 0),
  home_corners smallint check (home_corners >= 0),
  away_corners smallint check (away_corners >= 0),
  home_yellow_cards smallint check (home_yellow_cards >= 0),
  away_yellow_cards smallint check (away_yellow_cards >= 0),
  home_red_cards smallint check (home_red_cards >= 0),
  away_red_cards smallint check (away_red_cards >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists bookmakers (
  id uuid primary key default gen_random_uuid(),
  -- Canonical source code: B365, BW, AVG (market average) or PS (Pinnacle).
  code text not null unique check (btrim(code) <> ''),
  name text not null check (btrim(name) <> ''),
  -- False for benchmark-only sources the models never see (Pinnacle).
  is_model_input boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists odds (
  id uuid primary key default gen_random_uuid(),
  match_id uuid not null references matches(id) on delete cascade,
  bookmaker_id uuid not null references bookmakers(id) on delete restrict,
  -- pre_match feeds models and the backtest; closing is for evaluation only;
  -- live is the latest price from fixtures.csv.
  snapshot text not null check (snapshot in ('pre_match', 'closing', 'live')),
  -- Decimal odds. Missing prices are null; present ones must be > 1.0.
  home numeric(8, 3) check (home > 1.0),
  draw numeric(8, 3) check (draw > 1.0),
  away numeric(8, 3) check (away > 1.0),
  over_2_5 numeric(8, 3) check (over_2_5 > 1.0),
  under_2_5 numeric(8, 3) check (under_2_5 > 1.0),
  -- When the prices were observed (the CSV download time for live odds).
  captured_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (match_id, bookmaker_id, snapshot),
  -- 1X2 prices come as a set.
  check ((home is null) = (draw is null) and (draw is null) = (away is null)),
  check ((over_2_5 is null) = (under_2_5 is null))
);

-- Foreign keys and common lookups. Unique constraints above already index
-- leagues(code), bookmakers(code), teams(league_id, name),
-- matches(league_id, season, ...) (which also serves matches(league_id, season)),
-- match_stats(match_id) and
-- odds(match_id, bookmaker_id, snapshot), which also serves odds(match_id).
create index if not exists idx_matches_kickoff_at on matches(kickoff_at);
create index if not exists idx_matches_home_team on matches(home_team_id);
create index if not exists idx_matches_away_team on matches(away_team_id);
create index if not exists idx_matches_scheduled on matches(kickoff_at) where status = 'scheduled';
create index if not exists idx_odds_bookmaker on odds(bookmaker_id);
create index if not exists idx_teams_aliases on teams using gin (aliases);

drop trigger if exists set_timestamp_leagues on leagues;
create trigger set_timestamp_leagues
  before update on leagues
  for each row execute function update_updated_at();

drop trigger if exists set_timestamp_teams on teams;
create trigger set_timestamp_teams
  before update on teams
  for each row execute function update_updated_at();

drop trigger if exists set_timestamp_matches on matches;
create trigger set_timestamp_matches
  before update on matches
  for each row execute function update_updated_at();

drop trigger if exists set_timestamp_match_stats on match_stats;
create trigger set_timestamp_match_stats
  before update on match_stats
  for each row execute function update_updated_at();

drop trigger if exists set_timestamp_bookmakers on bookmakers;
create trigger set_timestamp_bookmakers
  before update on bookmakers
  for each row execute function update_updated_at();

drop trigger if exists set_timestamp_odds on odds;
create trigger set_timestamp_odds
  before update on odds
  for each row execute function update_updated_at();

-- RLS on, no anon/authenticated policies: the browser reads views only.
alter table leagues enable row level security;
alter table teams enable row level security;
alter table matches enable row level security;
alter table match_stats enable row level security;
alter table bookmakers enable row level security;
alter table odds enable row level security;

revoke all on leagues, teams, matches, match_stats, bookmakers, odds from anon, authenticated;
grant all on leagues, teams, matches, match_stats, bookmakers, odds to service_role;

-- Reference rows.
insert into leagues (code, name, country)
values ('E0', 'Premier League', 'England')
on conflict (code) do nothing;

insert into bookmakers (code, name, is_model_input)
values
  ('B365', 'Bet365', true),
  ('BW', 'Bwin', true),
  ('AVG', 'Market average', true),
  ('PS', 'Pinnacle', false)
on conflict (code) do nothing;

notify pgrst, 'reload schema';
