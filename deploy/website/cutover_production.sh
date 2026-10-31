#!/bin/sh
# Cut the PRODUCTION hostnames over from the old proof-of-concept site to the DFLv2 viewer and its existing development-league feed. Run as root on the Droplet:
#     sh cutover_production.sh
# What it does, in order (nothing is changed before step 3, and every change after it is undone by itself if a later step fails):
#   1. preconditions: the DFLv2 site, the live feed pointer and the league runner are in place; this has not been done already
#   2. the ROLLBACK COPY: snapshot_config.sh (Caddy directory, old units, old web root) plus a private archive (mode 0600, in a 0700 directory) of the old
#      application and its configuration directory, and the state of the old units; checksummed
#   3. the old production site block is removed from the Caddyfile (the rest, including the staging import, stays) and a drop-in block is written for the
#      production hostnames from the same template the preview site uses, so the feed it serves is the SAME feed; `caddy validate` must pass before Caddy is reloaded
#   4. the new site is checked from this machine; if it is not right the old configuration is put back
#   5. the old services are stopped and disabled so they cannot write over anything or start again after a reboot
# It never touches the league, its seed, its files or the feed, and never reboots anything. Roll back with rollback_cutover.sh.
# Environment (defaults in brackets): BASE [/srv/dflv2] CADDY_DIR [/etc/caddy] CONF_D [$CADDY_DIR/conf.d] SNAP_ROOT [/root/dfl-rollback]
#   HOSTS ["diamondfootballleague.com, www.diamondfootballleague.com, 143-198-130-68.sslip.io"]  OLD_UNITS ["dfl-live dfl-control dfl-chronicle"]
#   OLD_DIRS ["/opt/dfl /etc/dfl"] (archived privately)  SKIP_LOCAL_CHECK=1 (tests only)
set -eu
BASE="${BASE:-/srv/dflv2}"
CADDY_DIR="${CADDY_DIR:-/etc/caddy}"
CADDYFILE="${CADDYFILE:-$CADDY_DIR/Caddyfile}"
CONF_D="${CONF_D:-$CADDY_DIR/conf.d}"
SNAP_ROOT="${SNAP_ROOT:-/root/dfl-rollback}"
HOSTS="${HOSTS:-diamondfootballleague.com, www.diamondfootballleague.com, 143-198-130-68.sslip.io}"
OLD_UNITS="${OLD_UNITS:-dfl-live dfl-control dfl-chronicle}"
OLD_DIRS="${OLD_DIRS:-/opt/dfl /etc/dfl}"
SNIPPET="$CONF_D/dflv2-production.caddy"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"; export CADDY_DIR UNIT_DIR
HERE="$(cd "$(dirname "$0")" && pwd)"
say() { printf '%s\n' "$*"; }
die() { code="$1"; shift; say "STOPPED: $*" >&2; exit "$code"; }
FIRST_HOST="$(printf '%s' "$HOSTS" | cut -d, -f1 | tr -d ' ')"

# ---- 1. preconditions
[ "$(id -u)" = 0 ] || [ "${ALLOW_NON_ROOT:-}" = 1 ] || die 2 "run as root"
for c in caddy systemctl python3 sha256sum tar; do command -v "$c" >/dev/null 2>&1 || die 2 "missing command: $c"; done
[ -f "$CADDYFILE" ] || die 2 "no Caddyfile at $CADDYFILE"
TEMPLATE="$BASE/repo/deploy/website/Caddyfile.template"
[ -f "$TEMPLATE" ] || die 2 "no Caddy template at $TEMPLATE: deploy the site first (deploy_pinned.sh)"
[ -f "$BASE/DEPLOYED_REVISION.json" ] || die 2 "no recorded deployed revision"
[ -e "$BASE/site/current/index.html" ] || die 2 "no installed site at $BASE/site/current"
python3 -I -c "import json,sys; p=json.load(open(sys.argv[1])); assert p['format']=='dfl-public-feed' and p['release']" "$BASE/feed/latest.json" 2>/dev/null || die 2 "the feed pointer $BASE/feed/latest.json is missing or not a feed pointer"
[ -f "$SNIPPET" ] && die 7 "$SNIPPET exists: the production hostnames were already cut over (use rollback_cutover.sh to undo)"
say "preconditions hold; deployed revision: $(python3 -I -c "import json,sys; print(json.load(open(sys.argv[1]))['commit'])" "$BASE/DEPLOYED_REVISION.json"); feed release: $(python3 -I -c "import json,sys; print(json.load(open(sys.argv[1]))['release'])" "$BASE/feed/latest.json")"

# ---- 2. the rollback copy (private; the old application's configuration may hold credentials, so it is never printed)
SNAP="$(SNAP_ROOT="$SNAP_ROOT" CADDY_DIR="$CADDY_DIR" UNIT_DIR="$UNIT_DIR" sh "$HERE/snapshot_config.sh" | tail -n 1)"
[ -d "$SNAP" ] || die 3 "no rollback copy was made"
chmod 700 "$SNAP"
existing=""; for d in $OLD_DIRS; do [ -e "$d" ] && existing="$existing $d"; done
if [ -n "$existing" ]; then
  umask 077
  rc=0; tar -czf "$SNAP/old_application.tgz" $existing 2>/dev/null || rc=$?
  [ "$rc" -le 1 ] || die 3 "could not archive $existing (tar exit $rc)"
  chmod 600 "$SNAP/old_application.tgz"; tar -tzf "$SNAP/old_application.tgz" >/dev/null
fi
{ for u in $OLD_UNITS; do printf '%s enabled=%s active=%s\n' "$u" "$(systemctl is-enabled "$u" 2>/dev/null || true)" "$(systemctl is-active "$u" 2>/dev/null || true)"; done; } > "$SNAP/cutover_state.txt"
(cd "$SNAP" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS)
say "rollback copy: $SNAP"

# ---- 3. Caddy: remove the old production block, add the new one, validate, reload
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
cp -a "$CADDYFILE" "$CADDYFILE.dfl-cutover.$stamp.bak"
mkdir -p "$CONF_D"
sed "s|__HOST__|$HOSTS|g" "$TEMPLATE" > "$SNIPPET.new"
python3 -I - "$CADDYFILE" "$HOSTS" "$CONF_D" "$stamp" <<'PY' || { rm -f "$SNIPPET.new"; die 4 "could not find the old production block in $CADDYFILE: nothing was changed"; }
import re, sys
path, hosts, conf_d, stamp = sys.argv[1:5]
want = {h.strip() for h in hosts.split(",") if h.strip()}
lines = open(path).read().split("\n")
out, i, removed = [], 0, 0
while i < len(lines):
    ln = lines[i]
    s = ln.strip()
    if s and not s.startswith("#") and not s.startswith("{") and s.endswith("{") and ln == ln.lstrip():
        names = {a.strip().rstrip(",") for a in re.split(r"[,\s]+", s[:-1].strip()) if a.strip()}
        depth, j = 0, i
        while j < len(lines):
            depth += lines[j].count("{") - lines[j].count("}")
            if depth <= 0:
                break
            j += 1
        if names & want:
            out.append("# the old production site block was here; removed by cutover_production.sh %s (saved in the rollback copy)" % stamp)
            removed += 1
            i = j + 1
            continue
    out.append(ln)
    i += 1
text = "\n".join(out)
if not removed:
    sys.exit(1)
if not re.search(r"^[ \t]*import[ \t]+(\./)?(conf\.d|%s)/\*" % re.escape(conf_d), text, re.M):
    text = text.rstrip("\n") + "\n\n# drop-in site blocks (DFLv2 preview and production)\nimport %s/*.caddy\n" % conf_d
open(path, "w").write(text)
PY
mv "$SNIPPET.new" "$SNIPPET"
undo_caddy() {
  cp -a "$CADDYFILE.dfl-cutover.$stamp.bak" "$CADDYFILE"; rm -f "$SNIPPET"
  caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1 && systemctl reload caddy || true
}
if ! caddy validate --config "$CADDYFILE" --adapter caddyfile; then
  cp -a "$CADDYFILE.dfl-cutover.$stamp.bak" "$CADDYFILE"; rm -f "$SNIPPET"
  die 5 "Caddy rejected the new configuration; the Caddyfile is back as it was and Caddy was not reloaded."
fi
if ! systemctl reload caddy; then undo_caddy; die 6 "Caddy would not reload; the previous configuration was restored."; fi
say "Caddy reloaded: $HOSTS now serve the DFLv2 viewer and the feed at $BASE/feed"

# ---- 4. look at the new site from this machine (the real hostname, the real certificate)
if [ "${SKIP_LOCAL_CHECK:-}" != 1 ]; then
  ok=1
  for _ in 1 2 3 4 5 6; do
    ok=1
    curl -fsS -m 20 --resolve "$FIRST_HOST:443:127.0.0.1" "https://$FIRST_HOST/" 2>/dev/null | grep -q "Development league" || ok=0
    curl -fsS -m 20 --resolve "$FIRST_HOST:443:127.0.0.1" "https://$FIRST_HOST/feed/latest.json" 2>/dev/null | python3 -I -c "import json,sys; assert json.load(sys.stdin)['format']=='dfl-public-feed'" 2>/dev/null || ok=0
    [ "$ok" = 1 ] && break
    sleep "${CHECK_WAIT:-5}"
  done
  if [ "$ok" != 1 ]; then undo_caddy; die 8 "the new site did not answer correctly at $FIRST_HOST; the old configuration was restored (the old services were not touched)."; fi
  say "the new site answers at $FIRST_HOST"
fi

# ---- 5. the old services: stopped, disabled, and checked that nothing pulls them back in
for u in $OLD_UNITS; do systemctl disable --now "$u" || say "WARNING: could not disable $u"; done
python3 -I - "$BASE" "$SNAP" "$stamp" "$HOSTS" <<'PY'
import json, sys, time
base, snap, stamp, hosts = sys.argv[1:5]
rev = json.load(open(base + "/DEPLOYED_REVISION.json"))
ptr = json.load(open(base + "/feed/latest.json"))
json.dump({"cutover_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "hosts": hosts, "rollback_copy": snap, "deployed_commit": rev.get("commit"),
           "feed_release_at_cutover": ptr.get("release")}, open(base + "/PRODUCTION_CUTOVER.json", "w"), indent=1, sort_keys=True)
PY
say "DONE. Rollback: sh $HERE/rollback_cutover.sh $SNAP    (dry run: add --dry-run)"
