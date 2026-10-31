#!/bin/sh
# Install the DFLv2 STAGING site beside the live one. Run as root on the Droplet, from the unpacked deployment package:
#     sh install_staging.sh PACKAGE_DIR
# It never touches the production site block, never restarts Caddy (only `systemctl reload`, and only after `caddy validate` passes), and undoes its
# Caddy change itself if validation fails. Every step is repeatable.  See docs/STAGING_RUNBOOK.md.
#
# Environment (defaults in brackets):
#   HOST [preview.diamondfootballleague.com]   BASE [/srv/dflv2]   CADDY_DIR [/etc/caddy]   CADDYFILE [$CADDY_DIR/Caddyfile]
#   CONF_D [$CADDY_DIR/conf.d]   ROLLBACK_ROOT [/root/dfl-rollback]   SNAPSHOT_MAX_AGE_MIN [1440]   SERVICE_USER [dflv2]
#   FORCE_DNS=1  go on even though the host does not resolve to this machine (Caddy will then be unable to get a certificate)
set -eu
PKG="${1:?usage: install_staging.sh PACKAGE_DIR}"
HOST="${HOST:-preview.diamondfootballleague.com}"
BASE="${BASE:-/srv/dflv2}"
CADDY_DIR="${CADDY_DIR:-/etc/caddy}"
CADDYFILE="${CADDYFILE:-$CADDY_DIR/Caddyfile}"
CONF_D="${CONF_D:-$CADDY_DIR/conf.d}"
ROLLBACK_ROOT="${ROLLBACK_ROOT:-/root/dfl-rollback}"
SNAPSHOT_MAX_AGE_MIN="${SNAPSHOT_MAX_AGE_MIN:-1440}"
SERVICE_USER="${SERVICE_USER:-dflv2}"
SNIPPET="$CONF_D/dflv2-preview.caddy"
say() { printf '%s\n' "$*"; }
die() { code="$1"; shift; say "STOPPED: $*" >&2; exit "$code"; }

# ---- 0. preconditions (nothing is changed before all of these hold)
[ "$(id -u)" = 0 ] || [ "${ALLOW_NON_ROOT:-}" = 1 ] || die 2 "run as root"
for c in caddy systemctl python3 sha256sum; do command -v "$c" >/dev/null 2>&1 || die 2 "missing command: $c"; done
[ -f "$PKG/PACKAGE.json" ] && [ -f "$PKG/SHA256SUMS" ] && [ -d "$PKG/repo" ] && [ -d "$PKG/seed-feed" ] || die 2 "$PKG is not an unpacked deployment package"
(cd "$PKG" && sha256sum -c SHA256SUMS --quiet) || die 2 "the package does not match its SHA256SUMS: do not install it"
PKGID="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['id'])" "$PKG/PACKAGE.json")"
say "package $PKGID verified"
[ -f "$CADDYFILE" ] || die 2 "no Caddyfile at $CADDYFILE (set CADDYFILE if it lives elsewhere)"

# ---- 1. a rollback copy must exist and be recent
if ! find "$ROLLBACK_ROOT" -maxdepth 1 -mindepth 1 -mmin "-$SNAPSHOT_MAX_AGE_MIN" 2>/dev/null | grep -q .; then
  die 3 "no rollback copy newer than $SNAPSHOT_MAX_AGE_MIN minutes in $ROLLBACK_ROOT. Run  sh $PKG/repo/deploy/website/snapshot_config.sh  first (and take the DigitalOcean snapshot)."
fi
say "rollback copy found: $(ls -1dt "$ROLLBACK_ROOT"/* | head -n 1)"

# ---- 2. DNS: the staging host must already point here, or HTTPS cannot be issued
mine="$( (ip -o addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1; curl -fsS -m 3 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null) | sort -u)"
theirs="$(getent ahosts "$HOST" 2>/dev/null | awk '{print $1}' | sort -u)"
if [ -z "$theirs" ]; then
  [ "${FORCE_DNS:-}" = 1 ] || die 4 "$HOST does not resolve yet. Add the DNS record first (docs/STAGING_RUNBOOK.md, step 2), wait for it, and run this again."
  say "WARNING: $HOST does not resolve; continuing because FORCE_DNS=1 (no HTTPS certificate will be issued until it does)"
else
  hit=""; for a in $theirs; do echo "$mine" | grep -qx "$a" && hit="$a"; done
  if [ -z "$hit" ]; then
    [ "${FORCE_DNS:-}" = 1 ] || die 4 "$HOST resolves to [$(echo $theirs)] but this machine's addresses are [$(echo $mine)]. Fix the DNS record (or use FORCE_DNS=1 to go on anyway)."
    say "WARNING: DNS does not point at this machine; continuing because FORCE_DNS=1"
  else say "DNS ok: $HOST -> $hit (this machine)"; fi
fi

# ---- 3. user and directories (nothing existing is moved)
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home-dir "$BASE" --shell /usr/sbin/nologin "$SERVICE_USER"
mkdir -p "$BASE/repo-releases" "$BASE/site" "$BASE/feed" "$BASE/league"
chmod 755 "$BASE"

# ---- 4. the code, as a new directory beside any earlier one; BASE/repo is a symlink switched in one rename
dest="$BASE/repo-releases/$PKGID"
if [ ! -d "$dest" ]; then cp -a "$PKG/repo" "$dest.partial" && mv "$dest.partial" "$dest"; fi
ln -sfn "repo-releases/$PKGID" "$BASE/.repo.new" && mv -T "$BASE/.repo.new" "$BASE/repo"
say "code installed: $BASE/repo -> repo-releases/$PKGID"

# ---- 5. the site (a new release; the previous one stays for rollback.sh --site)
BASE="$BASE" sh "$BASE/repo/deploy/website/install_site.sh" "$BASE/repo/site" >/dev/null
say "site installed: $(readlink "$BASE/site/current")"

# ---- 6. the seed feed (verified before and after copying; the previous feed release, if any, stays)
chown -R "$SERVICE_USER":"$SERVICE_USER" "$BASE/feed"
if command -v runuser >/dev/null 2>&1; then runuser -u "$SERVICE_USER" -- python3 -I "$BASE/repo/tools/seed_feed.py" "$PKG/seed-feed" "$BASE/feed"
else su -s /bin/sh "$SERVICE_USER" -c "python3 -I '$BASE/repo/tools/seed_feed.py' '$PKG/seed-feed' '$BASE/feed'"; fi
chmod -R a+rX "$BASE/site" "$BASE/feed" "$BASE/repo-releases"

# ---- 7. Caddy: add one site block for the staging host in its own file; roll back at once if the configuration does not validate
mkdir -p "$CONF_D"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
cp -a "$CADDYFILE" "$CADDYFILE.dfl-staging.$stamp.bak"
had_snippet=""; [ -f "$SNIPPET" ] && { cp -a "$SNIPPET" "$SNIPPET.$stamp.bak"; had_snippet=1; }
sed "s/__HOST__/$HOST/g" "$BASE/repo/deploy/website/Caddyfile.template" > "$SNIPPET.new" && mv "$SNIPPET.new" "$SNIPPET"
IMPORT="import $CONF_D/*.caddy"
added=""
if ! grep -Eq "^[[:space:]]*import[[:space:]]+(\./)?(conf\.d|$CONF_D)/\*" "$CADDYFILE"; then printf '\n# added by install_staging.sh (DFLv2 staging): drop-in site blocks\n%s\n' "$IMPORT" >> "$CADDYFILE"; added=1; fi
undo() {
  cp -a "$CADDYFILE.dfl-staging.$stamp.bak" "$CADDYFILE"
  if [ -n "$had_snippet" ]; then cp -a "$SNIPPET.$stamp.bak" "$SNIPPET"; else rm -f "$SNIPPET"; fi
}
if ! caddy validate --config "$CADDYFILE" --adapter caddyfile; then
  undo; die 5 "Caddy rejected the configuration; the Caddyfile and snippet are back as they were and Caddy was not reloaded."
fi
if ! systemctl reload caddy; then
  undo; caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1 && systemctl reload caddy || true
  die 6 "Caddy would not reload; the previous configuration was restored."
fi
say "Caddy reloaded with the staging block for $HOST (Caddyfile import line added: ${added:-no, it was already there}). Backup: $CADDYFILE.dfl-staging.$stamp.bak"
say ""
say "NEXT: from any computer run:  python3 $BASE/repo/tools/verify_deployment.py https://$HOST --production https://${PRODUCTION_HOST:-diamondfootballleague.com} --baseline <file saved before the install>"
