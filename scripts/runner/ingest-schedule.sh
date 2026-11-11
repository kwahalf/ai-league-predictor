#!/usr/bin/env bash
# Creates the daily football ingest Temporal Schedule (06:00 UTC) on a running stack, or brings an
# existing one back to the definition in temporal/src/football/ingest/cli.py. Safe to re-run:
# it never creates a second schedule.
#
# Usage: scripts/runner/ingest-schedule.sh [dev|live]     (default: live)
# Run from the checkout whose stack is running, in the docker group. The live systemd unit runs it
# after `make up STACK=live` at every boot.
set -euo pipefail

stack="${1:-live}"
cd "$(dirname "$0")/../.."
exports="$(./scripts/stack-env.sh "$stack")" && eval "$exports"

# The worker container can take a moment to connect to Temporal after `make up`; retry briefly.
for attempt in 1 2 3 4 5 6; do
  if docker compose exec -T temporal-worker python -m src.football.ingest.cli schedule; then
    exit 0
  fi
  echo "ingest-schedule.sh: attempt $attempt failed on stack '$STACK_NAME'; retrying in 10s" >&2
  sleep 10
done
echo "ingest-schedule.sh: could not create the schedule on stack '$STACK_NAME'" >&2
exit 1
