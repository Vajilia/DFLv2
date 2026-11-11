#!/usr/bin/env bash
# run_rehearsal.sh RIG OUTDIR : full local rehearsal. RIG comes from build_rig.sh. Starts a local fake S3 (moto) with the rig's random synthetic keys,
# runs the guarded procedure end to end (INDEPENDENT=1, replay off, the exporter rewriting status.json all the while), then the failure/regression tests.
# Writes OUTDIR/rehearsal-independent-run.txt and OUTDIR/rehearsal-failure-tests.txt.  Nothing leaves this machine.
set -uo pipefail
RIG=${1:?usage: run_rehearsal.sh RIG OUTDIR}; OUT=${2:?usage: run_rehearsal.sh RIG OUTDIR}
RIG=$(cd "$RIG" && pwd); mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd); SCRIPT=$(cd "$HERE/.." && pwd)/dfl-recovery-run.sh
. "$RIG/env.sh"
bash -n "$SCRIPT" || exit 1
sha256sum "$SCRIPT" | tee "$OUT/script.sha256"
"$MOTO_BIN" -H 127.0.0.1 -p "$MOTO_PORT" > "$RIG/moto.log" 2>&1 & MOTO=$!
trap 'touch "$RIG/exporter.stop"; kill $MOTO 2>/dev/null' EXIT
for i in $(seq 1 40); do curl -s -o /dev/null "http://127.0.0.1:$MOTO_PORT/" && break; sleep 0.25; done
"$S3CMD" -c "$S3CFG" --bucket-location=us-east-1 mb "s3://$BUCKET" >/dev/null 2>&1 || { echo "cannot create the fake bucket"; exit 1; }

rm -f "$RIG/exporter.stop" "$RIG/exporter.ticks"; "$HERE/exporter-loop.sh" "$RIG" & sleep 2
(
  echo "# happy path: INDEPENDENT=$INDEPENDENT (hidden: $HIDE_DIRS)  REPLAY_FROM='${REPLAY_FROM}'  CDN_ENABLED=$CDN_ENABLED  exporter looping the whole time"
  echo "# synthetic data and credentials, local fake S3; nothing here is production"
  "$SCRIPT" all; rc=$?
  echo "# exit status: $rc;  exporter runs while this ran: $(wc -l < "$RIG/exporter.ticks")"
  RUN=$(ls -td "$WORK"/run-2* | head -1)
  echo "# the restore test (final lines):"; sed -e 's/^/#   /' "$RUN/drill/restore-test.txt" | cut -c1-200 | head -24
  echo "# bucket contents of this run:"; "$S3CMD" -c "$S3CFG" ls "s3://$BUCKET/sets/$(basename "$RUN" | sed 's/^run-//')/" | sed 's/^/#   /'
  echo "# restored feed vs captured backup (diff of files):"; [ ! -s "$RUN/drill/diff-restored-vs-captured.txt" ] && echo "#   identical"
  echo "# restored feed vs the MOVING live feed (not what the script compares):"; diff -rq "$LIVE_FEED" "$RUN/drill/restored-feed" | sed 's/^/#   /' | head -5
  exit $rc
) > "$OUT/rehearsal-independent-run.txt" 2>&1; HP=$?
touch "$RIG/exporter.stop"; sleep 1
"$HERE/failtests.sh" "$RIG" > "$OUT/rehearsal-failure-tests.txt" 2>&1; FT=$?
tail -3 "$OUT/rehearsal-independent-run.txt" | head -1; tail -1 "$OUT/rehearsal-failure-tests.txt"
[ $HP = 0 ] && [ $FT = 0 ]
