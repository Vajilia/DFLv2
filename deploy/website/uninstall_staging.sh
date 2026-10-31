#!/bin/sh
# Take the staging block back out. Run as root:  sh uninstall_staging.sh
# Removes only /etc/caddy/conf.d/dflv2-preview.caddy, validates, reloads. Leaves the data ($BASE), the Caddyfile and the production site alone.
# To put the whole Caddy configuration back as it was before the install use  rollback.sh --config <rollback copy>.
set -eu
CADDY_DIR="${CADDY_DIR:-/etc/caddy}"; CADDYFILE="${CADDYFILE:-$CADDY_DIR/Caddyfile}"; SNIPPET="${CONF_D:-$CADDY_DIR/conf.d}/dflv2-preview.caddy"
[ -f "$SNIPPET" ] || { echo "no staging block installed ($SNIPPET)"; exit 0; }
ROLLBACK_ROOT="${ROLLBACK_ROOT:-/root/dfl-rollback}"
KEPT="$ROLLBACK_ROOT/removed/dflv2-preview.caddy.$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$ROLLBACK_ROOT/removed" && cp -a "$SNIPPET" "$KEPT"
rm -f "$SNIPPET"
if caddy validate --config "$CADDYFILE" --adapter caddyfile; then systemctl reload caddy && echo "staging block removed; Caddy reloaded"
else echo "Caddy rejects the configuration without the block: restoring it" >&2; cp -a "$KEPT" "$SNIPPET"; exit 5; fi
