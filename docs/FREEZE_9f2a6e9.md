# Integration candidate frozen at `9f2a6e9`

**Frozen: 2026-11-07.** Branch `backend/integration-m6-monitor-2026-11-07`, candidate commit **`9f2a6e9c53ec065f1162df3b085806f5d88b1e94`**. Implementation and testing stopped at this commit. Anything after it on the branch is documents and preserved evidence only: `git diff 9f2a6e9 HEAD --stat -- src tools tests deploy data site` must print nothing. Any code change after this point makes a new candidate with a new hash; it is not an edit of this one.

Nothing in the candidate is installed, enabled, deployed or started on a host; no alert is wired; no credential was created. The Droplet and the running rehearsal were not contacted or modified by this work.

## What the freeze covers

The recurring-broadcast tools, unit templates, tests and runbook in `docs/INTEGRATION_CANDIDATE_2026-11-07.md` (read that for results and limits), including the one code fix made during validation: the daemon's monitor guard counts a heartbeat written **before the daemon started** as "none yet" (240 s grace from the daemon's own start), never as a silent monitor, and the grace never overrides a trip latch (`tools/broadcast_run.py`, `Guard.step`).

## Deployment gates (not met; each must be passed before any recurring service is installed)

1. **Actual systemd lifecycle testing, on a host, with the real units.** Nothing here ran under systemd (the test host has none), so the following are **unverified**, not "emulated and fine": the `After=` start order and a simultaneous start at boot; `Type=simple` readiness as the guard sees it; `Restart=on-failure`, `RestartSec`, `StartLimitBurst`/`StartLimitIntervalSec` behaviour, including a restart storm stopping at the limit; `RestartPreventExitStatus` for exit codes 1, 2, 70, 75; `OOMPolicy=stop` with a real `MemoryMax` on cgroup v2 (the OOM tests used cgroup v1 and a hand-made restart); `KillMode=control-group` and `TimeoutStopSec` on stop; `ProtectSystem=strict`, `ReadWritePaths`/`ReadOnlyPaths` with the real users and paths (a missing path fails the unit at start); `EnvironmentFile` rendering; and the monitor's 90% daemon-memory check against the real cgroup v2 files. Kernel OOM kills were tested; systemd's reaction to them was not.
2. **The public ceiling is the owner's number and is unset** (`PUBLIC_CEILING_GB=__CEILING_GB__`), chosen from fresh host measurements with `tools/capacity_calc.py`; the backup location is undecided and decides the capacity (section 13).
3. **The alert channel is decided and tested** (`docs/UNATTENDED_OPERATION.md`); none exists.
4. **An installer that renders and checks the templates** does not exist; the placeholders `__COMMIT__ __ROOT__ __PUBLIC__ __GUARD__ __HOST__ __CEILING_GB__` are unrendered.
5. **A rehearsal result on this code.** The rehearsal that is running uses an older commit (below). Its result is evidence about that commit, not about this candidate.

## The running rehearsal: supplied evidence

Supplied by the owner, not observed by this work: the installed rehearsal is pinned to **`d2e66bb6cf4781aed60436ac55cb5d0e7b940408`** and was started **2026-11-06 at 23:23:54Z**. The host was not contacted to confirm it. From the repository (read-only): `d2e66bb` is the head of `backend/monitor-tmp-race-2026-11-06`, it is **not an ancestor** of this branch, its `tools/rehearsal_check.py` is byte-identical to `5118701`'s, and its daemon guard has the **old** heartbeat rule (a heartbeat file older than 120 s trips the daemon at once, grace applying only when no file exists). The rehearsal therefore starts monitor-first and does not exercise the fix in this candidate. Its results are awaited and are not part of this freeze.

## How the reveal test is to be described

*Two real-time segments with an offline fixture jump between them. Not an uninterrupted run.* Segment A (real time, supported controls): season 1's end, the hold, the hand-over, the supervisor building season 2. The jump: with every process stopped, the clock was rewritten and the real daemon code driven in virtual time from 173,174 s to 181,594 s (about 2 h 20 min of broadcast that never ran in real time). Segment B (real time, supported controls only, nothing edits the clock after it starts): the processes restarted 137 s before season 2's first results reveal and ran 180 s past it. The "accelerated" boundary run is a separate test and is labelled so. What was not shown: any uninterrupted real-time run from the boundary to the first results, any later reveal, the end of season 2, or a second hand-over.

## Evidence

Preserved unchanged in `docs/evidence/reports/` with sha256 hashes in `docs/evidence/MANIFEST.md`; compact records of the dead-monitor restart and the reveal crossing sit beside them. The 1.1 GB finished season-1 root the runs used is not committed (it is regenerable). Every result in the integration document points to one of these files or to a test in `tests/`.
