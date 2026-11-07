# Evidence manifest

Copies of the scratch reports and logs behind `docs/INTEGRATION_CANDIDATE_2026-11-07.md`, preserved at the freeze of candidate `9f2a6e9` so they do not depend on a scratch folder. They are copied unchanged (sha256 below); nothing here was edited. Not included: the 1.1 GB finished season-1 root they ran on (`R1`, regenerable), and 12 MB normalised dumps. `status`: **final** = the run the document cites; **history** = an earlier run kept in the open; **superseded** = a run whose method was flawed, replaced by the one named.

The two compact records one level up (`dead_monitor_restart_2026-11-07.json`, `reveal_real_2026-11-07.json`) are derived from `reports/dead_monitor2.json` and `reports/reveal_real.json`.

| file | status | bytes | sha256 | what |
|---|---|---|---|---|
| `reports/boundary_supported.json` | final | 6867 | `4a7466f44bdeba00773fb147e818a4faf73d11791d82d513b4ca543f18d9bead` | Supported-controls boundary run, 21/21 (section 9) |
| `reports/boundary_supported.log.txt` | final | 2816 | `5b2e2e2cf4846a95e4a05ae8ee7b2373fa2c29d481b56ce611227a459ac388d8` | its log |
| `reports/boundary_accelerated.json` | final | 7628 | `104f50aaee818e20ff4159883c620bcbc8550fdd833d4eb253273f7c77751688` | Accelerated boundary run, 25/25; source of the 425/424 end state (sections 9, 16) |
| `reports/boundary_accelerated.log.txt` | final | 3592 | `43c3c9a34f1f42782ab91a522815b272b35d157abc80149b41aa5b45feb2730e` | its log |
| `reports/reconcile_final.json` | final | 509 | `390f7db70692ebc737bbaf81f730e642784844f23ecadf45e0b8a872177353d1` | index_reconcile on the accelerated run's final files (section 16) |
| `reports/boundary_real_run1.json` | history | 6301 | `58b3d0fca828b80e7a9baf595c8cd36dfe0ac2f546fd5d041360b7414e15fd1a` | First boundary run, failed one check: the monitor-first harness order (sections 9, 15) |
| `reports/boundary_real_run1.log.txt` | history | 2642 | `65f1ac0c078d5a2590cb606cf46ad1a20e3a97f91fc367e4300b490b1508f922` | its log |
| `reports/boundary_real_run2.json` | history | 6003 | `d4cc66e614628cdb442e1995cda6ec21bfa000d506ccd877fd4ac0438b3c9de0` | Second boundary run (superseded by the documented-argument runs) |
| `reports/boundary_real_run2.log.txt` | history | 2513 | `b6d94c38e2f2df22cbc2b572bedb6609b48152992cb455a45a8bd509c53f2aa3` | its log |
| `reports/boundary_real.json` | history | 6931 | `29fe9da3ce10c766a7138adb89388821ddd335f7ba2615d86deedfbde07cd777` | Third boundary run with the first accelerated tail (superseded) |
| `reports/boundary_real.log.txt` | history | 3123 | `2556728f2dd9380cd142902c2018d4f796bfa21be432bcf02daded1bc6a17f79` | its log |
| `reports/startup1.json` | final | 13699 | `16599c4253ea77d5808e734d25423bccf1e7529ea0e14419bbc6a0dacdba215c` | Start orders BEFORE the guard fix: daemon-first after an outage trips (section 15) |
| `reports/startup2.json` | final | 9895 | `2f388afb9afeb37a945828ed31d27868943d73bf68ae94e7493ceebcba2fb1a0` | Start orders AFTER the guard fix (section 15) |
| `reports/dead_monitor.json` | superseded | 26862 | `3995785465d2ae390b0217a7625a46ee843ddfc07ff7beeccbd38e3c9484298d` | First dead-monitor run: its sampler began only when the late monitor started, so it did not see the trip when it happened; kept in the open, replaced by dead_monitor2 |
| `reports/dead_monitor.log.txt` | superseded | 2069 | `b4d13db7dd707a44542772f58ebfeac8e3684ba0e68c2477c8f7ad90b2a24b8e` | its log |
| `reports/dead_monitor2.json` | final | 57436 | `6f1136f26ec32e4b7c80c0f45b5bfbf0cabe9bd6cf2e0d8510b85dca5658a700` | Daemon-restart / dead-monitor scenarios, 18/18 (section 15); compact form in dead_monitor_restart_2026-11-07.json |
| `reports/dead_monitor2.log.txt` | final | 2301 | `5febd8f4b649ff6e2ebc2eb8ab6dc50ad2eeb5ab4b1317e12a4b8f5fece5122d` | its log |
| `reports/reveal_real.json` | final | 6870 | `4375c09e97be9c7acce17b48051e8d5b8d27849a8e50e995037478bd0c941597` | Reveal run, segments A and B with the offline fixture jump, 22/22 (section 17); compact form in reveal_real_2026-11-07.json |
| `reports/reveal_real.log.txt` | final | 5926 | `14a8dd38d35f8b89ca9a51f91b152991c2a0e3e66c056c422cdd39c7b862d314` | its log |
| `reports/oom_check_run1.json` | history | 25511 | `6bceddc8644bbe558b4ec17eb1c87eb5193280872f20d1e1290d64fef5850855` | Induced OOM and SIGKILL scenarios, an early run, before the harness fixes described in section 11 |
| `reports/oom_check_run1.log.txt` | history | 11027 | `7242e24d68342a4da2a8da248f0f897afdc2cf231042569a6ae864fad4b41eb5` | its log |
| `reports/oom_check2.json` | history | 11110 | `7e51d3502c22c3bb19921bdeed83052e20a1c2736740b44808b30df08898219c` | Induced-OOM scenarios, a later run (the scenarios it ran are listed in the file) |
| `reports/oom_check2.log.txt` | history | 4339 | `f71d2dbb879c6c6eac28eb1bb98ec04489e72617afa2609b335032c25c06f6ab` | its log |
| `reports/oom_check3.json` | final | 4908 | `5d11747da4f2145ac59d6885bb5acdf3bfdb85de6a22a81bf08082776dc1bb94` | Induced-OOM scenarios, the last run (the scenarios it ran are listed in the file) |
| `reports/oom_check3.log.txt` | final | 2538 | `9b9890b34db8bf92f877446019c46209fa95782b3074b34f49a8155a4b6c57cb` | its log |
| `reports/measure_baseline.json` | final | 1381 | `dc0ab77450cdf029bdd6807fe48677c2c95ae77597a2cbe41c270366998919fe` | Contention measurement: baseline (section 6) |
| `reports/measure_default.json` | final | 1636 | `9f251167fb63e5f1961d1655d9eaefd20897e24ac9b333909e56a5c7651ef99f` | Contention measurement: default |
| `reports/measure_limits.json` | final | 2020 | `18cadc0644bae3dbc8f825c8df661c5ede89f940c30dc1f4906ca17c8e6ac90c` | Contention measurement: with limits |
| `reports/measure_tuned.json` | final | 2457 | `f1fea8794d0e96448c9dd1a6ab67b791ca8e14b8823cd51f3649627d6242a076` | Contention measurement: tuned |
| `reports/live_drill.log.txt` | final | 1493 | `f76a28be79917d2c54cf9f094c650ea6d80f98921d6930ed45f19d19095a26d9` | Live monitor drill against this candidate's monitor, 17/17 (section 12) |
| `reports/suite_br_real.out` | final | 304 | `432c44ac2a546c91074b2ce3939cc17538545750d67c40b0c0a4b9c12f5a21da` | Real backup-resume suite (section 10) |
| `reports/focused.out` | final | 41305 | `78f2930d466d1e73bdb05ca7b7801cd3e05a7362df68fbc6e0db5730e83f12c4` | Output of the focused test run of the 7ccc490 batch (78 tests) |
