#!/bin/sh
# exporter-loop.sh RIG : imitates the dfl-feed-export timer (which rewrites status.json every minute) about 80x as aggressively, until RIG/exporter.stop exists
R=${1:?usage: exporter-loop.sh RIG}
. "$R/env.sh"
while [ ! -e "$R/exporter.stop" ]; do
  python3 -I "$ENGINE_TREE/tools/export_public_feed.py" "$LIVE_LEAGUE" --out "$LIVE_FEED" --use-league-code --skip-if-current --runner-status "$R/srv/dflv2/league/runner_status.json" >/dev/null 2>&1
  echo "$(date +%s.%N)" >> "$R/exporter.ticks"
  sleep 0.2
done
