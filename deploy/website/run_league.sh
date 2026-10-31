#!/bin/sh
# Start the league runner with the settings in /etc/dflv2/league.env (kept outside the repository: the pace is the owner's decision, not the code's).
#   DFL_PACE   least seconds between the starts of two events            (default 3600; the runner is installed PAUSED, so this only matters once resumed)
#   DFL_UNTIL  stop (exit 0) once the league has this many events in all (default: no limit)
set -eu
BASE="${BASE:-/srv/dflv2}"
[ -r "${LEAGUE_ENV:-/etc/dflv2/league.env}" ] && . "${LEAGUE_ENV:-/etc/dflv2/league.env}"
exec "${PYTHON:-python3}" -I "$BASE/repo/tools/league_runner.py" "$BASE/league/league.db" --pace "${DFL_PACE:-3600}" ${DFL_UNTIL:+--until-events "$DFL_UNTIL"}
