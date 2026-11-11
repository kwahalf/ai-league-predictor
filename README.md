# AI League Predictor

Predicts English Premier League matches: home/draw/away probabilities, expected goals and likely
scorelines, compared with the bookmakers, and explained in plain language by an agent. An internal
tool, owned by @kwahalf.

- **What it does and why:** [`docs/specs/2026-11-11-match-predictor.md`](docs/specs/2026-11-11-match-predictor.md)
- **How it gets built, step by step:** [`docs/plans/2026-11-11-implementation-plan.md`](docs/plans/2026-11-11-implementation-plan.md)

It's built on the Volaris template: Supabase (Postgres, API, auth), Temporal (scheduled and durable
jobs, run by a Python worker), a JSON-driven React frontend, and an agent loop with human approval for
writes. The template's own guides are kept at the root (`AGENTS.md`, `DATABASE.md`,
`Generalisable_schema.md`, `Guide_for_agents_using_supabase_template.md`) and under `docs/`.

## Two stacks: dev and live

The app runs on a self-hosted runner as two stacks that share nothing:

| | Dev | Live |
|---|---|---|
| Purpose | Building and testing PRs | Daily schedules and the real data |
| Reset allowed | Yes (`make reset`) | **Never.** Holds the prediction track record |
| New migrations | `make reset` | `make migrate STACK=live` (backs up first) |
| Frontend | http://localhost:3000 | http://localhost:3100 |
| Agent API | http://localhost:8000 | http://localhost:8100 |
| Temporal UI | http://localhost:8081 | http://localhost:8181 |
| Supabase API | http://localhost:54321 | http://localhost:55321 |
| Supabase Studio | http://localhost:54323 | http://localhost:55323 |

Each stack's project ID, Compose project name and ports live in `stacks/<stack>.env`. Every `make`
target takes `STACK=dev` (the default) or `STACK=live`.

## Prerequisites

- Docker with Compose v2
- `make`
- **Supabase CLI 2.x.** `make up` runs `supabase start`. The 3.x CLI rejects this `config.toml` (its
  `[inbucket]` section was renamed), so stay on 2.x for now.
- Node 20+ (only to run the frontend outside Docker)

## Quick start

1. Copy the environment defaults and add your keys:
   `cp .env.example .env`
   Ports and URLs come from the stack, not `.env`. Set the model keys there. Set `AGENT_PROVIDER=fake`
   to run the agent with no keys at all.
2. Start the dev stack: `make up`
3. Open http://localhost:3000.

Common commands:

- `make down`: stop the stack (data is kept)
- `make reset`: wipe and recreate the dev stack, re-applying migrations and seed (refuses on live)
- `make migrate STACK=live`: apply new migrations to live, after an automatic backup
- `make backup STACK=live`: dump the database to `backups/` (gitignored)
- `make logs`, `make logs-temporal`, `make logs-frontend`: follow logs
- `make supabase-status`: Supabase URLs and local keys

Python tests run in the worker image:
`docker compose run --rm -v "$PWD/temporal:/app" --entrypoint python temporal-worker -m pytest tests -q`

## Reaching the app from your machine

Everything listens on the runner's localhost. Use an SSH tunnel for the ports you need, for example
dev:

```bash
ssh -N -L 3000:localhost:3000 -L 8000:localhost:8000 -L 54321:localhost:54321 -L 8081:localhost:8081 ubuntu@<runner>
```

The Supabase CLI publishes its own ports on all interfaces and has no setting to stop that. On the
runner, block outside access to them with:

```bash
sudo scripts/runner/restrict-supabase-ports.sh apply
```

## How changes get in

`main` is protected. Every change goes through a PR: CI (`pr-validation`, `architecture-audit`), a
Copilot review loop, then final sign-off and merge by @kwahalf. Details are in the plan's "Review
process" section. The template's other workflows (deploy, monitor, `pipeline-*`) are kept but have no
secrets, so they fail; only the two above count.
