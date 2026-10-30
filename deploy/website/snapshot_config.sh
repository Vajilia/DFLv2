#!/bin/sh
# Take the ROLLBACK COPY of the deployment configuration before the first change. Copies (never moves) what the live site depends on into
# $SNAP_ROOT/<timestamp>/ : the Caddyfile, the dfl-* systemd units, the web root listing and a checksum of the web root, and the running-state summary.
# Restore with rollback.sh --config <that directory>. Run as root on the Droplet.
set -eu
SNAP_ROOT="${SNAP_ROOT:-/root/dfl-rollback}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest="$SNAP_ROOT/$stamp"
mkdir -p "$dest"
[ -d /etc/caddy ] && cp -a /etc/caddy "$dest/caddy"
mkdir -p "$dest/systemd"
for u in /etc/systemd/system/dfl-*; do [ -e "$u" ] && cp -a "$u" "$dest/systemd/"; done
[ -d "${WEB_ROOT:-/var/www/dfl}" ] && tar -C "$(dirname "${WEB_ROOT:-/var/www/dfl}")" -czf "$dest/web_root.tgz" "$(basename "${WEB_ROOT:-/var/www/dfl}")"
systemctl list-units --type=service --no-pager > "$dest/services.txt" 2>/dev/null || true
(cd "$dest" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS)
echo "$dest"
