#!/bin/sh
# Install the development league's runner and the one-minute exporter, with the league PAUSED. Run as root on the server, after deploy_pinned.sh.
#   install_league.sh [--seed N]       (BASE, SERVICE_USER may be set)
# What it does: installs numpy from the distribution (the engine needs it); creates /srv/dflv2/league/league.db ONCE (never over an existing league)
# as the service user, with the full 22-player engine, the permanent archive and a snapshot after EVERY event (the exporter reads the newest
# snapshot and replays what follows it: measured on the Droplet, a snapshot every 8 events made one export replay a 82-second game week and take 3 minutes); creates the PAUSED file; installs and starts the exporter timer and
# the runner service. It does not touch Caddy, the production site, or the production runner. The league does not advance until
#   python3 /srv/dflv2/repo/tools/league_control.py resume
set -eu
BASE="${BASE:-/srv/dflv2}"; SERVICE_USER="${SERVICE_USER:-dflv2}"; SEED=20261031
[ "${1:-}" = "--seed" ] && SEED="${2:?--seed needs a number}"
say() { printf '%s\n' "$*"; }
[ "$(id -u)" = 0 ] || { say "run as root"; exit 2; }
[ -L "$BASE/repo" ] || { say "no $BASE/repo: run deploy_pinned.sh first"; exit 2; }
python3 -c 'import numpy' 2>/dev/null || { say "installing python3-numpy from the distribution"; DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-numpy; }
python3 -c 'import numpy; print("numpy", numpy.__version__)'
mkdir -p "$BASE/league"; chown "$SERVICE_USER":"$SERVICE_USER" "$BASE/league"; chmod 750 "$BASE/league"
LG="$BASE/league/league.db"
if [ -e "$LG" ]; then say "league exists, left alone: $LG"; else
  runuser -u "$SERVICE_USER" -- python3 -I "$BASE/repo/tools/league_store.py" init "$LG" --seed "$SEED"
  say "league created: $LG (seed $SEED)"
fi
[ -e "$BASE/league/PAUSED" ] || runuser -u "$SERVICE_USER" -- python3 -I "$BASE/repo/tools/league_control.py" pause --dir "$BASE/league" --reason "installed; waiting for the owner to approve a pace"
for u in dfl-feed-export.service dfl-feed-export.timer dflv2-league.service; do cp "$BASE/repo/deploy/website/$u" "/etc/systemd/system/$u"; done
systemctl daemon-reload
systemctl enable dfl-feed-export.timer dflv2-league.service
# restart (not just start) so a changed unit takes effect; the runner stops cleanly and the PAUSED file keeps the league still
systemctl restart dfl-feed-export.timer dflv2-league.service
say "installed. The league is PAUSED. status:"; python3 -I "$BASE/repo/tools/league_control.py" status --dir "$BASE/league"
