#!/usr/bin/env bash
# Installs and enables the systemd units that run the live stack at boot:
#   ai-league-predictor-firewall.service   restrict-supabase-ports.sh apply (root, from a root-owned copy)
#   ai-league-predictor-live.service       make up STACK=live, then the ingest schedule (as LIVE_USER)
#
# Usage: sudo LIVE_DIR=/home/ubuntu/ai-league-predictor-live scripts/runner/install-live-units.sh
# Settings (environment): LIVE_DIR (default /home/ubuntu/ai-league-predictor-live),
#                         LIVE_USER (default: the owner of LIVE_DIR).
# It enables the units but doesn't start them: see docs/runbooks/live-setup.md for the switch-over.
# Re-running it rewrites the units in place.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: it writes to /etc/systemd/system." >&2
  exit 1
fi

LIVE_DIR="${LIVE_DIR:-/home/ubuntu/ai-league-predictor-live}"
case "$LIVE_DIR" in
  */_sessions/*|*/claude-work/*)
    echo "Refusing: LIVE_DIR '$LIVE_DIR' is inside a Claude session folder." >&2; exit 1 ;;
esac
if [ ! -f "$LIVE_DIR/stacks/live.env" ] || [ ! -x "$LIVE_DIR/scripts/runner/restrict-supabase-ports.sh" ]; then
  echo "'$LIVE_DIR' is not a checkout of this repo (run scripts/runner/live-deploy.sh first)." >&2
  exit 1
fi
LIVE_USER="${LIVE_USER:-$(stat -c %U "$LIVE_DIR")}"
LIVE_HOME="$(getent passwd "$LIVE_USER" | cut -d: -f6)"
if [ "$LIVE_USER" = "root" ] || [ -z "$LIVE_HOME" ]; then
  echo "Refusing: the live stack must run as a normal user, got '$LIVE_USER'." >&2
  exit 1
fi

# Units come from this script's own checkout, so a reviewed copy is installed.
here="$(cd "$(dirname "$0")" && pwd)"

# The firewall unit runs as root: give it a root-owned copy of the script, so nobody who can write
# to the checkout can change what root runs at boot. Re-run this installer after a release that
# changes restrict-supabase-ports.sh.
install -o root -g root -m 755 "$here/restrict-supabase-ports.sh" /usr/local/sbin/ai-league-predictor-restrict-ports
echo "Installed /usr/local/sbin/ai-league-predictor-restrict-ports"
for unit in ai-league-predictor-firewall.service ai-league-predictor-live.service; do
  sed -e "s|@LIVE_DIR@|$LIVE_DIR|g" -e "s|@USER@|$LIVE_USER|g" -e "s|@HOME@|$LIVE_HOME|g" \
    "$here/systemd/$unit" > "/etc/systemd/system/$unit"
  chmod 644 "/etc/systemd/system/$unit"
  echo "Installed /etc/systemd/system/$unit"
done

systemctl daemon-reload
systemctl enable ai-league-predictor-firewall.service ai-league-predictor-live.service
echo "Enabled. Not started: follow docs/runbooks/live-setup.md to switch live over."
