# Staging deployment record: preview.diamondfootballleague.com (2026-10-31, about 00:00 to 00:30 Pacific)

Deployed by an assistant session driving the owner's signed-in DigitalOcean web console and IONOS DNS page in Chrome, with the owner's authorisation.
Package: `dfl-staging-package-20261031T051815Z-5a6bf27430.tar.gz`, SHA-256 `6cb8b0eb...9274605d` (matched the value given), built from commit 5a6bf27 (clean, spec Edition 8.58).
Nothing was merged. Production (`diamondfootballleague.com`, `www`, the old runner and its three units) was not edited.

## What was found (read-only inspection)
Ubuntu 24.04, Caddy 2.11.7 serving one site block (apex, www and `143-198-130-68.sslip.io`) from `/var/www/dfl` with `/api/*` behind basic auth; no `import` line, no `conf.d`;
units `dfl-live`, `dfl-control`, `dfl-chronicle` active; ufw open on 22/80/443; 6.5 % of 47 GB used; no `dflv2` user; Python 3.12.3 without numpy (not needed for staging).
DNS is hosted at IONOS (nameservers `ns10xx.ui-dns.*`), not DigitalOcean: apex and `www` A records to 143.198.130.68, mail records, no AAAA, no CAA, no DNSSEC, nothing for `preview`.

## What was changed
* DNS (IONOS): one record added, `preview` A 143.198.130.68, TTL 5 minutes. Nothing else touched.
* Server: user `dflv2`; `/srv/dflv2/{repo-releases,repo,site,feed,league}`; `/etc/caddy/conf.d/dflv2-preview.caddy`; one line `import /etc/caddy/conf.d/*.caddy` appended to `/etc/caddy/Caddyfile`
  (backup `/etc/caddy/Caddyfile.dfl-staging.20261031T070426Z.bak`). Caddy was validated, then reloaded (not restarted). Let's Encrypt issued the certificate on the first reload.
* The package was unpacked at `/opt/dflv2-staging-pkg` (outside `/root`), the download at `/root/dl/`.
* Rollback copy: `/root/dfl-rollback/20261031T065334Z` (Caddy files, dfl-* units, web root archive, service list, SHA256SUMS). Production baseline: `/root/prod-baseline.json`.

## What did not work first time (found at deployment; fixed in the repository after it)
1. `snapshot_config.sh` stopped after archiving the web root because tar exits 1 when the live runner changes a file while it is read. The last two steps were done by hand. Fixed: exit 1 is tolerated.
2. `install_staging.sh` ran the seed-feed step as `dflv2` reading from the package directory; that fails when the package sits under `/root`. The deployment avoided it by unpacking outside `/root`
   (`/root` was not loosened). Fixed: the seed is copied to a readable folder under `/srv/dflv2`, installed from there and removed. Tested with an unprivileged user in a scratch directory.
3. The generated Caddy block served 404 for every `/feed/` file (two `root` lines, the second with a matcher, did not override the first on Caddy 2.11). Fixed on the server by editing
   `/etc/caddy/conf.d/dflv2-preview.caddy` (backup `.before-fix`), validating, and reloading; fixed in `deploy/website/Caddyfile.template` (two `file_server` directives, one root each).
4. `verify_deployment.py` crashed with a traceback when the pointer was not JSON. Fixed: it reports a FAIL and carries on.
The package that was deployed therefore differs from the repository's current head in those four files (and the live Caddy block matches the fixed template).

## Verification
`verify_deployment.py https://preview.diamondfootballleague.com --production https://diamondfootballleague.com --baseline /root/prod-baseline.json`: 28 not failed, 0 failed, 0 warnings: pages and assets, feed pointer,
manifest and all 58 feed files by SHA-256, public-data check, simulation date and last published update, development label, POST/PUT/DELETE refused (405), the official map served (PNG, 1 538 149 bytes),
noindex, strict Content-Security-Policy, caching (pointer never cached, releases immutable), lock file not served, path traversal refused, certificate valid (Let's Encrypt, 89 days),
http redirects to https, and production unchanged since the baseline. Production services still active and the runner still writing after the change.
Browser check (Chrome): all ten pages render without errors; team page; History shows the Season 1 Diamond Coronation score "34-26 (recovered from the game archive)"; the map loads at 4800 x 4400 and links to the full-size file.
Not checked on the real URL: narrow-screen layout (the extension could not resize the window); it was checked at 390 px on the same files in the development environment.

## Limits
The preview shows one fixed development feed (simulation date 2001-09-08). No league runner, exporter or timer is installed for staging; nothing updates automatically. That is the next milestone.

## Rollback
Staging only: `sh /srv/dflv2/repo/deploy/website/uninstall_staging.sh` (removes the block, validates, reloads), then delete the `preview` A record at IONOS.
All of Caddy: `sh /srv/dflv2/repo/deploy/website/rollback.sh --config /root/dfl-rollback/20261031T065334Z`.
