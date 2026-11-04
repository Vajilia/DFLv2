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
* All tests of both sides pass on the merged tree, run one file at a time in a real headless browser: replay tools 12, replay links 15, home page 6, static site 20 (every page, desktop and 390 px), site deploy 29, site-only deploy 3, cutover 13. The static test that the frontend notes as failing (`team/ALA` overflows at 390 px) **did not fail here**, at the frontend base `7e3aabe` or at `edd5f20`: 20 of 20. It is probably specific to the other workspace's browser; this is not a live-phone check either.
* The frontend was tested against exporter output: the game file the current exporter writes for the fixture archive at `--every 3` is **byte-identical** to the frontend's fixture file (same name and hash `b0ce0c0abf`), and so is its index entry.
* The exporter's real run (1,600 games, four seasons) was verified against the archive and the feed before this plan: see `docs/REPLAY_TOOLS.md` section 3. The index the site will read has 1,600 entries and every game the feed lists finds its replay under the same id.

Not checked: the exporter's service sandbox against the live archive with the runner active; a live phone (see finding 7).

## 1a. Rehearsal at a review address (done 2026-11-03; production untouched)

Address: **https://preview.diamondfootballleague.com/feed/review/combined/** (site `edd5f20` with only `assets/config.js` changed, reading the live feed and `/feed/review/playback-full/`, the full 1,600-game export). The two folders are new; the earlier review folders and the daughter's demo are untouched. Undo: move `feed/review/combined` and `feed/review/playback-full` aside.

**The index checksum belongs to these exact settings.** A fresh export from the pinned code (`6970056`, `--every 2` = 0.1 s) of the archive copy (SHA-256 `871a0b94...0d24`) against the live feed (release `e00188-cf545fbf7b`) gives index SHA-256 `6ee0a76ef0c48a439b1f2c094aeebf6e5fc489deec28c049a8c130d09516ef30`, and the whole tree (index plus 1,600 game files) is byte-identical to the one in the review copy (`diff -r`). Every file records `sampling.every = 2` (0.10 s). The same game at 0.15 s gets a different file name and hash (`S001-reg-w01-g01`: `fdb479ca92` at 0.1 s, `79a6895dae` at 0.15 s), so a different sampling, archive or feed changes the index checksum; step 4's gate holds only for these settings.

**Replay loading and playback.** 68 games, sampled across all four seasons (per season: first, middle and last regular game, first and last tour game, all 10 playoff games, the Bowl and the Coronation), each loaded by route in a real browser at 1,920 px and in a 390 px window, with this result: all 136 loads passed. In each: the plays listed equal the index's play count, the teams match, the plays without movement equal the index's untraced count, the canvas animates after Play and shows Pause, and the final scoreboard equals the index score. No horizontal overflow at either width. Load time (route change to playable): median 264 ms and 250 ms, 90th percentile 442 ms and 652 ms, slowest 1,041 ms and 1,516 ms (desktop, 390 px); the largest game file is 796 KB (232 KB over the wire).

**Links and return navigation.** From History, for each of the four seasons: the Coronation link, the Bowl, a playoff game, a regular game and a tour game (20 flows at desktop width) all opened the viewer, the "← History" link returned to History, and the open sections and lists were still open (the frontend's new feature works with the real index). Also from the Playoffs page (11 replay links), Schedule week 1 (20) and a team page (20): each opened the viewer and returned to the right page ("← Playoffs", "← Schedule", "← Team"). At 390 px the same flow was also followed for the Coronation link of Seasons 4, 3 and 2 and for a regular-game link of Seasons 4 and 2, with the same result.

**History with the full index** (desktop 1,920 px / a 390 px popup window whose layout width is 375 px because of its scrollbar; this computer, not a phone):
* Cold load: 10 requests, 121 KB transferred, of which the index is 115 KB (491,910 B decoded) and takes about 120 to 140 ms; everything has arrived about 0.3 s after the request starts.
* The page has **27,341 DOM nodes** with every list collapsed, against 727 on today's production History page (37 times as many): each season's list of about 389 "other recorded replays" is built even while closed.
* Warm render of History: 231 ms (desktop), 95 ms (390 px). Opening a season: 33 to 50 ms; opening a replay list: 49 to 114 ms to paint (desktop), 33 ms (390 px). All lists open, the page is 231,801 px tall (desktop) and 316,613 px (390 px); scrolling it all: median frame 17 ms, 95th percentile 23 ms, slowest 78 ms (desktop); 8, 9 and 11 ms (390 px).
* The browser reports two long tasks on the cold desktop load (207 ms and 266 ms).
* No horizontal overflow at 390 px with everything closed or everything open; no element wider than the screen.

**What the measurements say about caching:** the network cost is one 115 KB gzipped file per full page load, 0.1 to 0.15 s here, and nothing else got slower. **No caching change is made** (finding 2).

**Server state during and after:** league inactive and `limit_reached: true`; feed release `e00188-cf545fbf7b`; `verify_revision.py --commit 7e59cbb...` (all lines, including the Caddy block) reported ALL MATCH after the review copy was installed; no Caddy file, unit, site release or feed release was changed; the existing `dfl-feed-export.timer` (the feed publisher, which skips when nothing changed) and the transient `dfl-site-probe.timer` were already there and were not touched. The playback exporter timer does not exist on the server.

## 2. Findings that affect the combination

1. **The two halves depend on each other only through files.** The site alone shows no Replay links; the replay files alone are inert until a page asks for them. Either can be installed first without breaking anything. The frontend release notes' step "publish an empty replay index first" is **not needed and should not be done**: the exporter writes the real index.
2. **The index is large and is fetched on every full page load; measured, and left unchanged.** `index.json` is 491,910 bytes (115,335 gzipped on the wire, which Caddy already does). `site/assets/app.js` fetches it with `cache: 'no-store'` on the home, schedule, playoffs, team, history and game pages, and the template also says `no-store` for it, so every full page load downloads about 115 KB. Hash navigation inside one page does not refetch. The rehearsal (section 1a) shows that this costs about 0.1 to 0.15 s on this connection and does not slow any page, so the caching stays as it is. If a real phone on a slow connection later shows a problem, the cheap fix needs one line on each side: the frontend fetches with the default cache mode, and Caddy answers the index with `no-cache` (the browser revalidates with the ETag and gets an empty 304). That change would be ChatGPT's (frontend) plus one template line; neither is made.
3. **Unnamed players in older seasons** (41%, 29%, 19%, 2% of roster entries for seasons 1 to 4). Play text names them "player NNNN" and the field label says "player NNNN (no public name)". Accepted prototype limit on both sides; only a new public source of past players' names would change it.
4. **Sampling.** The real export uses the exporter's default, 0.1 s (about 925 MB on disk, 270 MB gzipped); the fixtures use 0.15 s. The viewer reads each file's own `sampling`, so both work. The verified export is at 0.1 s; choosing 0.15 s (about 25% smaller) would mean exporting and verifying again. Recommended: keep 0.1 s.
5. **A merge on GitHub changes nothing on the server, and the playback rules live only in the replay-tools template.** The server's code tree `/srv/dflv2/repo` is replaced only by a full `deploy_pinned.sh <40-character commit>`; `deploy_site_only.sh` never touches it. Both Caddy renderers read the template from that tree: `install_staging.sh` (the preview snippet, run by `deploy_pinned.sh`) and `cutover_production.sh` (the production snippet; it refuses to run while that snippet exists). Today that tree is `7e59cbb`, whose template has no playback rules. So after step 5 added them by hand, any re-render from the server's tree would drop them silently (the files would still be served, but with `no-cache` instead of long-lived caching), and a merge to `main` alone would not prevent that. How the rules are kept, in order:
   * **Step 5 renders from the pinned checkout** of `6970056` (`/opt/dflv2-src/<commit>/deploy/website/Caddyfile.template`), never from `/srv/dflv2/repo`, and proves each live snippet equals that template rendered for its hosts (`cmp`, below).
   * **Until the server's tree contains the rules, nobody runs `install_staging.sh`, `deploy_pinned.sh` or the cutover scripts for a commit that lacks them.** `deploy_pinned.sh` needs a commit id, so that is a choice made on the command line, not an accident.
   * **The next full deployment must be a commit that contains this template** (the merged `main`, or any commit that descends from both branches). That deployment replaces `/srv/dflv2/repo`, re-renders the preview snippet from the new template, and `verify_revision.py --commit <that commit>` then checks the snippet byte-for-byte against it, so the rules are verified automatically from then on. The production snippet is not re-rendered by that deployment (only `rollback_cutover.sh` followed by `cutover_production.sh` does that), so it keeps the hand-rendered copy from step 5, which already equals the same template. A full deployment is a separate action with its own rehearsal and a state-fingerprint diff.
   * **A guard test** in this branch (`tests/test_replay_tools.py`) fails if the template loses the playback rules, so the rules cannot disappear from the template unnoticed.
   * **A one-line check** to run before and after any future Caddy change: both `/etc/caddy/conf.d/dflv2-*.caddy` files must contain `/feed/playback/games/*` exactly once (today: 0 and 0).
   * **`verify_revision.py` and the new rules.** Its Caddy line compares the preview snippet with the template of the commit it is given. Checked on the server on 2026-11-03: with today's snippets and `7e59cbb` it prints ALL MATCH; with the snippet rendered from `6970056`'s template it reports exactly one mismatch (the Caddy line) and ALL MATCH with `--no-caddy`. The live production snippet today equals `7e59cbb`'s template rendered for `diamondfootballleague.com, www.diamondfootballleague.com, 143-198-130-68.sslip.io`; the scratch production snippet equals `6970056`'s template rendered for the same list; the two templates differ only by the playback rules (the index added to `@latest`, the `@playgame` matcher and its header). So step 7 uses `--no-caddy` and the `cmp` checks of step 5.
6. **History with the full index** is measured in section 1a: fast to load and to scroll on this computer, but its DOM is 37 times larger than today's (the replay lists are built while collapsed). On a mid-range phone, whose processor is several times slower than this computer's, the first render of History is the part most likely to be noticeable. It could not be measured on a real phone here.
7. **Frontend issues found (reported, not fixed; for ChatGPT):**
   * **"← History" resets the scroll position to the top.** The page's `hashchange` handler scrolls to the top on every route change. A visitor who opens a replay from deep in a list (a link in Season 2's list is about 205,000 px down at 390 px; the lists remain open) lands back at the top, while the browser's own Back button returns to the exact position (checked at 390 px: 47,155 px to 0 with the link, 47,155 px to 47,155 with the browser button; 204,911 px to 0 and 204,911 px to 204,911). Suggested: the back link should go back in history when it came from that page.
   * **The replay lists are built in full while closed** (27,341 nodes on History). Suggested, optional: build a list's rows the first time it is opened.
   * The frontend note's "team/ALA overflows at 390 px" failure did not reproduce here (finding in section 1); its replay-link test file has 15 tests, which pass.

## 3. Sequence

Every step is additive, has a gate (what must be true before going on) and an undo. Nothing here starts the league, the runner or the timer.

**Step 0. Preconditions** (read-only). League stopped (`runner_status.json` has `limit_reached: true`, `dflv2-league` inactive); feed release `e00188-cf545fbf7b`; `DEPLOYED_REVISION.json` names `7e59cbb`; the recovery set exists on the Droplet and on your computer (checksum `5edea873...133e`) and `dfl-backup-141.tar` is kept. Take the state fingerprint (`tools/cap_state.py fingerprint ... --out before.json`).

**Step 1. Rehearsal at a review address: done (section 1a).** Gate met: nothing wrong with replays, playback or navigation across the four seasons; the History cost is acceptable on a computer, unmeasured on a phone; caching unchanged. Remaining before production, your call: look at the review address on a real phone, and decide whether the two frontend issues in finding 7 should be fixed first.

**Step 2. Rollback copy.** `sh deploy/website/snapshot_config.sh`; note the snapshot folder.

**Step 3. Exporter checkout.** Pinned checkout of `69700562fdfe9cfbed4ea15111ba66af1fbb039d` under `/opt/dflv2-src/` (`git rev-parse HEAD` equals it; clean). Not `install_staging.sh`, not `/srv/dflv2/repo`. Undo: remove that folder.

**Step 4. Publish the replay files.** Create `/srv/dflv2/feed/playback` (owner `dflv2`, 755), run the exporter once as `dflv2` from the pinned checkout on the production archive `/srv/dflv2/league/league.db.archive` (about 5 minutes), then `verify_playback_against_archive.py`, `replay_coverage.py --expect-complete`, `replay_public_scan.py`: all pass. Gate: the index is **byte-identical** to the verified one (SHA-256 `6ee0a76ef0c48a439b1f2c094aeebf6e5fc489deec28c049a8c130d09516ef30`, which holds only for the pinned code `6970056`, `--every 2` and this archive and feed, section 1a) while the feed is still `e00188-cf545fbf7b`; files 644, folders 755. At this point the files are public if someone knew the address, but no page links to them. Undo: move `feed/playback` aside (kept).

**Step 5. Caddy cache rules** on both snippets (`dflv2-preview.caddy`, `dflv2-production.caddy`). Render them from the **pinned checkout's** template, not from `/srv/dflv2/repo` (finding 5): preview with `sed "s/__HOST__/preview.diamondfootballleague.com/g"`, production with `sed "s|__HOST__|diamondfootballleague.com, www.diamondfootballleague.com, 143-198-130-68.sslip.io|g"` (the exact host list `cutover_production.sh` used; read it from the live snippet's first line and compare before writing). Write to `*.new`, `caddy validate` with both new files in a scratch config (done on 2026-11-03: "Valid configuration"), then move into place, `caddy validate` on the real config, `systemctl reload caddy`. Gates: each live snippet is byte-identical to the rendered template (`cmp`); the index answers 200 with the intended `Cache-Control` (`no-store` as the template stands); one game answers 200 with `immutable`; a name that does not exist answers 404 and never `immutable`; both snippets contain `/feed/playback/games/*` exactly once. Undo: `rollback.sh --config <snapshot>`.

**Step 6. The website.** `sh /srv/dflv2/repo/deploy/website/deploy_site_only.sh edd5f20e789656e88a7d30ba75d7826a6c26fd06`. It copies only `site/`, proves the installed files equal the commit's, records `SITE_REVISION.json` and restarts nothing. Gate: its own byte-identity check, then the same look as step 1 on the production address (History, a replay, home) at desktop and 390 px. Undo: `rollback.sh --site` (one step; the previous site release is kept).

**Step 7. After.** State fingerprint again and `cap_state.py diff before.json after.json` (exit 0); league still stopped at 188; `verify_revision.py --commit 7e59cbb7... --no-caddy --site-commit edd5f20e...` still passes for the code tree, the site and `DEPLOYED_REVISION.json`. **Run it with `--no-caddy`:** its Caddy line compares the preview snippet with `7e59cbb`'s own template and will report one mismatch once step 5 has added the playback rules; that mismatch is expected and is replaced by the two `cmp` checks of step 5 (and, after the next full deployment, by `verify_revision.py --commit <that commit>` with the Caddy line on). The demo and the review copies load; the replay-tools scratch folders on the Droplet can be removed when you say.

**Step 8. Three separate actions, each with its own go-ahead, none part of the go-live.**
* **A. Merge** `release/replay-tools-2026-11-03`, then `frontend/replay-integration`, into `main` as two merge commits. This changes only the source on GitHub. It does **not** change anything on the server (finding 5): `/srv/dflv2/repo` stays at `7e59cbb` and the live Caddy snippets keep the rules from step 5.
* **B. A full deployment of the merged commit** (`deploy_pinned.sh <merge commit>`), only if and when you want the server's tree to carry the new template and exporter. It needs its own rehearsal and the state-fingerprint diff, and afterwards `verify_revision.py --commit <merge commit>` (Caddy line on) must pass.
* **C. The exporter timer.** `dfl-playback-export.timer` is **not installed and stays disabled while the league is stopped at 188**: with no new games there is nothing for it to publish. Install and enable it only after A (so the unit's checkout is a merged commit) and before the league resumes, and test the unit against the live archive with the runner first stopped and then running.
* **Resuming the league** is a fourth, separate decision, taken after C.

## 4. Rollback, in this order

Site (`rollback.sh --site`), Caddy (`rollback.sh --config <snapshot>`), then move `feed/playback` aside and remove the pinned checkout. Each step restores the state before it; none touches the league, the archive, feed releases or any backup. With the files moved aside the index answers 404 and no page shows a Replay link, which is the state before this release.

## 5. Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| 1 | Rehearse at a review address before production | Done (section 1a). Look at it once on a real phone: https://preview.diamondfootballleague.com/feed/review/combined/ |
| 2 | Index caching | Unchanged (measured, finding 2) |
| 3 | The two frontend issues (finding 7): scroll reset on "← History"; lists built while collapsed | Send to ChatGPT; the first is a small fix, the second optional. Decide whether production waits for them |
| 4 | Names for players who have left | Accept for now |
| 5 | Sampling | Keep 0.1 s (the checksum above is for it) |
| 6 | Merge both branches into `main` (step 8 A); a full deployment of the merged commit (B); the exporter timer (C); resuming the league | Four separate go-aheads, in that order, none part of the go-live. The timer stays disabled and uninstalled while the league is stopped at 188 |

Not asked and not done: deployment, the timer, resuming the league, any change to the frontend branch.
