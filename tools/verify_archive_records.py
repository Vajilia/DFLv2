#!/usr/bin/env python3
"""Record-level check of the permanent archive (read-only; Python standard library alone). Run:  python3 -I -S tools/verify_archive_records.py ARCHIVE [--league LEAGUE_DB] [--deep] [--json]

File size proves nothing about what is stored, so this counts records and compares game ids:

  1. Per season and kind: the number of game records, the number of trace rows, and the sum of the games' declared trace counts.
  2. Every trace row belongs to a stored game; every game's declared `n_traces` equals the trace rows stored for it (SQL join, no payloads).
  3. No duplicates: a game id cannot repeat (PRIMARY KEY) and no two game ids may share a body hash (a record copied under a second id);
     within a season, no two postseason games may have the same kind, teams and score under one event.
  4. For every season with a season summary record (a completed season) the postseason that summary describes is rebuilt as game ids
     (`S001/po:<conf>:<round>:s<hi>v<lo>`, `S001/bowl`, `S001/coronation`) and compared with the ids stored: missing, extra, wrong kind, wrong
     event, wrong teams or score are all reported. For a season still in progress, the postseason games present are listed and not judged.
  5. Every postseason game (playoff, Ambassador Bowl, Coronation) is opened: checksum good, 22-player engine, its play table flags exactly the
     snaps whose trace rows exist, every simulated snap (one carrying a play call) has a trace, every trace container parses and is labelled
     with its own game id and play index. A postseason game with no trace is a failure.
  6. With --league: the league file's journal (event, record count, records digest) against the archive's `batches`: no event missing, none
     differing, and the archive no further ahead than one interrupted commit. Also that batches are the games and records stored for them.
  7. With --deep: re-hash and re-parse every row of the file (what `read_archive.py verify` does); slow on a large archive.

Exit status 1 if anything is wrong. Opens the files read-only; never writes."""
import argparse
import hashlib
import json
import os
import struct
import sqlite3
import sys
import zlib

ROUND_ORDER = ("wild_card", "divisional", "conference")
ROUND_SIZE = {"wild_card": 2, "divisional": 2, "conference": 1}
POST = ("playoff", "bowl", "coronation")
MAGIC = b"DFLT"


def connect(path):
    return sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def batch_digest(items):
    h = hashlib.sha256()
    for k in sorted(items):
        h.update(k.encode() + b"=" + items[k].encode() + b"\n")
    return h.hexdigest()


def trace_head(c):
    if c[:4] != MAGIC:
        raise ValueError("not a trace container")
    version, hl = struct.unpack("<BI", c[4:9])
    if version != 1:
        raise ValueError(f"trace container version {version}")
    head = json.loads(c[9:9 + hl])
    n = head["steps"]
    if len(c) != 9 + hl + n * 22 * 2 * 2 + n * 3 * 2:
        raise ValueError("trace container is the wrong length")
    return head


def expected_postseason(rec):
    """-> {game id: (kind, away, home, away_score, home_score)} from a season summary record (a completed season)."""
    res, season = rec["result"], rec["season"]
    seeds = res["outcome"]["seeds"]
    seed_of = {conf: {c: i + 1 for i, c in enumerate(cl)} for conf, cl in seeds.items()}
    log = res["playoff_log"]                       # [conf, away, home, away score, home score] in play order, round by round
    out, problems = {}, []
    for conf in sorted(seeds):
        mine = [g for g in log if g[0] == conf]
        i = 0
        for rd in ROUND_ORDER:
            chunk, i = mine[i:i + ROUND_SIZE[rd]], i + ROUND_SIZE[rd]
            if len(chunk) != ROUND_SIZE[rd]:
                problems.append(f"S{season:03d} {conf} {rd}: the summary lists {len(chunk)} games, expected {ROUND_SIZE[rd]}")
            for _, away, home, sa, sh in chunk:
                a, h = seed_of[conf][away], seed_of[conf][home]
                out[f"S{season:03d}/po:{conf}:{rd}:s{max(a, h)}v{min(a, h)}"] = ("playoff", away, home, sa, sh)
        if i != len(mine):
            problems.append(f"S{season:03d} {conf}: the summary lists {len(mine)} playoff games, expected {i}")
    b = res.get("bowl")
    if b:
        out[f"S{season:03d}/bowl"] = ("bowl", b["away"], b["home"], b["away_score"], b["home_score"])
    else:
        problems.append(f"S{season:03d}: the summary has no Ambassador Bowl")
    c = res.get("coronation")
    if c:
        out[f"S{season:03d}/coronation"] = ("coronation", c["away"], c["home"], c["away_score"], c["home_score"])
    elif res.get("coronation_winner"):
        out[f"S{season:03d}/coronation"] = ("coronation", None, None, None, None)      # season report without the score: the game is still required
    else:
        problems.append(f"S{season:03d}: the summary has no Coronation")
    return out, problems


def check_game(db, gid, problems):
    """Open one stored postseason game and its traces. -> dict of facts."""
    row = db.execute("SELECT sha256, size, payload, n_traces, engine FROM games WHERE game_id = ?", (gid,)).fetchone()
    facts = {"gid": gid}
    body = zlib.decompress(row[2])
    if sha(body) != row[0] or len(body) != row[1]:
        problems.append(f"{gid}: game record fails its checksum")
        return facts
    g = json.loads(body)
    flagged = {e["i"] for e in g["table"] if e.get("has_trace")}
    simulated = {e["i"] for e in g["table"] if "call" in e}
    stored = {i for (i,) in db.execute("SELECT play_i FROM traces WHERE game_id = ?", (gid,))}
    facts.update(engine=row[4], snaps=len(simulated), flagged=len(flagged), stored=len(stored), declared=row[3], score=g["score"], teams=[g["away"], g["home"]])
    if row[4] != "spatial":
        problems.append(f"{gid}: engine is {row[4]!r}, not the 22-player engine, so it has no spatial traces")
    if not stored:
        problems.append(f"{gid}: no spatial traces are stored")
    if stored != flagged:
        problems.append(f"{gid}: play table flags {len(flagged)} traced snaps, {len(stored)} trace rows stored")
    if row[3] != len(stored):
        problems.append(f"{gid}: declares {row[3]} traces, {len(stored)} stored")
    if row[4] == "spatial" and simulated - stored:
        problems.append(f"{gid}: {len(simulated - stored)} simulated snap(s) have no trace (first: play {min(simulated - stored)})")
    bad = 0
    for i, h, size, z in db.execute("SELECT play_i, sha256, size, payload FROM traces WHERE game_id = ?", (gid,)):
        try:
            c = zlib.decompress(z)
            head = trace_head(c)
            if sha(c) != h or len(c) != size or head["game_id"] != gid or head["play_i"] != i:
                bad += 1
        except Exception:
            bad += 1
    if bad:
        problems.append(f"{gid}: {bad} trace row(s) fail their checksum, do not parse, or are labelled for another snap")
    facts["trace_rows_ok"] = len(stored) - bad
    return facts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("archive")
    ap.add_argument("--league", help="the league file, to compare its journal with the archive's batches")
    ap.add_argument("--deep", action="store_true", help="also re-hash and re-parse every row (read_archive.py verify)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    a = ap.parse_args(argv)
    problems, report = [], {"archive": os.path.abspath(a.archive)}
    db = connect(a.archive)
    try:
        report["integrity_check"] = db.execute("PRAGMA quick_check").fetchall()[0][0]
        if report["integrity_check"] != "ok":
            problems.append("sqlite quick_check failed")
        # 1. counts
        kinds = db.execute("SELECT season, kind, COUNT(*), SUM(n_traces) FROM games GROUP BY season, kind ORDER BY season, kind").fetchall()
        trace_rows = dict(db.execute("SELECT g.season, COUNT(*) FROM traces t JOIN games g ON g.game_id = t.game_id GROUP BY g.season").fetchall())
        report["games"] = {f"S{s:03d}": {} for s, _, _, _ in kinds}
        for s, k, n, t in kinds:
            report["games"][f"S{s:03d}"][k] = {"games": n, "declared_traces": t or 0}
        for s in report["games"]:
            report["games"][s]["_trace_rows"] = trace_rows.get(int(s[1:]), 0)
        report["totals"] = {"games": db.execute("SELECT COUNT(*) FROM games").fetchone()[0], "traces": db.execute("SELECT COUNT(*) FROM traces").fetchone()[0],
                            "records": db.execute("SELECT COUNT(*) FROM records").fetchone()[0], "batches": db.execute("SELECT COUNT(*) FROM batches").fetchone()[0]}
        # 2. traces against games
        orphans = db.execute("SELECT COUNT(*) FROM traces t LEFT JOIN games g ON g.game_id = t.game_id WHERE g.game_id IS NULL").fetchone()[0]
        if orphans:
            problems.append(f"{orphans} trace row(s) belong to no stored game")
        mism = db.execute("SELECT g.game_id, g.n_traces, COUNT(t.play_i) FROM games g LEFT JOIN traces t ON t.game_id = g.game_id "
                          "GROUP BY g.game_id HAVING COUNT(t.play_i) != g.n_traces").fetchall()
        for gid, d, n in mism[:20]:
            problems.append(f"{gid}: declares {d} traces, {n} stored")
        if len(mism) > 20:
            problems.append(f"... and {len(mism) - 20} more games whose trace count differs from the declaration")
        report["games_with_trace_count_mismatch"] = len(mism)
        # 3. duplicates
        dup_body = db.execute("SELECT sha256, COUNT(*), MIN(game_id) FROM games GROUP BY sha256 HAVING COUNT(*) > 1").fetchall()
        for h, n, first in dup_body[:20]:
            problems.append(f"{n} game ids share one body hash (first {first})")
        report["duplicate_bodies"] = len(dup_body)
        norm = db.execute("SELECT LOWER(TRIM(game_id)), COUNT(*) FROM games GROUP BY 1 HAVING COUNT(*) > 1").fetchall()
        for k, n in norm[:20]:
            problems.append(f"game id {k!r} is stored {n} times under spellings that differ only in case or spaces")
        report["duplicate_ids_by_spelling"] = len(norm)
        # 4/5. postseason, season by season
        seasons = sorted({s for s, *_ in kinds} | {r[0] for r in db.execute("SELECT season FROM records WHERE kind = 'season'")})
        report["seasons"] = {}
        for s in seasons:
            name = f"S{s:03d}"
            have = {gid: (k, seq, home, away) for gid, k, seq, home, away in
                    db.execute("SELECT game_id, kind, seq, home, away FROM games WHERE season = ? AND kind IN ('playoff','bowl','coronation')", (s,))}
            row = db.execute("SELECT sha256, size, payload, seq FROM records WHERE rid = ?", (f"{name}/summary",)).fetchone()
            sec = {"summary_record": bool(row), "postseason_games_stored": len(have)}
            if row is None:
                sec["status"] = "in progress (no season summary yet)"
                sec["postseason_ids"] = sorted(have)
                report["seasons"][name] = sec
                continue
            body = zlib.decompress(row[2])
            if sha(body) != row[0] or len(body) != row[1]:
                problems.append(f"{name}: season summary fails its checksum")
                continue
            want, probs = expected_postseason(json.loads(body))
            problems += probs
            missing, extra = sorted(set(want) - set(have)), sorted(set(have) - set(want))
            for gid in missing:
                problems.append(f"{gid}: required by the {name} summary and not in the archive")
            for gid in extra:
                problems.append(f"{gid}: in the archive and not in the {name} summary")
            facts = []
            for gid in sorted(set(want) & set(have)):
                kind, away, home, sa, sh = want[gid]
                hk, seq, hhome, haway = have[gid]
                if hk != kind:
                    problems.append(f"{gid}: stored as kind {hk!r}, expected {kind!r}")
                if away is not None and (haway, hhome) != (away, home):
                    problems.append(f"{gid}: stored teams {haway} at {hhome}, the summary says {away} at {home}")
                f = check_game(db, gid, problems)
                if sa is not None and f.get("score") is not None and list(f["score"]) != [sh, sa]:      # the game record holds [home, away]; the summary lists away, home
                    problems.append(f"{gid}: stored score (home, away) {f['score']}, the summary says home {sh}, away {sa}")
                f["seq"] = seq
                facts.append(f)
            by_kind = {}
            for gid in have:
                by_kind[have[gid][0]] = by_kind.get(have[gid][0], 0) + 1
            sec.update(status="complete", summary_seq=row[3], expected=len(want), stored=len(have), missing=missing, extra=extra, by_kind=by_kind,
                       traces=sum(f.get("stored", 0) for f in facts), events={str(q): sorted(g for g in have if have[g][1] == q) for q in sorted({v[1] for v in have.values()})})
            sec["games"] = [{k: f.get(k) for k in ("gid", "seq", "snaps", "stored", "declared", "score")} for f in facts]
            # repeated pairing within a postseason event (same kind, teams and score) would be one game written twice under two ids
            seen = {}
            for f in facts:
                key = (f["seq"], tuple(f.get("teams") or ()), tuple(f.get("score") or ()))
                if key in seen and key[1]:
                    problems.append(f"{f['gid']} repeats {seen[key]} (same event, teams and score)")
                seen[key] = f["gid"]
            report["seasons"][name] = sec
        # 6. batches against what is stored, and against the league journal
        stored = {}
        for seq, gid, h in db.execute("SELECT seq, game_id, sha256 FROM games"):
            stored.setdefault(seq, {})[gid] = h
        for seq, rid, h in db.execute("SELECT seq, rid, sha256 FROM records"):
            stored.setdefault(seq, {})[rid] = h
        batches = {s: (n, h) for s, n, h in db.execute("SELECT seq, n, sha256 FROM batches")}
        bad = [s for s, items in stored.items() if batches.get(s) != (len(items), batch_digest(items))]
        bad += [s for s in batches if s not in stored]
        for s in bad[:20]:
            problems.append(f"batch for event {s} does not match the rows stored for it")
        report["batches"] = {"events": len(batches), "first": min(batches) if batches else None, "last": max(batches) if batches else None, "mismatched": len(bad)}
        if a.league:
            ldb = connect(a.league)
            try:
                journal = ldb.execute("SELECT seq, n_records, records_digest FROM journal ORDER BY seq").fetchall()
            finally:
                ldb.close()
            head = journal[-1][0] if journal else None
            missing, differ = [], []
            for seq, n, rd in journal:
                got = batches.get(seq)
                if n == 0:
                    if got is not None:
                        differ.append(seq)
                elif got is None:
                    missing.append(seq)
                elif got != (n, rd):
                    differ.append(seq)
            ahead = sorted(s for s in batches if head is not None and s > head)
            report["journal"] = {"events": len(journal), "head": head, "events_with_records": sum(1 for _, n, _ in journal if n), "records_in_journal": sum(n for _, n, _ in journal),
                                 "missing_in_archive": missing, "differing": differ, "archive_ahead": ahead}
            if missing:
                problems.append(f"the journal records events the archive lacks: {missing[:20]}")
            if differ:
                problems.append(f"the archive's records for events {differ[:20]} differ from the journal")
            if len(ahead) > 1:
                problems.append(f"the archive is {len(ahead)} events ahead of the league (an interrupted commit leaves one): {ahead}")
    finally:
        db.close()
    # 7. deep
    if a.deep:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import read_archive
        ddb = read_archive.connect(a.archive)
        try:
            bad = read_archive.verify(ddb)
        finally:
            ddb.close()
        report["deep"] = {"problems": len(bad)}
        problems += [f"deep: {b}" for b in bad[:20]]
    report["problems"] = problems
    if a.json:
        print(json.dumps(report, indent=1, sort_keys=True))
    else:
        print(f"archive {report['archive']}: {report['totals']}  batches {report['batches']}")
        for s, v in sorted(report["games"].items()):
            print(f"  {s}: " + ", ".join(f"{k}={x['games']}" if k != '_trace_rows' else f"trace_rows={x}" for k, x in sorted(v.items())))
        for s, v in sorted(report["seasons"].items()):
            if v["status"] == "complete":
                print(f"  {s} postseason: expected {v['expected']}, stored {v['stored']}, missing {v['missing']}, extra {v['extra']}, kinds {v['by_kind']}, trace rows {v['traces']}")
            else:
                print(f"  {s}: {v['status']}; postseason games stored: {v['postseason_games_stored']}")
        if "journal" in report:
            print(f"  journal vs batches: {report['journal']}")
        print(f"{len(problems)} problem(s)" + ("" if not problems else ":"))
        for p in problems:
            print("  -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
