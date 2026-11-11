# Candidate history (the code the validation harness tests)

`tools/vtest/PIN` names the one commit the harness installs and judges. Changing it is a new candidate and needs the owner's approval. No earlier pin, branch or result is deleted or rewritten; this table says what each was and what became of it.

| # | Commit | Branch (remote) | What it was | Result |
|---|---|---|---|---|
| 1 | `bfa632b` | `backend/memory-monitor-candidate-2026-11-08` | First memory-monitor candidate | **Failed V2b** (no latch after an OOM; the restarted daemon carried on). Unchanged. See `docs/OOM_INCIDENT_RECORD.md` |
| 2 | `47ed870` | `backend/memory-monitor-candidate2-2026-11-10` | Records the OOM from `ExecStopPost=` | Known bypass (OOM record, section 3). Superseded by candidate 3. Unchanged |
| 3 | `ecf62ea` | `backend/memory-monitor-candidate3-2026-11-10` | Persistent latch, run marker, start gate, stop hook | **Never run under systemd.** Pinned by the harness through 2026-11-11. Unchanged |
| 4 | `205f891` | `integration/launch-candidate-2026-11-11` | The integrated launch candidate: candidate 3 plus the recovery-run and fresh-rehearsal tooling, the monitor live-drill tool, and the test and CI fixes | **Not yet run under systemd.** Pinned 2026-11-11 on the owner's instruction ("Choose A"). Validated only as far as `docs/LAUNCH_ROADMAP_EVIDENCE.md` says |

**What changed between candidate 3 and candidate 4 in the code under test** (`src/`, `tools/` outside `tools/vtest/`, `deploy/`): twelve files **added**, none modified. The unit files, the daemon and the monitor that V0 to V4 judge are byte-for-byte candidate 3's; the added files are the Spaces recovery-run scripts and their rehearsal rig, `deploy/rehearsal/fresh_run.sh`, and `tools/monitor_live_drill.py`. `tools/vtest/*` (the harness) changed separately, and is tied to each recorded run by the harness revision (the commit the run was made from), not by the pin.

**Why the pin moved at all.** The pin gate treats an unrecognised new path under `src/`, `tools/` or `deploy/` as code ("when unsure, repeat"), and the gate is kept strict: the new files were not exempted and not moved to hide them. The honest way to make the gate green was to name the integrated commit as the candidate.

**Results recorded against earlier candidates stay with those candidates.** Nothing recorded for candidate 1, 2 or 3 is a result for candidate 4. Where candidate 4's code is the same file as candidate 3's, the stage still has to be run against candidate 4 before it counts.
