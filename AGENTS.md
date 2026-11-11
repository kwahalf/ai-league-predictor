# Repository Guidelines

## Project Structure & Module Organization
- Docs live at the repo root (`README.md`, `Guide_for_agents_using_supabase_template.md`, `DATABASE.md`, `Generalisable_schema.md`) and are the fastest way to understand the schema and roadmap. Project documentation lives under `docs/` (architecture, ADRs, specs, user guides); reusable fill-in doc templates live under `docs/_templates/`.
- Supabase assets sit in `supabase/`: `config.toml` (CLI config), `migrations/*.sql` (ordered by timestamp), and `seed.sql` (loads after migrations).
- Application code is checked in: `frontend/` (Vite + React + TanStack, JSON-driven UI engine under `src/engine/`) and `temporal/` (Python Temporal worker under `src/`).
- Migrations follow a modular pattern (`core` model first, `analytics` next). Keep new domain-specific tables in new migration files rather than editing shipped ones.

## Build, Test, and Development Commands
- `supabase start` — Launch local Supabase stack (Postgres, Studio, API, Realtime) using `supabase/config.toml`. Requires Docker and the Supabase CLI.
- `supabase db reset --config supabase/config.toml` — Recreate the local database, apply all migrations in order, then run `seed.sql`. Run before opening a PR to ensure migrations stay green.
- Full stack (Supabase stub + Temporal + frontend) — use the Makefile wrappers: `make up` to start (`USE_DEV=1 make up` for live-reload), `make down` to stop, `make reset` to tear down volumes and recreate. These wrap `docker compose -f docker-compose.yml` (plus `docker-compose.dev.yml` when `USE_DEV=1`).

## Coding Style & Naming Conventions
- SQL uses snake_case with UUID primary keys (`default gen_random_uuid()`), timestamp columns `created_at`/`updated_at`, and booleans like `is_current` for SCD2 status.
- Prefer `jsonb` for flexible payloads (`entity_versions.data`, `time_series_points.data`); use numeric facts in `entity_facts` with clear `fact_type` references.
- Migration files are timestamped `YYYYMMDDHHMMSS_description.sql`; keep them idempotent where practical (`create table if not exists`) and group related changes together.

## Testing Guidelines
- There is no automated test suite yet; rely on the Supabase CLI for safety checks. Run `supabase db reset --config supabase/config.toml` to verify migrations and seeds apply cleanly.
- For manual QA, start the stack (`supabase start`), connect via `psql` or Supabase Studio, and spot-check new tables/functions before committing.

## Logging Guidelines
- **One-line rule:** All log messages should be formatted as single-line entries rather than spanning multiple lines. This ensures grep efficiency and makes log parsing easier.
- If a `docs/Logging.md` file exists in this repository, follow those formatting and conventions instead of the one-line rule above.
- Single-line logs improve searchability, enable efficient filtering with standard Unix tools, and simplify automated log analysis.

## Commit & Pull Request Guidelines
- Git history favors short, imperative subjects (e.g., “Renamed parent_entity_id…”, “Add project overview”) with the occasional `feat:` prefix. Keep subjects ≤72 characters; add context in the body when needed.
- In PR descriptions, include: purpose, summary of schema changes (tables, columns, constraints), migration file names, seed data impact, and any follow-up tasks. Link issues if applicable; screenshots are only needed when UI changes are involved.

## Security & Configuration Tips
- Do not commit secrets (JWT signing keys, Twilio tokens, etc.). Use environment variables referenced in `supabase/config.toml` comments.
- Keep production and local configs separate; avoid hard-coding URLs or credentials inside migration files or seeds.
