# Evidence for `docs/MEMORY_MONITOR_FIX_2026-11-08.md`

- `failed_rehearsal_2026-11-07/MANIFEST.md` — what the first rehearsal's trip left on the Droplet (hashes, the two numbers the monitor kept, the daemon's cgroup counters read after the trip, production at event 188). Read-only; the files themselves stay on the Droplet.
- `workloads_cgroup_v1/` — `report.json` (all 8 scenarios, both rules, ten sampling phases) and one `trace_<scenario>.json` per scenario (1 Hz: memory.current, pinned, anon, file, dirty, writeback, mapped, shmem, kernel, events, pressure). Real kernel memory cgroups on a cgroup-v1 host; see the report's header and the doc for what that does and does not show. `anon_slow_leak` was re-run once after a wrongly written check (its `note` says so).
- `mutation_check.json` — the eight deliberate breakages of the rule and the tests that caught them.
- `pinned_monitor_patch/` — the memory rule as a patch to the rehearsal's installed commit `d2e66bb`, with the before/after sha256 of `rehearsal_check.py`. **A proposal artifact: not applied to anything.**
- `../../../tests/fixtures/cgroup_v2_droplet_2026-11-08/` — the Droplet daemon's real cgroup-v2 memory files (read while paused, not at the trip).
