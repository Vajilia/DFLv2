# Frontend replay integration

Branch: `frontend/replay-integration`, based on frontend release `7e3aabecf0dfa4257aded0f41db6e87fffd7ba7a`.
Exporter interface inspected: replay-tools release `049ae3bc4a80f4050978bcf1508b0e1650dfdc96`, `docs/REPLAY_TOOLS.md`.

The existing frontend already reads playback format 1 and uses season plus public game ID when linking from schedules and history. The replay route uses the archive `gid`, which is unique across seasons. No exporter interface change is required.

This change remembers open and closed History season sections, including their nested recorded-replay lists, while navigating within the page. Returning from a replay no longer collapses the list the visitor used. State lives only in the current browser page and resets on a full reload. The default remains: newest season open, other seasons and extra replay lists collapsed.

No engine, data, exporter, server configuration or deployment file changes. No deployment or league advancement performed. The missing-name and no-movement fallbacks remain as accepted prototype limits.

Deployment: this commit is a frontend-only candidate for the existing `deploy_site_only.sh` workflow. Replay files must be published separately using the replay-tools runbook. Publishing this branch alone does not supply recordings. Keep the daughter's isolated demo and the stopped event-188 league unchanged. Production deployment remains a separate action.

## Validation

- Replay navigation: 15 tests pass, including the new desktop/390px History-state check. The new check fails at both widths on the previous app.js.
- Homepage: 6 tests pass. Viewer recovery: 2 tests pass.
- Static site: 19 pass, 1 fails (`test_every_page_renders_without_errors_on_desktop_and_phone`: team/ALA overflows at 390px). Reproduced with the unmodified base app.js; not caused by this change. Chromium headless 134 / Playwright 1.51 in this workspace. Do not treat this as a fully green frontend suite or a live-phone check.
- JavaScript syntax and git whitespace checks pass. Engine/data diff from the frontend base is empty.

## Follow-up after the full-export rehearsal

The rehearsal at frontend `edd5f20` found that the explicit replay return link reset scroll position, and collapsed History lists still built every row.

- Capture the source scroll position on a normal Replay click; restore it after rendering the explicit return link's destination. Other tab navigation still starts at the top. Modified clicks are left to the browser.
- Build each season's extra replay rows on its first expansion, not while collapsed. Reopening a list does not duplicate rows. Previously open lists rebuild synchronously on return so their height exists before scroll restoration.
- No playback format, index caching, engine, exporter or deployment changes.
- Replay navigation now has 17 passing browser tests. New checks use a 1,600-entry synthetic index for deferred rendering and a 400-entry list for deep return navigation at 1920px and 390px. They exercise real viewer files with synthetic index entries; this is not a new full-export acceptance run or a real-phone test.

The updated frontend needs one isolated rehearsal pass with the real export before production deployment. The previous review copy and production have not been changed by this work.

Follow-up validation: 17 replay-navigation, 6 homepage and 2 recovery tests pass (25 total). Both new regressions fail against the previous frontend; deep-scroll return fails at both widths, and the previous collapsed list contains all 1,600 game rows. Syntax and whitespace checks pass.

## Playback progress slider

Production acceptance found that the within-play range was only synchronized when a play changed or playback paused. The animation loop now updates that range directly from the playback clock on each playing frame. It does not rerun the full text/list synchronization each frame.

A browser regression at 1000px and 390px checks that the range advances while playing, agrees with the viewer clock, stays fixed after pause, allows a seek while paused, and resets/disables on an untraced event. It fails on the previous playback.js at both widths. All 18 replay-navigation/control tests pass; JavaScript syntax and whitespace checks pass. No engine, replay data, format, exporter or deployment changes. This fix is prepared on the frontend branch and has not been deployed.

## History-test timing correction

The collapsed-list text/link test previously read the list immediately after a click. Since lazy row construction runs on the details toggle event, a successful single suite run did not establish repeated reliability. The test now waits up to five seconds for the visible replay link before retaining the same label and exact-destination assertions. No application code changes.

Validation: the corrected test passes 20 consecutive repetitions in fresh pages; the complete replay-link/control file passes all 18 tests. This is a bounded stability check, not proof that every race is excluded. Production needs no redeployment for this test/documentation change.

The reported one-off canvas change after pause remains an unreproduced observation; this change does not claim to resolve or explain it.
