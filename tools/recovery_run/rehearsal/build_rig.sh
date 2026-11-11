#!/usr/bin/env bash
# build_rig.sh RIG_DIR : build a SYNTHETIC copy of the production layout for rehearsing dfl-recovery-run.sh.
#
# Everything here is scratch data made from the repository's own tools: a small league (26 events under the old release, upgraded, then 12 more), a feed,
# the existing-recovery-set folder and its tar (the stand-in for dfl-recovery-set-20261103.tar, hidden in a deep folder so that it has to be LOCATED), and a
# credentials file with RANDOM synthetic keys for a local fake S3. No production path, key or byte is read or written.
#
# Needs: git, python3, and a repo that contains the four pinned commits (REPO, default: the repo this script lives in).
set -Eeuo pipefail
R=${1:?usage: build_rig.sh RIG_DIR}
REPO=${REPO:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}
VENV=${VENV:-/tmp/s3v}                       # holds s3cmd and moto_server (pip install 's3cmd' 'moto[server]'), local rehearsal only
PORT=${PORT:-5055}
BUCKET=${BUCKET:-dfl-rehearsal}
OLD=59ea680
ENGINE=7e59cbbaea23b0de353545c64a6f5c39cffe774d
TOOL=0f72562bb8231b6c1af3671560953f38b4024959
PB=284125a40826e3a40a18badf46378a90c8ef26eb
[ ! -e "$R" ] || { echo "$R already exists; choose a new folder" >&2; exit 1; }
mkdir -p "$R/trees"; R=$(cd "$R" && pwd)

for spec in "old:$OLD" "engine:$ENGINE" "tool:$TOOL" "pb:$PB"; do
  n=${spec%%:*}; c=${spec#*:}; mkdir -p "$R/trees/$n"; git -C "$REPO" archive "$c" | tar -x -C "$R/trees/$n"; echo "tree $n <- $c"
done
S=$R/srv/dflv2; mkdir -p "$S/league-build" "$S/league" "$S/league-v2-upgrade-input" "$S/league-141-original" "$S/feed"
python3 -I "$R/trees/old/tools/league_store.py" init "$S/league-build/league.db" --seed 11 --play-level >/dev/null
python3 -I "$R/trees/old/tools/league_store.py" run "$S/league-build/league.db" --events 26 >/dev/null
cp "$S/league-build/league.db" "$S/league-v2-upgrade-input/league.db"
python3 -I "$R/trees/old/tools/league_backup.py" create "$S/league-build/league.db" "$S/league-141-original-tmp" --note "preserved original" >/dev/null
cp "$S/league-141-original-tmp/league.db" "$S/league-141-original/league.db"; cp "$S/league-141-original-tmp/archive.db" "$S/league-141-original/archive.db"
cp "$S/league-build/league.db.archive" "$S/league/league.db.archive"
python3 -I "$R/trees/engine/tools/league_store.py" upgrade "$S/league-v2-upgrade-input/league.db" "$S/league/league.db" --run-originating-code --archive "$S/league/league.db.archive" >/dev/null
python3 -I "$R/trees/engine/tools/league_store.py" run "$S/league/league.db" --events 12 >/dev/null
python3 -I "$R/trees/engine/tools/league_store.py" verify "$S/league/league.db" --deep >/dev/null
rm -rf "$S/league-141-original-tmp"
echo '{"limit_reached": true, "state":"stopped"}' > "$S/league/runner_status.json"
python3 -I "$R/trees/engine/tools/export_public_feed.py" "$S/league/league.db" --out "$S/feed" --use-league-code --runner-status "$S/league/runner_status.json" >/dev/null
python3 -I "$R/trees/tool/tools/export_playback.py" --archive "$S/league/league.db.archive" --feed "$S/feed" --out "$S/feed/playback" --limit 120 >/dev/null
python3 -I - "$S/feed" <<'PY'      # a feed of realistic weight: 1,600 synthetic playback games listed in the index, 2,500 review files
import hashlib, json, os, random, sys
F = sys.argv[1]; random.seed(5)
pg = os.path.join(F, "playback", "games"); os.makedirs(pg, exist_ok=True); games = []
for i in range(1600):
    body = json.dumps({"gid": "g%05d" % i, "frames": [random.random() for _ in range(900)]}).encode()
    sha = hashlib.sha256(body).hexdigest(); name = "g%05d.%s.json" % (i, sha[:10])
    open(os.path.join(pg, name), "wb").write(body)
    games.append({"gid": "g%05d" % i, "file": "games/" + name, "sha256": sha, "bytes": len(body)})
ip = os.path.join(F, "playback", "index.json"); idx = json.load(open(ip)); idx["games"] = games
json.dump(idx, open(ip, "w"), separators=(",", ":"), sort_keys=True)
rv = os.path.join(F, "review", "combined"); os.makedirs(rv, exist_ok=True)
for i in range(2500):
    open(os.path.join(rv, "f%05d.js" % i), "wb").write(os.urandom(random.randint(2000, 9000)))
PY
mkdir -p "$R/opt/dflv2-src" "$R/opt/dfl-recovery-tools"
cp -a "$R/trees/engine" "$R/opt/dflv2-src/$ENGINE"; cp -a "$R/trees/tool" "$R/opt/dfl-recovery-tools/$TOOL"; cp -a "$R/trees/pb" "$R/opt/dfl-recovery-tools/$PB"

# the existing recovery set (folder) and its artifact (tar), hidden deep in a folder so the script has to FIND it by size and full SHA-256
python3 -I "$R/opt/dfl-recovery-tools/$TOOL/tools/league_recovery_set.py" create "$R/existing-set" --league "$S/league/league.db" --original "$S/league-v2-upgrade-input/league.db" \
   --equivalent "$S/league-141-original/league.db" --code-tree "$R/opt/dflv2-src/$ENGINE" >/dev/null
mkdir -p "$R/found/a/b/c"
tar -cf "$R/found/a/b/c/dfl-recovery-set-20261103.tar" -C "$R" existing-set

# synthetic credentials for the local fake S3 (random every time; they exist nowhere else)
KEY="SYNTH$(head -c 12 /dev/urandom | base64 | tr -dc 'A-Z0-9' | head -c 15)"; SEC="synth$(head -c 30 /dev/urandom | base64 | tr -dc 'a-z0-9' | head -c 32)"
cat > "$R/s3cfg" <<EOF
[default]
access_key = $KEY
secret_key = $SEC
host_base = 127.0.0.1:$PORT
host_bucket = 127.0.0.1:$PORT
use_https = False
signature_v2 = False
EOF
chmod 600 "$R/s3cfg"
ART=$R/found/a/b/c/dfl-recovery-set-20261103.tar
cat > "$R/env.sh" <<EOF
R=$R
export LIVE_LEAGUE=\$R/srv/dflv2/league/league.db LIVE_ORIGINAL=\$R/srv/dflv2/league-v2-upgrade-input/league.db LIVE_EQUIV=\$R/srv/dflv2/league-141-original/league.db
export LIVE_FEED=\$R/srv/dflv2/feed
export ENGINE_TREE=\$R/opt/dflv2-src/$ENGINE
export TOOL_TREE=\$R/opt/dfl-recovery-tools/$TOOL
export PB_TREE=\$R/opt/dfl-recovery-tools/$PB
export WORK=\$R/work S3CMD=$VENV/bin/s3cmd S3CFG=\$R/s3cfg BUCKET=$BUCKET
export ORIGIN_BASES="http://127.0.0.1:$PORT/$BUCKET/" CDN_ENABLED=0 CDN_BASE="http://127.0.0.1:9/$BUCKET/" ANON_LIST_URL=
export EXPECT_EVENTS=38 MIN_FREE_AFTER_BYTES=1000000000 SYSTEMCTL_CHECK=0
export EXISTING_SET=\$R/existing-set CADDY_DIR=\$R/nocaddy RUN_GUARD="nice -n 19"
export EXISTING_ARTIFACT= EXISTING_ARTIFACT_NAME=dfl-recovery-set-20261103.tar SEARCH_DIRS=\$R/found
export EXISTING_ARTIFACT_BYTES=$(stat -c %s "$ART") EXISTING_ARTIFACT_SHA256=$(sha256sum "$ART" | cut -d' ' -f1)
export INDEPENDENT=1 HIDE_DIRS="\$R/srv/dflv2 \$R/opt/dflv2-src" REPLAY_FROM=
export S3_PUT_OPTS="--multipart-chunk-size-mb=300"          # the fake S3 cannot read the ACL of a multipart upload; the real service is not rehearsed here
export MOTO_PORT=$PORT MOTO_BIN=$VENV/bin/moto_server
EOF
echo "rig ready: $R  (feed files: $(find "$S/feed" -type f | wc -l), artifact $(stat -c %s "$ART") bytes)"
