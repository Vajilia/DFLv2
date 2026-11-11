#!/bin/sh
# Set a FAILED rehearsal aside whole and prepare a CLEAN run from broadcast position zero, WITHOUT rerunning the installer (the installer rewrites the Caddy block,
# the units and the environment file). Four stages; each prints what it would do and changes nothing unless --apply is given. Run as root on the Droplet.
#
#   sh fresh_run.sh preserve         [--apply]   stop first (units must be inactive): MOVE private/, feed/ and monitor/ into $KEEP_ROOT/<UTC stamp>/ (a rename on one
#                                                filesystem: nothing is copied, deleted or overwritten), copy the env file, units, Caddy block, journal and status, write
#                                                FAILED_RUN.txt and MANIFEST.sha256, make the folder read-only. Refuses if the target exists or a unit is active.
#   sh fresh_run.sh prepare PKG_DIR  [--apply]   PKG_DIR = an unpacked, checksum-verified rehearsal package. NEW code directory $SRC_ROOT/<commit> (cp -a: independent files,
#                                                never hard links, never over an existing directory); NEW empty feed/ and monitor/, a NEW private store from the package's
#                                                store.tar.gz (a new canary); the installer's owners and modes; the pre-flight with the NEW code; units re-rendered for the new
#                                                commit; the env file reset to a deadline in the PAST. Starts nothing.
#   sh fresh_run.sh start            [--apply]   deadline = NOW + 54 h computed here, written to the env file, then the monitor and immediately the daemon are started; the
#                                                clock starts at position 0 when the daemon starts, so the cap is start + 54 h (less the few seconds between the two lines).
#   sh fresh_run.sh plan                          print all four without changing anything
# Not touched in any stage: /srv/dflv2 (production), the Caddy block and Caddy, DNS, $REH/site, the old code directories under $SRC_ROOT, the earlier quarantine.
# Exit codes: 2 refused (precondition), 5 pre-flight failed.
set -eu
umask 022
REH="${REH:-/srv/dflv2-rehearsal}"
KEEP_ROOT="${KEEP_ROOT:-/root/rehearsal-failed-runs}"
SRC_ROOT="${SRC_ROOT:-/opt/dflv2-src}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
ETC_DIR="${ETC_DIR:-/etc/dflv2}"
CONF_D="${CONF_D:-/etc/caddy/conf.d}"
SERVICE_USER="${SERVICE_USER:-dflv2}"
MIN_FREE_GB="${MIN_FREE_GB:-6}"
MAX_PUBLIC_GB="${MAX_PUBLIC_GB:-3}"
HOURS="${HOURS:-54}"
HOST="${HOST:-rehearsal.diamondfootballleague.com}"
STAMP="${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
say() { printf '%s\n' "$*"; }
die() { code="$1"; shift; say "REFUSED: $*" >&2; exit "$code"; }
APPLY=0; STAGE="${1:-plan}"; [ $# -gt 0 ] && shift
PKG=""
for a in "$@"; do case "$a" in --apply) APPLY=1;; *) PKG="$a";; esac; done
do_() { if [ "$APPLY" = 1 ]; then "$@"; else say "  would run: $*"; fi; }
active() { systemctl is-active --quiet "$1" 2>/dev/null; }
need_stopped() { for u in dfl-rehearsal dfl-rehearsal-monitor; do ! active "$u" || die 2 "$u is active: stop the daemon first, then the monitor (stopping the monitor under a running daemon trips it), then run this again."; done; }

stage_preserve() {
  KEEP="$KEEP_ROOT/$STAMP"
  need_stopped
  [ -d "$REH/private" ] && [ -d "$REH/feed" ] && [ -d "$REH/monitor" ] || die 2 "$REH does not hold private/, feed/ and monitor/: nothing to set aside (already done?)"
  [ ! -e "$KEEP" ] || die 2 "$KEEP already exists: never overwritten"
  mkdir -p "$KEEP_ROOT"
  [ "$(stat -c %d "$REH")" = "$(stat -c %d "$KEEP_ROOT")" ] || die 2 "$KEEP_ROOT is on another filesystem than $REH: a move would copy and then delete. Choose KEEP_ROOT on the same filesystem."
  say "preserve -> $KEEP   (private store, public folder, monitor folder moved whole; configuration and logs copied)"
  if [ "$APPLY" = 1 ]; then
    mkdir -p "$KEEP/config" && chmod 700 "$KEEP_ROOT" "$KEEP"
    for d in private feed monitor; do mv "$REH/$d" "$KEEP/$d"; done
    cp -a "$ETC_DIR/rehearsal.env" "$KEEP/config/" 2>/dev/null || true
    for u in dfl-rehearsal.service dfl-rehearsal-monitor.service; do cp -a "$SYSTEMD_DIR/$u" "$KEEP/config/" 2>/dev/null || true; done
    cp -a "$CONF_D/dflv2-rehearsal.caddy" "$KEEP/config/" 2>/dev/null || true
    cp -a "$REH/.installed_commit" "$KEEP/config/" 2>/dev/null || true
    { journalctl -u dfl-rehearsal -u dfl-rehearsal-monitor --no-pager 2>&1 || true; } > "$KEEP/journal.txt"
    { date -u; df -h "$REH" 2>&1 || true; systemctl status dfl-rehearsal dfl-rehearsal-monitor --no-pager 2>&1 || true; } > "$KEEP/state.txt"
    cat > "$KEEP/FAILED_RUN.txt" <<EOF
This is the state of the FIRST real-time rehearsal run, set aside whole on $STAMP.
VERDICT: FAILED (docs/BROADCAST_REHEARSAL.md section 1: any violation entry or automatic pause). It stays FAILED; the next run earns its own result.
Cause of the stop: monitor_error FileNotFoundError on a .tmp- file renamed during atomic publication (docs/MONITOR_TMP_RACE_2026-11-06.md).
Nothing here was deleted or overwritten. Do not purge. MANIFEST.sha256 lists every file with its SHA-256 as of the move.
EOF
    (cd "$KEEP" && find . -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum > MANIFEST.sha256)
    chmod -R a-w "$KEEP"
    say "preserved: $(wc -l < "$KEEP/MANIFEST.sha256") files in the manifest; folder is read-only; $KEEP/FAILED_RUN.txt"
  fi
}

stage_prepare() {
  [ -n "$PKG" ] || die 2 "usage: fresh_run.sh prepare PKG_DIR [--apply]"
  need_stopped
  [ -f "$PKG/PACKAGE.json" ] && [ -f "$PKG/SHA256SUMS" ] && [ -d "$PKG/repo" ] && [ -f "$PKG/store.tar.gz" ] || die 2 "$PKG is not an unpacked rehearsal package"
  (cd "$PKG" && sha256sum -c SHA256SUMS >/dev/null) || die 2 "the package does not match its SHA256SUMS"
  COMMIT="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["commit"])' "$PKG/PACKAGE.json")"
  [ -n "$COMMIT" ] || die 2 "PACKAGE.json names no commit"
  dest="$SRC_ROOT/$COMMIT"
  [ ! -e "$dest" ] || die 2 "$dest already exists: new code is always a new directory"
  for d in private feed monitor; do [ ! -e "$REH/$d" ] || die 2 "$REH/$d exists: run  fresh_run.sh preserve --apply  first (a run is never prepared over another one)"; done
  say "prepare: code $dest (cp -a, independent files); new store from the package; empty feed and monitor; units re-rendered; deadline reset to the past"
  if [ "$APPLY" = 1 ]; then
    mkdir -p "$SRC_ROOT"
    cp -a "$PKG/repo" "$dest.partial"
    chmod -R u=rwX,go=rX "$dest.partial"
    mv "$dest.partial" "$dest"
    [ -z "$(find "$dest" -type f -links +1 | head -1)" ] || die 2 "hard links found in the new code directory"
    mkdir -p "$REH/private" "$REH/feed" "$REH/monitor"
    tar -xzf "$PKG/store.tar.gz" -C "$REH/private"
    chown -R "$SERVICE_USER" "$REH/private" "$REH/feed" "$REH/monitor" 2>/dev/null || [ "${ALLOW_NON_ROOT:-}" = 1 ] || die 2 "could not give $REH to $SERVICE_USER"
    chmod 755 "$REH" "$REH/feed"; chmod 700 "$REH/private" "$REH/monitor"
    printf '%s\n' "$COMMIT" > "$REH/.installed_commit"; chmod 644 "$REH/.installed_commit"
    if ! python3 -I "$dest/tools/rehearsal_check.py" preflight --root "$REH/private" --public "$REH/feed" --min-free-gb "$MIN_FREE_GB" --growth-gb "$MAX_PUBLIC_GB" --require-canary --guard "$REH/monitor"; then
      say "pre-flight FAILED. Nothing was started. The failed run is intact under $KEEP_ROOT." >&2; exit 5
    fi
    mkdir -p "$SYSTEMD_DIR" "$ETC_DIR"
    for u in dfl-rehearsal.service dfl-rehearsal-monitor.service; do
      sed "s/__COMMIT__/$COMMIT/g; s/__HOST__/$HOST/g" "$PKG/repo/deploy/rehearsal/$u" > "$SYSTEMD_DIR/$u.new" && chmod 644 "$SYSTEMD_DIR/$u.new" && mv "$SYSTEMD_DIR/$u.new" "$SYSTEMD_DIR/$u"
    done
    printf 'REHEARSAL_DEADLINE=2000-01-01T00:00:00Z\n' > "$ETC_DIR/rehearsal.env"; chmod 644 "$ETC_DIR/rehearsal.env"
    systemctl daemon-reload
    say "prepared $COMMIT. Nothing is running. The deadline in $ETC_DIR/rehearsal.env is in the PAST until  fresh_run.sh start --apply."
  fi
}

stage_start() {
  need_stopped
  [ -f "$ETC_DIR/rehearsal.env" ] && grep -q '^REHEARSAL_DEADLINE=2000-01-01T00:00:00Z$' "$ETC_DIR/rehearsal.env" || die 2 "the env file does not hold the past deadline a prepared run has: run prepare first"
  [ -z "$(ls -A "$REH/feed" 2>/dev/null)" ] || die 2 "$REH/feed is not empty: a clean run starts with an empty public folder"
  [ ! -e "$REH/monitor/TRIPPED" ] && [ ! -e "$REH/private/store/clock.json" ] || die 2 "a latch or a clock exists: this is not a clean run"
  DEADLINE="$(date -u -d "+$HOURS hours" +%Y-%m-%dT%H:%M:%SZ)"
  say "start: deadline $DEADLINE (now + $HOURS h), then the monitor, then the daemon"
  if [ "$APPLY" = 1 ]; then
    printf 'REHEARSAL_DEADLINE=%s\n' "$DEADLINE" > "$ETC_DIR/rehearsal.env"
    systemctl start dfl-rehearsal-monitor
    systemctl start dfl-rehearsal
    n=0; while [ ! -f "$REH/private/store/clock.json" ] && [ "$n" -lt 30 ]; do sleep 1; n=$((n + 1)); done
    say "started at $(date -u +%Y-%m-%dT%H:%M:%SZ); deadline $DEADLINE; clock: $(cat "$REH/private/store/clock.json" 2>/dev/null | tr -d '\n ' | head -c 300)"
  fi
}

case "$STAGE" in
  preserve) stage_preserve ;;
  prepare) stage_prepare ;;
  start) stage_start ;;
  plan) say "stages: preserve, prepare PKG_DIR, start (each takes --apply). See the header of this file."; ;;
  *) die 2 "unknown stage '$STAGE'" ;;
esac
