#!/bin/sh
# Install a build of the static site as a new release and switch to it atomically. Usage: install_site.sh SITE_DIR
# Layout:  $BASE/site/releases/<id>/ ... and $BASE/site/current -> releases/<id>  (the symlink is replaced in one rename; no moment without a site).
# It never touches the feed ($BASE/feed) or the league. Keeps the last $KEEP releases so rollback.sh can switch back.
set -eu
SRC="${1:?usage: install_site.sh SITE_DIR}"
BASE="${BASE:-/srv/dflv2}"
KEEP="${KEEP:-5}"
[ -f "$SRC/index.html" ] && [ -d "$SRC/assets" ] || { echo "not a site directory: $SRC" >&2; exit 2; }
id="$(date -u +%Y%m%dT%H%M%SZ)"
rel="$BASE/site/releases/$id"
mkdir -p "$BASE/site/releases"
cp -a "$SRC" "$rel.partial"
mv "$rel.partial" "$rel"
ln -sfn "releases/$id" "$BASE/site/.current.new"
mv -T "$BASE/site/.current.new" "$BASE/site/current"
ls -1 "$BASE/site/releases" | sort | head -n -"$KEEP" | while read -r old; do rm -rf "$BASE/site/releases/$old"; done
echo "$id"
