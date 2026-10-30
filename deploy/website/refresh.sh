#!/bin/sh
# The refresh path: export the newest committed state of a league file as a feed release and publish it. Safe to run at any time and as often as
# you like (a second run with nothing new changes nothing). It reads a COPY of the league file; it never writes to the league.
#   refresh.sh LEAGUE_FILE            (BASE, REPO, PYTHON may be set)
set -eu
BASE="${BASE:-/srv/dflv2}"
REPO="${REPO:-$BASE/repo}"
exec "${PYTHON:-python3}" -I "$REPO/tools/export_public_feed.py" "${1:?usage: refresh.sh LEAGUE_FILE}" --out "$BASE/feed" ${EXPORT_ARGS:-}
