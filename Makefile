export DOCKER_BUILDKIT=0

COMPOSE_BASE=docker-compose.yml
COMPOSE_DEV=docker-compose.dev.yml
USE_DEV?=0

ifeq ($(USE_DEV),1)
COMPOSE_FILES=$(COMPOSE_BASE) $(COMPOSE_DEV)
else
COMPOSE_FILES=$(COMPOSE_BASE)
endif

COMPOSE_CMD=docker compose $(foreach file,$(COMPOSE_FILES),-f $(file))

.PHONY: up down reset logs logs-temporal logs-frontend supabase-status

# `up` starts the full local Supabase stack (Postgres + API/Kong + Auth +
# Storage + Studio) via the Supabase CLI, applying migrations and seed, THEN
# brings up Temporal + worker + frontend via docker compose.
up:
	supabase start
	@exports="$$(./scripts/supabase-env.sh)" && eval "$$exports" && $(COMPOSE_CMD) up -d --wait
	@echo ""
	@echo "Stack up. Frontend http://localhost:3000 | Temporal UI http://localhost:8080 | Supabase Studio http://localhost:54323"

down:
	$(COMPOSE_CMD) down
	supabase stop

# Full wipe: tear down compose volumes AND the Supabase stack (incl. its DB),
# then recreate everything from scratch (migrations + seed re-applied).
reset:
	$(COMPOSE_CMD) down -v
	-supabase stop --no-backup
	$(MAKE) up

logs:
	$(COMPOSE_CMD) logs -f

logs-temporal:
	$(COMPOSE_CMD) logs -f temporal temporal-worker

logs-frontend:
	$(COMPOSE_CMD) logs -f frontend

# Supabase is CLI-managed, not a compose service -- use this for its status/keys.
supabase-status:
	supabase status
