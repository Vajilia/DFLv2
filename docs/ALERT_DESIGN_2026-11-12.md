# Failure alerts: design and local build (2026-11-12)

**Status: built and tested locally; installed nowhere, enabled nowhere, connected to nothing.** No credential exists or was created; no message was sent. The package is in `alerts/`; its tests are `tests/test_dfl_alert.py` (40), `tests/test_alert_release_config.py` (18) and, for the drill and installer, `tests/test_vtest_v5.py`, `tests/test_vtest_install_alerts.py` (section "Local test results" in `docs/VALIDATION_NEXT_SESSION_2026-11-12.md`). **No test here runs on a real systemd.** What only a real machine can show is listed under "Assumptions". The design answers two findings from the first systemd validation of candidate 4 (`205f891`): `docs/RESTART_BUDGET_FINDING_2026-11-12.md` (the daemon's start budget runs out and nothing tells a person) and the monitor finding in `docs/LAUNCH_ROADMAP_EVIDENCE.md` section 3 (the monitor exits 2 by design when it cannot write its latch, stays stopped, and nothing tells a person).

## What must alert

| Event (incident name) | How it shows in systemd | Consequence if nobody knows |
|---|---|---|
| **Monitor failed** (`monitor_failed`: exit 2 after a failed latch write, or any other failure) or **not running** (`monitor_not_running`: stopped, never started after a reboot; raised after two consecutive polls) | `dfl-broadcast-monitor.service` is `failed` (`RestartPreventExitStatus=1 2` stops the automatic restart) or `inactive` | The monitor's heartbeat goes silent, the daemon pauses itself after its stale limit, and every protection the monitor gives is gone until a person starts it. |
| **Monitor heartbeat stale** (`monitor_heartbeat_stale`, if the heartbeat file is configured) | the unit is `active` but its heartbeat file is older than 120 s | The monitor is hung, not dead: the daemon will pause itself. |
| **Daemon start budget exhausted** (`daemon_start_limit_hit`; `StartLimitBurst=3` in 3600 s, `StartLimitAction=none`) | `failed`, `Result=start-limit-hit` | The daemon is not running and will not be started again by systemd. |
| **Daemon failed** another way (`daemon_failed`) | `failed` | The same, and the reason is in the journal. |
| **Budget nearly used** (`daemon_budget_low`, warning) | `NRestarts` is at `StartLimitBurst - 1` while active | The next crash will be refused. `NRestarts` counts restarts since the unit last ran cleanly, **not** starts inside the 3600 s window, so this is a heuristic: it can warn late or early. V5 records whether it fired and does not judge it. |

## Constraints (unchanged, and now enforced by tests)

1. **Works when the monitor is dead.** The alert path reads only systemd (`systemctl show`) and, optionally, the age of a heartbeat file (stat). It never asks the monitor, the daemon, the guard folder, the store or the public folder anything.
2. **Never recovers.** The alert path never releases a latch, resumes publication, resets a failure, or starts, stops or restarts a unit. In `dfl_alert.py` the only `systemctl` verb is `show`; the only subprocess calls are `systemctl show`, the delivery command and the optional ping; every file write goes through one function that refuses a path outside the alert folder; and the unit that runs it is read-only everywhere else (`ProtectSystem=strict`, its own `StateDirectory`). Tests: `NeverRecovers` (a source scan, a runtime check that guard, store and public folders are byte-identical after every scenario, a refusal test for outside paths). V5 then checks it on a real machine: after the alert went out, the failed unit is **still failed**, with the same invocation, for a hold period.
3. **No secret in the repository.** The delivery command and its credential live in a root-only (0600) environment file on the host; `alerts/alert.env.template` has no values (a test enforces it).
4. **Not part of the candidate.** The candidate's unit files, daemon and monitor stay byte-identical to the pin; the package adds files beside them (below).
5. **Host death is out of reach of any on-host alert.** See "Off-host" below.

## What was built

* `alerts/dfl_alert.py`: `poll`, `event UNIT`, `retry`, `ack ID --by NAME`, `status`, `test-delivery`.
  * **Record first, then deliver.** The incident file is written before the hand-off is tried. With **no delivery command configured** it is still recorded and the run exits non-zero (found while building the package: an earlier version refused to run at all, which would have left a host with an unfinished channel both silent and unrecorded; a test now covers it).
  * **Retry with back-off** (60, 120, 300, 600, 1800, then 3600 s) for a failed hand-off; an incident with a dead channel is never dropped.
  * **Duplicate suppression:** one open incident per `event:unit`; the failure hook and the poll seeing the same condition make one incident and one message. A reminder is sent at most once per hour while nobody has acknowledged it. A cleared condition closes the incident with one recovery note that says nothing was changed by the tool.
  * **One run at a time:** an exclusive lock in the alert folder (the failure hook and the poll timer can start together); a second run waits up to 60 s and otherwise exits 4 having changed nothing.
* `alerts/templates/*.in` and `alerts/release_config.py`: the units, rendered deterministically for the validation names and for the production names (the validation set is the production set with other names: a test proves it).
* `alerts/RELEASE_MANIFEST.json`: SHA-256 of every file of the package and of every rendered unit, for both name sets.

## The release configuration

The drop-ins are not decoration: `OnFailure=` is part of how the production units behave, so the package is **release configuration**, treated like the unit files.

| Part | Where | What it does |
|---|---|---|
| Drop-in for the daemon and one for the monitor | `/etc/systemd/system/<unit>.d/50-dfl-alert.conf` | adds the single directive `OnFailure=<prefix>alert@%n.service` and nothing else (a test forbids `Restart`, `StartLimit*`, `Exec*`, `Memory*`, `OOMPolicy`, `User`, `Environment`) |
| Alert unit | `<prefix>alert@.service` | `Type=oneshot`, runs `dfl_alert.py event %I`; no failure hook of its own (an alert about the alert would loop); `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `NoNewPrivileges`, own `StateDirectory` (0700) |
| Poll service and timer | `<prefix>alert-poll.service` / `.timer` | every minute, `dfl_alert.py poll` (finds what the failure hook cannot: a unit that is simply not running, a stale heartbeat, a failed delivery to retry) |
| Tool and environment file | `dfl_alert.py`; `alert.env` (0600, owner's values) | |

**Exact revisions and effective settings are recorded, not assumed.** `vtest.py install-alerts` first checks the folder against the manifest (a changed, added or missing file, or a rendering that differs, is refused before anything is written), installs the files, starts the poll timer, and writes `alerts_install.json`: the harness git revision, the tree ids of `alerts/` and of the candidate's unit folder, the manifest and tool hashes, the hash of every rendered file, and **read back from systemd** the effective settings (`OnFailure`, `DropInPaths`, `FragmentPath`, `Restart`, `RestartUSec`, `RestartPreventExitStatus`, `StartLimit*`, `MemoryMax/High/SwapMax`, `TasksMax`, `OOMPolicy`, `TimeoutStopUSec`, `User`, `ProtectSystem`, `ExecStart`, and more) with `systemctl cat` of each unit including its drop-ins, for the daemon, the monitor, the alert unit, the poll service and the timer. V5 checks that `OnFailure` and the drop-in are really in force. Production gets the same record from the same commands; that is the file to keep.

**A change to the package names the stages it repeats.** `common.classify_why` classifies anything under `alerts/` as `release_config`, not code under test (the pin gate stays green: the candidate did not change), repeating **V0, V2, V3, V4, V5**, and records why V1 is not named (the package neither reads nor changes the monitor's memory rule and is idle between polls; a change that raises its memory use or poll cost must add V1). Changing the candidate's own unit files instead (for example to move `OnFailure=` into them) **is** a candidate change: new pin, owner approval, the stages `affected` names.

## Delivery channel: a proposal (nothing created, nothing sent)

**Constraints from the owner:** no account or credential is created by Claude; none appears in chat, GitHub, logs or public folders; the credential sits in a root-only file on the host.

**Proposal: two independent channels, because they fail differently.**

1. **A push to a phone** (an app that makes a sound). Candidates: ntfy or Pushover. Both need one secret on the host and a phone app. ntfy needs no account on its public server, but then the topic name is the only protection: anyone who learns it can read what is sent and post to it, and the service is third-party. Pushover needs an account and an application token. The message text is written to contain no secret (unit, event, one-line meaning, host label), so a leaked topic discloses that the league's host had a problem, not how to get in. The delivery command is a small `curl` that reads the message on standard input; its secret goes in a 0600 file passed with `--config`, not on the command line where `ps` shows it. **Not created here.**
2. **An off-host dead-man's switch** (a "healthchecks"-style service): the poll pings it every minute (`DFL_ALERT_PING_CMD`); **the service e-mails when the pings stop**. It is the only option that notices the host dying, the alert unit dying, or the push channel being cut, because it alerts on **silence**. It also gives the e-mail channel a different failure domain (spam folders are the usual failure of e-mail, so e-mail is a second channel, never the only one).

**Challenge to the obvious choices.** An uptime monitor on the public website alone is not enough (see below). A chat webhook alone is one secret and one provider. E-mail alone fails silently. A second host that polls the first is more machinery than the league needs now, and a host the owner cannot reach by SCP or a shell is one more thing to keep alive.

**Before it is real:** the owner chooses; the owner creates the account and the secret and puts the secret in the host's file; `dfl_alert.py test-delivery` sends **one clearly labelled test message** by hand; the owner says whether it arrived and when. Only that last step shows "received" for the channel, and it is the owner's statement, recorded.

## Delivery attempted is not notification received

Every incident carries two separate facts.

* `delivery.state`: `attempted_ok` means the delivery command **exited 0**, which is a hand-off to the channel (an HTTP 200 from a push service says the service accepted it, not that a phone buzzed). `attempted_failed` means it did not; the incident is kept and retried. Every attempt is listed with its time and reason.
* `notification_received`: **empty** until someone runs `dfl_alert.py ack ID --by NAME`, which records who and when. The tool never sets it. Until then an incident is reminded hourly.

**A gap the owner should see:** `ack` is a command on the host, which needs a shell. If the owner cannot or does not want to log in, the hourly reminder simply continues, and "received" is never recorded for production. Decide who acknowledges and how (a shell, a small authenticated page, or accepting reminders as the substitute) before production. V5 acknowledges once **as the harness** and says so in its row: that proves the mechanism, not that a person read anything.

## Website uptime does not prove simulation health

The public site is static files served by a web server. With the daemon failed, the monitor dead and the broadcast clock stopped, the web server keeps answering HTTP 200 for every page, and `status.json` is a file that is simply no longer being rewritten. An external uptime check on the home page therefore stays green while the simulation is down, and a check that only looks for a keyword in `status.json` stays green as long as the stale file still contains it. What can see the simulation is a check on **freshness of content** (a field that must advance, or the file's age, which `docs/UNATTENDED_OPERATION.md` section 7 names as `latest.json` age) plus the host-side checks in this package, which read the units themselves. Even then an external check sees publication only, not latches, heartbeat or restart budget. V5 includes a recorded demonstration (not a pass/fail criterion): while the daemon is failed by start-limit exhaustion, the harness fetches the local site and reads the broadcast clock, and the row says what the site answered and whether the clock moved.

## Assumptions that only a real systemd can settle (V5 judges what it can; the rest is recorded)

* That `OnFailure=` fires for the monitor's exit-2 failure and for `start-limit-hit`, and how often it fires during an automatic restart loop. V5 records the path that found each incident (the failure hook or only the poll) as information; whether it is the hook is **not** a pass criterion, because the poll is the designed safety net and the incident is what matters.
* That the alert unit runs with the monitor dead, the guard folder immutable and the daemon held, and that `systemctl show` works under `ProtectSystem=strict`. V5's exit-2 drill covers the immutable-folder case; if the monitor does not exit 2, that check is **invalid**, not passed.
* That a failed hand-off leaves a record and is retried (V5's delivery-failure drill, using a file channel the harness switches off).
* That the one-minute timer and the lock behave under load (the timer runs through V2's OOM and V3's faults: this is the interference check).
* Whether the key limits in the evidence-transfer method exist as described (`docs/EVIDENCE_TRANSFER_PRIVATE.md`): a separate assumption.
* `systemd-analyze verify` accepts the three units for both name sets (offline, checked locally); the drop-ins need the candidate's unit files beside them and are checked on the machine.

## Pin and validation

* **No new candidate pin** as long as the candidate's unit files stay byte-identical: a test compares the code under test with the pin and the pin gate stays green.
* **V5 (alert drill)** is the new stage (`tools/vtest/stage_v5.py`): installed-package rows; a healthy baseline with the timer running and no false alarm; the monitor stopped; the daemon's budget exhausted by `kill -9`; the monitor's exit 2 (guard folder immutable during a memory trip); the monitor failing while the channel is down. Each is judged on: recorded within the time limit, one message, delivered once, not claimed received, **the failed unit unchanged for the hold after the alert**, and, after the harness recovers the documented way, the incident clears with one recovery note. Evaluators were mutation-checked (10 mutants, all caught).
* **Interference:** the package is installed from the first stage of the next session, so V0 to V4 run with the drop-ins and the poll timer in force (V3's monitor start-limit drill will fire the monitor's drop-in). That is the behavioural reason all of V0 to V4 repeat; see `docs/VALIDATION_NEXT_SESSION_2026-11-12.md`.
* **Delivery to the real channel** is a separate owner drill (one labelled test message); it is not part of the disposable-machine session.

## Decisions for the owner

* The delivery channel, as proposed above or otherwise, and who creates the account and holds the secret.
* Who acknowledges, and how (see the gap above).
* Whether the monitor's exit-2 behaviour should also change (for example a supervised restart that waits for storage to recover, never releasing a latch). That would be a candidate change; it is deliberately not proposed, because alerts remove the silence without altering what the candidate does.
