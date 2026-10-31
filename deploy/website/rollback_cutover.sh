#!/bin/sh
# Undo cutover_production.sh: put the old production site block back, remove the production drop-in, re-enable and start the old services.
#     sh rollback_cutover.sh SNAPDIR            (the rollback copy printed by cutover_production.sh)
#     sh rollback_cutover.sh SNAPDIR --dry-run  (checks that the saved configuration still validates and the saved units exist; changes nothing)
# The preview site's drop-in is left as it is. Run as root on the Droplet.
set -eu
SNAP="${1:?usage: rollback_cutover.sh SNAPDIR [--dry-run]}"
MODE="${2:-}"
CADDY_DIR="${CADDY_DIR:-/etc/caddy}"
CADDYFILE="${CADDYFILE:-$CADDY_DIR/Caddyfile}"
CONF_D="${CONF_D:-$CADDY_DIR/conf.d}"
OLD_UNITS="${OLD_UNITS:-dfl-live dfl-control dfl-chronicle}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
SNIPPET="$CONF_D/dflv2-production.caddy"
say() { printf '%s\n' "$*"; }
die() { code="$1"; shift; say "STOPPED: $*" >&2; exit "$code"; }
[ "$(id -u)" = 0 ] || [ "${ALLOW_NON_ROOT:-}" = 1 ] || die 2 "run as root"
[ -f "$SNAP/caddy/Caddyfile" ] || die 2 "$SNAP is not a rollback copy (no caddy/Caddyfile)"
(cd "$SNAP" && sha256sum -c SHA256SUMS --quiet) || die 3 "the rollback copy does not match its checksums"
for u in $OLD_UNITS; do [ -f "$SNAP/systemd/$u.service" ] || die 2 "the rollback copy has no $u.service"; done
if [ "$MODE" = "--dry-run" ]; then
  T="$(mktemp -d)"; chmod 700 "$T"; trap 'rm -rf "$T"' EXIT
  mkdir -p "$T/conf.d"
  # the saved Caddyfile with its import pointed at a scratch drop-in directory that holds only the preview block (the production block is what rollback removes)
  sed "s|$CONF_D|$T/conf.d|g" "$SNAP/caddy/Caddyfile" > "$T/Caddyfile"
  [ -f "$CONF_D/dflv2-preview.caddy" ] && cp "$CONF_D/dflv2-preview.caddy" "$T/conf.d/"
  caddy validate --config "$T/Caddyfile" --adapter caddyfile >/dev/null 2>&1 || die 5 "the SAVED Caddyfile does not validate: rollback would fail"
  for u in $OLD_UNITS; do
    [ -f "$UNIT_DIR/$u.service" ] && ! cmp -s "$SNAP/systemd/$u.service" "$UNIT_DIR/$u.service" && die 5 "$u.service differs from the saved copy"
  done
  say "rollback path OK: the saved Caddyfile validates with the preview block, and the saved units are present ($OLD_UNITS). Nothing was changed."
  exit 0
fi
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
cp -a "$CADDYFILE" "$CADDYFILE.dfl-rollback.$stamp.bak"
if [ -f "$SNIPPET" ]; then mkdir -p "$SNAP/removed_by_rollback"; mv "$SNIPPET" "$SNAP/removed_by_rollback/dflv2-production.caddy.$stamp"; fi
cp -a "$SNAP/caddy/Caddyfile" "$CADDYFILE"
for u in $OLD_UNITS; do [ -f "$UNIT_DIR/$u.service" ] || cp -a "$SNAP/systemd/$u.service" "$UNIT_DIR/$u.service"; done
systemctl daemon-reload
if ! caddy validate --config "$CADDYFILE" --adapter caddyfile; then
  cp -a "$CADDYFILE.dfl-rollback.$stamp.bak" "$CADDYFILE"
  [ -f "$SNAP/removed_by_rollback/dflv2-production.caddy.$stamp" ] && mv "$SNAP/removed_by_rollback/dflv2-production.caddy.$stamp" "$SNIPPET"
  die 5 "the restored configuration did not validate; the state before this rollback was put back."
fi
for u in $OLD_UNITS; do
  if grep -q "^$u enabled=enabled" "$SNAP/cutover_state.txt" 2>/dev/null; then systemctl enable --now "$u" || say "WARNING: could not start $u"; fi
done
systemctl reload caddy || die 6 "Caddy would not reload"
say "rolled back: the old production site block is back, the old services are enabled and started as they were. The DFLv2 league, its feed and the preview site were not touched."
