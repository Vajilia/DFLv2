#!/bin/sh
# Install the broadcast REHEARSAL beside the live site. Run as root on the Droplet, from the unpacked rehearsal package:
#     sh install_rehearsal.sh PACKAGE_DIR
# It installs files, a Caddy block and two systemd units. It NEVER starts or enables a unit, never creates a timer, never touches /srv/dflv2 (the production
# league, feed and site), never restarts Caddy (only `systemctl reload`, and only after `caddy validate` passes) and puts the previous Caddy configuration
# back itself if validation fails. The installed environment file holds a deadline in the PAST: even an accidental `systemctl start dfl-rehearsal` pauses
# at once and exits. See docs/BROADCAST_REHEARSAL.md.
#
# Environment (defaults in brackets):
#   HOST [rehearsal.diamondfootballleague.com]   REH [/srv/dflv2-rehearsal]   SRC_ROOT [/opt/dflv2-src]   CADDY_DIR [/etc/caddy]   CADDYFILE [$CADDY_DIR/Caddyfile]
#   CONF_D [$CADDY_DIR/conf.d]   ROLLBACK_ROOT [/root/dfl-rollback]   SNAPSHOT_MAX_AGE_MIN [1440]   SERVICE_USER [dflv2]   SYSTEMD_DIR [/etc/systemd/system]
#   ETC_DIR [/etc/dflv2]   MIN_FREE_GB [6]   FORCE_DNS=1 (go on although the host does not resolve here)   ALLOW_NON_ROOT=1 (tests only)
set -eu
PKG="${1:?usage: install_rehearsal.sh PACKAGE_DIR}"
HOST="${HOST:-rehearsal.diamondfootballleague.com}"
REH="${REH:-/srv/dflv2-rehearsal}"
SRC_ROOT="${SRC_ROOT:-/opt/dflv2-src}"
CADDY_DIR="${CADDY_DIR:-/etc/caddy}"
CADDYFILE="${CADDYFILE:-$CADDY_DIR/Caddyfile}"
CONF_D="${CONF_D:-$CADDY_DIR/conf.d}"
ROLLBACK_ROOT="${ROLLBACK_ROOT:-/root/dfl-rollback}"
SNAPSHOT_MAX_AGE_MIN="${SNAPSHOT_MAX_AGE_MIN:-1440}"
SERVICE_USER="${SERVICE_USER:-dflv2}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
ETC_DIR="${ETC_DIR:-/etc/dflv2}"
MIN_FREE_GB="${MIN_FREE_GB:-6}"
SNIPPET="$CONF_D/dflv2-rehearsal.caddy"
say() { printf '%s\n' "$*"; }
die() { code="$1"; shift; say "STOPPED: $*" >&2; exit "$code"; }

# ---- 0. preconditions (nothing is changed before all of these hold)
[ "$(id -u)" = 0 ] || [ "${ALLOW_NON_ROOT:-}" = 1 ] || die 2 "run as root"
for c in caddy systemctl python3 sha256sum tar; do command -v "$c" >/dev/null 2>&1 || die 2 "missing command: $c"; done
[ -f "$PKG/PACKAGE.json" ] && [ -f "$PKG/SHA256SUMS" ] && [ -d "$PKG/repo" ] && [ -f "$PKG/store.tar.gz" ] || die 2 "$PKG is not an unpacked rehearsal package"
(cd "$PKG" && sha256sum -c SHA256SUMS --quiet) || die 2 "the package does not match its SHA256SUMS: do not install it"
COMMIT="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['commit'])" "$PKG/PACKAGE.json")"
case "$COMMIT" in *[!0-9a-f]*|"") die 2 "PACKAGE.json has no 40-hex commit";; esac
[ "${#COMMIT}" = 40 ] || die 2 "PACKAGE.json has no 40-hex commit"
say "rehearsal package for commit $COMMIT verified"
case "$REH" in /srv/dflv2|/srv/dflv2/*|/var/www|/var/www/*|/|"") die 2 "REH=$REH is a production or system path";; esac
case "$(basename "$REH")" in *rehearsal*) ;; *) die 2 "REH must be a folder with 'rehearsal' in its name (got $REH)";; esac
[ ! -e "$REH" ] || die 2 "$REH already exists. Nothing is overwritten: remove it with  sh uninstall_rehearsal.sh --purge  first."
[ -f "$CADDYFILE" ] || die 2 "no Caddyfile at $CADDYFILE (set CADDYFILE if it lives elsewhere)"

# ---- 1. a rollback copy must exist and be recent
if ! find "$ROLLBACK_ROOT" -maxdepth 1 -mindepth 1 -mmin "-$SNAPSHOT_MAX_AGE_MIN" 2>/dev/null | grep -q .; then
  die 3 "no rollback copy newer than $SNAPSHOT_MAX_AGE_MIN minutes in $ROLLBACK_ROOT. Run  sh $PKG/repo/deploy/website/snapshot_config.sh  first (and take the DigitalOcean snapshot)."
fi
say "rollback copy found: $(ls -1dt "$ROLLBACK_ROOT"/* | head -n 1)"

# ---- 2. DNS: the host must already point here, or HTTPS cannot be issued
mine="$( (ip -o addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1; curl -fsS -m 3 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null) | sort -u)"
theirs="$(getent ahosts "$HOST" 2>/dev/null | awk '{print $1}' | sort -u)"
if [ -z "$theirs" ]; then
  [ "${FORCE_DNS:-}" = 1 ] || die 4 "$HOST does not resolve yet. Add its A record first (docs/BROADCAST_REHEARSAL.md, step 3), wait for it, and run this again."
  say "WARNING: $HOST does not resolve; continuing because FORCE_DNS=1"
else
  hit=""; for a in $theirs; do echo "$mine" | grep -qx "$a" && hit="$a"; done
  if [ -z "$hit" ]; then
    [ "${FORCE_DNS:-}" = 1 ] || die 4 "$HOST resolves to [$(echo $theirs)] but this machine's addresses are [$(echo $mine)]. Fix the DNS record (or FORCE_DNS=1)."
    say "WARNING: DNS does not point at this machine; continuing because FORCE_DNS=1"
  else say "DNS ok: $HOST -> $hit (this machine)"; fi
fi

# ---- 3. disk
parent="$(dirname "$REH")"; mkdir -p "$parent"
free_kb="$(df -Pk "$parent" | awk 'NR==2{print $4}')"
[ "$free_kb" -ge $((MIN_FREE_GB * 1024 * 1024)) ] || die 2 "less than $MIN_FREE_GB GB free under $parent"

# ---- 4. the code, as a new directory (never over an existing one)
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home-dir "$REH" --shell /usr/sbin/nologin "$SERVICE_USER"
mkdir -p "$SRC_ROOT"
dest="$SRC_ROOT/$COMMIT"
if [ ! -d "$dest" ]; then cp -a "$PKG/repo" "$dest.partial" && mv "$dest.partial" "$dest"; else say "code for $COMMIT is already at $dest (kept)"; fi
say "code: $dest"

# ---- 5. data: the private store (the ONLY place the broadcast's unreleased results exist), an empty public folder, the site, the monitor's folder
mkdir -p "$REH/private" "$REH/feed" "$REH/site" "$REH/monitor"
tar -xzf "$PKG/store.tar.gz" -C "$REH/private"
cp -a "$PKG/repo/site/." "$REH/site/"
printf '%s\n' "$COMMIT" > "$REH/.installed_commit"
chmod 755 "$REH" "$REH/site" "$REH/feed"; chmod 750 "$REH/private" "$REH/monitor"
chown -R "$SERVICE_USER" "$REH/private" "$REH/feed" "$REH/monitor" 2>/dev/null || [ "${ALLOW_NON_ROOT:-}" = 1 ] || die 2 "could not give $REH to $SERVICE_USER"
say "data: $REH (private store, empty public folder, site)"

# ---- 6. pre-flight: refuse to go on unless the store is complete, the folders are separate and neither is a production path
if ! python3 -I "$dest/tools/rehearsal_check.py" preflight --root "$REH/private" --public "$REH/feed" --min-free-gb "$MIN_FREE_GB"; then
  die 5 "pre-flight failed (above). Nothing in Caddy or systemd was changed. Undo with  sh $PKG/repo/deploy/rehearsal/uninstall_rehearsal.sh --purge"
fi

# ---- 7. systemd units and the environment file. Installed, NOT enabled, NOT started. No timer.
mkdir -p "$SYSTEMD_DIR" "$ETC_DIR"
for u in dfl-rehearsal.service dfl-rehearsal-monitor.service; do
  sed "s/__COMMIT__/$COMMIT/g; s/__HOST__/$HOST/g" "$PKG/repo/deploy/rehearsal/$u" > "$SYSTEMD_DIR/$u.new" && mv "$SYSTEMD_DIR/$u.new" "$SYSTEMD_DIR/$u"
done
[ -f "$ETC_DIR/rehearsal.env" ] || cp "$PKG/repo/deploy/rehearsal/rehearsal.env.template" "$ETC_DIR/rehearsal.env"
systemctl daemon-reload
say "units installed (not enabled, not started); $ETC_DIR/rehearsal.env holds a deadline in the past: $(cat "$ETC_DIR/rehearsal.env")"

# ---- 8. Caddy: last, validated, reloaded only if valid, and put back if not
mkdir -p "$CONF_D"
had=0; [ -f "$SNIPPET" ] && { cp -a "$SNIPPET" "$SNIPPET.before"; had=1; }
sed "s/__HOST__/$HOST/g" "$PKG/repo/deploy/rehearsal/Caddyfile.rehearsal.template" > "$SNIPPET"
grep -q '^[[:space:]]*import[[:space:]].*conf\.d' "$CADDYFILE" || say "NOTE: $CADDYFILE has no import line for $CONF_D; the staging install normally added it."
if caddy validate --config "$CADDYFILE" --adapter caddyfile; then
  systemctl reload caddy && say "Caddy reloaded with the rehearsal block for $HOST"
else
  if [ "$had" = 1 ]; then mv "$SNIPPET.before" "$SNIPPET"; else rm -f "${SNIPPET:?}"; fi
  die 6 "Caddy rejects the rehearsal block: the previous configuration is back and Caddy was not reloaded."
fi
say ""
say "INSTALLED. Nothing is running. Next: docs/BROADCAST_REHEARSAL.md, section 'Start'."
