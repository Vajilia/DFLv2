#!/usr/bin/env bash
# failtests.sh RIG : failure-injection and regression rehearsal for dfl-recovery-run.sh (synthetic data, local fake S3 that run_rehearsal.sh has started).
# Every test starts new runs; nothing is deleted. Exit status 0 only if every expectation held.
RIG=${1:?usage: failtests.sh RIG}; RIG=$(cd "$RIG" && pwd)
SCRIPT=${SCRIPT:-$(cd "$(dirname "$0")/.." && pwd)/dfl-recovery-run.sh}
. "$RIG/env.sh"; X=$SCRIPT; OUT=$RIG/ft.out
pass=0; bad=0
ok()  { echo "PASS  $1"; pass=$((pass+1)); }
no()  { echo "FAIL  $1"; bad=$((bad+1)); }
yes() { if [ "$1" = 0 ]; then ok "$2"; else no "$2"; fi; }          # yes RC DESC
s3()  { "$S3CMD" -c "$S3CFG" "$@"; }
has_obj() { s3 ls "s3://$BUCKET/$1" 2>/dev/null | awk -v u="s3://$BUCKET/$1" '$4 == u {f=1} END {exit !f}'; }
new() { $X new; }
snap() { find "$1" -printf '%P %s %T@\n' | sort | md5sum | cut -d' ' -f1; }
adv() { local r=$1 s; shift; for s in "$@"; do $X "$s" "$r" >"$OUT.adv" 2>&1 || { echo "   (setup: stage $s failed for run $r)"; tail -5 "$OUT.adv"; return 1; }; done; }
expect_fail() { # expect_fail "desc" RUN stage : exit 1 (a STOP)
  $X "$3" "$2" >"$OUT" 2>&1; local rc=$?
  if [ $rc = 1 ]; then ok "$1"; else no "$1 (exit $rc, expected 1)"; fi; }
expect_refused() { # expect_refused "desc" RUN stage : exit 3, REFUSED, and NOTHING in the run folder changes (no FAILED, no log line, no marker)
  local before after; before=$(snap "$WORK/run-$2")
  $X "$3" "$2" >"$OUT" 2>&1; local rc=$?
  after=$(snap "$WORK/run-$2")
  if [ $rc = 3 ] && grep -q '^REFUSED' "$OUT"; then ok "$1"; else no "$1 (exit $rc, expected a REFUSED with exit 3)"; fi
  if [ "$before" != "$after" ]; then no "   ...and the refusal MODIFIED the run folder"; fi; }

echo "### F1 wrong 'original' file -> create must fail; verify/package/upload must not run"
R1=$(new); adv $R1 check canary
LIVE_ORIGINAL=$LIVE_EQUIV expect_fail "create refuses an original whose SHA-256 is not the lineage's" $R1 create
expect_refused "verify refuses to run after a failed create" $R1 verify
expect_refused "package refuses" $R1 package; expect_refused "upload refuses" $R1 upload
[ -e "$WORK/run-$R1/FAILED" ] && ok "run marked FAILED" || no "run marked FAILED"
expect_refused "a failed run cannot be continued even by an earlier stage" $R1 check
s3 ls "s3://$BUCKET/sets/$R1/" | grep -q . && no "nothing uploaded for failed run" || ok "nothing uploaded for failed run"
a=$(md5sum < "$WORK/run-$R1/FAILED"); $X create $R1 >/dev/null 2>&1; $X check $R1 >/dev/null 2>&1; b=$(md5sum < "$WORK/run-$R1/FAILED")
[ "$a" = "$b" ] && ok "the first failure record is preserved byte for byte when a failed run is poked again" || no "the first failure record is preserved"
st=$($X status $R1 2>&1); grep -q 'STATE: FAILED' <<<"$st" && ok "status reports FAILED" || no "status reports FAILED"

echo "### F2 tampered set between create and verify"
R2=$(new); adv $R2 check canary create
python3 - "$WORK/run-$R2/set/league-set/active/archive.db" <<'PY'
import sys; f=open(sys.argv[1],"r+b"); f.seek(5000); b=f.read(1); f.seek(5000); f.write(bytes([b[0]^0xFF])); f.close()
PY
expect_fail "verify catches one flipped byte in the archive" $R2 verify
expect_refused "package refuses after failed verify" $R2 package; expect_refused "upload refuses" $R2 upload
s3 ls "s3://$BUCKET/sets/$R2/" | grep -q . && no "nothing uploaded" || ok "nothing uploaded"

echo "### F3 feed with a missing referenced file -> feed backup create fails"
cp -a "$LIVE_FEED" "$RIG/feed-broken"; rm "$(ls "$RIG"/feed-broken/playback/games/*.json | head -1)"
R3=$(new); adv $R3 check canary
LIVE_FEED=$RIG/feed-broken expect_fail "create refuses a feed whose index names a missing file" $R3 create
ls "$WORK/run-$R3/set" 2>/dev/null | grep -q partial && ok "evidence left as .partial inside the run folder" || no "evidence .partial present"
expect_refused "verify refuses" $R3 verify
rm -rf "$RIG/feed-broken"

echo "### F4 Spaces unreachable during upload"
R4=$(new); adv $R4 check canary create verify package
sed 's/host_base = .*/host_base = 127.0.0.1:5099/; s/host_bucket = .*/host_bucket = 127.0.0.1:5099/' "$S3CFG" > "$RIG/s3cfg-down"; chmod 600 "$RIG/s3cfg-down"
S3CFG=$RIG/s3cfg-down expect_fail "upload stops when the endpoint is unreachable" $R4 upload
expect_refused "drill refuses without a finished upload" $R4 drill
has_obj "sets/$R4/UPLOAD-COMPLETE.json" && no "no UPLOAD-COMPLETE.json for the failed upload" || ok "no UPLOAD-COMPLETE.json for the failed upload"

echo "### F5 object altered in the bucket after upload -> drill must catch it, and the failure must be recorded accurately"
R5=$(new); adv $R5 check canary create verify package upload; P5=$(cat "$WORK/run-$R5/upload.prefix")
has_obj "$P5/UPLOAD-COMPLETE.json" && ok "after upload: UPLOAD-COMPLETE.json is present" || no "after upload: UPLOAD-COMPLETE.json is present"
has_obj "$P5/DRILL-VERIFIED.json" && no "after upload: DRILL-VERIFIED.json is NOT present yet" || ok "after upload: DRILL-VERIFIED.json is NOT present yet"
has_obj "$P5/COMPLETE.json" && no "the old ambiguous COMPLETE.json no longer exists" || ok "the old ambiguous COMPLETE.json no longer exists"
st=$($X status $R5 2>&1); grep -q 'UPLOAD-COMPLETE only' <<<"$st" && ok "status says upload complete, drill not passed" || no "status says upload complete, drill not passed"
head -c "$(stat -c %s "$WORK/run-$R5/set.tar")" /dev/urandom > "$RIG/evil.tar"
s3 --acl-private $S3_PUT_OPTS put "$RIG/evil.tar" "s3://$BUCKET/$P5/set.tar" >/dev/null 2>&1
expect_fail "drill detects that the DOWNLOADED set.tar fails its checksum" $R5 drill
grep -q "does not match its checksum" "$WORK/run-$R5/FAILED" && ok "failure names the checksum" || no "failure names the checksum"
has_obj "$P5/FAILED.json" && ok "the failure is recorded in the bucket prefix (FAILED.json)" || no "FAILED.json recorded in the bucket"
has_obj "$P5/DRILL-VERIFIED.json" && no "a failed drill leaves NO DRILL-VERIFIED.json" || ok "a failed drill leaves NO DRILL-VERIFIED.json"
s3 get "s3://$BUCKET/$P5/FAILED.json" "$RIG/ft-failed.json" --force >/dev/null 2>&1; grep -q "checksum" "$RIG/ft-failed.json" && grep -q "NOT a verified recovery set" "$RIG/ft-failed.json" && ok "FAILED.json says what failed and that the prefix is not a verified set" || no "FAILED.json content"
rm -f "$RIG/evil.tar"

echo "### F6 public object / lying ACL must be caught by the canary stage"
cat > "$RIG/s3-public.sh" <<EOS
#!/bin/sh
# misconfigured client: uploads PUBLIC
args=""; for a in "\$@"; do [ "\$a" = "--acl-private" ] && a="--acl-public"; args="\$args \$a"; done
exec $S3CMD \$args
EOS
cat > "$RIG/s3-liar.sh" <<EOS
#!/bin/sh
# public upload, and an ACL view that hides it
for a in "\$@"; do [ "\$a" = "info" ] && { echo "ACL: owner: FULL_CONTROL"; exit 0; }; done
args=""; for a in "\$@"; do [ "\$a" = "--acl-private" ] && a="--acl-public"; args="\$args \$a"; done
exec $S3CMD \$args
EOS
chmod +x "$RIG/s3-public.sh" "$RIG/s3-liar.sh"
R6=$(new); adv $R6 check
S3CMD=$RIG/s3-public.sh expect_fail "canary stage fails when the object ends up public (ACL shows a public grant)" $R6 canary
R6b=$(new); adv $R6b check
S3CMD=$RIG/s3-liar.sh expect_fail "canary stage fails on the ANONYMOUS probe even when the ACL view looks clean" $R6b canary
grep -q "anonymous GET" "$WORK/run-$R6b/FAILED" && ok "failure came from the anonymous probe" || no "failure came from the anonymous probe"

echo "### F7 stage order, repeats, and web root"
R7=$(new); expect_refused "drill cannot run first" $R7 drill; R7b=$(new); expect_refused "upload cannot run first" $R7b upload
[ ! -e "$WORK/run-$R7/FAILED" ] && ok "an out-of-order command does not mark the run FAILED" || no "out-of-order command left FAILED"
R7c=$(new); adv $R7c check; expect_refused "a stage never runs twice" $R7c check
mkdir -p "$RIG/fakecaddy"; printf 'x.test {\n  root * %s\n}\n' "$(dirname "$WORK")" > "$RIG/fakecaddy/Caddyfile"
R7d=$(new); CADDY_DIR=$RIG/fakecaddy expect_fail "check refuses a work folder inside a web root" $R7d check
R7e=$(new); EXPECT_EVENTS=999 expect_fail "check refuses when production is not at the expected event" $R7e check
R7f=$(new); MIN_FREE_AFTER_BYTES=99999999999999 expect_fail "check refuses when disk would be too tight" $R7f check
R7g=$(new); EXISTING_SET= expect_fail "check refuses to run without the existing recovery set" $R7g check
chmod 644 "$S3CFG"; R7h=$(new); expect_fail "check refuses a credential file that is not mode 600" $R7h check; chmod 600 "$S3CFG"
R7i=$(new); HIDE_DIRS="$RIG/srv/dflv2 $RIG/does-not-exist" expect_fail "INDEPENDENT=1 preflight fails at check, not at the end of the run, when a path cannot be hidden" $R7i check
R7j=$(new); ORIGIN_BASES= expect_fail "check refuses an empty list of endpoints to probe" $R7j check
R7k=$(new); BUCKET= ORIGIN_BASES=http://127.0.0.1:5055/x/ expect_fail "check refuses to run without BUCKET (the bucket name is not in the repository)" $R7k check

echo "### F8 existing set records different history -> verify must fail"
python3 -I "$TOOL_TREE/tools/league_recovery_set.py" create "$RIG/existing-noequiv" --league "$LIVE_LEAGUE" --original "$LIVE_ORIGINAL" --code-tree "$ENGINE_TREE" >/dev/null 2>&1
R8=$(new); adv $R8 check canary create
EXISTING_SET=$RIG/existing-noequiv expect_fail "history comparison fails when the existing set preserves different pieces" $R8 verify
grep -q "same history" "$WORK/run-$R8/FAILED" && ok "failure names the history comparison" || no "failure names the history comparison"

echo "### A  the existing artifact is LOCATED and compared by size and FULL SHA-256"
$X locate > "$OUT" 2>&1; rc=$?; [ $rc = 0 ] && grep -q "found/a/b/c/dfl-recovery-set-20261103.tar" "$OUT" && ok "locate finds the artifact in a deep folder nobody told it about, by size and full hash" || no "locate finds the artifact"
RA=$(new); adv $RA check; [ "$(cat "$WORK/run-$RA/existing-artifact.path")" = "$RIG/found/a/b/c/dfl-recovery-set-20261103.tar" ] && ok "check records the located path" || no "check records the located path"
h=$EXISTING_ARTIFACT_SHA256; ch=$([ "${h:30:1}" = a ] && echo b || echo a); MUT="${h:0:30}$ch${h:31}"
[ "${MUT:0:8}" = "${h:0:8}" ] && [ "${MUT: -4}" = "${h: -4}" ] && [ "$MUT" != "$h" ] && ok "(test input: a checksum with the SAME first 8 and last 4 digits, different in the middle)" || no "test input construction"
RA1=$(new); EXISTING_ARTIFACT_SHA256=$MUT expect_fail "an artifact whose hash matches only the prefix and suffix is rejected (full comparison)" $RA1 check
grep -q "NOT the recorded" "$WORK/run-$RA1/existing-artifact.notes" && ok "the notes say it has the right size but is not the recorded hash" || no "notes"
RA2=$(new); EXISTING_ARTIFACT_BYTES=$((EXISTING_ARTIFACT_BYTES-1)) expect_fail "a file of the wrong size is not accepted" $RA2 check
mkdir -p "$RIG/found2"; cp "$RIG/found/a/b/c/dfl-recovery-set-20261103.tar" "$RIG/found2/dfl-recovery-set-20261103.tar"
python3 - "$RIG/found2/dfl-recovery-set-20261103.tar" <<'PY'
import sys; f=open(sys.argv[1],"r+b"); f.seek(1000); b=f.read(1); f.seek(1000); f.write(bytes([b[0]^1])); f.close()
PY
RA3=$(new); SEARCH_DIRS=$RIG/found2 expect_fail "same name, same size, one flipped byte: rejected" $RA3 check
grep -q "right size but SHA-256" "$WORK/run-$RA3/existing-artifact.notes" && ok "...and reported as the right size with another hash" || no "same-size note"
mkdir -p "$RIG/found-empty"; RA4=$(new); SEARCH_DIRS=$RIG/found-empty expect_fail "no artifact anywhere: check stops and does not assume a path" $RA4 check
RA5=$(new); EXISTING_ARTIFACT=$RIG/does-not-exist.tar expect_fail "an explicit wrong path is rejected" $RA5 check
RA5b=$(new); EXISTING_ARTIFACT_SHA256=5edea873 expect_fail "a shortened checksum is refused (full 64 digits required)" $RA5b check
python3 -I "$TOOL_TREE/tools/league_recovery_set.py" create "$RIG/existing-other" --league "$LIVE_LEAGUE" --original "$LIVE_ORIGINAL" --code-tree "$ENGINE_TREE" >/dev/null 2>&1
mkdir -p "$RIG/found3"; tar -cf "$RIG/found3/other.tar" -C "$RIG" existing-other
RA6=$(new); SEARCH_DIRS=$RIG/found3 EXISTING_ARTIFACT_BYTES=$(stat -c %s "$RIG/found3/other.tar") EXISTING_ARTIFACT_SHA256=$(sha256sum "$RIG/found3/other.tar" | cut -d' ' -f1) \
  expect_fail "an artifact with the right hash but a different recovery set than EXISTING_SET is rejected" $RA6 check
grep -q "manifest differs" "$WORK/run-$RA6/FAILED" && ok "...and the failure says the manifests differ" || no "manifest-differs message"
mkdir -p "$RIG/found4"; cp "$RIG/found/a/b/c/dfl-recovery-set-20261103.tar" "$RIG/found4/dfl.tar"
RA7=$(new); SEARCH_DIRS=$RIG/found4 adv $RA7 check canary create
python3 - "$RIG/found4/dfl.tar" <<'PY'
import sys; f=open(sys.argv[1],"r+b"); f.seek(2000); b=f.read(1); f.seek(2000); f.write(bytes([b[0]^1])); f.close()
PY
SEARCH_DIRS=$RIG/found4 expect_fail "the artifact is re-hashed at verify: a copy altered after check is caught" $RA7 verify
grep -q "now hashes" "$WORK/run-$RA7/FAILED" && ok "...and the failure names the new hash" || no "re-hash message"

echo "### L  the lock: a competing process REFUSES and must not touch the active run"
RL=$(new); adv $RL check canary create verify package
cat > "$RIG/s3-slow.sh" <<EOS
#!/bin/sh
# slows ONLY the upload of set.tar, to keep a stage running while competitors arrive
slow=""; isput=""; for a in "\$@"; do case "\$a" in */set.tar) slow=1;; put) isput=1;; esac; done
[ -n "\$slow" ] && [ -n "\$isput" ] && sleep 9
exec $S3CMD "\$@"
EOS
chmod +x "$RIG/s3-slow.sh"
S3CMD=$RIG/s3-slow.sh $X upload "$RL" > "$RIG/out-first.txt" 2>&1 &
P1=$!
for i in $(seq 1 40); do grep -q "== stage upload" "$WORK/run-$RL/log.txt" 2>/dev/null && break; sleep 0.25; done; sleep 2
st=$($X status "$RL" 2>&1); grep -q 'in progress' <<<"$st" && ok "status (read-only) works while a stage runs and says in progress" || no "status while running"
B=$(snap "$WORK/run-$RL"); L1=$(md5sum < "$WORK/run-$RL/log.txt")
expect_refused "a second process asking for the SAME stage is refused" "$RL" upload
expect_refused "a second process asking for the NEXT stage is refused" "$RL" drill
expect_refused "a second process asking for an EARLIER stage is refused" "$RL" check
A=$(snap "$WORK/run-$RL"); L2=$(md5sum < "$WORK/run-$RL/log.txt")
[ "$B" = "$A" ] && ok "the active run's files are byte-for-byte and timestamp-for-timestamp untouched by the competitors" || no "the competitors modified the active run"
[ "$L1" = "$L2" ] && ok "the active run's log was not touched" || no "log touched"
[ ! -e "$WORK/run-$RL/FAILED" ] && ok "no FAILED was written into the active run" || no "FAILED written into the active run"
wait $P1; rc=$?; [ $rc = 0 ] && ok "the original process finished its stage normally (exit 0)" || no "original process exit $rc"
[ -e "$WORK/run-$RL/stages/upload.ok" ] && [ ! -e "$WORK/run-$RL/FAILED" ] && ok "upload stage OK and the run is not failed" || no "upload.ok"
echo "### M  markers: upload-complete is not a drill proof; a verified drill (INDEPENDENT=1, replay off) writes the last marker"
PL=$(cat "$WORK/run-$RL/upload.prefix")
has_obj "$PL/UPLOAD-COMPLETE.json" && ! has_obj "$PL/DRILL-VERIFIED.json" && ok "after upload: UPLOAD-COMPLETE.json yes, DRILL-VERIFIED.json no" || no "markers after upload"
st=$($X status "$RL" 2>&1); grep -q 'UPLOAD-COMPLETE only' <<<"$st" && ok "status: UPLOAD-COMPLETE only" || no "status upload-only"
$X drill "$RL" > "$RIG/out-drill.txt" 2>&1; rc=$?; [ $rc = 0 ] && ok "drill passes with INDEPENDENT=1 and REPLAY_FROM unset" || { no "drill passed (exit $rc)"; tail -15 "$RIG/out-drill.txt"; }
has_obj "$PL/DRILL-VERIFIED.json" && ! has_obj "$PL/FAILED.json" && ! has_obj "$PL/COMPLETE.json" && ok "after the drill: DRILL-VERIFIED.json yes, FAILED.json no, no ambiguous COMPLETE.json" || no "markers after drill"
grep -q "original server paths are not available" "$WORK/run-$RL/drill/restore-test.txt" && ok "the restore test ran with the original server paths hidden (INDEPENDENT)" || no "paths hidden"
grep -q "PASS  recovery from restore point" "$WORK/run-$RL/drill/restore-test.txt" && no "replay was NOT run" || ok "restore-point replay was not run (off by default)"
s3 get "s3://$BUCKET/$PL/DRILL-VERIFIED.json" "$RIG/ft-dv.json" --force >/dev/null 2>&1
grep -q '"independent_of_server_paths": "yes' "$RIG/ft-dv.json" && grep -q 'SAME host' "$RIG/ft-dv.json" && grep -q '"restore_point_replay": "not run"' "$RIG/ft-dv.json" && grep -q 'disabled' "$RIG/ft-dv.json" \
  && ok "DRILL-VERIFIED.json records: same host, independent=yes, replay not run, CDN disabled" || no "DRILL-VERIFIED.json content"
s3 get "s3://$BUCKET/$PL/UPLOAD-COMPLETE.json" "$RIG/ft-uc.json" --force >/dev/null 2>&1; grep -q 'NOT proof' "$RIG/ft-uc.json" && ok "UPLOAD-COMPLETE.json says it is not proof of recovery" || no "UPLOAD-COMPLETE.json content"
st=$($X status "$RL" 2>&1); grep -q 'STATE: DRILL-VERIFIED' <<<"$st" && ok "status: DRILL-VERIFIED" || no "status verified"
B=$(snap "$WORK/run-$RL"); expect_refused "repeating the drill on a verified run is refused" "$RL" drill; expect_refused "repeating check on a verified run is refused" "$RL" check
[ "$B" = "$(snap "$WORK/run-$RL")" ] && [ ! -e "$WORK/run-$RL/FAILED" ] && ok "a verified run is unchanged by repeated commands (no FAILED appears)" || no "verified run modified"
echo "### M3 public upload is caught BEFORE any upload-complete marker"
R9=$(new); adv $R9 check canary create verify package; S3CMD=$RIG/s3-public.sh expect_fail "upload fails when set.tar ends up public" $R9 upload
! has_obj "sets/$R9/UPLOAD-COMPLETE.json" && ok "no UPLOAD-COMPLETE.json for a public upload" || no "UPLOAD-COMPLETE.json written despite public object"

echo "### C  CDN is disabled for this bucket: recorded, not probed; probed only when enabled"
RC1=$(new); adv $RC1 check
$X canary $RC1 > "$OUT" 2>&1; rc=$?   # CDN_ENABLED=0 and CDN_BASE points at a dead port (as a missing CDN hostname would)
[ $rc = 0 ] && ok "CDN disabled + a CDN endpoint that cannot connect: the canary stage still passes" || no "canary with disabled CDN (exit $rc)"
grep -q "^disabled" "$WORK/run-$RC1/cdn.txt" && grep -q "authenticated retrieval OK" "$OUT" && grep -q "anonymous denied (403)" "$OUT" && ok "authenticated retrieval + anonymous denial on the origin, and CDN recorded as disabled" || no "cdn.txt / origin proofs"
! grep -q "http://127.0.0.1:9/" "$OUT" && ok "the disabled CDN endpoint was not probed" || no "CDN endpoint was probed"
RC2=$(new); adv $RC2 check; CDN_ENABLED=1 expect_fail "CDN enabled + unreachable CDN endpoint: the stage fails (a probe that cannot connect proves nothing)" $RC2 canary
RC3=$(new); adv $RC3 check; CDN_ENABLED=1 CDN_BASE=$ORIGIN_BASES $X canary $RC3 > "$OUT" 2>&1; rc=$?
[ $rc = 0 ] && grep -q "^enabled" "$WORK/run-$RC3/cdn.txt" && ok "CDN enabled + a working endpoint that denies anonymous reads: passes and is recorded as enabled" || no "CDN enabled working (exit $rc)"
RC4=$(new); adv $RC4 check; ORIGIN_BASES="http://127.0.0.1:9/$BUCKET/" expect_fail "an unreachable ORIGIN endpoint still fails even with the CDN disabled" $RC4 canary

echo; echo "RESULT: $pass passed, $bad failed"; [ $bad = 0 ]
