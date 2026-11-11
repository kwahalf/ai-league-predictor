#!/usr/bin/env bash
# Puts the long-lived live checkout on a release tag and rebuilds the live images.
#
# Live runs tagged releases, not the latest main (plan, "Hosting on the runner"). This script only
# prepares the code: it does NOT start, stop, migrate or reset anything. The runbook
# (docs/runbooks/live-setup.md) says what to run next.
#
# Usage: scripts/runner/live-deploy.sh <release-tag>          e.g. v0.2
# Run as the user that owns the live checkout (ubuntu), in the docker group (`sg docker -c ...`).
# Settings (environment):
#   LIVE_DIR   the live checkout (default /home/ubuntu/ai-league-predictor-live). Must be outside
#              Claude session folders, which aren't guaranteed to last.
#   REPO_URL   where to clone from the first time (default the GitHub repo over HTTPS; a private
#              repo needs git credentials for this user, e.g. `gh auth setup-git`).
set -euo pipefail

tag="${1:-}"
if [ -z "$tag" ]; then
  echo "Usage: $0 <release-tag>   (e.g. v0.2)" >&2
  exit 1
fi
LIVE_DIR="${LIVE_DIR:-/home/ubuntu/ai-league-predictor-live}"
REPO_URL="${REPO_URL:-https://github.com/kwahalf/ai-league-predictor.git}"

case "$LIVE_DIR" in
  /*) ;;
  *) echo "LIVE_DIR must be an absolute path, got '$LIVE_DIR'." >&2; exit 1 ;;
esac
case "$LIVE_DIR" in
  */_sessions/*|*/claude-work/*)
    echo "Refusing: LIVE_DIR '$LIVE_DIR' is inside a Claude session folder. Live needs a long-lived checkout." >&2
    exit 1 ;;
esac

if [ ! -d "$LIVE_DIR/.git" ]; then
  if [ -e "$LIVE_DIR" ] && [ -n "$(ls -A "$LIVE_DIR" 2>/dev/null)" ]; then
    echo "Refusing: '$LIVE_DIR' exists, isn't a git checkout and isn't empty." >&2
    exit 1
  fi
  echo "Cloning $REPO_URL into $LIVE_DIR"
  git clone --quiet "$REPO_URL" "$LIVE_DIR"
fi

cd "$LIVE_DIR"
git fetch --quiet --tags --force origin
if ! git rev-parse -q --verify "refs/tags/$tag^{commit}" >/dev/null; then
  echo "'$tag' is not a tag in $REPO_URL. Live only runs tagged releases." >&2
  exit 1
fi
# Tracked files must be untouched: live runs exactly the release. (.env and backups/ are ignored.)
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Refusing: $LIVE_DIR has local changes to tracked files:" >&2
  git status --short --untracked-files=no >&2
  exit 1
fi

previous="$(git describe --tags --always 2>/dev/null || echo none)"
git -c advice.detachedHead=false checkout --quiet --detach "refs/tags/$tag"
echo "Live checkout: $previous -> $(git describe --tags --always) ($(git rev-parse --short HEAD))"

# The worker, agent API and frontend images are built from this checkout. `make up` only builds
# missing images, so rebuild here or a new release would keep running the old code.
# Two statements, not `a && b`: under `set -e` a failure on the left of `&&` doesn't stop the
# script, and compose would then build for whatever stack the environment defaults to.
exports="$(./scripts/stack-env.sh live)"
eval "$exports"
docker compose build temporal-worker agent-api frontend
echo "Images for stack '$STACK_NAME' rebuilt from $tag. Nothing was started or migrated."
