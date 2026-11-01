#!/usr/bin/env python3
"""Independent check of the permanent archive: expected games are derived from the schedule template, the committed events and the playoff
bracket, NOT from the season summaries, and then compared with the archive, the summaries and the league journal. Read-only.

  python3 -I tools/verify_archive_independent.py ARCHIVE --league LEAGUE_DB [--tables data/season1] [--deep] [--allow-engine play] [--json]

What is derived, and from what (nothing below reads a season summary until step 5):
  1. The journal says which events committed (`week_N`, `tour_round_N`, `wild_card`, ..., `year_close`) and how many archive records each produced.
  2. The Season 1 template (SCHEDULE.csv, TOUR.csv, TEAMS.csv) gives every regular-season and Tour game of Season 1: its id
     (`S001/reg:w01:g01`, `S001/tour:r1:g01`), away and home club. Later seasons use the same template relabelled by that season's division
     state; the state is rolled forward from the PREVIOUS season's standings, which are recomputed here from the ARCHIVED regular-season scores.
  3. The playoff bracket is replayed from those standings: seeds 1-6 per conference, the wild-card pairs, the reseeded divisional round, the
     conference game, each round's participants taken from the winners of the ARCHIVED games before it. That gives the 10 playoff game ids,
     the two finalists, the Coronation's away/home (designation after Week 4) and the Ambassador Bowl's two All-Star sides.
  4. Archive against the derivation, game by game: id present, kind, away and home, the journal event that wrote it, traces. Games the
     derivation does not expect are "extra"; expected games not stored are "missing"; a game id cannot repeat (PRIMARY KEY), and equal bodies
     under two ids are reported.
  5. Only then the season summary: its regular and Tour results, playoff log, champions, Bowl, Coronation, seeds and division orders are
     compared with what was derived from the archive, so an omission or a wrong result shared by archive and summary shows up against the
     template and the bracket.
  6. The journal: for every committed event, `n_records` equals the games the template/bracket say that event plays (plus the season summary at
     `year_close`); no other event carries records; total journal records equal total expected records; hash chain and archive agreement
     (persist.verify with archive=True).
  7. --deep: every archive row is re-hashed and every trace container parsed (archive.verify).

Exit status 1 on any problem."""
import argparse
import json
import os
import random
import sqlite3
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
from dfl import archive as A, league, persist, standings  # noqa: E402

ROUND_EVENT = {"wild_card": "wild_card", "divisional": "divisional", "conference": "conference_championships"}
POST_KEYS = ("wild_card", "divisional", "conference_championships", "ambassador_bowl", "coronation", "year_close")


def ro(path):
    return sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)


def load_game(db, gid):
    sha, size, z = db.execute("SELECT sha256, size, payload FROM games WHERE game_id = ?", (gid,)).fetchone()
    body = zlib.decompress(z)
    if A.sha(body) != sha or len(body) != size:
        raise A.ArchiveError(f"{gid} fails its checksum")
    return json.loads(body)


def segment(journal):
    """-> [{season, events: [(seq, key, n_records)], closed}] by counting year_close events."""
    out, cur, n = [], None, 1
    for seq, key, n_rec in journal:
        if cur is None:
            cur = {"season": n, "events": [], "closed": False}
        cur["events"].append((seq, key, n_rec))
        if key == "year_close":
            cur["closed"] = True
            out.append(cur)
            cur, n = None, n + 1
    if cur is not None:
        out.append(cur)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("archive")
    ap.add_argument("--league", required=True)
    ap.add_argument("--tables", default=os.path.join(ROOT, "data", "season1"))
    ap.add_argument("--deep", action="store_true")
    ap.add_argument("--allow-engine", default="spatial", help="engine every game must have (default spatial); `play` for test leagues without traces")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    t0 = time.time()
    problems, report = [], {"archive": os.path.abspath(a.archive), "league": os.path.abspath(a.league), "seasons": {}}
    ldb = ro(a.league)
    try:
        meta = persist.read_meta(ldb)
        journal = ldb.execute("SELECT seq, key, n_records FROM journal ORDER BY seq").fetchall()
    finally:
        ldb.close()
    seed_str = f"dfl{meta['seed']}"
    report["journal_events"] = len(journal)
    report["head"] = journal[-1][0] if journal else 0
    tpl = league.Template(a.tables)
    state = tpl.initial_state()
    db = ro(a.archive)
    exp_total_records = 0
    try:
        arch = {}                # gid -> (kind, season, seq, home, away, engine, n_traces)
        for gid, season, seq, kind, home, away, engine, nt in db.execute("SELECT game_id, season, seq, kind, home, away, engine, n_traces FROM games"):
            arch[gid] = (kind, season, seq, home, away, engine, nt)
        stored_traces = dict(db.execute("SELECT game_id, COUNT(*) FROM traces GROUP BY game_id").fetchall())
        seen_ids = set()
        facts_cache = {}

        def facts(gid):
            if gid not in facts_cache:
                g = load_game(db, gid)
                flagged = {e["i"] for e in g["table"] if e.get("has_trace")}
                simulated = {e["i"] for e in g["table"] if "call" in e}
                facts_cache[gid] = {"score": g["score"], "engine": g["engine"], "flagged": len(flagged), "simulated": len(simulated),
                                    "untraced": len(simulated - flagged)}
            return facts_cache[gid]

        for seg in segment(journal):
            n = seg["season"]
            name = f"S{n:03d}"
            keys = {key: (seq, n_rec) for seq, key, n_rec in seg["events"]}
            sec = {"closed": seg["closed"], "events": len(seg["events"])}
            report["seasons"][name] = sec
            teams, sched, tour = league.season_tables(tpl, state, n)
            active = {r["team_city"]: (r["conference"], r["division"]) for r in teams if r["club_status"] == "active"}
            ambass = {r["team_city"]: (r["conference"], r["division"]) for r in teams if r["club_status"] == "ambassador"}
            # ---- expected regular season and Tour, per committed event
            expect = {}          # gid -> (kind, away, home, event key)
            per_event = {}       # event key -> expected game count
            for r in sched:
                key = f"week_{int(r['week'])}"
                if key in keys:
                    expect[f"{name}/reg:w{int(r['week']):02d}:g{int(r['game']):02d}"] = ("regular", r["away"], r["home"], key)
                    per_event[key] = per_event.get(key, 0) + 1
            for r in tour:
                key = f"tour_round_{int(r['tour_round'])}"
                if key in keys:
                    expect[f"{name}/tour:r{int(r['tour_round'])}:g{int(r['game']):02d}"] = ("tour", r["away_nominal"], r["home_nominal"], key)
                    per_event[key] = per_event.get(key, 0) + 1
            weeks_template = {f"week_{int(r['week'])}" for r in sched}
            rounds_template = {f"tour_round_{int(r['tour_round'])}" for r in tour}
            for key in keys:
                if (key.startswith("week_") and key not in weeks_template) or (key.startswith("tour_round_") and key not in rounds_template):
                    problems.append(f"{name}: journal event {key} (event {keys[key][0]}) is not in the template schedule")
            if seg["closed"]:
                for key in sorted(weeks_template | rounds_template):
                    if key not in keys:
                        problems.append(f"{name}: scheduled event {key} never committed")
            # ---- recompute standings from the ARCHIVED regular results
            reg_have = {g: v for g, v in arch.items() if v[1] == n and v[0] == "regular"}
            played = []
            for r in sched:
                gid = f"{name}/reg:w{int(r['week']):02d}:g{int(r['game']):02d}"
                if gid in reg_have:
                    s = facts(gid)["score"]
                    played.append(dict(r, away_score=s[1], home_score=s[0]))
            all_reg_done = len(played) == len(sched)
            outcome = None
            if all_reg_done:
                outcome = standings.season_outcome(active, played, f"{seed_str}|season{n}")
            # ---- the bracket, round by round, from archived winners
            derived_log = []
            champs = {}
            if outcome is not None:
                by_seed = {conf: {i + 1: c for i, c in enumerate(cl)} for conf, cl in outcome["seeds"].items()}
                alive = {conf: [1, 2, 3, 4, 5, 6] for conf in by_seed}

                def play(conf, rd, host, away_seed):
                    lo, hi = standings.host(host, away_seed), max(host, away_seed)
                    gid = f"{name}/po:{conf}:{rd}:s{hi}v{lo}"                         # the game id names the round; the journal names the event
                    ev = ROUND_EVENT[rd]
                    expect[gid] = ("playoff", by_seed[conf][hi], by_seed[conf][lo], ev)
                    per_event[ev] = per_event.get(ev, 0) + 1
                    if gid not in arch:
                        return None
                    s = facts(gid)["score"]            # [home, away]
                    derived_log.append([conf, by_seed[conf][hi], by_seed[conf][lo], s[1], s[0]])
                    if s[0] == s[1]:
                        problems.append(f"{gid}: a playoff game ended level {s}")
                        return None
                    return lo if s[0] > s[1] else hi
                for rd in ("wild_card", "divisional", "conference"):
                    if ROUND_EVENT[rd] not in keys:
                        break
                    for conf in sorted(by_seed):
                        if rd == "wild_card":
                            res = [play(conf, rd, a_, b_) for a_, b_ in standings.wild_card_pairs()]
                            alive[conf] = [1, 2] + res if None not in res else None
                        elif alive[conf] is None:
                            continue
                        elif rd == "divisional":
                            res = [play(conf, rd, a_, b_) for a_, b_ in standings.divisional_pairs(alive[conf])]
                            alive[conf] = res if None not in res else None
                        else:
                            w = play(conf, rd, *sorted(alive[conf]))
                            if w is not None:
                                champs[conf] = by_seed[conf][w]
                    if rd == "wild_card" and any(v is None for v in alive.values()):
                        problems.append(f"{name}: the wild-card round is incomplete in the archive; the bracket cannot be followed further")
                        break
                if "ambassador_bowl" in keys:
                    order = ["Western", "Eastern"]
                    if random.Random(f"{seed_str}|bowlhome|{n}").random() < 0.5:
                        order.reverse()
                    expect[f"{name}/bowl"] = ("bowl", f"{order[0]} All-Stars", f"{order[1]} All-Stars", "ambassador_bowl")
                    per_event["ambassador_bowl"] = 1
                if "coronation" in keys:
                    if len(champs) == 2:
                        des = standings.conference_designation(active, played, f"{seed_str}|season{n}")
                        other = next(c for c in champs if c != des["home_conference"])
                        expect[f"{name}/coronation"] = ("coronation", champs[other], champs[des["home_conference"]], "coronation")
                    else:
                        problems.append(f"{name}: the Coronation was committed but the archive does not give two conference champions")
                    per_event["coronation"] = 1
            else:
                if any(k in keys for k in POST_KEYS[:5]):
                    problems.append(f"{name}: postseason events committed but only {len(played)} of {len(sched)} regular-season games are archived")
            # ---- archive against the derivation
            have = {g: v for g, v in arch.items() if v[1] == n}
            missing = sorted(set(expect) - set(have))
            extra = sorted(set(have) - set(expect))
            for gid in missing:
                problems.append(f"{gid}: expected ({expect[gid][3]}) and not in the archive")
            for gid in extra:
                problems.append(f"{gid}: in the archive and not expected for {name}")
            bad_parts = 0
            event_seq = {k: v[0] for k, v in keys.items()}
            for gid in sorted(set(expect) & set(have)):
                kind, away, home, key = expect[gid]
                hk, _, seq, hhome, haway, engine, nt = have[gid]
                if hk != kind:
                    problems.append(f"{gid}: stored kind {hk!r}, derived {kind!r}")
                if (haway, hhome) != (away, home):
                    bad_parts += 1
                    problems.append(f"{gid}: stored away {haway}, home {hhome}; derived away {away}, home {home}")
                if seq != event_seq.get(key):
                    problems.append(f"{gid}: written by event {seq}, but the committed {key} is event {event_seq.get(key)}")
                f = facts(gid)
                if f["engine"] != a.allow_engine:
                    problems.append(f"{gid}: engine {f['engine']!r}, expected {a.allow_engine!r}")
                if a.allow_engine == "spatial":
                    if nt == 0 or stored_traces.get(gid, 0) != nt or f["flagged"] != nt:
                        problems.append(f"{gid}: traces declared {nt}, stored {stored_traces.get(gid, 0)}, flagged in the play table {f['flagged']}")
                    if f["untraced"]:
                        problems.append(f"{gid}: {f['untraced']} simulated snap(s) have no trace")
            # ---- the summary, only now
            srow = db.execute("SELECT sha256, size, payload, seq FROM records WHERE rid = ?", (f"{name}/summary",)).fetchone()
            sec.update(expected_games=len(expect), stored_games=len(have), missing=len(missing), extra=len(extra),
                       kinds={k: sum(1 for v in have.values() if v[0] == k) for k in ("regular", "tour", "playoff", "bowl", "coronation")})
            sec["expected_kinds"] = {k: sum(1 for v in expect.values() if v[0] == k) for k in ("regular", "tour", "playoff", "bowl", "coronation")}
            exp_total_records += len(expect) + (1 if seg["closed"] else 0)
            if seg["closed"] and srow is None:
                problems.append(f"{name}: the season closed (event {keys['year_close'][0]}) and no summary record is stored")
            if not seg["closed"] and srow is not None:
                problems.append(f"{name}: a summary record exists for a season whose year_close has not committed")
            if srow is not None:
                body = zlib.decompress(srow[2])
                if A.sha(body) != srow[0] or len(body) != srow[1]:
                    problems.append(f"{name}: summary fails its checksum")
                else:
                    res = json.loads(body)["result"]
                    if srow[3] != keys.get("year_close", (None,))[0]:
                        problems.append(f"{name}: summary written by event {srow[3]}, year_close is event {keys.get('year_close', (None,))[0]}")
                    sg = {(int(g["week"]), int(g["game"])): (g["away"], g["home"], int(g["away_score"]), int(g["home_score"])) for g in res["games"]}
                    ag = {}
                    for r in sched:
                        gid = f"{name}/reg:w{int(r['week']):02d}:g{int(r['game']):02d}"
                        if gid in have:
                            s = facts(gid)["score"]
                            ag[(int(r["week"]), int(r["game"]))] = (r["away"], r["home"], s[1], s[0])
                    if sg != ag:
                        d = sorted(set(sg) ^ set(ag) | {k for k in set(sg) & set(ag) if sg[k] != ag[k]})
                        problems.append(f"{name}: summary and archive disagree on {len(d)} regular-season game(s), first {d[:3]}")
                    st = {(int(g["tour_round"]), int(g["game"])): (g["away"], g["home"], int(g["away_score"]), int(g["home_score"])) for g in res["tour_games"]}
                    at = {}
                    for r in tour:
                        gid = f"{name}/tour:r{int(r['tour_round'])}:g{int(r['game']):02d}"
                        if gid in have:
                            s = facts(gid)["score"]
                            at[(int(r["tour_round"]), int(r["game"]))] = (r["away_nominal"], r["home_nominal"], s[1], s[0])
                    if st != at:
                        problems.append(f"{name}: summary and archive disagree on the Tour results ({len(set(st) ^ set(at))} missing from one side)")
                    if outcome is not None:
                        want_seeds = {c: list(v) for c, v in outcome["seeds"].items()}
                        if res["outcome"]["seeds"] != want_seeds:
                            problems.append(f"{name}: summary seeds differ from the seeds recomputed from the archived results")
                        want_orders = {"/".join(dv): list(o) for dv, o in outcome["division_orders"].items()}
                        if res["outcome"]["division_orders"] != want_orders:
                            problems.append(f"{name}: summary division orders differ from those recomputed from the archived results")
                    if sorted(map(json.dumps, res["playoff_log"])) != sorted(map(json.dumps, derived_log)):
                        problems.append(f"{name}: summary playoff log ({len(res['playoff_log'])} games) differs from the bracket derived from the archive ({len(derived_log)} games)")
                    if res["champions"] != champs:
                        problems.append(f"{name}: summary champions {res['champions']} differ from derived {champs}")
                    for k, gid in (("bowl", f"{name}/bowl"), ("coronation", f"{name}/coronation")):
                        if gid in have:
                            s = facts(gid)["score"]
                            want = {"away": have[gid][4], "home": have[gid][3], "away_score": s[1], "home_score": s[0]}
                            got = {f: res[k].get(f) for f in want} if res.get(k) else None
                            if got != want:
                                problems.append(f"{gid}: summary says {got}, archive says {want}")
                        elif k in res and res[k]:
                            problems.append(f"{gid}: the summary describes it and the archive does not hold it")
            # ---- journal: n_records per committed event against the derivation
            for seq, key, n_rec in seg["events"]:
                want = per_event.get(key, 0) + (1 if key == "year_close" and seg["closed"] else 0)
                if n_rec != want:
                    problems.append(f"{name}: journal event {seq} ({key}) carries {n_rec} records, the derivation expects {want}")
            # ---- next season's state, from the recomputed standings
            if outcome is not None:
                state = {dv: {"seeds": standings.next_division_state(outcome["division_orders"][dv], state[dv]["amb"])["seeds"],
                              "amb": standings.next_division_state(outcome["division_orders"][dv], state[dv]["amb"])["ambassador"]} for dv in league.DIVISIONS}
            elif seg["closed"]:
                problems.append(f"{name}: closed without a complete regular season; later seasons cannot be derived")
                break
        # ---- totals
        total_games = len(arch)
        in_scope = {g for g, v in arch.items() if v[1] <= len(report["seasons"])}
        report["totals"] = {"archive_games": total_games, "expected_records": exp_total_records, "journal_records": sum(n for _, _, n in journal),
                            "summaries": db.execute("SELECT COUNT(*) FROM records").fetchone()[0], "traces": db.execute("SELECT COUNT(*) FROM traces").fetchone()[0],
                            "snaps_simulated": sum(f["simulated"] for f in facts_cache.values()), "snaps_traced": sum(f["flagged"] for f in facts_cache.values())}
        if report["totals"]["journal_records"] != exp_total_records:
            problems.append(f"journal records total {report['totals']['journal_records']}, derivation expects {exp_total_records}")
        if report["totals"]["archive_games"] + report["totals"]["summaries"] != exp_total_records:
            problems.append(f"archive holds {report['totals']['archive_games']} games and {report['totals']['summaries']} summaries, derivation expects {exp_total_records} records")
        if len(in_scope) != total_games:
            problems.append(f"{total_games - len(in_scope)} archived game(s) belong to seasons the journal has no events for")
        dup = db.execute("SELECT COUNT(*) FROM (SELECT sha256 FROM games GROUP BY sha256 HAVING COUNT(*) > 1)").fetchone()[0]
        if dup:
            problems.append(f"{dup} body hash(es) are shared by more than one game id")
        report["duplicate_bodies"] = dup
        report["games_opened"] = len(facts_cache)
    finally:
        db.close()
    # ---- journal chain, archive agreement, and (deep) every row
    jp = persist.verify(a.league, archive=True)
    report["journal_and_archive_agreement"] = jp or "ok"
    problems += [f"league: {p}" for p in jp]
    if a.deep:
        t = time.time()
        ap_ = A.verify(a.archive)
        report["deep"] = {"problems": len(ap_), "seconds": round(time.time() - t, 1)}
        problems += [f"archive: {p}" for p in ap_[:20]]
    report["seconds"] = round(time.time() - t0, 1)
    report["problems"] = problems
    if a.json:
        print(json.dumps(report, indent=1, sort_keys=True, default=str))
    else:
        print(f"head {report['head']}, journal events {report['journal_events']}, totals {report['totals']}")
        for s, v in sorted(report["seasons"].items()):
            print(f"  {s} ({'closed' if v['closed'] else 'in progress'}, {v['events']} events): expected {v['expected_kinds']}  stored {v['kinds']}  missing {v['missing']} extra {v['extra']}")
        print(f"  journal/archive agreement: {report['journal_and_archive_agreement']}" + (f"; deep: {report['deep']}" if "deep" in report else ""))
        print(f"{len(problems)} problem(s) in {report['seconds']} s" + (":" if problems else ""))
        for p in problems[:60]:
            print("  -", p)
        if len(problems) > 60:
            print(f"  ... and {len(problems) - 60} more")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
