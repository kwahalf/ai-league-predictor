#!/usr/bin/env bash
# Blocks outside access to the Supabase ports of both stacks on the runner.
#
# The Supabase CLI publishes its ports (API, Postgres, Studio, ...) on all network
# interfaces and has no setting to bind them to localhost. Docker's published ports
# also bypass the host firewall (ufw), so the rules go in Docker's own DOCKER-USER
# chain. They drop only NEW connections that arrive on the outside interface;
# traffic from this machine, from containers, and through an SSH tunnel is unaffected.
#
# Usage: sudo scripts/runner/restrict-supabase-ports.sh [apply|remove|status]
# Rules don't survive a reboot on their own: run `apply` at boot (see docs/plans/).
set -euo pipefail

action="${1:-apply}"
tag="ai-league-predictor: block outside access to Supabase"
# dev 54320-54329 + 8083 (includes the mail UI, SUPABASE_LOCAL_SMTP_PORT), live 55320-55329 + 8183 (see stacks/*.env)
ports=(54320:54329 8083 55320:55329 8183)

uplink="${UPLINK:-$(ip route get 1.1.1.1 | awk '{for (i=1;i<NF;i++) if ($i=="dev") print $(i+1); exit}')}"
if [ -z "$uplink" ]; then echo "Can't find the outside interface; set UPLINK=<interface>." >&2; exit 1; fi

tools=(iptables)
if ip6tables -S DOCKER-USER >/dev/null 2>&1; then tools+=(ip6tables); fi

remove() {
  for t in "${tools[@]}"; do
    while read -r rule; do
      [ -n "$rule" ] || continue
      eval "$t ${rule/-A /-D }"
    done < <($t -S DOCKER-USER | grep -F "$tag" || true)
  done
}

case "$action" in
  apply)
    remove
    for t in "${tools[@]}"; do
      for p in "${ports[@]}"; do
        # --ctorigdstport matches the port the client asked for (before Docker's DNAT).
        $t -I DOCKER-USER -i "$uplink" -p tcp -m conntrack --ctstate NEW --ctorigdstport "$p" \
          -m comment --comment "$tag" -j DROP
      done
    done
    echo "Blocked outside access on $uplink to ports ${ports[*]} (${tools[*]})."
    ;;
  remove) remove; echo "Removed." ;;
  status) for t in "${tools[@]}"; do echo "== $t"; $t -S DOCKER-USER | grep -F "$tag" || echo "(no rules)"; done ;;
  *) echo "Usage: $0 [apply|remove|status]" >&2; exit 1 ;;
esac
