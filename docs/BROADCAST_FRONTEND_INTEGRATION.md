# Season-mode frontend integration

Branch: `frontend/broadcast-honors-2026-11-05`, based on backend `656a61f18877f7d6217f3fd959938ed2b82ace59`.
Status: implemented and locally verified; not deployed or merged into a release/main branch. No backend, engine or data changes. No production files, timers, configuration, backups or the league at event 188 were opened or changed.

## Changes

- `health()` recognizes `status.json` with `source: broadcast`. It says Broadcast running/paused/stopped, does not predict the next simulation event, and retains stale-heartbeat/publish-error warnings. Legacy simulation/review-limit wording remains unchanged for other sources.
- The Honors tab (`#/honors`) reads only `broadcast/honors/index.json` and the exact part files it lists. The index is refreshed every 15 seconds independently of feed-release updates. An initial 404 is a normal unrevealed state, not an invented empty ceremony.
- Tour Rings, Tiaras, hall scopes/registers/entries and the fan entrant are rendered from the public documents. Notes, rules and unavailable reasons retain the source text. Missing/none records and empty halls stay empty. Staff do not require player IDs. Names and all other source strings become text nodes, never HTML.
- Part descriptors are validated before fetching: a known part, positive integer season, and the exact relative `sNNN/part.json` path. Unsupported formats/identities do not render; failed part fetches retry. No guessing of future award URLs, award recipients, missing statistics or analytics.
- Recipient/hall sections retain their open state as later parts arrive. Part files are immutable and cached within the current view; only the index and failed/missing parts are reread. Leaving the route cancels its timer and fetches.
- Isolated `broadcastOnly` mode now allows Watch now and Honors without reading simulation feed/status or the ordinary full-replay index.
- Watch now renders an announced upcoming window with no participants as “Participants to be announced.” Its countdown still follows `start_s`; participant gates remain on the backend.
- Ordinary Replay links are unchanged: missing links before a results-block reveal are expected. Watch now may offer the finished replay before Schedule/History do.

## Validation

Commands run locally:

```
python -m unittest discover -s tests -p test_site_honors.py
python -m unittest discover -s tests -p test_site_status.py
python -m unittest discover -s tests -p test_site_broadcast.py
python -m unittest discover -s tests -p test_site_replay_links.py
python -m unittest discover -s tests -p test_site_home.py
python -m unittest discover -s tests -p test_site_recovery.py
python -m unittest discover -s tests -p test_site_static.py
python -m unittest discover -s tests -p test_honors.py
```

Results:

| Checks | Tests | Result |
|---|---:|---|
| Honors frontend | 7 | pass; uses the backend document builders with synthetic recorded-world inputs |
| Status/verifier | 13 | pass, including broadcast vs simulation wording |
| Watch now | 7 | pass, including unannounced participants |
| Replay/control navigation | 18 | pass |
| Homepage | 6 | pass |
| Recovery | 2 | pass |
| Static site | 20 | pass in this environment |
| Honors backend document shape | 6 | pass |
| **Total** | **79** | **pass** |

The progressive Honors reveal/open-section test was also repeated ten times. Syntax/compilation/whitespace checks pass. The previous 390px team/ALA overflow did not reproduce; this work does not claim its cause was found or fixed. Phone-width checks use a browser viewport, not a physical phone.

A separate fresh private builder smoke (seed 20261106) initialized the real engine and built `offseason_open` and `hiring_open`, one at a time. A SeasonBroadcaster tick placed genesis into a separate public folder with source=broadcast, runner.state=paused, zero revealed events and no Honors index. This was local scratch data, not the production league. It did not compute a whole season or reproduce Claude's whole-year verification.

Visual inspection used the supported local preview: a copied fresh public feed plus explicitly labelled synthetic Honors fixture documents. The paused broadcast header, Honors navigation, recorded recipient disclosure and empty/unavailable sections rendered correctly. That fixture is not evidence of actual season recipients. The preview was stopped after inspection.

## Handoff to backend/release owner

1. Fetch this branch; its parent is the verified backend integration commit. Use this site tree for the next isolated combined review.
2. Reuse the already-built private verification season if still available: no new engine computation is required solely for these frontend changes. Put the frontend over the daemon-owned public folder at genesis, Bowl ceremony, Tiara, halls and fan-entrant boundaries.
3. Verify that only the parts named by the current index appear, missing ordinary Replay links stay missing until their results block, and all normal pages show broadcast-specific status.
4. A physical-phone check, production cache/directory-listing rules and a real-time rehearsal are still outstanding. Multi-year Honors/replay carry-over remains a backend limitation. No single-year frontend change claims to solve those.
5. Keep the existing website, daughter demo, historical replays, verified backups and league at 188 unchanged. This branch is a review candidate, not authorization to deploy or resume anything.
