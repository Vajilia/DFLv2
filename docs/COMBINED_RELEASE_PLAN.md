# Combined release plan: replay tools + frontend (prepared 2026-11-03, nothing deployed)

Two candidates, kept as separate branches, deployed by two separate mechanisms:

| Part | Branch | Pin (full commit) | Deployed by | Changes |
|---|---|---|---|---|
| Engine and tools already on the server | `release/engine-memo-2026-11-02` | `7e59cbbaea23b0de353545c64a6f5c39cffe774d` | (already deployed; **not touched**) | nothing |
| Replay files, Caddy cache rules, exporter | `release/replay-tools-2026-11-03` | `69700562fdfe9cfbed4ea15111ba66af1fbb039d` | a pinned checkout under `/opt/dflv2-src/` plus the steps in `docs/REPLAY_TOOLS.md` section 5 | `feed/playback/` (new), two Caddy snippets, later a timer |
| Website | `frontend/replay-integration` | `edd5f20e789656e88a7d30ba75d7826a6c26fd06` | `deploy_site_only.sh <commit>` (existing helper) | `site/` only |

Use `6970056`, not `049ae3b` (the replay-tools head that `docs/FRONTEND_REPLAY_INTEGRATION.md` cites). The only difference is one test fixture that had been left out of the commit by a
`*.sqlite` rule in `.gitignore`; the tools are byte-identical. `049ae3b`'s tests fail on a fresh checkout; `6970056`'s pass.

**The league stays stopped at event 188 throughout. The engine tree `/srv/dflv2/repo`, the league files, the feed publisher, the runner timer and every backup are never touched.**

## 1. What was checked on the combination (a local scratch merge, not pushed)

* The branches change 21 different files; none is changed by both, so the merge is clean. Nothing under `src/dfl/` or `data/` differs from `7e59cbb`; the code fingerprint is `efaa7167...` (the deployed one).
* All tests of both sides pass on the merged tree, run one file at a time in a real headless browser: replay tools 11, replay links 14, home page 6, static site 20 (every page, desktop and 390 px), site deploy 29, site-only deploy 3, cutover 13. The static test that the frontend notes as failing (`team/ALA` overflows at 390 px) **did not fail here**, at the frontend base `7e3aabe` or at `edd5f20`: 20 of 20. It is probably specific to the other workspace's browser; this is not a live-phone check either.
* The frontend was tested against exporter output: the game file the current exporter writes for the fixture archive at `--every 3` is **byte-identical** to the frontend's fixture file (same name and hash `b0ce0c0abf`), and so is its index entry.
* The exporter's real run (1,600 games, four seasons) was verified against the archive and the feed before this plan: see `docs/REPLAY_TOOLS.md` section 3. The index the site will read has 1,600 entries and every game the feed lists finds its replay under the same id.

Not checked: the combined site against the real 1,600-game index in a browser (step 1 below does that); the exporter's service sandbox against the live archive with the runner active; a live phone.

## 2. Findings that affect the combination

1. **The two halves depend on each other only through files.** The site alone shows no Replay links; the replay files alone are inert until a page asks for them. Either can be installed first without breaking anything. The frontend release notes' step "publish an empty replay index first" is **not needed and should not be done**: the exporter writes the real index.
2. **The index is large and is fetched on every full page load.** `index.json` is 491,910 bytes (119,637 gzipped, which Caddy already does). `site/assets/app.js` fetches it with `cache: 'no-store'` on the home, schedule, playoffs, team, history and game pages, and the Caddy rule in `deploy/website/Caddyfile.template` also says `no-store` for it. So every page load downloads about 120 KB, on a phone too. Hash navigation inside one page does not refetch. This is acceptable and not a blocker, but it is the largest avoidable cost in the combination. A cheap fix needs one line on each side: the frontend fetches with the default cache mode (not `no-store`), and Caddy answers the index with `no-cache` (the browser then revalidates with the file's ETag and gets an empty 304 while no game has been added). **Frontend change: reported, not made. Caddy line: not changed either, until you decide.**
3. **Unnamed players in older seasons** (41%, 29%, 19%, 2% of roster entries for seasons 1 to 4). The site shows "player NNNN (no public name)". Accepted prototype limit on both sides; only a new public source of past players' names would change it.
4. **Sampling.** The real export uses the exporter's default, 0.1 s (about 925 MB on disk, 270 MB gzipped); the fixtures use 0.15 s. The viewer reads each file's own `sampling`, so both work. The verified export is at 0.1 s; choosing 0.15 s (about 25% smaller) would mean exporting and verifying again. Recommended: keep 0.1 s.
5. **The Caddy change is only in the replay-tools template.** The server's own copy of the template (`/srv/dflv2/repo`, revision `7e59cbb`) does not have it. If anyone later re-renders the Caddy snippets from that old tree (`install_staging.sh`), the playback cache rules disappear: the files are still served, only with `no-cache` instead of long-lived caching. The permanent fix is for the next full deployment to come from a revision that contains this branch (see step 8).
6. **A History page of a past season gets a collapsed list of about 390 replay links** (regular, tour, bowl). Not measured against real data; step 1 measures it.

## 3. Sequence

Every step is additive, has a gate (what must be true before going on) and an undo. Nothing here starts the league, the runner or the timer.

**Step 0. Preconditions** (read-only). League stopped (`runner_status.json` has `limit_reached: true`, `dflv2-league` inactive); feed release `e00188-cf545fbf7b`; `DEPLOYED_REVISION.json` names `7e59cbb`; the recovery set exists on the Droplet and on your computer (checksum `5edea873...133e`) and `dfl-backup-141.tar` is kept. Take the state fingerprint (`tools/cap_state.py fingerprint ... --out before.json`).

**Step 1. Rehearsal at a review address (recommended before touching production paths; needs your approval).** Copy the verified export (`/root/replay-work/playback`, 1,600 games) to `/srv/dflv2/feed/review/playback-full/`, and install the `edd5f20` site as a review copy (`/srv/dflv2/feed/review/combined/`) whose `config.js` sets `window.DFL_CONFIG = { feed: '/feed/', playback: '/feed/review/playback-full/' }`, the way the earlier review copy was done. No Caddy change, no production path, the earlier review copies and the daughter's demo untouched. (Both Caddy hosts serve `/srv/dflv2/feed/`, so the review address also answers on the production host if someone knew the path; it was the same for the earlier review copies.) Check on desktop and at 390 px: History for each of the four seasons (the open and the collapsed lists, the Coronation row, a Season 2 playoff replay and its back link), a Season 4 playoff from the Playoffs page, one game with unnamed players, the home page, page load time and bytes with the real index (finding 2), console errors. Gate: nothing wrong, and you accept the numbers. Undo: move the two review folders aside.

**Step 2. Rollback copy.** `sh deploy/website/snapshot_config.sh`; note the snapshot folder.

**Step 3. Exporter checkout.** Pinned checkout of `69700562fdfe9cfbed4ea15111ba66af1fbb039d` under `/opt/dflv2-src/` (`git rev-parse HEAD` equals it; clean). Not `install_staging.sh`, not `/srv/dflv2/repo`. Undo: remove that folder.

**Step 4. Publish the replay files.** Create `/srv/dflv2/feed/playback` (owner `dflv2`, 755), run the exporter once as `dflv2` from the pinned checkout on the production archive `/srv/dflv2/league/league.db.archive` (about 5 minutes), then `verify_playback_against_archive.py`, `replay_coverage.py --expect-complete`, `replay_public_scan.py`: all pass. Gate: the index is **byte-identical** to the verified one (SHA-256 `6ee0a76ef0c48a439b1f2c094aeebf6e5fc489deec28c049a8c130d09516ef30`) while the feed is still `e00188-cf545fbf7b`; files 644, folders 755. At this point the files are public if someone knew the address, but no page links to them. Undo: move `feed/playback` aside (kept).

**Step 5. Caddy cache rules** on both snippets (`dflv2-preview.caddy`, `dflv2-production.caddy`), validated first in a scratch copy (done on 2026-11-03: "Valid configuration"), then `caddy validate` on the real config, `systemctl reload caddy`. Gate: the index answers 200 with the intended `Cache-Control` (`no-store` as the template stands; `no-cache` if you choose finding 2's fix); one game answers 200 with `immutable`; a name that does not exist answers 404 and never `immutable`. Undo: `rollback.sh --config <snapshot>`.

**Step 6. The website.** `sh /srv/dflv2/repo/deploy/website/deploy_site_only.sh edd5f20e789656e88a7d30ba75d7826a6c26fd06`. It copies only `site/`, proves the installed files equal the commit's, records `SITE_REVISION.json` and restarts nothing. Gate: its own byte-identity check, then the same look as step 1 on the production address (History, a replay, home) at desktop and 390 px. Undo: `rollback.sh --site` (one step; the previous site release is kept).

**Step 7. After.** State fingerprint again and `cap_state.py diff before.json after.json` (exit 0); league still stopped at 188; `verify_revision.py` still matches `7e59cbb`; the demo and the review copies load; the replay-tools scratch folders on the Droplet can be removed when you say.

**Step 8. Afterwards, not part of the go-live.** Merge `release/replay-tools-2026-11-03` and then `frontend/replay-integration` into `main` as two merge commits, so that later deployments carry the Caddy rules and the exporter (finding 5). Before the league is resumed: install and enable the exporter timer (`docs/REPLAY_TOOLS.md` step 5), and test the unit against the live archive with the runner first stopped and then running. Resuming the league is a separate decision.

## 4. Rollback, in this order

Site (`rollback.sh --site`), Caddy (`rollback.sh --config <snapshot>`), then move `feed/playback` aside and remove the pinned checkout. Each step restores the state before it; none touches the league, the archive, feed releases or any backup. With the files moved aside the index answers 404 and no page shows a Replay link, which is the state before this release.

## 5. Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| 1 | Rehearse at a review address (step 1) before production? | Yes. It is the only place the real 1,600-game index meets the real site, and it is isolated |
| 2 | Index caching (finding 2): leave as is, or change the frontend fetch and the Caddy rule? | Leave as is for the first release unless step 1 shows it is slow; decide with the measured numbers. If changed, the frontend change is ChatGPT's |
| 3 | Names for players who have left (finding 3) | Accept for now |
| 4 | Sampling (finding 4) | Keep 0.1 s |
| 5 | Merge both branches into `main` after production is verified (step 8) | Yes, as two merge commits |

Not asked and not done: deployment, the timer, resuming the league, any change to the frontend branch.
