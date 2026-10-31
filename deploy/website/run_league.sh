#!/bin/sh
# Start the league runner with the settings in /etc/dflv2/league.env (kept outside the repository: the pace is the owner's decision, not the code's).
#   DFL_PACE   least seconds between the starts of two events            (default 3600)
#   DFL_UNTIL  stop (exit 0) once the league has this many events in all. REQUIRED: a missing limit is refused rather than read as "no limit", so a lost
#              or edited settings file can never leave the league running unbounded. The limit is on the league's TOTAL events, so it holds across restarts
#              and reboots. `none` removes it, deliberately.
set -eu
BASE="${BASE:-/srv/dflv2}"
[ -r "${LEAGUE_ENV:-/etc/dflv2/league.env}" ] && . "${LEAGUE_ENV:-/etc/dflv2/league.env}"
case "${DFL_UNTIL:-}" in
  "") echo "refusing to start: DFL_UNTIL is not set in ${LEAGUE_ENV:-/etc/dflv2/league.env} (a number of events, or 'none')" >&2; exit 2 ;;
  none) exec "${PYTHON:-python3}" -I "$BASE/repo/tools/league_runner.py" "$BASE/league/league.db" --pace "${DFL_PACE:-3600}" ;;
  *[!0-9]*) echo "refusing to start: DFL_UNTIL must be a whole number or 'none'" >&2; exit 2 ;;
esac
exec "${PYTHON:-python3}" -I "$BASE/repo/tools/league_runner.py" "$BASE/league/league.db" --pace "${DFL_PACE:-3600}" --until-events "$DFL_UNTIL"
