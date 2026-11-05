# Broadcast preparation timestamp and open-page status

Branch: `frontend/broadcast-release-label-2026-11-05`.
Base: `1dde9454378b25d5452be513bfcc710af2f0ace9` (Claude's immediate pause-status write, including frontend `acac7fc`).

The season builder computes releases ahead of their broadcast. `manifest.generated_at` is their preparation time, not their reveal time. No reveal timestamp is introduced or inferred.

## Changes

- When `status.source` is `broadcast`, the header says **Release prepared** and displays the existing timestamp without an “ago” age. Its tooltip explicitly says this is computation time, not reveal time.
- Other sources keep **Last published update**, including the existing relative age.
- Already-open ordinary broadcast pages poll only `status.json` every two seconds, with at most one fast-poll request in flight. They do not reload the release, replay or body to update the health line. Unchanged health wording does not rebuild the header each poll. Failed fast-poll and ordinary minute-refresh status requests retain the last known broadcast status, whose stale-heartbeat warning still applies.
- The normal release check remains once a minute. Isolated `broadcastOnly` mode still does not request the simulation feed/status; Watch now continues to follow its own broadcast clock.
- Backend/schema/engine/data and deployment configuration are unchanged from the base. Nothing was deployed, merged into a release/main branch, timer-activated or resumed. The production league at 188 and all backups remain outside this work.

## Validation

| Suite | Tests | Result |
|---|---:|---|
| Site status | 15 | pass |
| Static site | 20 | pass |
| Watch now | 7 | pass |
| Honors frontend | 7 | pass |
| **Total** | **49** | **pass** |

The new timestamp regression fails on the base app.js and passes on the fix. Another browser regression changes a real broadcast status file from running to paused and back while keeping the same page/release; it verifies the label changes without another manifest request. The same test advances the browser clock through a minute refresh with a missing status file and verifies the page retains its broadcast provenance rather than falling back to the misleading legacy label. Syntax and whitespace checks pass. Test environment: Playwright 1.51 with Chromium headless 134; a missing/newer browser download was repaired before these passing runs.

Commands (with a working Playwright/Chromium installation):

```
python -m unittest discover -s tests -p test_site_status.py
python -m unittest discover -s tests -p test_site_static.py
python -m unittest discover -s tests -p test_site_broadcast.py
python -m unittest discover -s tests -p test_site_honors.py
```

These are focused frontend results, not a full engine-suite, physical-phone, full-season or 48-hour real-time rehearsal claim. Claude's one unidentified `test_site_broadcast` failure under CPU saturation remains an unreproduced failure; its assertion was not captured and it is not called fixed by this patch.

## Next review

Use this branch as the combined review candidate; its ancestry already includes the backend pause fix. Reuse the verified private season store. Check the label during a prepared-ahead broadcast and pause/resume on an already-open page. No new reveal-time field is needed for this wording.

Before production, the separate outstanding work is the capped real-time rehearsal, physical-phone check and server cache/directory-listing configuration. Multi-year Honors/replay carry-over also remains a backend limitation. Retain first-failure logs and the exact assertion if another flaky check appears; do not erase that evidence with reruns. Keep existing production, demo and league at 188 untouched.
