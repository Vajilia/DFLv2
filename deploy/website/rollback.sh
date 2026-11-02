#!/bin/sh
# Roll back. Two kinds:
#   rollback.sh --site              switch $BASE/site/current back to the previous site release (instant; the feed is untouched)
#   rollback.sh --feed RELEASE      point $BASE/feed/latest.json at an older, still-present feed release (RELEASE as listed in feed/releases/)
#   rollback.sh --config SNAPDIR    restore the Caddyfile and dfl-* units saved by snapshot_config.sh, validate, and reload Caddy
set -eu
BASE="${BASE:-/srv/dflv2}"
case "${1:-}" in
  --site)
    cur="$(readlink "$BASE/site/current")"; cur="${cur#releases/}"
    prev="$(ls -1 "$BASE/site/releases" | sort | grep -v "^$cur\$" | grep -v partial | tail -n 1)"
    [ -n "$prev" ] || { echo "no earlier site release" >&2; exit 2; }
    ln -sfn "releases/$prev" "$BASE/site/.current.new"; mv -T "$BASE/site/.current.new" "$BASE/site/current"; echo "site -> $prev"
    # the site's own revision record follows the site (DEPLOYED_REVISION.json, the engine/tools revision, is not touched)
    if [ -f "$BASE/site-revisions/$prev.json" ]; then cp "$BASE/site-revisions/$prev.json" "$BASE/SITE_REVISION.json.new"
    else printf '{\n "site_commit": null,\n "site_release": "%s",\n "note": "no revision record exists for this release (it was installed before records were kept)"\n}\n' "$prev" > "$BASE/SITE_REVISION.json.new"; fi
    chmod 644 "$BASE/SITE_REVISION.json.new"; mv "$BASE/SITE_REVISION.json.new" "$BASE/SITE_REVISION.json"; echo "site revision record -> $prev" ;;
  --feed)
    rel="${2:?release name}"; m="$BASE/feed/releases/$rel/manifest.json"
    [ -f "$m" ] || { echo "no such feed release: $rel" >&2; exit 2; }
    sha="$(sha256sum "$m" | cut -d' ' -f1)"
    tmp="$BASE/feed/.latest.rollback.$$"
    python3 - "$BASE/feed/latest.json" "$rel" "$sha" "$tmp" <<'PY'
import json, sys
path, rel, sha, tmp = sys.argv[1:5]
p = json.load(open(path)); m = json.load(open(path.replace("latest.json", "releases/%s/manifest.json" % rel)))
p.update(release=rel, manifest_sha256=sha, content_hash=m["content_hash"], generated_at=m["generated_at"])
open(tmp, "w").write(json.dumps(p, sort_keys=True, separators=(",", ":")))
PY
    mv "$tmp" "$BASE/feed/latest.json"; echo "feed -> $rel" ;;
  --config)
    snap="${2:?snapshot directory}"
    (cd "$snap" && sha256sum -c SHA256SUMS >/dev/null) || { echo "snapshot does not verify" >&2; exit 3; }
    cp -a "$snap/caddy/." /etc/caddy/
    [ -d "$snap/systemd" ] && cp -a "$snap/systemd/." /etc/systemd/system/ && systemctl daemon-reload
    caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy && echo "configuration restored from $snap" ;;
  *) echo "usage: rollback.sh --site | --feed RELEASE | --config SNAPDIR" >&2; exit 2 ;;
esac
