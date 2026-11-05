#!/bin/sh
# Set the INACTIVE leftovers of a rehearsal install aside, reversibly. NOTHING IS DELETED, nothing is purged.
#     sh quarantine_rehearsal.sh                 move them into a private quarantine directory
#     sh quarantine_rehearsal.sh --restore DIR   put every one back exactly where it was
# Use it when an install stopped half way (the installer refuses to run over an existing $REH) and you want a clean retry that can still be undone.
#
# Moved (only what exists):  $REH (the private store, the public folder, the monitor's logs)  |  $SRC_ROOT/<commit> for the commit named in $REH/.installed_commit
#                            $SYSTEMD_DIR/dfl-rehearsal.service and dfl-rehearsal-monitor.service  |  $ETC_DIR/rehearsal.env (with its expired deadline).
# Never touched:             /srv/dflv2 (league, feed, site), Caddy's files, $ROLLBACK_ROOT, evidence folders, DNS, any other directory under $SRC_ROOT, any package or build directory.
# Refused (exit 3/4):        a rehearsal unit that is active or enabled, or a rehearsal Caddy block still installed. This script only sets aside what is inactive; stop and remove
#                            a live block with uninstall_rehearsal.sh (without --purge) first.
#
# The quarantine is $QUARANTINE_ROOT/<UTC timestamp> (mode 700, owner root). It holds items/<original path> (the moved things, same filesystem so a rename: owners, modes, times, ACLs and
# hard links are untouched), manifest.tsv (original path <TAB> new path <TAB> kind <TAB> mode owner size mtime), listing.tsv.gz (every file under $REH before the move: type, mode,
# owner, size, mtime, link target, relative path), state_before.txt / state_after.txt (systemd state of the two units, the Caddy conf directory) and RESTORE.txt. After the move the
# listing is recomputed from the new location and must be identical (it is printed), and again after --restore.
# Exit codes: 2 paths/commands/usage, 3 a unit is active or enabled, 4 a rehearsal Caddy block is installed, 5 restore blocked (an original path exists again) or a listing differs.
set -eu
umask 077
REH="${REH:-/srv/dflv2-rehearsal}"; SRC_ROOT="${SRC_ROOT:-/opt/dflv2-src}"; CADDY_DIR="${CADDY_DIR:-/etc/caddy}"; CONF_D="${CONF_D:-$CADDY_DIR/conf.d}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"; ETC_DIR="${ETC_DIR:-/etc/dflv2}"; QUARANTINE_ROOT="${QUARANTINE_ROOT:-/root/rehearsal-quarantine}"
SNIPPET="$CONF_D/dflv2-rehearsal.caddy"
UNITS="dfl-rehearsal dfl-rehearsal-monitor"
say() { printf '%s\n' "$*"; }
die() { code="$1"; shift; say "STOPPED: $*" >&2; exit "$code"; }
[ "$(id -u)" = 0 ] || [ "${ALLOW_NON_ROOT:-}" = 1 ] || die 2 "run as root"
for c in systemctl sha256sum gzip find; do command -v "$c" >/dev/null 2>&1 || die 2 "missing command: $c"; done
case "$REH" in /srv/dflv2|/srv/dflv2/*|/var/www|/var/www/*|/|""|*..*) die 2 "REH=$REH is a production or system path: refusing";; esac
case "$(basename "$REH")" in *rehearsal*) ;; *) die 2 "REH must have 'rehearsal' in its name: refusing";; esac

listing() {   # $1 = directory; a stable, comparable description of everything under it (independent of where it lives)
  (cd "$1" && find . -printf '%y %m %U:%G %s %T@ %l|%P\n' | LC_ALL=C sort)
}
state() {
  for u in $UNITS; do
    printf '%s enabled=%s active=%s\n' "$u" "$(systemctl is-enabled "$u" 2>&1 | head -n 1)" "$(systemctl is-active "$u" 2>&1 | head -n 1)"
  done
  if [ -d "$CONF_D" ]; then ls -la "$CONF_D" | sed 's/^/conf.d: /'; else echo "conf.d: absent"; fi
  [ -e "$SNIPPET" ] && echo "rehearsal snippet: PRESENT" || echo "rehearsal snippet: absent"
}
moveto() {   # $1 original path, $2 quarantine dir : rename into items/<path>
  dest="$2/items$1"
  mkdir -p "$(dirname "$dest")"
  mv -T "$1" "$dest"
}

if [ "${1:-}" = "--restore" ]; then
  Q="${2:?usage: quarantine_rehearsal.sh --restore QUARANTINE_DIR}"
  [ -f "$Q/manifest.tsv" ] && [ -d "$Q/items" ] || die 2 "$Q is not a quarantine directory"
  # every original path must be free BEFORE anything is moved back
  while IFS="$(printf '\t')" read -r orig new kind rest; do
    [ "$orig" = "original" ] && continue
    [ -e "$new" ] || die 5 "$new is missing from the quarantine: nothing was moved back"
    [ ! -e "$orig" ] || die 5 "$orig exists again: nothing was moved back (set the newer one aside first)"
  done < "$Q/manifest.tsv"
  while IFS="$(printf '\t')" read -r orig new kind rest; do
    [ "$orig" = "original" ] && continue
    mkdir -p "$(dirname "$orig")"; mv -T "$new" "$orig"; say "restored $orig"
  done < "$Q/manifest.tsv"
  systemctl daemon-reload
  if [ -f "$Q/listing.tsv.gz" ]; then
    gzip -dc "$Q/listing.tsv.gz" > "$Q/.listing.restored.expected"
    listing "$REH" > "$Q/.listing.restored.now"
    if cmp -s "$Q/.listing.restored.expected" "$Q/.listing.restored.now"; then say "restore verified: $REH is identical to its listing from before the move ($(wc -l < "$Q/.listing.restored.now") entries)"
    else die 5 "restored $REH differs from its listing before the move (see $Q/.listing.restored.*)"; fi
    rm -f "$Q/.listing.restored.expected" "$Q/.listing.restored.now"
  fi
  state > "$Q/state_after_restore.txt"; cat "$Q/state_after_restore.txt"
  say "RESTORED. The quarantine directory $Q is kept."
  exit 0
fi
[ $# = 0 ] || die 2 "usage: quarantine_rehearsal.sh [--restore QUARANTINE_DIR]"

# ---- refuse unless everything is inactive
for u in $UNITS; do
  a="$(systemctl is-active "$u" 2>&1 | head -n 1 || true)"; e="$(systemctl is-enabled "$u" 2>&1 | head -n 1 || true)"
  case "$a" in active|activating|reloading) die 3 "$u is $a: stop it first (this script only sets aside what is inactive)";; esac
  case "$e" in enabled|enabled-runtime|static|alias|linked) [ ! -e "$SYSTEMD_DIR/$u.service" ] || die 3 "$u is $e: this script only sets aside units that are disabled";; esac
done
[ ! -e "$SNIPPET" ] || die 4 "the rehearsal Caddy block is still installed ($SNIPPET): remove it with uninstall_rehearsal.sh (no --purge) first"

# ---- what exists
items=""
[ -d "$REH" ] && items="$items $REH"
CODE=""
if [ -f "$REH/.installed_commit" ]; then
  c="$(cat "$REH/.installed_commit")"
  case "$c" in *[!0-9a-f]*|"") ;; *) [ "${#c}" = 40 ] && [ -d "$SRC_ROOT/$c" ] && CODE="$SRC_ROOT/$c";; esac
fi
[ -n "$CODE" ] && items="$items $CODE"
for u in $UNITS; do [ -e "$SYSTEMD_DIR/$u.service" ] && items="$items $SYSTEMD_DIR/$u.service"; done
[ -e "$ETC_DIR/rehearsal.env" ] && items="$items $ETC_DIR/rehearsal.env"
[ -n "$items" ] || { say "nothing to set aside: no rehearsal leftovers found"; exit 0; }

Q="$QUARANTINE_ROOT/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$QUARANTINE_ROOT"; chmod 700 "$QUARANTINE_ROOT"
mkdir "$Q"; chmod 700 "$Q"
say "quarantine: $Q"
state > "$Q/state_before.txt"
printf 'original\tquarantined\tkind\tmode owner size mtime\n' > "$Q/manifest.tsv"
for p in $items; do
  kind=file; [ -d "$p" ] && kind=dir
  printf '%s\t%s\t%s\t%s\n' "$p" "$Q/items$p" "$kind" "$(stat -c '%a %U:%G %s %y' "$p")" >> "$Q/manifest.tsv"
done
if [ -d "$REH" ]; then
  listing "$REH" > "$Q/.listing.before"
  gzip -c "$Q/.listing.before" > "$Q/listing.tsv.gz"
fi
cat > "$Q/RESTORE.txt" <<EOF
Set aside by quarantine_rehearsal.sh on $(date -u +%FT%TZ). Nothing was deleted.
To put everything back exactly where it was:   sh quarantine_rehearsal.sh --restore $Q
Original paths and metadata are in manifest.tsv; state_before.txt is the systemd/Caddy state at that moment.
EOF
for p in $items; do moveto "$p" "$Q"; say "moved $p"; done
systemctl daemon-reload
state > "$Q/state_after.txt"
if [ -f "$Q/.listing.before" ]; then
  listing "$Q/items$REH" > "$Q/.listing.after"
  if cmp -s "$Q/.listing.before" "$Q/.listing.after"; then
    say "verified: the moved $REH is identical to its listing before the move ($(wc -l < "$Q/.listing.after") entries, sha256 $(sha256sum < "$Q/.listing.after" | cut -c1-16))"
  else die 5 "the moved tree differs from its listing before the move: see $Q/.listing.before and .listing.after. Nothing was deleted; restore with --restore $Q"; fi
  rm -f "$Q/.listing.before" "$Q/.listing.after"
fi
cat "$Q/state_after.txt"
say "DONE. Restore with:  sh $0 --restore $Q"
