#!/usr/bin/env bash
# dfl-recovery-run.sh : guarded run that builds a verified DFL recovery set, uploads it PRIVATELY to Spaces, then proves it comes back intact.
#
# Reads production files, writes only inside $WORK/run-<id>/ and under s3://$BUCKET/sets/<id>/ (and canary/). Never deletes, never overwrites, never
# touches a service. One stage per command; a stage runs only if the previous stage finished OK and nothing failed. ANY failure stops the run for good:
# a failed run is never continued, only moved aside (a rename) and replaced by a NEW run with a new id.
#
#   dfl-recovery-run.sh locate              read-only: find the existing recovery-set tar by size and FULL SHA-256 (writes nothing, takes no lock)
#   dfl-recovery-run.sh new                 create a run, print its id
#   dfl-recovery-run.sh <stage> <RUN_ID>    stages in order: check canary create verify package upload drill
#   dfl-recovery-run.sh all                 new + every stage in order, stopping at the first failure
#   dfl-recovery-run.sh status <RUN_ID>     read-only: what the run has done and whether it failed
#
# Two kinds of exit.  STOP (exit 1): a stage failed. The run is marked FAILED (first failure kept), and, once objects exist in the bucket prefix, a
# FAILED.json is also placed there (best effort).  REFUSED (exit 3): the command was not allowed (another process holds the lock, the run already
# failed, a stage out of order or repeated). A refusal NEVER writes into the run or the bucket; it only prints why.
#
# What the markers in s3://$BUCKET/sets/<id>/ mean (see docs/SPACES_RECOVERY_RUN.md):
#   UPLOAD-COMPLETE.json   the tar and its checksum were uploaded, sizes match, both were private (uploaded --acl-private, authenticated retrieval returned the same
#                          bytes, anonymous GET denied; the ACL is also read when Spaces allows it, otherwise this is recorded). NOT a recovery proof.
#   DRILL-VERIFIED.json    the set was downloaded, verified, restored and passed the restore test. Written last. Usable set = this file present
#                          AND FAILED.json absent.
#   FAILED.json            something failed after the upload began. A set with FAILED.json is not a verified set whatever else it contains.
#
# Everything below can be overridden from the environment (the local rehearsal does exactly that); the defaults are the production values.
set -Eeuo pipefail
umask 077

# ---- configuration -------------------------------------------------------------------------------------------------------------------------------
LIVE_LEAGUE=${LIVE_LEAGUE:-/srv/dflv2/league/league.db}
LIVE_ORIGINAL=${LIVE_ORIGINAL:-/srv/dflv2/league-v2-upgrade-input/league.db}     # the exact file the event-141 upgrade read (its SHA-256 is in the lineage)
LIVE_EQUIV=${LIVE_EQUIV:-/srv/dflv2/league-141-original/league.db}               # the preserved league-141-original (same history, different bytes)
LIVE_FEED=${LIVE_FEED:-/srv/dflv2/feed}
ENGINE_TREE=${ENGINE_TREE:-/opt/dflv2-src/7e59cbbaea23b0de353545c64a6f5c39cffe774d}              # the tree that runs the active league (already on the server)
TOOL_TREE=${TOOL_TREE:-/opt/dfl-recovery-tools/0f72562bb8231b6c1af3671560953f38b4024959}         # league_recovery_set.py + league_backup.py (new pinned checkout)
PB_TREE=${PB_TREE:-/opt/dfl-recovery-tools/284125a40826e3a40a18badf46378a90c8ef26eb}             # public_backup.py (new pinned checkout)
WORK=${WORK:-/var/backups/dfl-recovery}
S3CMD=${S3CMD:-s3cmd}
S3CFG=${S3CFG:-/root/.config/dfl-spaces/s3cfg}
BUCKET=${BUCKET:-}                                                                                  # REQUIRED (export BUCKET=<the private bucket>); deliberately not written into the repository
# Anonymous probes (no credentials). Each base must end with "/"; the object key is appended. Every one must be DENIED (401/403).
# ORIGIN_BASES are the endpoints that exist for this bucket: a probe that cannot connect proves nothing, so an unreachable origin FAILS the stage.
ORIGIN_BASES=${ORIGIN_BASES-"https://$BUCKET.sfo3.digitaloceanspaces.com/ https://sfo3.digitaloceanspaces.com/$BUCKET/"}
# The CDN is DISABLED for this bucket. A hostname that does not exist can fail DNS or TLS, which says nothing about whether the object is private, so
# it is not probed. It is recorded as disabled and is probed only when CDN_ENABLED=1 (then an unreachable CDN endpoint fails like any other probe).
CDN_ENABLED=${CDN_ENABLED:-0}
CDN_BASE=${CDN_BASE-"https://$BUCKET.sfo3.cdn.digitaloceanspaces.com/"}
ANON_LIST_URL=${ANON_LIST_URL-"https://$BUCKET.sfo3.digitaloceanspaces.com/"}                   # anonymous bucket listing must be denied; empty = skip (rehearsal only)
EXPECT_EVENTS=${EXPECT_EVENTS:-188}                                                               # the league must be at this event; empty = do not check
EXISTING_SET=${EXISTING_SET:-}                                                                   # the existing event-141 recovery set folder (REQUIRED unless ALLOW_NO_EXISTING=1)
ALLOW_NO_EXISTING=${ALLOW_NO_EXISTING:-0}
# The artifact of the existing recovery set (2026-11-03) and its recorded checksum. It is LOCATED, never assumed: a regular file of exactly this many
# bytes whose full 64-hex SHA-256 equals the recorded value (no prefix/suffix shortcut). EXISTING_ARTIFACT may name a path to try; otherwise SEARCH_DIRS is searched.
EXISTING_ARTIFACT=${EXISTING_ARTIFACT:-}
EXISTING_ARTIFACT_NAME=${EXISTING_ARTIFACT_NAME:-dfl-recovery-set-20261103.tar}                  # informational only: a renamed copy still counts
EXISTING_ARTIFACT_BYTES=${EXISTING_ARTIFACT_BYTES:-622387200}
EXISTING_ARTIFACT_SHA256=${EXISTING_ARTIFACT_SHA256-5edea873ad660ee1dfd47cfc8773eefc9a176c5b744f2fe3e5f1c974167d133e}
SEARCH_DIRS=${SEARCH_DIRS:-"/root /var/backups /srv /home /opt /mnt /var/tmp /tmp"}
ALLOW_NO_ARTIFACT=${ALLOW_NO_ARTIFACT:-0}
MIN_FREE_AFTER_BYTES=${MIN_FREE_AFTER_BYTES:-8589934592}                                         # free space that must remain after the peak (8 GiB)
INDEPENDENT=${INDEPENDENT:-1}                                                                     # 1 = also hide the original server paths during the restore test (the production drill uses 1)
HIDE_DIRS=${HIDE_DIRS:-"/srv/dflv2 /opt/dflv2-src"}
S3_PUT_OPTS=${S3_PUT_OPTS:-}                                                                      # extra s3cmd put options (rehearsal only)
REPLAY_FROM=${REPLAY_FROM:-}                                                                      # optional restore-point replay (very slow on production: about 26 min from 94); empty = off
CADDY_DIR=${CADDY_DIR:-/etc/caddy}
SYSTEMCTL_CHECK=${SYSTEMCTL_CHECK:-1}
if [ -z "${RUN_GUARD+x}" ]; then
  if [ -d /run/systemd/system ] && command -v systemd-run >/dev/null 2>&1; then
    RUN_GUARD="systemd-run --scope -q -p MemoryMax=700M -p CPUQuota=70% nice -n 19 ionice -c3"
  else
    RUN_GUARD="nice -n 19"
  fi
fi
STAGES="check canary create verify package upload drill"
ANON_BASES="$ORIGIN_BASES"
if [ "$CDN_ENABLED" = 1 ]; then
  ANON_BASES="$ORIGIN_BASES $CDN_BASE"; CDN_NOTE="enabled: probed at $CDN_BASE"
else
  CDN_NOTE="disabled (the bucket's CDN setting is off): not probed, because a missing CDN hostname can fail DNS or TLS and that proves nothing about the object"
fi

# ---- helpers -------------------------------------------------------------------------------------------------------------------------------------
RUN_ID=""; RUN=""; CUR=""
say()  { printf '%s %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
refuse() {  # REFUSED: print why and exit WITHOUT touching any run folder or bucket object
  echo "" >&2; echo "REFUSED: $1" >&2; echo "(nothing was changed: no run file and no bucket object was written by this refusal)" >&2
  exit 3
}
write_json() {  # write_json FILE key value [key value ...] : flat JSON object of strings
  python3 -I -c 'import json,sys
a=sys.argv[2:]; d={a[i]:a[i+1] for i in range(0,len(a),2)}
open(sys.argv[1],"w").write(json.dumps(d,indent=1,sort_keys=True)+"\n")' "$@"
}
remote_failed() {  # best effort: record the failure next to the uploaded objects, once
  local pf="$RUN/remote-marker.prefix" P
  { [ -f "$pf" ] && [ ! -e "$RUN/remote-failed.done" ]; } || return 0
  P=$(cat "$pf"); : > "$RUN/remote-failed.done"
  write_json "$RUN/FAILED.json" kind run-failed run "$RUN_ID" stage "$CUR" reason "$1" time "$(date -u +%FT%TZ)" \
    meaning "This run failed. Whatever else is in this prefix is NOT a verified recovery set." 2>/dev/null || return 0
  if timeout 60 "$S3CMD" -c "$S3CFG" --acl-private --no-guess-mime-type --no-progress put "$RUN/FAILED.json" "s3://$BUCKET/$P/FAILED.json" >/dev/null 2>&1; then
    echo "FAILED.json was recorded in s3://$BUCKET/$P/" >&2
  else
    echo "could not record FAILED.json in the bucket; the local FAILED file is authoritative" >&2
  fi
}
fail() {
  local code=${2:-1}
  trap - ERR; set +e
  echo "" >&2; echo "STOP: $1" >&2
  if [ -n "$RUN" ] && [ -d "$RUN" ]; then
    if [ ! -e "$RUN/FAILED" ]; then   # the FIRST failure is the one kept
      { echo "stage=$CUR"; echo "reason=$1"; date -u +%FT%TZ; } > "$RUN/FAILED" 2>/dev/null
    fi
    remote_failed "$1"
    cat >&2 <<EOF

This run ($RUN_ID) FAILED in stage "$CUR" and will not be continued. Nothing outside $RUN and (for stages upload and canary) the new objects under
s3://$BUCKET/sets/$RUN_ID/ and s3://$BUCKET/canary/ was written; no production file, service or earlier object was changed.
To retry:  1) read $RUN/log.txt     2) mv $RUN $WORK/failed-$RUN_ID     (a rename, nothing is deleted)
           3) fix the cause        4) $0 all      (a NEW run id; uploaded objects of a failed run stay, are never overwritten, and are never a verified
              set: they lack DRILL-VERIFIED.json, and carry FAILED.json when it could be written)
EOF
  fi
  exit "$code"
}
trap 'fail "unexpected error (exit $?) at line $LINENO: $BASH_COMMAND"' ERR

# Every s3cmd call goes through s3x. s3cmd prints a warning that QUOTES a malformed line of its own config file ("Ignoring invalid line in ...: <the line>")
# and that line can be the secret itself. s3x therefore holds s3cmd's stderr in memory, replaces any such warning with a fixed sentence, masks the access key
# and secret values wherever they appear, and only then passes the text on (to the log, or to the file named by its first argument). s3cmd's stdout is not touched.
s3x() {  # s3x ERRDEST args... : ERRDEST is a file for the cleaned stderr, or - for this script's stderr. Returns s3cmd's own exit status.
  local dest=$1; shift
  ${S3X_GUARD:-} python3 -I -c '
import re, subprocess, sys
dest, cfg, exe = sys.argv[1:4]
vals = set()
try:
    for ln in open(cfg, encoding="utf-8", errors="replace"):
        m = re.match(r"\s*(access_key|secret_key|access_token)\s*=\s*(\S+)\s*$", ln)
        if m and len(m.group(2)) >= 8:
            vals.add(m.group(2))
except OSError:
    pass
try:
    p = subprocess.Popen([exe, "-c", cfg] + sys.argv[4:], stderr=subprocess.PIPE)
except OSError as e:
    sys.stderr.write("cannot run s3cmd: %s\n" % e.strerror); sys.exit(127)
err = p.communicate()[1].decode("utf-8", "replace")
out = []
for ln in err.splitlines(True):
    if re.search(r"invalid line", ln, re.I):
        ln = "WARNING: s3cmd reported a problem with a line of its config file; that line is never logged\n"
    for v in vals:
        ln = ln.replace(v, "[redacted]")
    out.append(ln)
txt = "".join(out)
if dest == "-":
    sys.stderr.write(txt); sys.stderr.flush()
else:
    open(dest, "w").write(txt)
sys.exit(p.returncode if p.returncode >= 0 else 128 - p.returncode)
' "$dest" "$S3CFG" "$S3CMD" "$@"
}
s3()   { s3x - "$@"; }
jcheck() { python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if eval(sys.argv[2]) else 1)' "$1" "$2"; }
need() { [ -e "$1" ] || fail "missing: $1"; }
sizeof() { stat -c %s "$1"; }
guard() { $RUN_GUARD "$@"; }

load_run() {
  # Order matters: take the lock and look at the run BEFORE anything is allowed to write into it, so that a competing process can only ever REFUSE.
  local id=${1:-}
  [ -n "$id" ] || refuse "give a run id (create one with: $0 new)"
  [ -d "$WORK" ] || refuse "no work folder $WORK"
  [ -d "$WORK/run-$id" ] || refuse "no such run: $WORK/run-$id"
  exec 9>>"$WORK/.lock" || refuse "cannot open the lock file $WORK/.lock"
  flock -n 9 || refuse "another process holds $WORK/.lock (a stage is running); wait for it to finish, then look at '$0 status $id'"
  [ ! -e "$WORK/run-$id/FAILED" ] || refuse "run $id already failed ($(tr '\n' ' ' < "$WORK/run-$id/FAILED")); move it aside and start a new run"
  RUN_ID=$id; RUN="$WORK/run-$id"
  mkdir -p "$RUN/stages"
}
precheck() {  # precheck STAGE : refuse (touching nothing, not even the log) unless the previous stage is OK and this one has not run
  local cur=$1 prev="" s
  for s in $STAGES; do [ "$s" = "$cur" ] && break; prev=$s; done
  [ -z "$prev" ] || [ -e "$RUN/stages/$prev.ok" ] || refuse "stage $cur needs stage $prev to have finished OK first"
  [ ! -e "$RUN/stages/$cur.ok" ] || refuse "stage $cur already finished in this run; a stage never runs twice (start a new run)"
}
begin() { CUR=$1; say "== stage $CUR (run $RUN_ID)"; }
finish() { date -u +%FT%TZ > "$RUN/stages/$CUR.ok"; say "== stage $CUR OK"; }
snap_live() {  # size + mtime of the production files that must not change (sidecars and the exporter's heartbeat excluded)
  local f
  for f in "$LIVE_LEAGUE" "$LIVE_LEAGUE.archive" "$LIVE_ORIGINAL" "$LIVE_EQUIV" "$LIVE_FEED/latest.json"; do
    [ -e "$f" ] && stat -c '%n %s %Y' "$f" || echo "$f absent"
  done
}
anon_denied() {  # anon_denied URL TOKEN : unauthenticated GET must be 401/403 and must not return the token
  local url=$1 token=${2:-} code
  code=$(curl -sS -m 30 -r 0-65535 -o "$RUN/anon.body" -w '%{http_code}' "$url") || fail "anonymous probe could not reach $url (a probe that cannot connect proves nothing)"
  case "$code" in 401|403) ;; *) fail "anonymous GET $url returned $code (expected 401 or 403)";; esac
  if [ -n "$token" ] && grep -q -F "$token" "$RUN/anon.body"; then fail "anonymous GET $url RETURNED THE CANARY CONTENT"; fi
  say "   anonymous denied ($code): $url"
}
# ---- privacy of an uploaded object -------------------------------------------------------------------------------------------------------------------
# What "private" rests on, per object: (1) it was uploaded with --acl-private; (2) the ACL is read when Spaces allows it, and a public grant fails the run;
# (3) AUTHENTICATED retrieval returns exactly the local bytes (so the object exists and a later anonymous 403 is not just "no such key");
# (4) an ANONYMOUS GET is denied (401/403) on every working origin endpoint.
# A bucket-scoped Spaces key is NOT allowed to read ACLs (observed 2026-11-11: s3cmd info shows the object's metadata, then the ACL read returns 403 AccessDenied).
# That single, specific refusal is tolerated, recorded as "ACL inspection unavailable", and never described as an ACL having been verified. Anything else
# (bad credentials, a missing object, a network error, any other status, a metadata read that is itself refused) fails the stage.
ACL_UNAVAILABLE_NOTE="ACL inspection unavailable; privacy verified by authenticated retrieval and anonymous probes."
acl_note() { printf '%s %s\n' "$2" "$1" >> "$RUN/acl-inspection.txt"; }     # acl_note TARGET readable|unavailable
acl_summary() {  # the sentence recorded in the markers: it never claims an ACL was verified when it could not be read
  if [ -s "$RUN/acl-inspection.txt" ] && grep -q '^unavailable ' "$RUN/acl-inspection.txt"; then printf '%s' "$ACL_UNAVAILABLE_NOTE"
  elif [ -s "$RUN/acl-inspection.txt" ]; then printf '%s' "ACL read on every checked object: no public grant; also verified by authenticated retrieval and anonymous probes."
  else printf '%s' "no object checked yet"; fi
}
acl_check() {  # acl_check BUCKET/KEY
  local target=$1 out="" ef rc=0
  ef="$RUN/acl-error-$(printf '%s' "$target" | tr -c 'A-Za-z0-9._-' '_').txt"
  out=$(s3x "$ef" info "s3://$target") || rc=$?
  if [ "$rc" = 0 ]; then
    printf '%s\n' "$out" | grep -q '^ *ACL:' || fail "s3cmd did not show the ACL of $target, so it cannot be told whether it is private"
    if printf '%s\n' "$out" | grep -i -E 'anon|AllUsers|http://acs.amazonaws.com/groups' >/dev/null; then fail "object $target has a public grant: $out"; fi
    acl_note "$target" readable; say "   ACL read: no public grant: $target"; return 0
  fi
  if printf '%s\n' "$out" | grep -q '^ *File size:' && printf '%s\n' "$out" | grep -q 'MD5 sum:' && ! printf '%s\n' "$out" | grep -q '^ *ACL:' \
     && [ "$(grep -c 'S3 error' "$ef")" = 1 ] && grep -q 'S3 error: 403 (AccessDenied)' "$ef"; then
    acl_note "$target" unavailable
    say "   the ACL read of $target was refused with 403 AccessDenied (its metadata read worked); the ACL itself was NOT inspected"
    say "   $ACL_UNAVAILABLE_NOTE (the checks below must pass)"
    return 0
  fi
  cat "$ef" >&2
  fail "cannot read the ACL of $target, and this is not the one tolerated case (only the ACL read itself being refused with AccessDenied, after the metadata read worked)"
}
private_object() {  # private_object KEY LOCALFILE : see above; the authenticated copy must equal LOCALFILE byte for byte (streamed, nothing is written to disk)
  local key=$1 lf=$2 b want got
  acl_check "$BUCKET/$key"
  want=$(sha256sum "$lf" | cut -d' ' -f1)
  got=$(S3X_GUARD="$RUN_GUARD" s3 --no-progress get "s3://$BUCKET/$key" - | sha256sum | cut -d' ' -f1) || fail "authenticated retrieval of $key failed"
  [ "$got" = "$want" ] || fail "authenticated retrieval of $key returned different bytes (SHA-256 $got, local $want)"
  say "   authenticated retrieval OK, content equals the local file: $key"
  for b in $ANON_BASES; do anon_denied "$b$key" ""; done
}
validate_s3cfg() {  # the shape of the credential file; prints line numbers and kinds of problem, NEVER a value
  local rc=0
  python3 -I - "$S3CFG" <<'PY' || rc=$?
import re, sys
want = {"access_key", "secret_key", "host_base", "host_bucket", "use_https", "signature_v2"}
seen, problems = {}, []
lines = open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines()
if not lines or lines[0].strip() != "[default]":
    problems.append("the first line is not [default]")
for n, ln in enumerate(lines[1:], 2):
    if not ln.strip():
        continue
    m = re.fullmatch(r"([a-z_0-9]+) = (\S.*)", ln)
    if not m:
        problems.append("line %d is not in 'name = value' form" % n); continue
    k, v = m.groups()
    if k not in want:
        problems.append("line %d has a name that is not expected here" % n); continue
    if k in seen:
        problems.append("line %d repeats a name already given" % n); continue
    seen[k] = v
for k in sorted(want - set(seen)):
    problems.append("%s is missing" % k)
a, s = seen.get("access_key", ""), seen.get("secret_key", "")
if a and not re.fullmatch(r"[A-Z0-9]{20}", a):
    problems.append("access_key is not 20 capital letters/digits (it has %d characters)" % len(a))
if s and not (40 <= len(s) <= 64 and re.fullmatch(r"[A-Za-z0-9+/=_-]+", s)):
    problems.append("secret_key does not look like a Spaces secret (it has %d characters)" % len(s))
if problems:
    print("credential file problems (values are never printed): " + "; ".join(problems)); sys.exit(1)
print("credential file structure OK: access key %d characters, secret key %d characters (values not shown)" % (len(a), len(s)))
PY
  [ "$rc" = 0 ] || fail "the credential file $S3CFG is malformed (see above; its contents are never printed); re-enter it"
}

# ---- the existing recovery-set artifact: located, never assumed; full SHA-256 ------------------------------------------------------------------------
locate_artifact() {  # stdout: one path per file that has the exact size AND the full recorded SHA-256. exit 0 if at least one, else 1. Notes go to stderr.
  local f h sz d
  local cand=() dirs=() matches=()
  [[ "$EXISTING_ARTIFACT_SHA256" =~ ^[0-9a-f]{64}$ ]] || { echo "EXISTING_ARTIFACT_SHA256 must be the full 64 lowercase hex digits" >&2; return 1; }
  [[ "$EXISTING_ARTIFACT_BYTES" =~ ^[0-9]+$ ]] || { echo "EXISTING_ARTIFACT_BYTES must be a number" >&2; return 1; }
  if [ -n "$EXISTING_ARTIFACT" ]; then
    cand=("$EXISTING_ARTIFACT")
  else
    for d in $SEARCH_DIRS; do if [ -d "$d" ]; then dirs+=("$d"); fi; done
    if [ "${#dirs[@]}" -eq 0 ]; then echo "none of the search folders exists: $SEARCH_DIRS" >&2; return 1; fi
    while IFS= read -r -d '' f; do cand+=("$f"); done < <(find "${dirs[@]}" -type f -size "${EXISTING_ARTIFACT_BYTES}c" -print0 2>/dev/null || true)
  fi
  for f in "${cand[@]}"; do
    if [ ! -f "$f" ]; then echo "note: $f is not a regular file" >&2; continue; fi
    sz=$(stat -c %s "$f")
    if [ "$sz" != "$EXISTING_ARTIFACT_BYTES" ]; then echo "note: $f is $sz bytes, not $EXISTING_ARTIFACT_BYTES" >&2; continue; fi
    h=$(sha256sum "$f" | cut -d' ' -f1)
    if [ "$h" = "$EXISTING_ARTIFACT_SHA256" ]; then matches+=("$f")
    else echo "note: $f has the right size but SHA-256 $h, which is NOT the recorded $EXISTING_ARTIFACT_SHA256" >&2; fi
  done
  if [ "${#matches[@]}" -eq 0 ]; then return 1; fi
  printf '%s\n' "${matches[@]}"
}
check_artifact() {
  if [ -z "$EXISTING_ARTIFACT_SHA256" ]; then
    [ "$ALLOW_NO_ARTIFACT" = 1 ] || fail "EXISTING_ARTIFACT_SHA256 is empty; set ALLOW_NO_ARTIFACT=1 only if you accept not checking the old artifact"
    say "   WARNING: the old artifact was NOT checked (ALLOW_NO_ARTIFACT=1)"; return 0
  fi
  locate_artifact > "$RUN/existing-artifact.found" 2> "$RUN/existing-artifact.notes" || {
    cat "$RUN/existing-artifact.notes" >&2
    fail "no regular file of $EXISTING_ARTIFACT_BYTES bytes with the full SHA-256 $EXISTING_ARTIFACT_SHA256 was found (${EXISTING_ARTIFACT:-searched: $SEARCH_DIRS}); not guessing a path"; }
  local art; art=$(head -1 "$RUN/existing-artifact.found")
  cat "$RUN/existing-artifact.notes"
  say "   existing artifact located: $art ($EXISTING_ARTIFACT_BYTES bytes, full SHA-256 equals the recorded value)"
  [ "$(basename "$art")" = "$EXISTING_ARTIFACT_NAME" ] || say "   note: its name is $(basename "$art"), not $EXISTING_ARTIFACT_NAME (the content matches, so it still counts)"
  [ "$(wc -l < "$RUN/existing-artifact.found")" -le 1 ] || say "   note: more copies with identical content: $(tail -n +2 "$RUN/existing-artifact.found" | tr '\n' ' ')"
  echo "$art" > "$RUN/existing-artifact.path"
  if [ -n "$EXISTING_SET" ]; then   # the artifact's own manifest must be the manifest of the set folder we compare against
    local rc=0
    python3 -I - "$art" "$EXISTING_SET/RECOVERY_SET.json" <<'PY' || rc=$?
import hashlib, sys, tarfile
art, mf = sys.argv[1:3]
want = hashlib.sha256(open(mf, "rb").read()).hexdigest()
best = None
with tarfile.open(art) as t:
    for m in t:
        if m.isfile() and (m.name == "RECOVERY_SET.json" or m.name.endswith("/RECOVERY_SET.json")):
            if best is None or len(m.name) < len(best.name):
                best = m
    if best is None:
        print("no RECOVERY_SET.json inside the artifact"); sys.exit(2)
    got = hashlib.sha256(t.extractfile(best).read()).hexdigest()
print("artifact member %s sha256 %s | EXISTING_SET manifest sha256 %s" % (best.name, got, want))
sys.exit(0 if got == want else 1)
PY
    case $rc in
      0) say "   the artifact's manifest is byte-identical to $EXISTING_SET/RECOVERY_SET.json" ;;
      2) say "   WARNING: the artifact holds no RECOVERY_SET.json, so it could not be tied to $EXISTING_SET (its checksum still matched)" ;;
      *) fail "the artifact's manifest differs from $EXISTING_SET/RECOVERY_SET.json: they are not the same recovery set" ;;
    esac
  fi
}
recheck_artifact() {  # stage verify: the located file still has the recorded full hash
  [ -s "$RUN/existing-artifact.path" ] || return 0
  local art h; art=$(cat "$RUN/existing-artifact.path"); [ -f "$art" ] || fail "the existing artifact $art has disappeared"
  h=$(sha256sum "$art" | cut -d' ' -f1)
  [ "$h" = "$EXISTING_ARTIFACT_SHA256" ] || fail "$art now hashes to $h, not to the recorded $EXISTING_ARTIFACT_SHA256"
  say "   existing artifact re-hashed: still equals the recorded SHA-256 ($art)"
}

# ---- stages --------------------------------------------------------------------------------------------------------------------------------------
st_check() {
  begin check
  need "$LIVE_LEAGUE"; need "$LIVE_LEAGUE.archive"; need "$LIVE_ORIGINAL"; need "$LIVE_EQUIV"; need "$LIVE_FEED/latest.json"
  need "$ENGINE_TREE/src"; need "$TOOL_TREE/tools/league_recovery_set.py"; need "$PB_TREE/tools/public_backup.py"; need "$S3CFG"
  command -v "$S3CMD" >/dev/null || fail "s3cmd is not installed"
  [ "$(stat -c %a "$S3CFG")" = "600" ] || fail "$S3CFG must be mode 600"
  [ "$(stat -c %u "$S3CFG")" = "$(id -u)" ] || fail "$S3CFG must be owned by the user running this"
  [ -n "$BUCKET" ] || fail "BUCKET is not set (export BUCKET=<the private bucket name>)"
  [ -n "${ORIGIN_BASES// /}" ] || fail "ORIGIN_BASES is empty: at least one working endpoint must be probed anonymously"
  say "   CDN: $CDN_NOTE"
  if [ -n "$EXISTING_SET" ]; then need "$EXISTING_SET/RECOVERY_SET.json"; elif [ "$ALLOW_NO_EXISTING" != 1 ]; then
    fail "EXISTING_SET (the existing event-141 recovery set folder) is required; set ALLOW_NO_EXISTING=1 only if you accept skipping that comparison"; fi
  check_artifact
  if [ "$SYSTEMCTL_CHECK" = 1 ] && command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet dflv2-league 2>/dev/null; then fail "dflv2-league is ACTIVE; this procedure expects the stopped league (do not pause it from here)"; fi
  fi
  for f in "$LIVE_ORIGINAL" "$LIVE_EQUIV"; do
    if [ -s "$f-wal" ]; then fail "$f has data in its write-ahead log; the byte copy would miss it"; fi
  done
  if [ "$INDEPENDENT" = 1 ]; then   # fail now, not at the end of a long run: can this machine hide the server paths in a private mount namespace?
    command -v unshare >/dev/null || fail "INDEPENDENT=1 needs unshare"
    local d
    for d in $HIDE_DIRS; do
      [ -d "$d" ] || fail "INDEPENDENT=1: $d does not exist, so there is nothing to hide (HIDE_DIRS=$HIDE_DIRS)"
      unshare -m --propagation private sh -c "mount -t tmpfs tmpfs '$d' && [ -z \"\$(ls -A '$d')\" ]" || fail "INDEPENDENT=1: cannot hide $d in a private mount namespace"
    done
    say "   independent-mode preflight OK: $HIDE_DIRS can be hidden (inside a private namespace; the real folders are untouched)"
  fi
  # tree pins (when the trees are git checkouts)
  local t c
  for t in "$TOOL_TREE" "$PB_TREE" "$ENGINE_TREE"; do
    if [ -d "$t/.git" ]; then c=$(git -C "$t" rev-parse HEAD); [ "$c" = "$(basename "$t")" ] || fail "$t is at $c, not at the commit its folder name says";
       [ -z "$(git -C "$t" status --porcelain)" ] || fail "$t has uncommitted changes"; fi
  done
  # the feed's event count (cheap read; the league file itself is only opened on the COPY, in stage verify)
  local rel; rel=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["release"])' "$LIVE_FEED/latest.json")
  say "   feed release: $rel"
  if [ -n "$EXPECT_EVENTS" ]; then
    [ "$(printf 'e%05d-' "$EXPECT_EVENTS")" = "${rel:0:7}" ] || fail "the feed's live release $rel is not at event $EXPECT_EVENTS (production state differs from the records: stop and look)"; fi
  # disk: measured peak 5.1-5.2 x S (set, tar, downloaded tar, extracted set, restored feed, restore-test work); the guard uses 6 x S
  mkdir -p "$WORK"; chmod 700 "$WORK"
  local S free
  S=$(( $(sizeof "$LIVE_LEAGUE") + $(sizeof "$LIVE_LEAGUE.archive") + $(sizeof "$LIVE_ORIGINAL") + $(sizeof "$LIVE_EQUIV") + $(du -sb "$LIVE_FEED" | cut -f1) ))
  free=$(df -B1 --output=avail "$WORK" | tail -1 | tr -d ' ')
  say "   S (sources) = $S bytes; peak need about 6 x S = $((6*S)) (measured 5.1-5.2 x); free on $WORK = $free; must keep $MIN_FREE_AFTER_BYTES free afterwards"
  [ $(( free - 6*S )) -ge "$MIN_FREE_AFTER_BYTES" ] || fail "not enough disk: free $free - 6 x $S leaves less than $MIN_FREE_AFTER_BYTES"
  case "$(df --output=fstype "$WORK" | tail -1 | tr -d ' ')" in tmpfs|ramfs) fail "$WORK is RAM-backed";; esac
  # the work folder and the credential must not be under any web root
  if [ -d "$CADDY_DIR" ]; then
    local root
    for root in $(grep -rhoE '^[[:space:]]*root[[:space:]]+(\*[[:space:]]+)?[^[:space:]]+' "$CADDY_DIR" 2>/dev/null | awk '{print $NF}'); do
      case "$WORK/" in "$root"/*) fail "$WORK is inside the web root $root";; esac
      case "$(dirname "$S3CFG")/" in "$root"/*) fail "$S3CFG is inside the web root $root";; esac
    done
  fi
  validate_s3cfg
  s3 ls "s3://$BUCKET" >/dev/null || fail "cannot list s3://$BUCKET with the configured credential (outbound HTTPS blocked, wrong key, or wrong bucket)"
  snap_live > "$RUN/live-before.txt"
  finish
}

st_canary() {
  begin canary
  local tok key
  tok="DFL-CANARY-$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
  key="canary/canary-$RUN_ID.txt"
  printf '%s test token, not a secret\n' "$tok" > "$RUN/canary.txt"
  s3 --acl-private --no-guess-mime-type --no-progress put "$RUN/canary.txt" "s3://$BUCKET/$key" > "$RUN/canary-put.txt" || fail "canary upload failed"
  acl_check "$BUCKET/$key"
  s3 --no-progress get "s3://$BUCKET/$key" "$RUN/canary-got.txt" > "$RUN/canary-get.txt" || fail "authenticated retrieval of the canary failed"
  cmp "$RUN/canary.txt" "$RUN/canary-got.txt" || fail "the canary came back with different content"
  grep -q -F "$tok" "$RUN/canary-got.txt" || fail "the canary token is missing from the authenticated copy"
  say "   authenticated retrieval OK and content verified (the object exists and is readable with the key)"
  local b
  for b in $ANON_BASES; do anon_denied "$b$key" "$tok"; done
  say "   CDN: $CDN_NOTE"
  echo "$CDN_NOTE" > "$RUN/cdn.txt"
  if [ -n "$ANON_LIST_URL" ]; then anon_denied "$ANON_LIST_URL" ""; else say "   anonymous listing probe SKIPPED (ANON_LIST_URL empty)"; fi
  echo "$key" > "$RUN/canary.key"
  finish
}

st_create() {
  begin create
  local SET="$RUN/set"; mkdir "$SET"
  say "   league recovery set (active pair + exact upgrade input + equivalent + code tree)"
  guard python3 -I "$TOOL_TREE/tools/league_recovery_set.py" create "$SET/league-set" --league "$LIVE_LEAGUE" --original "$LIVE_ORIGINAL" \
        --equivalent "$LIVE_EQUIV" --code-tree "$ENGINE_TREE" > "$RUN/create-league.json" 2> "$RUN/create-league.err" || { cat "$RUN/create-league.err" >&2; fail "league recovery set create failed"; }
  say "   public feed backup (--require-quiescent; the exporter may keep rewriting status.json)"
  guard python3 -I "$PB_TREE/tools/public_backup.py" create --src "$LIVE_FEED" --out "$SET/feed" --require-quiescent > "$RUN/create-feed.json" 2> "$RUN/create-feed.err" || { cat "$RUN/create-feed.err" >&2; fail "feed backup create failed (a .partial folder was left in $SET as evidence)"; }
  jcheck "$RUN/create-feed.json" 'd["ok"] is True and d["quiescent"] is True' || fail "feed backup did not report ok and quiescent"
  snap_live > "$RUN/live-after.txt"
  cmp "$RUN/live-before.txt" "$RUN/live-after.txt" || fail "a production file changed during the backup (see live-before.txt / live-after.txt)"
  finish
}

st_verify() {
  begin verify
  local SET="$RUN/set"
  guard python3 -I "$TOOL_TREE/tools/league_recovery_set.py" verify "$SET/league-set" > "$RUN/verify-league.json" || fail "league recovery set does not verify"
  jcheck "$RUN/verify-league.json" 'd["problems"] == []' || fail "league recovery set verify reported problems"
  guard python3 -I "$PB_TREE/tools/public_backup.py" verify "$SET/feed" > "$RUN/verify-feed.json" || fail "feed backup does not verify"
  jcheck "$RUN/verify-feed.json" 'd["ok"] is True and d["n_problems"] == 0' || fail "feed backup verify reported problems"
  # pairing: the league's head event equals the feed's live release event
  python3 -I - "$SET" "$EXPECT_EVENTS" <<'PY' || fail "league set and feed are not at the same event (or not at the expected event)"
import json, sys
set_dir, expect = sys.argv[1], sys.argv[2]
m = json.load(open(set_dir + "/league-set/RECOVERY_SET.json"))
seq = m["active"]["head"]["seq"]
rel = json.load(open(set_dir + "/feed/files/latest.json"))["release"]
fev = int(rel[1:6])
print("league head event", seq, "| feed release", rel)
assert seq == fev, "league head %s != feed release event %s" % (seq, fev)
if expect:
    assert seq == int(expect), "head %s != expected %s" % (seq, expect)
assert m["original"]["head"]["seq"] < seq, "the original must be an earlier event"
assert m["link"]["continues_event"] == m["original"]["head"]["seq"]
assert m["link"]["active_lineage_source_file_sha256"] == [c for c in m["components"] if c["role"] == "original-league"][0]["sha256"], "lineage does not name the original file"
print("lineage names the exact original by SHA-256; continues event", m["link"]["continues_event"])
PY
  # the existing recovery set (made 2026-11-03) : the preserved history must be the same history
  if [ -n "$EXISTING_SET" ]; then
    guard python3 -I "$TOOL_TREE/tools/league_recovery_set.py" verify "$EXISTING_SET" > "$RUN/verify-existing.json" || fail "the EXISTING recovery set does not verify"
    jcheck "$RUN/verify-existing.json" 'd["problems"] == []' || fail "the existing recovery set verify reported problems"
    python3 -I - "$EXISTING_SET/RECOVERY_SET.json" "$SET/league-set/RECOVERY_SET.json" <<'PY' || fail "the new set does not preserve the same history as the existing recovery set"
import json, sys
old, new = (json.load(open(p)) for p in sys.argv[1:3])
def sha(m, role):
    return sorted(c["sha256"] for c in m["components"] if c["role"] == role)
checks = {
  "exact upgrade input (original-141) SHA-256 identical": sha(old, "original-league") == sha(new, "original-league") != [],
  "preserved equivalent copy SHA-256 identical": sha(old, "original-equivalent") == sha(new, "original-equivalent"),
  "original head (event, chain, digest) identical": old["original"]["head"] == new["original"]["head"],
  "original journal rows and restore points identical": all(old["original"][k] == new["original"][k] for k in ("journal_rows", "checkpoint_snapshots", "other_snapshots")),
  "originating-code fingerprint identical": old["original"]["fingerprint"] == new["original"]["fingerprint"],
  "active-code fingerprint identical": old["active"]["fingerprint"] == new["active"]["fingerprint"] == new["code"]["tree_fingerprint"],
  "lineage link identical": old["link"] == new["link"],
  "active head identical (same event and chain: the league did not move)": old["active"]["head"] == new["active"]["head"],
  "archive counts identical": old["active"]["archive_counts"] == new["active"]["archive_counts"],
}
bad = [k for k, v in checks.items() if not v]
for k, v in checks.items():
    print(("  ok   " if v else "  FAIL ") + k)
sys.exit(1 if bad else 0)
PY
    recheck_artifact
  else
    say "   WARNING: no existing recovery set given (ALLOW_NO_EXISTING=1): history NOT compared with it"
  fi
  write_history_readme
  finish
}

write_history_readme() {
  python3 -I - "$RUN/set" "$TOOL_TREE" "$PB_TREE" "$ENGINE_TREE" "$RUN_ID" <<'PY' || fail "could not write RECOVERY_HISTORY.md"
import json, os, subprocess, sys
set_dir, tool, pb, eng, rid = sys.argv[1:6]
m = json.load(open(set_dir + "/league-set/RECOVERY_SET.json"))
def commit(t):
    try:
        return subprocess.run(["git", "-C", t, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or os.path.basename(t)
    except OSError:
        return os.path.basename(t)
txt = """# Recovery history of this DFL league (run %(rid)s)

This folder holds TWO verified parts, taken as one run:

* `league-set/`  the league's complete recovery set (tool: tools/league_recovery_set.py, commit %(tool)s)
* `feed/`        the public feed backup (tool: tools/public_backup.py, commit %(pb)s)

## Why the league needs more than the active file
The active league file continues an upgrade from event %(base)s. It holds journal rows %(first)s..%(head)s and one base snapshot; events 1..%(base)s and the
earlier restore points exist ONLY in the original file. So the set keeps:

| What | Where | Identity |
|---|---|---|
| Active league + archive | `league-set/active/` | head event %(head)s, chain %(chain)s |
| The exact file the upgrade read | `league-set/original-141/league.db` | SHA-256 %(osha)s (the active file's lineage records this value) |
| The preserved original (same history, different bytes) | `league-set/original-equivalent/` | checked for identical journal rows, restore-point checksums, code records |
| Code that runs the active league | `league-set/code/engine-tree.tar.gz` | fingerprint %(afp)s, engine commit %(eng)s |
| Code that wrote the original | inside `original-141/league.db` (its `code` table) | fingerprint %(ofp)s (the tools-release tree, commit 59ea680) |

## Recover (on any machine with Python 3 and the two pinned checkouts above)
1. `python3 -I TOOL/tools/league_recovery_set.py verify league-set`           every file against its manifest
2. `python3 -I TOOL/tools/league_recovery_set.py restore-test league-set WORK`   restores everything from the set alone and checks it (add `--replay-from 94` to also replay restore points; slow)
3. To get the active league back: `python3 -I TOOL/tools/league_backup.py restore league-set/active DEST`
4. To read or replay the first %(base)s events: `python3 -I ENGINE/tools/league_store.py extract-code league-set/original-141/league.db DIR` gives the exact originating code; run it from there.
5. Public feed: `python3 -I PB/tools/public_backup.py verify feed` then `restore feed DEST`.
Nothing here is needed to keep the site running; it exists so history survives loss of the server.
""" % dict(rid=rid, tool=commit(tool), pb=commit(pb), eng=commit(eng), base=m["original"]["head"]["seq"], first=m["active"]["journal_first_seq"],
           head=m["active"]["head"]["seq"], chain=m["active"]["head"]["chain"], osha=m["link"]["active_lineage_source_file_sha256"],
           afp=m["active"]["fingerprint"], ofp=m["original"]["fingerprint"])
open(set_dir + "/RECOVERY_HISTORY.md", "w").write(txt)
PY
}

st_package() {
  begin package
  ( cd "$RUN" && tar -cf set.tar set && sha256sum set.tar > set.tar.sha256 && sha256sum -c set.tar.sha256 > package-check.txt ) || fail "packaging or its checksum check failed"
  grep -q '^set.tar: OK$' "$RUN/package-check.txt" || fail "checksum file does not verify set.tar (names must match)"
  [ "$(awk '{print $2}' "$RUN/set.tar.sha256")" = "set.tar" ] || fail "checksum file must name set.tar"
  [ "$(tar -tf "$RUN/set.tar" | wc -l)" = "$(cd "$RUN" && find set | wc -l)" ] || fail "tar entry count differs from the set folder"
  if tar -tf "$RUN/set.tar" | grep -E '^/|(^|/)\.\.(/|$)' >/dev/null; then fail "tar has unsafe paths"; fi
  say "   set.tar $(sizeof "$RUN/set.tar") bytes, $(cut -d' ' -f1 "$RUN/set.tar.sha256")"
  finish
}

st_upload() {
  begin upload
  local P="sets/$RUN_ID" n sha
  sha=$(cut -d' ' -f1 "$RUN/set.tar.sha256")
  n=$(s3 ls "s3://$BUCKET/$P/" | wc -l) || fail "cannot list the target prefix"
  [ "$n" = 0 ] || fail "s3://$BUCKET/$P/ is not empty; a set never overwrites anything"
  echo "$P" > "$RUN/remote-marker.prefix"     # from here on, a failure also leaves FAILED.json in the prefix (best effort)
  s3 --acl-private --no-guess-mime-type --no-progress $S3_PUT_OPTS put "$RUN/set.tar" "s3://$BUCKET/$P/set.tar" > "$RUN/upload-tar.txt" || fail "upload of set.tar failed"
  s3 --acl-private --no-guess-mime-type --no-progress $S3_PUT_OPTS put "$RUN/set.tar.sha256" "s3://$BUCKET/$P/set.tar.sha256" > "$RUN/upload-sha.txt" || fail "upload of set.tar.sha256 failed"
  local rsize; rsize=$(s3 ls "s3://$BUCKET/$P/set.tar" | awk -v u="s3://$BUCKET/$P/set.tar" '$4 == u {print $3}')   # exact name: a bare prefix also matches set.tar.sha256
  [ "$rsize" = "$(sizeof "$RUN/set.tar")" ] || fail "remote set.tar is $rsize bytes, local is $(sizeof "$RUN/set.tar")"
  # privacy of the two data objects is established BEFORE any marker says the upload is complete
  private_object "$P/set.tar" "$RUN/set.tar"
  private_object "$P/set.tar.sha256" "$RUN/set.tar.sha256"
  write_json "$RUN/UPLOAD-COMPLETE.json" kind upload-complete run "$RUN_ID" set_tar_bytes "$(sizeof "$RUN/set.tar")" set_tar_sha256 "$sha" \
    checked "set.tar and set.tar.sha256 uploaded with --acl-private; remote size equals local size; authenticated retrieval returned exactly the local bytes; anonymous GET denied on: $ANON_BASES" \
    privacy "$(acl_summary)" \
    cdn "$CDN_NOTE" time "$(date -u +%FT%TZ)" \
    meaning "UPLOAD finished and the two data objects are private. This is NOT proof that the set restores: that is DRILL-VERIFIED.json, written last. A prefix with FAILED.json is never a verified set."
  s3 --acl-private --no-guess-mime-type --no-progress $S3_PUT_OPTS put "$RUN/UPLOAD-COMPLETE.json" "s3://$BUCKET/$P/UPLOAD-COMPLETE.json" > "$RUN/upload-marker.txt" || fail "upload of UPLOAD-COMPLETE.json failed"
  private_object "$P/UPLOAD-COMPLETE.json" "$RUN/UPLOAD-COMPLETE.json"
  echo "$P" > "$RUN/upload.prefix"
  finish
}

st_drill() {
  begin drill
  local P D; P=$(cat "$RUN/upload.prefix"); D="$RUN/drill"; mkdir "$D"
  say "   downloading from Spaces into the isolated folder $D"
  ( cd "$D" && s3 --no-progress get "s3://$BUCKET/$P/set.tar" set.tar && s3 --no-progress get "s3://$BUCKET/$P/set.tar.sha256" set.tar.sha256 \
      && s3 --no-progress get "s3://$BUCKET/$P/UPLOAD-COMPLETE.json" UPLOAD-COMPLETE.json ) > "$RUN/drill-download.txt" 2>&1 || fail "download from Spaces failed"
  ( cd "$D" && sha256sum -c set.tar.sha256 ) || fail "the DOWNLOADED set.tar does not match its checksum"
  cmp "$D/set.tar.sha256" "$RUN/set.tar.sha256" || fail "the downloaded checksum file differs from the one made before upload"
  [ "$(cut -d' ' -f1 "$D/set.tar.sha256")" = "$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["set_tar_sha256"])' "$D/UPLOAD-COMPLETE.json")" ] || fail "UPLOAD-COMPLETE.json names a different checksum"
  ( cd "$D" && tar -xf set.tar ) || fail "extraction failed"
  guard python3 -I "$TOOL_TREE/tools/league_recovery_set.py" verify "$D/set/league-set" > "$D/verify-league.json" || fail "downloaded league set does not verify"
  jcheck "$D/verify-league.json" 'd["problems"] == []' || fail "downloaded league set has problems"
  guard python3 -I "$PB_TREE/tools/public_backup.py" verify "$D/set/feed" > "$D/verify-feed.json" || fail "downloaded feed backup does not verify"
  jcheck "$D/verify-feed.json" 'd["ok"] is True and d["n_problems"] == 0' || fail "downloaded feed backup has problems"
  guard python3 -I "$PB_TREE/tools/public_backup.py" restore "$D/set/feed" "$D/restored-feed" > "$D/restore-feed.json" || fail "feed restore failed"
  jcheck "$D/restore-feed.json" 'd["ok"] is True' || fail "feed restore did not report ok"
  # compare the restored feed with the CAPTURED backup (never with the moving live folder)
  diff -r "$D/set/feed/files" "$D/restored-feed" > "$D/diff-restored-vs-downloaded.txt" || fail "restored feed differs from the downloaded backup's files"
  diff -r "$RUN/set/feed/files" "$D/restored-feed" > "$D/diff-restored-vs-captured.txt" || fail "restored feed differs from the backup captured before upload"
  say "   restored feed is byte-identical to the captured backup (before upload) and to the downloaded copy"
  local rt=("$D/set/league-set" "$D/restore-work") indep
  if [ -n "$REPLAY_FROM" ]; then rt+=(--replay-from "$REPLAY_FROM"); fi
  if [ "$INDEPENDENT" = 1 ]; then
    case "$WORK/" in */srv/dflv2/*|*/opt/dflv2-src/*) fail "WORK is inside a hidden path; cannot run the independent variant";; esac
    rt+=(--require-sources-absent)
    local cmd="" d; for d in $HIDE_DIRS; do cmd="$cmd mount -t tmpfs tmpfs $d &&"; done
    guard unshare -m --propagation private sh -c "$cmd python3 -I $TOOL_TREE/tools/league_recovery_set.py restore-test ${rt[*]}" > "$D/restore-test.txt" 2>&1 \
      || { tail -20 "$D/restore-test.txt" >&2; fail "restore test (original server paths hidden) failed"; }
    indep="yes: these paths were hidden during the restore test: $HIDE_DIRS"
  else
    guard python3 -I "$TOOL_TREE/tools/league_recovery_set.py" restore-test "${rt[@]}" > "$D/restore-test.txt" 2>&1 || { tail -20 "$D/restore-test.txt" >&2; fail "restore test failed"; }
    indep="no (INDEPENDENT=0)"
  fi
  tail -1 "$D/restore-test.txt" | python3 -I -c 'import json,sys; d=json.loads(sys.stdin.read()); sys.exit(0 if d["ok"] and not d["failed"] else 1)' || fail "restore test did not end ok"
  # every check above passed: only now is the drill marker written, and it is the last object of the run
  local head rel replay
  head=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["active"]["head"]["seq"])' "$D/set/league-set/RECOVERY_SET.json")
  rel=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["release"])' "$D/set/feed/files/latest.json")
  replay=${REPLAY_FROM:+"yes, from restore point $REPLAY_FROM"}; replay=${replay:-"not run"}
  write_json "$RUN/DRILL-VERIFIED.json" kind drill-verified run "$RUN_ID" set_tar_sha256 "$(cut -d' ' -f1 "$D/set.tar.sha256")" league_head_event "$head" feed_release "$rel" \
    drill_host "the SAME host that made the set (this is a first drill, not a host-loss test)" independent_of_server_paths "$indep" restore_point_replay "$replay" cdn "$CDN_NOTE" \
    privacy "$(acl_summary) Every object was uploaded with --acl-private, retrieved with the key and compared byte for byte, and denied to anonymous GET on: $ANON_BASES" \
    proven "downloaded set.tar matched its checksum; league set and feed verified; feed restored byte-identical to the captured backup; league recovery set passed its restore test (active league and archive, exact event-141 upgrade input, preserved equivalent, originating code, lineage)" \
    not_proven "recovery after losing this server; restore-point replay unless stated above" time "$(date -u +%FT%TZ)"
  cp "$RUN/DRILL-VERIFIED.json" "$D/DRILL-VERIFIED.json"
  s3 --acl-private --no-guess-mime-type --no-progress $S3_PUT_OPTS put "$RUN/DRILL-VERIFIED.json" "s3://$BUCKET/$P/DRILL-VERIFIED.json" > "$RUN/drill-marker.txt" || fail "upload of DRILL-VERIFIED.json failed"
  private_object "$P/DRILL-VERIFIED.json" "$RUN/DRILL-VERIFIED.json"
  cat <<EOF

=========================================================================================================================================
FIRST DRILL, SAME HOST. What this proves and what it does not.
 PROVEN : the set came back from Spaces byte for byte (SHA-256), verified, the feed restored identically to the captured backup, and the league
          recovery set restored and passed its restore test (active league + archive, the exact event-141 input, the originating code, the lineage).
 NOT PROVEN: recovery after losing this server (this drill ran on it); behaviour at a restore point replay$( [ -n "$REPLAY_FROM" ] && echo " (replayed from $REPLAY_FROM)" || echo " (not run)" );
          independence from the original server paths$( [ "$INDEPENDENT" = 1 ] && echo " (WAS tested: $HIDE_DIRS were hidden)" || echo " (NOT tested: INDEPENDENT=0)" ).
 CDN: $CDN_NOTE
 PRIVACY: $(acl_summary)
 Markers in s3://$BUCKET/$P/ : UPLOAD-COMPLETE.json (upload only) and DRILL-VERIFIED.json (this drill passed). Neither FAILED.json nor any failure exists.
 Nothing was started, nothing production was written, nothing was deleted. Drill copies stay in $D until you decide.
=========================================================================================================================================
EOF
  finish
}

# ---- entry ---------------------------------------------------------------------------------------------------------------------------------------
cmd=${1:-}; shift || true
case "$cmd" in
  locate)   # read-only, no run, no lock
    found=$(mktemp); notes=$(mktemp); rc=0
    locate_artifact > "$found" 2> "$notes" || rc=$?
    cat "$notes"; if [ "$rc" = 0 ]; then echo "FOUND (exact size and full SHA-256 match):"; cat "$found"; else echo "NOT FOUND: no regular file of $EXISTING_ARTIFACT_BYTES bytes has the full SHA-256 $EXISTING_ARTIFACT_SHA256"; fi
    rm -f "$found" "$notes"; exit "$rc" ;;
  new)
    mkdir -p "$WORK"; chmod 700 "$WORK"
    RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"; RUN="$WORK/run-$RUN_ID"; mkdir "$RUN" || fail "could not create a new run folder"; mkdir "$RUN/stages"
    echo "$RUN_ID" ;;
  status)   # read-only: works on any run, failed or not, and takes no lock
    sid=${1:-}; [ -n "$sid" ] && [ -d "$WORK/run-$sid" ] || refuse "no such run: ${sid:-<none>}"
    echo "stages finished: $(ls "$WORK/run-$sid/stages" 2>/dev/null | sed 's/\.ok$//' | tr '\n' ' ')"
    if [ -e "$WORK/run-$sid/FAILED" ]; then echo "STATE: FAILED"; cat "$WORK/run-$sid/FAILED"
    elif [ -e "$WORK/run-$sid/DRILL-VERIFIED.json" ] && [ -e "$WORK/run-$sid/stages/drill.ok" ]; then echo "STATE: DRILL-VERIFIED (uploaded, downloaded, restored and tested)"
    elif [ -e "$WORK/run-$sid/stages/upload.ok" ]; then echo "STATE: UPLOAD-COMPLETE only (the drill has not passed yet)"
    else echo "STATE: in progress (nothing uploaded yet)"; fi ;;
  check|canary|create|verify|package|upload|drill)
    load_run "${1:-}"; precheck "$cmd"; exec > >(tee -a "$RUN/log.txt") 2>&1; "st_$cmd" ;;
  all)
    RUN_ID=$("$0" new); for s in $STAGES; do "$0" "$s" "$RUN_ID" || exit $?; done; echo "run $RUN_ID complete: $WORK/run-$RUN_ID" ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
