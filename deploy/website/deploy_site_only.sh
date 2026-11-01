#!/bin/sh
# Deploy ONLY the website files (site/) of one pinned commit, and nothing else. Run as root on the Droplet.
#   sh deploy_site_only.sh <40-character commit id>        [REPO_URL=...] [SRC=/opt/dflv2-src] [BASE=/srv/dflv2]
# What it does: fetches exactly that commit (a commit id names its content), refuses anything but a clean checkout of it, copies the commit's site/ folder
# in as a NEW site release (install_site.sh: a timestamped folder, then one atomic rename of the `current` link), and proves that the live site files are
# byte-identical to that commit's site/ folder.
# What it never does: it does not touch the league, the league runner or its timer, the feed or the exporter, the engine code ($BASE/repo), Caddy or any
# configuration, and it restarts nothing. The previous site release stays, so the change is undone in one step:
#     sh $BASE/repo/deploy/website/rollback.sh --site
# The pages read only published files, so a new site release cannot change a result; the league's saved state is not even opened.
set -eu
COMMIT="${1:?usage: deploy_site_only.sh <40-character commit id>}"
REPO_URL="${REPO_URL:-https://github.com/Vajilia/DFLv2.git}"
SRC="${SRC:-/opt/dflv2-src}"
BASE="${BASE:-/srv/dflv2}"
die() { echo "STOPPED: $*" >&2; exit 2; }
echo "$COMMIT" | grep -Eq '^[0-9a-f]{40}$' || die "give the full 40-character commit id, not a branch or abbreviation"
mkdir -p "$SRC"
w="$SRC/$COMMIT"
if [ ! -d "$w/.git" ]; then
  rm -rf "$w"; mkdir -p "$w"
  git -C "$w" init -q
  git -C "$w" fetch -q --depth 1 "$REPO_URL" "$COMMIT"
  git -C "$w" checkout -q --detach FETCH_HEAD
fi
[ "$(git -C "$w" rev-parse HEAD)" = "$COMMIT" ] || die "the checkout in $w is not $COMMIT"
[ -z "$(git -C "$w" status --porcelain)" ] || die "the checkout in $w has changes; remove it and run again"
[ -f "$w/site/index.html" ] && [ -d "$w/site/assets" ] || die "commit $COMMIT has no site/ folder"
[ -d "$BASE/site" ] || die "no site installed under $BASE yet: use deploy_pinned.sh for the first deployment"
before="$(readlink "$BASE/site/current" || true)"
echo "commit $COMMIT fetched and clean; site now: ${before:-none}"
id="$(BASE="$BASE" sh "$w/deploy/website/install_site.sh" "$w/site")"
now="$(readlink "$BASE/site/current")"
[ "$now" = "releases/$id" ] || die "the site link points at $now, expected releases/$id"
diff -r "$w/site" "$BASE/site/current/" >/dev/null || die "the installed site differs from commit $COMMIT's site/ folder; roll back with: sh $BASE/repo/deploy/website/rollback.sh --site"
echo "site installed and verified byte-identical to $COMMIT: $now"
echo "previous release (for rollback): ${before:-none}"
echo "undo with: sh $BASE/repo/deploy/website/rollback.sh --site"
