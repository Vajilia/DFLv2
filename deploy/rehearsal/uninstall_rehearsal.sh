#!/bin/sh
# Take the broadcast REHEARSAL back out. Run as root:  sh uninstall_rehearsal.sh [--purge]
# Always: stops and removes the two rehearsal units, removes /etc/caddy/conf.d/dflv2-rehearsal.caddy (a copy is kept under $ROLLBACK_ROOT/removed), validates,
# reloads Caddy (the snippet is put back if Caddy then rejects the configuration).
# --purge also deletes $REH (the private store, the public folder, the monitor's logs), the code under $SRC_ROOT that this install put there, and the
# environment file. Without --purge the data and the logs stay for inspection. It never touches /srv/dflv2 (league, feed, site, backups) or Caddy's other blocks.
# Removing the `rehearsal` DNS record at IONOS is a manual step.
set -eu
REH="${REH:-/srv/dflv2-rehearsal}"; SRC_ROOT="${SRC_ROOT:-/opt/dflv2-src}"; CADDY_DIR="${CADDY_DIR:-/etc/caddy}"; CADDYFILE="${CADDYFILE:-$CADDY_DIR/Caddyfile}"
SNIPPET="${CONF_D:-$CADDY_DIR/conf.d}/dflv2-rehearsal.caddy"; ROLLBACK_ROOT="${ROLLBACK_ROOT:-/root/dfl-rollback}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"; ETC_DIR="${ETC_DIR:-/etc/dflv2}"
PURGE=0; [ "${1:-}" = "--purge" ] && PURGE=1
say() { printf '%s\n' "$*"; }
[ "$(id -u)" = 0 ] || [ "${ALLOW_NON_ROOT:-}" = 1 ] || { say "run as root" >&2; exit 2; }
case "$REH" in /srv/dflv2|/srv/dflv2/*|/var/www|/var/www/*|/|""|*..*) say "REH=$REH is a production or system path: refusing" >&2; exit 2;; esac
case "$(basename "$REH")" in *rehearsal*) ;; *) say "REH must have 'rehearsal' in its name: refusing" >&2; exit 2;; esac

for u in dfl-rehearsal-monitor dfl-rehearsal; do
  systemctl stop "$u" 2>/dev/null || true
  systemctl disable "$u" 2>/dev/null || true
  rm -f "${SYSTEMD_DIR:?}/${u:?}.service"
done
systemctl daemon-reload
say "rehearsal units stopped and removed"

if [ -f "$SNIPPET" ]; then
  mkdir -p "$ROLLBACK_ROOT/removed"; KEPT="$ROLLBACK_ROOT/removed/dflv2-rehearsal.caddy.$(date -u +%Y%m%dT%H%M%SZ)"
  cp -a "$SNIPPET" "$KEPT"; rm -f "${SNIPPET:?}"
  if caddy validate --config "$CADDYFILE" --adapter caddyfile; then systemctl reload caddy && say "rehearsal block removed; Caddy reloaded"
  else say "Caddy rejects the configuration without the block: restoring it" >&2; cp -a "$KEPT" "$SNIPPET"; exit 5; fi
else say "no rehearsal Caddy block installed"; fi

if [ "$PURGE" = 1 ]; then
  if [ -f "$REH/.installed_commit" ]; then
    c="$(cat "$REH/.installed_commit")"
    case "$c" in *[!0-9a-f]*|"") ;; *) if [ -d "$SRC_ROOT/$c" ]; then rm -rf "${SRC_ROOT:?}/${c:?}"; say "removed $SRC_ROOT/$c"; fi;; esac
  fi
  rm -rf "${REH:?}"; say "removed $REH"
  rm -f "${ETC_DIR:?}/rehearsal.env"
else say "data kept in $REH (use --purge to delete it)"; fi
say "done. Production (/srv/dflv2) was not touched."
