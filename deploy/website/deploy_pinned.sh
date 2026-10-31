#!/bin/sh
# Deploy one pinned commit of the repository to this machine's staging site, using nothing but git and that commit id. Run as root.
#   sh deploy_pinned.sh <40-character commit id>         [REPO_URL=https://github.com/Vajilia/DFLv2.git]  [SRC=/opt/dflv2-src]  [PKGS=/opt/dflv2-pkgs]
# It fetches exactly that commit (a commit id names its content, so nothing else can arrive), refuses anything but a clean checkout of it, builds the
# deployment tree from it, runs that commit's own install_staging.sh (which needs the rollback copy and the DNS record, as always), and then proves the
# installed files are that commit with verify_revision.py. No package file and no side channel is involved. To bootstrap the very first time, fetch this
# one script from the same commit:  curl -fsSL https://raw.githubusercontent.com/Vajilia/DFLv2/<commit>/deploy/website/deploy_pinned.sh -o deploy_pinned.sh
set -eu
COMMIT="${1:?usage: deploy_pinned.sh <40-character commit id>}"
REPO_URL="${REPO_URL:-https://github.com/Vajilia/DFLv2.git}"
SRC="${SRC:-/opt/dflv2-src}"
PKGS="${PKGS:-/opt/dflv2-pkgs}"
BASE="${BASE:-/srv/dflv2}"; HOST="${HOST:-preview.diamondfootballleague.com}"; CADDY_DIR="${CADDY_DIR:-/etc/caddy}"; CONF_D="${CONF_D:-$CADDY_DIR/conf.d}"
export BASE HOST CADDY_DIR CONF_D
die() { echo "STOPPED: $*" >&2; exit 2; }
echo "$COMMIT" | grep -Eq '^[0-9a-f]{40}$' || die "give the full 40-character commit id, not a branch or abbreviation"
mkdir -p "$SRC" "$PKGS"
w="$SRC/$COMMIT"
if [ ! -d "$w/.git" ]; then
  rm -rf "$w"; mkdir -p "$w"
  git -C "$w" init -q
  git -C "$w" fetch -q --depth 1 "$REPO_URL" "$COMMIT"
  git -C "$w" checkout -q --detach FETCH_HEAD
fi
[ "$(git -C "$w" rev-parse HEAD)" = "$COMMIT" ] || die "the checkout in $w is not $COMMIT"
[ -z "$(git -C "$w" status --porcelain)" ] || die "the checkout in $w has changes; remove it and run again"
echo "commit $COMMIT fetched and clean"
tree="$(python3 -I "$w/tools/build_staging_package.py" --no-tar --out "$PKGS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tree"])')"
[ -d "$tree/repo" ] || die "the deployment tree was not built"
sh "$w/deploy/website/install_staging.sh" "$tree"
python3 -I "$w/tools/verify_revision.py" --commit "$COMMIT" --repo-url "$REPO_URL" --base "$BASE" --host "$HOST" --snippet "$CONF_D/dflv2-preview.caddy"
