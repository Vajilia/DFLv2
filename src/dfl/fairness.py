"""Fairness bands (Phase 4): measure a living league against the owner's working limits and give each band an error-bar verdict.

Input is a list of leagues; a league is a list of season records (see `season_record`). Everything is computed from those records, so
the analysis is testable on small made-up leagues and the long simulations only have to produce the records.

A band is judged on the mean over leagues with a two-standard-error interval: PASS if the whole interval sits inside the band, FAIL if the
whole interval sits outside it, otherwise UNCLEAR (more leagues or years needed). `worst_league_titles` is a maximum over leagues and is
judged on that single value. Nothing is tuned from a verdict; trends outside the bands are reported.
"""
import csv
import math
import os
import statistics as S

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BANDS_FILE = os.path.join(ROOT, "data", "fairness", "BANDS.csv")
POINTS_PER_WIN_PCT = 34.0         # half a win per point, 17 games: 1.00 of win % = 34 points
WARMUP = 8
CLOSE = 8


def club_strength(world, cid):
    """Mean hidden rating of the best healthy-or-not starters a club could field (what 'strongest on paper' means here)."""
    from .engine.squad import OFF, DEF
    by = {}
    for pid in world.L.clubs[cid].roster:
        by.setdefault(world.L.players[pid].pos, []).append(world.rating[pid])
    vals = []
    for pos, k in OFF + DEF + (("K", 1), ("P", 1)):
        vals += sorted(by.get(pos, []), reverse=True)[:k]
    return S.mean(vals) if vals else 0.0


def load_bands(path=BANDS_FILE):
    with open(path, encoding="utf-8", newline="") as f:
        return {r["name"]: (float(r["low"]), float(r["high"]), r["meaning"]) for r in csv.DictReader(f)}


def season_record(year, report, strength):
    """Compact, JSON-friendly record of one finished season from a `Universe` report."""
    res = report["result"]
    out = res["outcome"]
    return {
        "year": year,
        "records": {c: [r.w, r.l, r.t, r.pf, r.pa] for c, r in out["records"].items()},
        "orders": {"|".join(dv): list(o) for dv, o in out["division_orders"].items()},
        "exiled": {"|".join(dv): c for dv, c in out["exiled"].items()},
        "seeds": {conf: list(s) for conf, s in out["seeds"].items()},
        "champion": res["coronation_winner"],
        "draft_order": list(res["draft_order"]),
        "margins": [abs(g["home_score"] - g["away_score"]) for g in res["games"]],
        "strength": strength,
    }


def win_pct(rec):
    w, l, t = rec[0], rec[1], rec[2]
    gp = w + l + t
    return (w + 0.5 * t) / gp if gp else 0.0


def corr(x, y):
    if len(x) < 3:
        return float("nan")
    sx, sy = S.pstdev(x), S.pstdev(y)
    if not sx or not sy:
        return float("nan")
    mx, my = S.mean(x), S.mean(y)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (len(x) * sx * sy)


def finish(season):
    """club -> division finish (1-5) for the clubs active that season."""
    return {c: i + 1 for o in season["orders"].values() for i, c in enumerate(o)}


def league_metrics(seasons, warmup=WARMUP):
    """All band metrics for one league (a list of season records in order)."""
    seasons = list(seasons)
    n = len(seasons)
    use = range(warmup, n)
    m = {}
    sds = [S.pstdev([win_pct(r) for r in seasons[i]["records"].values()]) for i in use]
    m["win_pct_sd"] = S.mean(sds)
    xs, ys = [], []
    for i in range(max(warmup, 1), n):
        a, b = seasons[i - 1]["records"], seasons[i]["records"]
        for c in b:
            if c in a:
                xs.append(win_pct(a[c]))
                ys.append(win_pct(b[c]))
    m["year_to_year_corr"] = corr(xs, ys)
    margins = [x for i in use for x in seasons[i]["margins"]]
    m["close_game_share"] = sum(1 for x in margins if x <= CLOSE) / len(margins)
    champs = [s["champion"] for s in seasons]
    reps = [champs[i] == champs[i - 1] for i in range(max(warmup, 1), n)]
    m["repeat_champion_rate"] = sum(reps) / len(reps)
    win = 20
    best = []
    for i in range(warmup, n - win + 1):
        cnt = {}
        for c in champs[i:i + win]:
            cnt[c] = cnt.get(c, 0) + 1
        best.append(max(cnt.values()))
    m["max_titles_in_20"] = S.mean(best) if best else float("nan")
    m["worst_league_titles"] = max(best) if best else float("nan")
    hits = 0
    for i in use:
        st = seasons[i]["strength"]
        act = seasons[i]["records"]
        top = max(act, key=lambda c: (st.get(c, 0), c))
        hits += champs[i] == top
    m["best_team_title_odds"] = hits / len(use)
    # exile: a club exiled after season s0 is the ambassador in s0+1 and returns in s0+2
    fin = [finish(s) for s in seasons]
    ret = []                                           # (s0, club) with the return season inside the data
    for s0 in range(max(0, warmup - 2), n - 2):
        for c in seasons[s0]["exiled"].values():
            ret.append((s0, c))
    first = [(s0, c, fin[s0 + 2].get(c)) for s0, c in ret if s0 + 2 >= warmup and fin[s0 + 2].get(c)]
    if first:
        f = [x[2] for x in first]
        m["returner_avg_finish"] = S.mean(f)
        m["returner_win_div"] = sum(1 for x in f if x == 1) / len(f)
        m["returner_fifth_again"] = sum(1 for x in f if x == 5) / len(f)
        m["returner_playoff_first"] = sum(1 for s0, c, _ in first if any(c in sd for sd in seasons[s0 + 2]["seeds"].values())) / len(first)
    t3 = [(s0, c) for s0, c in ret if s0 + 4 < n and s0 + 2 >= warmup]
    if t3:
        ok = sum(1 for s0, c in t3 if any(fin[s].get(c, 9) <= 3 for s in (s0 + 2, s0 + 3, s0 + 4)))
        m["returner_top3_in_3"] = ok / len(t3)
    d5, d4 = [], []
    for s in range(warmup, n - 2):
        for c, k in fin[s].items():
            w2 = seasons[s + 2]["records"].get(c)
            if w2 is not None and k in (4, 5):
                (d5 if k == 5 else d4).append(win_pct(w2))
    if d5 and d4:
        m["exile_effect"] = (S.mean(d5) - S.mean(d4)) * POINTS_PER_WIN_PCT
    dbl = []
    for s0 in range(max(0, warmup - 2), n - 2):
        for c in seasons[s0]["exiled"].values():
            order = seasons[s0 + 2]["draft_order"]
            if c in order and s0 + 2 >= warmup:
                dbl.append(order.index(c) < 16)
    if dbl:
        m["exile_double_early"] = sum(dbl) / len(dbl)
    clubs = {c for s in seasons for c in s["records"]}
    seq = {c: [(i, fin[i][c]) for i in range(n) if c in fin[i]] for c in clubs}
    tot = stuck = 0
    for c, ss in seq.items():
        for j in range(3, len(ss)):
            if ss[j][0] < warmup:
                continue
            tot += 1
            stuck += all(k >= 4 for _, k in ss[j - 3:j])
    m["stuck_at_bottom"] = stuck / tot if tot else float("nan")
    return m


def verdicts(leagues, bands=None, warmup=WARMUP, z=2.0):
    """Per band: mean over leagues, standard error, band, verdict. `leagues` is a list of season-record lists."""
    bands = bands or load_bands()
    per = [league_metrics(lg, warmup) for lg in leagues]
    rows = []
    for name, (lo, hi, meaning) in bands.items():
        vals = [p[name] for p in per if name in p and not math.isnan(p[name])]
        if not vals:
            rows.append({"band": name, "mean": None, "se": None, "low": lo, "high": hi, "verdict": "NO DATA", "n": 0, "meaning": meaning})
            continue
        mean = S.mean(vals)
        if name == "worst_league_titles":
            mean, se = max(vals), 0.0
        else:
            se = S.stdev(vals) / math.sqrt(len(vals)) if len(vals) > 1 else float("nan")
        a, b = (mean, mean) if math.isnan(se) else (mean - z * se, mean + z * se)
        if lo <= a and b <= hi:
            v = "PASS"
        elif b < lo or a > hi:
            v = "FAIL"
        else:
            v = "UNCLEAR"
        rows.append({"band": name, "mean": mean, "se": se, "low": lo, "high": hi, "verdict": v, "n": len(vals), "meaning": meaning})
    return rows


def format_table(rows):
    out = ["| band | measured (mean ± 2 SE) | allowed | verdict |", "| --- | --- | --- | --- |"]
    for r in rows:
        if r["mean"] is None:
            out.append(f"| {r['band']} | no data | {r['low']} to {r['high']} | NO DATA |")
            continue
        se = "" if r["se"] != r["se"] else f" ± {2 * r['se']:.3f}"
        out.append(f"| {r['band']} | {r['mean']:.3f}{se} | {r['low']} to {r['high']} | {r['verdict']} |")
    return "\n".join(out)
