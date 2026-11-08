# Validation plan: the memory rule on cgroup v2 under real systemd

**Status: a plan and a written, unit-tested harness. Nothing has been run under systemd.** `docs/MEMORY_MONITOR_RULE.md` marks the rule as verified only on synthetic v2 files and on real workloads under cgroup v1 without systemd. This plan closes that gap on a disposable machine with generated data only.
It deploys nothing, touches no existing service, running broadcast or real season data, and sends nothing anywhere.

**Revision note.** This replaces the earlier five-stage plan. It is narrowed to four stages (V1-V4), the load is injected into the daemon's own cgroup, and the estimate-error measurement is removed (section 2).

## 0. Pins, entry, exit, stop

* **Candidate pin:** `bfa632b0e8961d9d576bdcded86abea90a3cc675`, recorded in `tools/vtest/PIN`. The harness extracts exactly that tree with `git archive` and verifies it blob by blob, so harness commits made on top do not change the code under test.
* **Entry:** the owner approves the plan, names the machine, and approves any cost (section 6). Nothing starts before that.
* **Stop on failure:** the run stops at the first stage that does not pass. Nothing is cleaned up; evidence is preserved; a later run is refused until the stop is acknowledged with a recorded reason. `invalid` (the workload did not do what the test needs) is not a pass.
* **Exit:** a report (section 8) with each check pass, fail, invalid or not run, the raw samples, and a manifest of hashes.
* **Abort at once:** any write outside the harness paths, free disk under 4 GiB, or any route to another host.

## 1. The four stages

| Stage | Question | Checks (summary) |
|---|---|---|
| **V1** | Does real cache growth cause a false trip? | Clean file cache fills the daemon's cgroup to its limit for at least 30 min (invalid if it does not). No trip, no `oom_kill`, no restart, loader still reading; the kernel was demonstrably reclaiming; pressure stays low; heartbeat breakdown present and consistent. |
| **V2a** | Does sustained anonymous memory trip as designed? | Anonymous load to 93 % of `MemoryMax`, held. Latch kind `memory_high` from the monitor 58-90 s after the 90 % line is crossed, before any kill; evidence has the breakdown; daemon paused and its clock stopped. |
| **V2b** | What happens on an OOM? | Load to 3x the limit. Kernel kill seen; systemd recognises the OOM; with `OOMPolicy=stop` no process of the unit survives; the unit restarts as configured; public files intact; clock continues. **Required: a persistent incident record and a hold.** After the restart a latch with new evidence exists, the daemon stays paused, the clock does not advance and the latch is unchanged over 15 s. An OOM that leaves no latch **fails V2**: a quick restart must not bypass the protection. **The victim is reported** (read from the kernel log: daemon, injected load, other, or unknown). Unless the daemon itself was killed, `V2b.daemon_oom_exercised` is **not_run (incomplete), never pass**. |
| **V3** | Monitor dies or hangs | `kill -9` (V3a): restarts in time, no trip. Stop (V3b) and `SIGSTOP` hang (V3c): the daemon trips itself (`monitor_silent`, source daemon) within the stale limit and pauses safely; recoverable by the documented `release` and `resume`. Repeated monitor OOM until the start limit leaves the unit failed (V3d): the daemon trips. |
| **V4** | Does a latch survive restarts? | With an existing latch: daemon restart, monitor restart, both in each order, `kill -9` of each: latch byte-identical, daemon still paused, clock stopped, public files unchanged, `resume` refused. Refusals (`resume` rc 3, short reason rc 2, stale heartbeat rc 4). Documented `release` keeps the latch in `evidence/released/`, deletes nothing, and `resume` then works. A second trip makes a second latch. Optional reboot phase. |

Step ids in the harness (`V1.load`, `V2b.overrun`, ...) are the ids in `tools/vtest/stage_*.py`; a test checks the code and these steps do not drift apart. Exact settings are printed by `vtest.py plan`.

## 2. What V1 can and cannot measure

The kernel offers no exact "non-reclaimable footprint". The monitor's `pinned` is an **estimate** of it, built from `memory.stat`. Therefore:

* **Not claimed:** any estimation-error figure, or a ratio "estimate / truth". There is no truth value to compute it against.
* **Measured:** (a) a *controlled input*: the loader logs the bytes it read, the daemon's own anonymous memory is known, so the estimate must stay consistent with those (a consistency bound with a stated margin; it can fail, it cannot prove accuracy); (b) *reclaim activity*: `memory.events max`, `pgscan`, `pgsteal` rise, so the cache was really being reclaimed at the limit; (c) *reclaim probes*: `memory.reclaim` asks the kernel to give memory back (kernel 5.19+); what it returns is lower-bound evidence of reclaimable memory; (d) *pressure counters*: PSI stays under a stated ceiling; (e) *survival*: no trip, no kill, no restart, the workload still progressing.
* A pass says: on this kernel, with this workload, the old false-trip pattern did not recur and the estimate stayed consistent with a known input. It does not say the estimate is accurate in general. The original trip remains classified as "likely cache-related, not proven false".

## 3. Method

* **Injection into the daemon's cgroup.** Loaders join the daemon's cgroup through `cgroup.procs`, so the limit, OOM killer, `OOMPolicy` and the monitor's `--cgroup` path are the real ones. Limits and every restart and OOM setting of the units are unchanged from the templates (checked by `invariants`); only names, paths, loopback URL, user and the disk floor differ, and each difference is printed and recorded.
* **Sampler:** 1 Hz JSONL of `memory.current/max/stat/events/pressure` and the monitor's own estimate, using the pinned monitor's reader. It tolerates the cgroup disappearing.
* **Loaders** log ground truth every second (bytes read, bytes allocated, resident size).
* **Fixture:** a generated league and season (the repository's test generator), a loopback static server, no real data.
* **Smoke run first** (`run --smoke`): short settings, non-qualifying, to shake out harness faults before the real run. Its results are never reported as acceptance.

## 4. Known limits

* **OOM victim and incompleteness.** The kernel kills the largest process in the cgroup, which is normally the injected load, not the daemon. A load-only kill makes systemd stop and restart the unit (`OOMPolicy=stop`) but does not show that the daemon's own death is recovered from. V2b reports the victim from the kernel log; if the daemon was not killed, or the log cannot be read, the daemon-OOM check stays **incomplete** and V2 is not a pass. Exercising a daemon kill (for example by making the load unkillable so the daemon is the only candidate) is a harness step **not yet written**; until then daemon-OOM validation cannot be completed. An incomplete V2 stops a full run (anything but pass stops it); later stages can be run with `--stages V3,V4 --acknowledge-stop '<reason>'`.
* **Missing incident record is a defect, not a finding.** If V2b fails `incident_record_and_hold`, that is a defect of the candidate: it is fixed in a new, separately tested candidate with its own pin and a full re-run. The harness proposes no runtime fix and contains none. Whether the monitor misses the kill because the cgroup is recreated between its 10 s samples is a hypothesis the stage tests, not a known fact.
* Injected load stands in for a leak; it does not reproduce the daemon's own allocation behaviour.
* The harness has not run under systemd. Results are valid only for the machine, kernel and systemd version recorded.

## 5. Change impact: what to repeat

Documentation-only changes invalidate nothing. Otherwise (`vtest.py affected --recorded <rev>`):

| Change | Repeat |
|---|---|
| Code under test (`src/`, `tools/` outside `tools/vtest/`, unit templates under `deploy/`, `tests/test_season.py`, fixtures) | All stages, **and it is a new candidate** with a new pin |
| A stage file | that stage only |
| Shared harness modules or `PIN` | all stages |
| `docs/`, other tests, `vtest.py`, `preflight.py`, README | none |

## 6. Environment and minimum resources

No suitable environment exists in the session that prepared this plan: it runs under cgroup v1 with no systemd as PID 1 and no way to start a VM, so **the harness could not be executed here**. A disposable machine must be provided.

Minimum: 1 vCPU, 1 GiB RAM with swap off, 5 GiB free disk, kernel >= 5.4 (>= 5.19 for reclaim probes), systemd >= 243 as PID 1, writable cgroup v2 with the memory controller, `python3`, `git`, root; created for this test and not used for anything else. A cloud VM of this size costs money and needs the owner's explicit approval; a local VM or spare machine does not. The operator creates `/etc/dfl-vtest-disposable` by hand; without it the harness refuses to run. `vtest.py preflight` (read-only) reports READY or exactly what is missing.

## 7. Isolation, rollback, evidence

* No real unit, path or data: preflight refuses a machine with real DFL units or paths. Writes are confined to `/srv/dfl-vtest`, `/opt/dfl-vtest-src`, `/etc/dfl-vtest` and `dfl-vtest-*` unit files; `systemctl` may change only `dfl-vtest-*` units; cgroup writes are limited to `memory.reclaim` and `cgroup.procs` of those units. Units are never enabled; nothing is installed to start at boot.
* Loopback only; no mail, alert or message of any kind.
* Nothing is deleted automatically. `teardown` needs `--yes` and keeps evidence unless told otherwise; the machine's own destruction is a manual step after the report is accepted.
* Each stage writes `result.json` and a `MANIFEST.sha256`. Evidence is reviewed before any of it leaves the machine, and is not published.

## 8. Report

The revision tested and the pin; the machine facts (`uname -r`, systemd version, controllers); every check with its status and deciding file; V1 stated as in section 2, with no error figure; trip times against the 58-90 s window; the V2b incident-record result and the reported OOM victim, and whether daemon-OOM validation is complete or incomplete; every deviation; and a recommendation. Delivered to the owner, who decides.

## 9. Not covered

Deployment itself; the real units' install, enable and boot ordering (a separate gate); a full-length broadcast; the engine's behaviour; public-folder capacity beyond the fixture; any change to limits.
