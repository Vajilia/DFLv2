# Staging runbook: preview.diamondfootballleague.com (prepared 2026-10-30)

**Status: nothing in this runbook has been run on the Droplet.** The session that wrote it has no shell access to the server and could not
look up the domain's DNS (its network only reaches package registries and GitHub). So the server, its DNS and its Caddy configuration have **not
been inspected**; the first steps below are read-only inspection, and the install script refuses to change anything until a rollback copy exists
and the DNS record points at the machine. Every statement about what the scripts do is tested against stand-ins on a scratch directory
(`tests/test_site_deploy.py`); what no test could prove is marked **FIRST-RUN RISK**.

What this installs: the static viewer plus one shipped feed (a development league, simulation date and last published update visible in the
page header) at `https://preview.diamondfootballleague.com`, served by the Caddy that is already on the Droplet. It adds **one** new site block in
its own file and **one** `import` line to the Caddyfile. It does **not** replace, edit or restart the production site, does not start a league or a
timer, and does not publish anything you have not seen. Production stays on `diamondfootballleague.com` exactly as it is.

You (or whoever has the Droplet's login) run these commands, as root, in the Droplet's console or over SSH. Copy each block as it is.
`<PACKAGE>` is the file name of the package you were sent, and `<DROPLET_IP>` is the Droplet's public IPv4 address.

---------------------------------------------------------------------------------------------------------------------------------------------

## Step 1. Put the package on the Droplet (changes nothing that is live)

From the computer that has the package file:

```
scp <PACKAGE>.tar.gz <PACKAGE>.tar.gz.sha256 root@<DROPLET_IP>:/root/
```

(Any file-transfer program that talks SFTP works the same way: upload both files to `/root/`.) Then, on the Droplet:

```
cd /root
sha256sum -c <PACKAGE>.tar.gz.sha256          # must print:  <PACKAGE>.tar.gz: OK
mkdir -p /root/dfl-staging && tar -xzf <PACKAGE>.tar.gz -C /root/dfl-staging --strip-components=1
cat /root/dfl-staging/PACKAGE.json            # commit, spec edition, seed release: compare with the message you were sent
(cd /root/dfl-staging && sha256sum -c SHA256SUMS --quiet) && echo "package files verified"
```

## Step 2. Inspect first (read-only; changes nothing). STOP HERE and send both outputs back

```
sh /root/dfl-staging/repo/deploy/website/inspect_server.sh 2>&1 | tee /root/inspect_server.txt
sh /root/dfl-staging/repo/deploy/website/inspect_dns.sh 2>&1 | tee /root/inspect_dns.txt
```

`inspect_server.sh` masks password hashes. It reports the Caddyfile and its imports, running services, listening ports, disk and memory, the
directories under `/srv`, `/opt/dfl` and `/var/www/dfl`, the `dflv2` user, and whether Python and numpy are present. `inspect_dns.sh` reports the
A, AAAA, CNAME and CAA records of the production name, `www` and `preview`, the Droplet's own public address, and who is listening on ports 80
and 443. Send both files back (or paste them); the plan is checked against what is really there before the next step. The decisions it settles:

| What the inspection shows | What it means |
|---|---|
| `preview` has no A record | add one (step 3) |
| `preview` A record equals the Droplet's IPv4 | nothing to add |
| `preview` has an AAAA record that is not this Droplet's IPv6 | remove it or correct it; Caddy's certificate check can fail through it |
| a CAA record exists and does not list `letsencrypt.org` | add `0 issue "letsencrypt.org"` or HTTPS will not be issued |
| something other than Caddy is listening on 80 or 443 | stop and ask: the staging block cannot work beside it |
| `/etc/caddy/Caddyfile` has no `import` line | the script appends one (it shows you the line); nothing else in the file is edited |
| the production site is not served by Caddy | stop and ask; this runbook assumes the documented setup |
| less than about 1 GB of disk free, or little free memory | stop and ask |

## Step 3. DNS record (only after step 2 has been reviewed)

At whoever hosts the DNS for `diamondfootballleague.com` (DigitalOcean: Networking, Domains; otherwise the registrar's DNS page), add:

| Type | Name / Host | Value | TTL |
|---|---|---|---|
| A | `preview` | `<DROPLET_IP>` | 300 (or the lowest offered) |

Do not change the existing `@` or `www` records. Wait until the name answers (this can take a few minutes, rarely longer):

```
getent hosts preview.diamondfootballleague.com          # must print <DROPLET_IP>
```

If a DigitalOcean cloud firewall is attached to the Droplet, inbound TCP **80 and 443** must be allowed (Networking, Firewalls). Caddy needs
both: 80 to prove ownership of the name and to redirect to HTTPS, 443 to serve it.

## Step 4. Rollback copy (required; the installer refuses to run without one less than a day old)

1. In the DigitalOcean control panel take a **snapshot of the Droplet** (Droplet, Snapshots, Take snapshot). Optional but strongly advised: it is the
   only copy of the whole machine.
2. On the Droplet:

```
sh /root/dfl-staging/repo/deploy/website/snapshot_config.sh        # prints e.g. /root/dfl-rollback/20261031T040000Z
ls -la /root/dfl-rollback/
```

It **copies** (never moves) `/etc/caddy`, the `dfl-*` systemd units and the web root into `/root/dfl-rollback/<time>/` with checksums.

## Step 5. Remember what the live site looks like now (so it can be proved unchanged)

```
python3 /root/dfl-staging/repo/tools/verify_deployment.py --production https://diamondfootballleague.com --save-baseline /root/prod-baseline.json
```

## Step 6. Install the staging site

```
sh /root/dfl-staging/repo/deploy/website/install_staging.sh /root/dfl-staging
```

What it does, in order, stopping at the first problem and printing why: verifies the package; requires the rollback copy of step 4; requires
`preview.diamondfootballleague.com` to resolve to this machine; creates the `dflv2` user and `/srv/dflv2/{repo-releases,site,feed,league}`; copies
the code to `/srv/dflv2/repo-releases/<package id>/` and points `/srv/dflv2/repo` at it; installs the site as a new release under
`/srv/dflv2/site/releases/` and switches `site/current` to it; installs the shipped feed (verified before and after copying) under `/srv/dflv2/feed/`;
writes `/etc/caddy/conf.d/dflv2-preview.caddy` for the preview name; appends `import /etc/caddy/conf.d/*.caddy` to the Caddyfile if there is no such
line (a dated backup `Caddyfile.dfl-staging.<time>.bak` is kept); runs `caddy validate`; **only if that passes** runs `systemctl reload caddy`. If
validation or the reload fails it puts the previous Caddyfile back, removes the new block, and says so (exit 5 or 6). It never restarts or stops
Caddy and never touches the production block.

**FIRST-RUN RISK:** this is the first time the generated block meets the real Caddy. It was written without a Caddy binary to run, so
`caddy validate` on the server is its first syntax check; the script is built to fail safe at exactly that point. HTTPS then depends on Caddy
obtaining a Let's Encrypt certificate for `preview.diamondfootballleague.com`, which needs step 3 and open ports 80 and 443; the first request can
take up to a minute while it does. If `journalctl -u caddy -n 50` shows certificate errors, see "Troubleshooting".

## Step 7. Verify

On the Droplet and again from your own computer (Python 3.9+; nothing else needed, the file is in the package):

```
python3 /root/dfl-staging/repo/tools/verify_deployment.py https://preview.diamondfootballleague.com \
    --production https://diamondfootballleague.com --baseline /root/prod-baseline.json
```

Every line must be PASS (no WARN is expected: the official map ships in the package and the check requires it to be served). It checks: the page and
its scripts answer; the feed pointer, manifest and all 58 feed files match their SHA-256 and size; the served data passes the public-data check (no
hidden ratings or fields); POST, PUT and DELETE are refused; search engines are told not to index; the strict Content-Security-Policy; the pointer is
never cached and a release is cached for good; hidden files such as the publisher lock are not served; path traversal is refused; the HTTPS
certificate verifies for this name and has days left; plain `http://` redirects to `https://`; and **the production site is byte-for-byte what it
was at step 5**.

Then look at it in a browser (phone and laptop) and check by eye: the header shows **Simulation date** and **Last published update**; Home,
Schedule, Standings, Playoffs, Teams (open one team), Calendar, History (the Season 1 Coronation line shows its score), Rules and Map all open (the Map page shows the official DFL map, current alignment of 30 October 2026, and selecting it opens the full-size image);
the page says it is a development league.

## Step 8. Roll back (any time; each action is independent)

| Situation | Command | Effect |
|---|---|---|
| remove the staging block only | `sh /root/dfl-staging/repo/deploy/website/uninstall_staging.sh` | deletes `conf.d/dflv2-preview.caddy`, validates, reloads; data stays |
| put all of Caddy's configuration back as it was | `sh /root/dfl-staging/repo/deploy/website/rollback.sh --config /root/dfl-rollback/<time>` | verifies the checksums, restores, validates, reloads |
| a bad site build | `BASE=/srv/dflv2 sh /srv/dflv2/repo/deploy/website/rollback.sh --site` | `site/current` goes back one release |
| a bad feed release | `BASE=/srv/dflv2 sh /srv/dflv2/repo/deploy/website/rollback.sh --feed <release>` | the pointer goes to an older release still on disk |
| the whole machine | restore the DigitalOcean snapshot from step 4 | loses everything written since |

Removing the DNS record for `preview` finishes the withdrawal. None of these touch the production site.

## Troubleshooting

* **`install_staging.sh` stops with 4**: the name does not resolve here yet (step 3), or resolves to another address. Fix DNS; do not use `FORCE_DNS=1`
  unless you know why.
* **`caddy validate` fails (exit 5)**: nothing was changed. Send the printed error; the block is in `deploy/website/Caddyfile.template`.
* **Certificate not issued**: `journalctl -u caddy -n 80 --no-pager | grep -i -E "acme|certificate|error"`. Usual causes: the A record is wrong or not yet
  visible, a stray AAAA record, a CAA record excluding Let's Encrypt, port 80 or 443 blocked by a firewall, or another program already holding the port.
* **Browser shows a certificate warning right after install**: wait a minute and reload; Caddy fetches the certificate on the first request.

## What this package does not do (on purpose)

* No cutover: the production hostname is not pointed at the new site. That is a separate step after you have reviewed staging.
* No league on the server and no timer: the feed is the shipped seed. Running the exporter on the Droplet (a league file, numpy, the timer
  `dfl-feed-export.timer`) is the next milestone. When it is done, **the league runner and the exporter must run as the same user** (`dflv2`): a
  read-only user cannot open a SQLite write-ahead-log database when no runner has it open (tested here: "attempt to write a readonly database"), and
  an exporter running as a different user could create a file that locks the runner out. The shipped unit file already grants the exporter write access to
  the league folder for that reason. Python on Ubuntu 24.04 is 3.12 (this was built and tested on 3.13), and numpy comes from `apt install python3-numpy`;
  neither has been tried against a real league there.
* A map update: the official map (`site/assets/map/dfl-map.png`, alignment of 30 October 2026) ships in the package. A later map is a new site release (replace the file and `map.json`, see `site/assets/map/README.txt`); it needs no change to the league or the feed.
