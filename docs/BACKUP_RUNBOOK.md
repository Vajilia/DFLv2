# Backup and restore runbook for the broadcast (M8b)

Scope: taking, checking, restoring and recovering from a failed **recovery set** (one league backup, one private-store backup and one public-folder backup, taken and checked as a unit; `docs/PUBLIC_BACKUP.md`). Every command below was executed on a disposable scratch copy; the commands, exit codes and output are in `docs/BACKUP_RUNBOOK_REHEARSAL_2026-11-06.md`, produced by `tools/backup_runbook_rehearsal.py`. **Not yet run on the Droplet or on a real production backup** (the owner has not allowed contact).

Names used here: `ROOT` is the builder root (`ROOT/league`, `ROOT/store`), `PUBLIC` is the folder the daemon publishes, `SET` is the set folder (new, never overwritten), `DEST` is a new folder to restore into. All tools are in `tools/`; run them with the system `python3`.

## 0. Rules that never change

1. **Nothing is deleted.** An unfinished set is moved aside (quarantine), never removed. Old valid sets are never removed by these tools.
2. **A valid set is never touched by a failed attempt.** Failure leaves `SET.partial` (evidence) and no `SET`.
3. **The estimate is not a reservation.** The preflight can be right and the disk can still fill (another process). Step 6 is the answer.
4. **Do not put a set on RAM-backed storage** (tmpfs, ramfs): it vanishes at reboot, and its working index competes with the broadcast for memory. The tools refuse unless told `--allow-ram`; do not.
5. **Keep a set off the host being protected** once the first one has been proven (a set on the same disk is lost with the disk, and costs the broadcast's own free space; `docs/COMPLETION_ROADMAP_2026-11-06.md` section 3a).
6. The **daemon may keep publishing** during `create`; the **builder is excluded** (the root's lock). If the builder is running, `create` refuses (exit 1) or the builder refuses (exit 75): wait for it, do not stop services to make room.  (Builder exclusion is covered by `tests/test_recovery_set.py`; it was not part of the scratch rehearsal.)

## 1. Before you start

```
python3 tools/broadcast_build.py status --root ROOT           # events_built, games_built: write them down
python3 tools/league_store.py digest ROOT/league/league.db    # the league fingerprint: write it down
df -h SET_PARENT                                               # free space where the set will go
```

## 2. Preflight (what the set will need)

```
python3 tools/backup_space.py create --src PUBLIC --out SET_PARENT/probe        # public folder alone
```

`create` of a set runs the same check for all three parts and refuses by itself (exit 1, JSON with `"ok": false` and `problems`). Read the `problems`: *insufficient space* (shows needed and free, per filesystem), *RAM-backed*, *in progress* (another attempt holds the name), *exists* (a set is never overwritten), and existing `.partial` or quarantine folders (listed, **never counted as free**). Fix the cause; do not use `--no-preflight` to get past a refusal.

## 3. Create

```
python3 tools/recovery_set.py create --root ROOT --public PUBLIC --out SET --note "why"
```

Exit 0 and `"ok": true`: the set is committed (written as `SET.partial`, descriptor last, renamed). Exit 1: go to section 6 or 7.

## 4. Verify (always, straight after create, and again before relying on it)

```
python3 tools/recovery_set.py verify SET --deep
```

`--deep` re-hashes every file, re-checks the reference closure and recomputes the set rules S1 to S4 from the component manifests. A `.partial` folder or a folder without `RECOVERY_SET.json` never verifies. Exit 0 required.

## 5. Refusals you should expect (and what to do)

| Output | Meaning | Action |
|---|---|---|
| `RAM-backed` | the destination is tmpfs/ramfs | choose a disk |
| `insufficient space` | the three parts plus index plus margins do not fit on that filesystem | free space, or choose another volume; the report says how much; existing `.partial`s and quarantines are shown, not counted |
| `in progress` | another create/restore holds the name's lock | wait for it; never quarantine a running attempt (the tool refuses) |
| `exists` | `SET` or `SET.partial` already there | use a new name, or quarantine the `.partial` first (section 7) |

## 6. The disk fills during copying

Symptom: `create` exits 1 with "the disk is full ... already valid backups were not touched". State afterwards: `SET.partial` present (copy and `index.sqlite`, kept as evidence), `SET` absent, every earlier valid set unchanged.

1. Do not delete anything to "make room" blindly. Read `df`, find what grew (the daemon's public folder, a core dump, a log).
2. Confirm earlier sets still verify: `python3 tools/recovery_set.py verify OLDSET --deep`.
3. Quarantine the unfinished set (section 7).
4. Retry (section 8) on a volume with room.

For a restore the same applies to `DEST.restoring`: it is kept, `restore` returns `"ok": false, "stage": "copy"`, and the source set is untouched.

## 7. Quarantine

```
python3 tools/recovery_set.py quarantine SET.partial            # moved to SET_PARENT/partial-quarantine/<UTC stamp>-SET.partial
```

A rename within one filesystem (so it works even when the disk is full) plus a small note file; the name stays `.partial` so no tool can mistake it for a finished set. It refuses anything not ending `.partial`/`.restoring` and anything whose name is locked by a running operation. Keep the folder until you know why the run failed; deleting it is the owner's decision, by hand.

Two things seen in the rehearsal: (1) before the fix of 2026-11-06 the full-disk message for a *set* suggested quarantining the inner `SET.partial/store.partial` with `public_backup.py`; it now names the outer `SET.partial` and `recovery_set.py quarantine` (always quarantine the outer folder; finding 1 of the rehearsal record); (2) a `.SET.lock` file stays beside the set; that is intended, leave it.

## 8. Retry

Same command as section 3, on a volume with room, then section 4. A name that was quarantined can be used again.

## 9. Restore (drill, or after a loss)

```
python3 tools/recovery_set.py restore SET DEST        # DEST must not exist; written as DEST.restoring, renamed at the end
python3 tools/broadcast_build.py status --root DEST
python3 tools/league_store.py digest DEST/league/league.db        # equals the number written down in section 1
python3 tools/public_backup.py check --src DEST/public             # reference closure on the restored public folder
```

`DEST` holds `league/`, `store/`, `public/`. To go live again the daemon and builder are pointed at `DEST` by the (not yet written) production procedure; that step is outside this runbook and is not rehearsed here. A restore is first rehearsed on a scratch host, never over a running broadcast.

## 10. What a clean drill looks like

Create exit 0; deep verify exit 0; refusals leave the target empty; a full disk leaves `SET.partial` and no `SET`, and the earlier set is byte-identical; quarantine is a rename; the retry verifies; the restored league digest equals the original, the restored public and store folders are byte-identical, and the builder fires the next event with the same league digest as the original would have.

## 11. Known limits

See `docs/PUBLIC_BACKUP.md` section 8: the lock is advisory and local to one host; the preflight is an estimate; whole-index closure checks scale with the number of listed games; no scheduling, no off-host transfer and no retention exist yet; the Droplet's disk and a cold cache were not measured.
