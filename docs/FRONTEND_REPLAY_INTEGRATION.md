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
