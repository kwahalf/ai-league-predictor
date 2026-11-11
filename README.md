# Boilerplate Stack

Phase 1 scaffolding for the JSON-driven Supabase + Temporal starter.

## Prerequisites
- Docker Desktop with Compose v2
- `make` (comes with macOS/Linux; install via Xcode CLT on macOS)
- **Supabase CLI** — required; `make up` runs `supabase start` to launch the local Supabase stack
- Node 18+ (optional for running the frontend outside Docker)

## Quick Start
1) Copy environment defaults  
   `cp .env.example .env`
2) Start everything  
   `make up`  
   (add `USE_DEV=1` for live-reload mounts)  
   This runs `supabase start` (Postgres + API + Auth + Studio, with migrations and seed applied), then brings up Temporal, the worker, and the frontend.
3) Open services  
   - Frontend: http://localhost:3000  
   - Agent: http://localhost:3000/agent  
   - Agent API: http://localhost:8000  
   - Temporal UI: http://localhost:8080  
   - Temporal gRPC: localhost:7234  
   - Supabase API: http://localhost:54321  
   - Supabase Studio: http://localhost:54323

Common commands:
- `make down` — stop containers and the Supabase stack
- `make reset` — tear down volumes + Supabase, then recreate (re-applies migrations and seed)
- `make logs` — stream all service logs
- `make logs-temporal` / `make logs-frontend` — targeted logs
- `make supabase-status` — show Supabase URLs and local keys

## What’s Included
- Local Supabase stack (Postgres + API + Auth + Storage + Studio) via the Supabase CLI, with migrations and seed applied
- Docker Compose stack with Temporal server, UI, worker, and frontend dev server
- Development overrides in `docker-compose.dev.yml` for live-reloading frontend and worker code
- An adaptable agent: a durable tool-calling loop with human approval for writes, where a domain
  supplies tools (`temporal/src/agent/tools.py`) and an agent config (`temporal/agents/*.yaml`) and
  never edits the loop. Defaults to the LiteLLM proxy (`docker compose --profile litellm up -d
  litellm`), and runs with no model credentials at all via `AGENT_PROVIDER=fake`. See
  [`docs/user-guide/agent.md`](docs/user-guide/agent.md).
- Makefile wrappers for the usual lifecycle commands
- `.env.example` capturing required variables for frontend, Temporal, and Supabase

## Notes
- Supabase runs via the CLI (`supabase start`), not docker compose. The worker reaches it at `host.docker.internal:54321`; the browser/frontend at `localhost:54321`.
- The Temporal worker activities call Supabase and SMTP for real; see `docs/runbooks/local-startup.md`
  for what has been verified and what has not.
- Python tests run in the worker image: `docker compose run --rm -v "$PWD/temporal:/app" --entrypoint python temporal-worker -m pytest tests -q`
