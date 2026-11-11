export DOCKER_BUILDKIT=0

# bash, not sh: recipes rely on `pipefail` so a failure anywhere in a pipeline stops the target.
SHELL := /bin/bash

# Which stack to act on: dev (default) or live. Each has its own Supabase project ID,
# Compose project and ports (stacks/<stack>.env), so the two never share containers,
# ports or volumes. See "Hosting on the runner" in docs/plans/.
STACK ?= dev

COMPOSE_BASE=docker-compose.yml
COMPOSE_DEV=docker-compose.dev.yml
USE_DEV?=0

ifeq ($(USE_DEV),1)
COMPOSE_FILES=$(COMPOSE_BASE) $(COMPOSE_DEV)
else
COMPOSE_FILES=$(COMPOSE_BASE)
endif

COMPOSE_CMD=docker compose $(foreach file,$(COMPOSE_FILES),-f $(file))

# Prefix for every recipe line: loads the stack's settings into the environment.
# The assignment carries stack-env.sh's exit status, so an unknown stack stops the recipe
# instead of running it against whatever settings happen to be in the environment.
STACK_ENV=exports="$$(./scripts/stack-env.sh $(STACK))" && eval "$$exports" &&

BACKUP_DIR?=backups

.PHONY: up down reset migrate backup logs logs-temporal logs-frontend supabase-status

# `up` starts the full local Supabase stack (Postgres + API/Kong + Auth +
# Storage + Studio) via the Supabase CLI, applying migrations and seed on first
# start, THEN brings up Temporal + worker + frontend via docker compose.
up:
	@$(STACK_ENV) supabase start
	@$(STACK_ENV) exports="$$(./scripts/supabase-env.sh)" && eval "$$exports" && $(COMPOSE_CMD) up -d --wait
	@$(STACK_ENV) echo "" && echo "Stack '$$STACK_NAME' up. Frontend http://localhost:$$FRONTEND_PORT | Temporal UI http://localhost:$$TEMPORAL_UI_PORT | Supabase Studio http://localhost:$$SUPABASE_STUDIO_PORT"

down:
	@$(STACK_ENV) $(COMPOSE_CMD) down
	@$(STACK_ENV) supabase stop

# Full wipe: tear down compose volumes AND the Supabase stack (incl. its DB),
# then recreate everything from scratch (migrations + seed re-applied).
# Dev only: live holds the prediction track record, which can't be recreated.
# Fails closed: anything other than exactly `dev` is refused before any destructive command.
reset:
	@if [ "$(STACK)" != "dev" ]; then echo "make reset only runs on the dev stack (got STACK=$(STACK)): it would erase that stack's data. For live, use 'make migrate STACK=live'." >&2; exit 1; fi
	@$(STACK_ENV) $(COMPOSE_CMD) down -v
	-@$(STACK_ENV) supabase stop --no-backup
	$(MAKE) up STACK=$(STACK)

# Apply only the migrations the stack doesn't have yet. Never touches existing data.
# Takes a backup first.
migrate: backup
	@$(STACK_ENV) supabase migration up --local

# Dump the stack's database to $(BACKUP_DIR)/<stack>-<UTC timestamp>.sql.gz
# Dumps can contain auth data and the runner is shared: the directory is owner-only (700)
# and every archive is created owner-only (600).
backup:
	@umask 077; mkdir -p $(BACKUP_DIR) && chmod 700 $(BACKUP_DIR) && find $(BACKUP_DIR) -type f -exec chmod 600 {} +
	@umask 077; set -o pipefail; $(STACK_ENV) out="$(BACKUP_DIR)/$$STACK_NAME-$$(date -u +%Y%m%dT%H%M%SZ).sql.gz" && \
		if docker exec "supabase_db_$$SUPABASE_PROJECT_ID" pg_dump -U postgres postgres | gzip > "$$out" && \
			[ "$$(gzip -dc "$$out" | head -c 1 | wc -c)" -eq 1 ]; then echo "Backup written to $$out"; \
		else rm -f "$$out"; echo "Backup FAILED for stack $$STACK_NAME: nothing was changed." >&2; exit 1; fi

logs:
	@$(STACK_ENV) $(COMPOSE_CMD) logs -f

logs-temporal:
	@$(STACK_ENV) $(COMPOSE_CMD) logs -f temporal temporal-worker

logs-frontend:
	@$(STACK_ENV) $(COMPOSE_CMD) logs -f frontend

# Supabase is CLI-managed, not a compose service -- use this for its status/keys.
supabase-status:
	@$(STACK_ENV) supabase status
