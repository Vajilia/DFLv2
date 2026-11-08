Real cgroup-v2 files of `dfl-rehearsal.service` on the Droplet (Ubuntu 24.04, kernel 6.8.0-142, cgroup2fs), read-only, 2026-11-08 ~07:38 UTC, via the Droplet web console.
They were read while the daemon was PAUSED (after the `memory_high` trip), NOT at the trip. At the trip memory.current was 578011136 (monitor evidence); this breakdown is unknown for that moment.
memory.max is the unit's MemoryMax (600 MiB). Typed from the console text; anon + file + kernel == memory.current (checked).
