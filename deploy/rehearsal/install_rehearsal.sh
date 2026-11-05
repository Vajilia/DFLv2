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
#   ETC_DIR [/etc/dflv2]   MIN_FREE_GB [6: the floor the monitor stops the broadcast at]   MAX_PUBLIC_GB [3]   CHECK_URL [https://$HOST]   CHECK_WAIT_S [180]
#   FORCE_DNS=1 (go on although the host does not resolve here)   ALLOW_NON_ROOT=1 (tests only)
# Exit codes: 2 package/paths/commands, 3 no rollback copy, 4 DNS, 5 pre-flight, 6 Caddy rejected the block (before any change), 7 the live server check failed (block removed again),
#             8 access check failed: something this install put in place cannot be read (or, for private data, can be reached) by the user that must (or must not) reach it.
#
# PERMISSIONS do not depend on the caller's umask. The script sets umask 022 itself and then sets every mode explicitly: the code and the viewer (site/) are world-readable;
# $REH and the empty public folder are 755; the private store and the guard folder are 700 and owned by the service user (never made public, never recursed). The access checks
# then run AS THE REAL SERVICE USERS (root can read everything, so a root-only check proves nothing): Caddy's user (systemctl show caddy -p User, or CADDY_USER) must read the viewer
# and the snippet, `caddy adapt` as that user must accept the whole configuration, and that user must NOT be able to reach private/, monitor/ or the canary; the daemon's user
# (SERVICE_USER) must read the code and write the public, private and monitor folders. Each check walks every parent directory, so an unreadable parent (for example a directory
# left at 750 by an earlier umask-027 run) is caught and named. Nothing is chmod-ed outside what this install created.
set -eu
umask 022
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
CADDY_USER="${CADDY_USER:-$(systemctl show caddy -p User --value 2>/dev/null || true)}"; CADDY_USER="${CADDY_USER:-caddy}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
ETC_DIR="${ETC_DIR:-/etc/dflv2}"
MIN_FREE_GB="${MIN_FREE_GB:-6}"
MAX_PUBLIC_GB="${MAX_PUBLIC_GB:-3}"
CHECK_WAIT_S="${CHECK_WAIT_S:-180}"
SNIPPET="$CONF_D/dflv2-rehearsal.caddy"
CHECK_URL="${CHECK_URL:-https://$HOST}"
say() { printf '%s\n' "$*"; }
die() { code="$1"; shift; say "STOPPED: $*" >&2; exit "$code"; }

# ---- access checks as the real service users (see the header). root bypasses permissions, so every check below runs through runuser.
as_user() { u="$1"; shift; runuser -u "$u" -- "$@"; }
user_checks() {   # 0: checks can run as user $1;  1: skipped (tests without root only);  a real install that cannot check stops
  if [ "$(id -u)" = 0 ] && command -v runuser >/dev/null 2>&1 && id "$1" >/dev/null 2>&1; then return 0; fi
  [ "${ALLOW_NON_ROOT:-}" = 1 ] && return 1
  die 8 "cannot check access as the user '$1' (no such user, or no runuser): refusing to install something that was only verified as root."
}
first_blocked() {  # the first directory on the way to $2 that user $1 cannot enter
  u="$1"; d="$2"; chain=""
  while [ -n "$d" ] && [ "$d" != "/" ] && [ "$d" != "." ]; do chain="$d $chain"; d="$(dirname "$d")"; done
  for d in $chain; do as_user "$u" test -x "$d" 2>/dev/null || { printf '%s' "$d"; return 0; }; done
  return 0
}
why() {  # $1 user, $2 path
  b="$(first_blocked "$1" "$(dirname "$2")")"
  if [ -n "$b" ]; then printf '%s cannot enter %s (mode %s), which is on the way to %s' "$1" "$b" "$(stat -c %a "$b" 2>/dev/null)" "$2"
  else printf '%s cannot read %s (mode %s owner %s)' "$1" "$2" "$(stat -c %a "$2" 2>/dev/null)" "$(stat -c %U:%G "$2" 2>/dev/null)"; fi
}
need_read()  { as_user "$1" test -r "$2" || die 8 "$(why "$1" "$2")"; }
need_enter() { as_user "$1" test -x "$2" || die 8 "$(why "$1" "$2/")"; }
need_write() { { as_user "$1" test -w "$2" && as_user "$1" test -x "$2"; } || die 8 "$1 cannot write in $2 ($(stat -c '%a %U:%G' "$2" 2>/dev/null)) or cannot reach it: $(first_blocked "$1" "$2")"; }
need_tree()  {    # everything under $2 is readable (directories also searchable) by $1
  need_enter "$1" "$2"
  bad="$(as_user "$1" find "$2" \( -type d \( ! -readable -o ! -executable \) -o -type f ! -readable \) -print 2>/dev/null | head -n 3 | tr '\n' ' ')"
  [ -z "$bad" ] || die 8 "$1 cannot read everything under $2, for example: $bad"
}
need_hidden() {   # private data: user $1 must not be able to read or enter $2
  if as_user "$1" test -r "$2" 2>/dev/null || as_user "$1" test -x "$2" 2>/dev/null; then
    die 8 "$1 can reach $2 ($(stat -c '%a %U:%G' "$2" 2>/dev/null)): private data must never be reachable by the web server's user"
  fi
}

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
# the block, rendered, must be accepted by the server's real Caddy on its own BEFORE anything is installed or changed
RENDERED="$(mktemp)"; trap 'rm -f "$RENDERED"' EXIT
sed "s/__HOST__/$HOST/g" "$PKG/repo/deploy/rehearsal/Caddyfile.rehearsal.template" > "$RENDERED"
caddy validate --config "$RENDERED" --adapter caddyfile >/dev/null 2>"$RENDERED.err" || { cat "$RENDERED.err" >&2; rm -f "$RENDERED.err"; die 6 "the real Caddy rejects the rehearsal block on its own: nothing was changed."; }
rm -f "$RENDERED.err"
say "caddy $(caddy version 2>/dev/null | head -n 1): the rehearsal block validates on its own"

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
[ "$free_kb" -ge $((MIN_FREE_GB * 1024 * 1024)) ] || die 2 "less than $MIN_FREE_GB GB free under $parent (the floor the monitor stops the broadcast at; the pre-flight below also leaves room for the public folder to grow to $MAX_PUBLIC_GB GB)"

# ---- 4. the code, as a new directory (never over an existing one)
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home-dir "$REH" --shell /usr/sbin/nologin "$SERVICE_USER"
mkdir -p "$SRC_ROOT"
dest="$SRC_ROOT/$COMMIT"
if [ ! -d "$dest" ]; then
  cp -a "$PKG/repo" "$dest.partial"
  chmod -R u=rwX,go=rX "$dest.partial"                                           # the code is public (it is the repository): readable by the service user whatever modes the package carried
  mv "$dest.partial" "$dest"
else say "code for $COMMIT is already at $dest (kept; its permissions are checked below)"; fi
say "code: $dest"
if user_checks "$SERVICE_USER"; then need_tree "$SERVICE_USER" "$dest"; say "access: $SERVICE_USER can read all of the code"; else say "NOTE: not root: access checks skipped (tests only)"; fi

# ---- 5. data: the private store (the ONLY place the broadcast's unreleased results exist), an empty public folder, the site, the monitor's folder
mkdir -p "$REH/private" "$REH/feed" "$REH/site" "$REH/monitor"
tar -xzf "$PKG/store.tar.gz" -C "$REH/private"
cp -a "$PKG/repo/site/." "$REH/site/"
printf '%s\n' "$COMMIT" > "$REH/.installed_commit"
chown -R "$SERVICE_USER" "$REH/private" "$REH/feed" "$REH/monitor" 2>/dev/null || [ "${ALLOW_NON_ROOT:-}" = 1 ] || die 2 "could not give $REH to $SERVICE_USER"
# explicit modes, whatever the umask or the package carried. Public: $REH, the empty feed, and the viewer (site/) only. Private: never made public, never chmod -R-ed.
chmod 755 "$REH" "$REH/feed"
find "$REH/site" -type d -exec chmod 755 {} +; find "$REH/site" -type f -exec chmod 644 {} +
chmod 700 "$REH/private" "$REH/monitor"
chmod 644 "$REH/.installed_commit"
say "data: $REH (private store, empty public folder, site)"
if user_checks "$CADDY_USER"; then
  need_tree "$CADDY_USER" "$REH/site"; need_read "$CADDY_USER" "$REH/site/index.html"; need_enter "$CADDY_USER" "$REH/feed"
  need_hidden "$CADDY_USER" "$REH/private"; need_hidden "$CADDY_USER" "$REH/monitor"; need_hidden "$CADDY_USER" "$REH/private/store/CANARY.txt"
  say "access: $CADDY_USER reads the viewer and the feed folder, and cannot reach private/, monitor/ or the canary"
fi
if user_checks "$SERVICE_USER"; then
  need_write "$SERVICE_USER" "$REH/feed"; need_write "$SERVICE_USER" "$REH/private"; need_write "$SERVICE_USER" "$REH/monitor"
  say "access: $SERVICE_USER can write the public, private and monitor folders"
fi

# ---- 6. pre-flight: refuse to go on unless the store is complete, the folders are separate and neither is a production path
if ! python3 -I "$dest/tools/rehearsal_check.py" preflight --root "$REH/private" --public "$REH/feed" --min-free-gb "$MIN_FREE_GB" --growth-gb "$MAX_PUBLIC_GB" --require-canary --guard "$REH/monitor"; then
  die 5 "pre-flight failed (above). Nothing in Caddy or systemd was changed. Undo with  sh $PKG/repo/deploy/rehearsal/uninstall_rehearsal.sh --purge"
fi

# ---- 7. systemd units and the environment file. Installed, NOT enabled, NOT started. No timer.
mkdir -p "$SYSTEMD_DIR" "$ETC_DIR"
for u in dfl-rehearsal.service dfl-rehearsal-monitor.service; do
  sed "s/__COMMIT__/$COMMIT/g; s/__HOST__/$HOST/g" "$PKG/repo/deploy/rehearsal/$u" > "$SYSTEMD_DIR/$u.new" && chmod 644 "$SYSTEMD_DIR/$u.new" && mv "$SYSTEMD_DIR/$u.new" "$SYSTEMD_DIR/$u"
done
if [ ! -f "$ETC_DIR/rehearsal.env" ]; then cp "$PKG/repo/deploy/rehearsal/rehearsal.env.template" "$ETC_DIR/rehearsal.env"; chmod 644 "$ETC_DIR/rehearsal.env"; fi
systemctl daemon-reload
say "units installed (not enabled, not started); $ETC_DIR/rehearsal.env holds a deadline in the past: $(cat "$ETC_DIR/rehearsal.env")"

# ---- 8. Caddy: last, validated, reloaded only if valid, and put back if not
mkdir -p "$CONF_D"
had=0; [ -f "$SNIPPET" ] && { cp -a "$SNIPPET" "$SNIPPET.before"; had=1; }
sed "s/__HOST__/$HOST/g" "$PKG/repo/deploy/rehearsal/Caddyfile.rehearsal.template" > "$SNIPPET"
chmod 644 "$SNIPPET"
grep -q '^[[:space:]]*import[[:space:]].*conf\.d' "$CADDYFILE" || say "NOTE: $CADDYFILE has no import line for $CONF_D; the staging install normally added it."
putback() { if [ "$had" = 1 ]; then cp -a "$SNIPPET.before" "$SNIPPET"; else rm -f "${SNIPPET:?}"; fi; }
# Caddy reads its configuration as ITS OWN user, not as root: `caddy validate` as root passed on an unreadable snippet once and the reload then failed. Prove the real reader can adapt it.
if user_checks "$CADDY_USER"; then
  ADAPT_ERR="$(mktemp)"
  if ! { as_user "$CADDY_USER" test -r "$SNIPPET" && as_user "$CADDY_USER" test -r "$CADDYFILE" && as_user "$CADDY_USER" caddy adapt --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>"$ADAPT_ERR"; }; then
    if as_user "$CADDY_USER" test -r "$SNIPPET" 2>/dev/null && as_user "$CADDY_USER" test -r "$CADDYFILE" 2>/dev/null; then reason="both files are readable, so \`caddy adapt\` itself failed as $CADDY_USER: output above"
    elif as_user "$CADDY_USER" test -r "$SNIPPET" 2>/dev/null; then reason="$(why "$CADDY_USER" "$CADDYFILE")"; else reason="$(why "$CADDY_USER" "$SNIPPET")"; fi
    cat "$ADAPT_ERR" >&2; rm -f "$ADAPT_ERR"; putback
    die 8 "Caddy's own user cannot read or adapt the configuration with the rehearsal block in it ($reason). The block was taken out again and Caddy was NOT reloaded."
  fi
  rm -f "$ADAPT_ERR"; say "access: $CADDY_USER can read the block and adapt the whole configuration"
fi
if caddy validate --config "$CADDYFILE" --adapter caddyfile; then
  systemctl reload caddy || { putback; die 6 "Caddy would not reload; the previous rehearsal-block state is back."; }
  say "Caddy reloaded with the rehearsal block for $HOST"
else
  putback
  die 6 "Caddy rejects the whole configuration with the rehearsal block in it: the previous configuration is back and Caddy was not reloaded."
fi

# ---- 9. the live server rules, proven on the real server before this script says INSTALLED: cache headers per kind of file, no listings, no private names, write
# methods refused, and a harmless private canary that must be unreachable by ~300 encoded path-traversal requests. The feed is empty (the daemon has not run).
CHECK_LOG="$(mktemp)"; ok=0; waited=0
while :; do
  if python3 -I "$dest/tools/rehearsal_check.py" http --url "$CHECK_URL" --before-start --canary "$REH/private/store/CANARY.txt" >"$CHECK_LOG" 2>&1; then ok=1; break; fi
  [ "$waited" -ge "$CHECK_WAIT_S" ] && break
  sleep 10; waited=$((waited + 10))                                            # a new host may need a minute for its certificate
done
if [ "$ok" = 1 ]; then tail -n 3 "$CHECK_LOG"; rm -f "$CHECK_LOG"; say "server rules verified live (headers, listings, write methods, traversal, canary)"
else
  cat "$CHECK_LOG" >&2; cp -a "$CHECK_LOG" "$REH/monitor/install-check.log" 2>/dev/null || true; rm -f "$CHECK_LOG"
  putback
  caddy validate --config "$CADDYFILE" --adapter caddyfile && systemctl reload caddy
  die 7 "the live server check failed (output above, kept in $REH/monitor/install-check.log): the rehearsal block was taken out again and Caddy reloaded. Units are installed but not enabled."
fi
rm -f "$SNIPPET.before"
say ""
say "INSTALLED. Nothing is running. Next: docs/BROADCAST_REHEARSAL.md, section 'Start'."
