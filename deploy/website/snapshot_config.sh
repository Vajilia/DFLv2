#!/bin/sh
# Take the ROLLBACK COPY of the deployment configuration before the first change. Copies (never moves) what the live site depends on into
# $SNAP_ROOT/<timestamp>/ : the Caddyfile, the dfl-* systemd units, the web root listing and a checksum of the web root, and the running-state summary.
# Restore with rollback.sh --config <that directory>. Run as root on the Droplet.
set -eu
SNAP_ROOT="${SNAP_ROOT:-/root/dfl-rollback}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest="$SNAP_ROOT/$stamp"
mkdir -p "$dest"
CADDY_DIR="${CADDY_DIR:-/etc/caddy}"; UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
[ -d "$CADDY_DIR" ] && cp -a "$CADDY_DIR" "$dest/caddy"
mkdir -p "$dest/systemd"
for u in "$UNIT_DIR"/dfl-*; do [ -e "$u" ] && cp -a "$u" "$dest/systemd/"; done
if [ -d "${WEB_ROOT:-/var/www/dfl}" ]; then
  # tar exits 1 when a file changed while it was read (the live runner writes into the web root); that is a warning, not a failure. Exit 2 or more is.
  rc=0; tar -C "$(dirname "${WEB_ROOT:-/var/www/dfl}")" -czf "$dest/web_root.tgz" "$(basename "${WEB_ROOT:-/var/www/dfl}")" || rc=$?
  [ "$rc" -le 1 ] || { echo "tar failed with exit $rc" >&2; exit "$rc"; }
  tar -tzf "$dest/web_root.tgz" >/dev/null
fi
systemctl list-units --type=service --no-pager > "$dest/services.txt" 2>/dev/null || true
(cd "$dest" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS)
echo "$dest"
