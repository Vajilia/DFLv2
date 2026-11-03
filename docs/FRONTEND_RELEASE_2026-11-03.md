# Frontend-only release for review (2026-11-03)

Branch `release/frontend-replay-2026-11-03`, based on the deployed engine release `7e59cbbaea23b0de353545c64a6f5c39cffe774d`. **Not deployed. The league is not resumed.**

## What is in it (only `site/`, `tests/`, `docs/`)

1. **Home page, by phase.** The line under the schedule used to say "The playoff bracket is under way" in every phase from the moment the Season was complete, including the finished season. Now (`playoffLine` in `site/assets/app.js`): nothing before the field is set; "The playoff field is set: see the bracket" when seeded; "The playoff bracket is under way" only while rounds are being played; "The Diamond Coronation has been played: see the bracket" once it has a winner but the league year has not closed; "See the final playoff bracket and the Diamond Coronation result" in a completed season. The champion card also appears as soon as the Coronation is played (taken from the playoffs file when the season file has not named one yet), and a completed season adds "Season N is complete."
2. **Replay viewer (the accepted one, with the live-site navigation).** Replay links on every list of completed games (schedule, team pages, playoffs, home, and now **History, including older seasons**). A link appears only when the published replay index has the same season, game id, clubs and score. History looks a past season's games up under that season, so the same game id in two seasons never borrows the other's replay. The viewer's page has a back link to where the visitor came from (History for an earlier season, Playoffs for a playoff game, Schedule otherwise). The viewer code (`playback.js`, `playback.css`) loads only when a replay is opened.
3. **Honest fallbacks, unchanged:** plays without recorded movement are listed as "no movement recorded"; a player missing from the public rosters is shown as "player NNNN (no public name)"; a missing or incompatible index means no links and a plain message on a replay address.
4. `site/assets/config.js` is empty in production (the page reads `feed/` and `feed/playback/` beside it); a review copy can point it elsewhere.

Nothing outside `site/`, `tests/`, `docs/` differs from `7e59cbb` (checked with `git diff --stat 7e59cbb`), so `verify_revision.py` still matches the engine and tools byte for byte.

## Tests (run one at a time)

| file | tests | what it covers |
|---|---|---|
| `tests/test_site_home.py` (new) | 6 | preseason, regular season, playoffs seeded, playoffs in progress, Coronation played but year open, completed season, in a real browser against variant feeds. Three of them fail on the old `app.js` and pass on the new one. |
| `tests/test_site_replay_links.py` (new) | 6 | History link for an older season uses that season; the same id in the current season gets no link; changed score gets no link; no index gives no links and a plain message; the viewer opens from History with a "← History" back link and "no movement recorded"; a player missing from the roster shows as "player NNNN (no public name)". |
| `tests/test_site_static.py` | 20 | every page on desktop and phone, no console errors, nothing requested outside the site. |
| other `test_site_*` files | 130 | feed, publisher, deploy, site-only deploy, cutover, recovery, status, probe: all pass. |

## To deploy, only after approval

1. `sh /srv/dflv2/repo/deploy/website/deploy_site_only.sh <commit>` (this branch's head commit, 40 characters). It copies only `site/`, records `SITE_REVISION.json`, restarts nothing, and `rollback.sh --site` undoes it in one step.
2. Before or with it, publish an **empty valid replay index** at `/srv/dflv2/feed/playback/index.json` (`{"format":"dfl-public-playback","format_version":1,"kind":"index","league_id":null,"games":[]}`), because the production feed has no `playback/` folder yet and the page would otherwise log one 404 per page load. The exporter's clean-up only removes its own temporary files, so the file stays. Caddy serves `/feed/*` with `no-cache`, so a later index is picked up at the next load.
3. **Replay links will not appear until the replay files exist.** The exporter (`tools/export_playback.py`, `src/dflsite/playback.py`) and verifier belong to a separate tools release that is not part of this one. Until that is deployed and has been run, the site is correct but shows no Replay links. Which games get a replay (including older seasons) is decided by what the exporter publishes, not by this release.

## Known limits

* Tested in a real browser against fixtures and variant feeds, not yet against the live feed; a staging copy of this branch can be put at the review address before approval if wanted.
* The replay fixture game used by the tests is a regular-season game relabelled as a Season 1 game in a variant feed (to exercise the older-season lookup); a real older-season playoff game will be exercised only once the exporter has published one.
