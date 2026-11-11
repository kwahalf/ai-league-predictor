#!/usr/bin/env bash
# Emits `export VAR=value` lines for one stack (dev or live), read from
# stacks/<stack>.env, plus the URLs derived from its ports.
#
# The Supabase CLI reads SUPABASE_* variables as overrides of supabase/config.toml
# (SUPABASE_PROJECT_ID, SUPABASE_API_PORT, ...), and docker compose reads the rest.
# Exported values win over .env, so a stack's ports and URLs can't be overridden
# by a stale .env by accident.
set -euo pipefail

stack="${1:-dev}"
here="$(cd "$(dirname "$0")/.." && pwd)"
file="$here/stacks/$stack.env"

if [ ! -f "$file" ]; then
  echo "stack-env.sh: unknown stack '$stack' (expected one of: $(ls "$here/stacks" | sed 's/\.env$//' | tr '\n' ' '))" >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a; . "$file"; set +a

grep -E '^[A-Z_]+=' "$file" | cut -d= -f1 | while read -r var; do
  printf 'export %s=%q\n' "$var" "${!var}"
done

# URLs derived from the ports. The worker runs inside Docker and reaches the host's
# Supabase via host.docker.internal; the browser reaches everything on localhost.
printf 'export SUPABASE_URL=%q\n' "http://host.docker.internal:$SUPABASE_API_PORT"
printf 'export VITE_SUPABASE_URL=%q\n' "http://localhost:$SUPABASE_API_PORT"
printf 'export VITE_API_URL=%q\n' "http://localhost:$SUPABASE_API_PORT/functions/v1"
printf 'export VITE_AGENT_API_URL=%q\n' "http://localhost:$AGENT_API_PORT"
printf 'export AGENT_API_ALLOWED_ORIGINS=%q\n' "http://localhost:$FRONTEND_PORT,http://127.0.0.1:$FRONTEND_PORT"
printf 'export TEMPORAL_CORS_ORIGINS=%q\n' "http://localhost:$FRONTEND_PORT"
printf 'export SUPABASE_AUTH_SITE_URL=%q\n' "http://127.0.0.1:$FRONTEND_PORT"
