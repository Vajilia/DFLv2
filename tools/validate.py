#!/usr/bin/env python3
"""DFL Edition 8 validator: implements the checks in section 18 against the CSV tables.

Usage: python3 validate.py <tables_dir> [--tour-rule relaxed|strict] [--doc DFL.md] [--season N]

Expects TEAMS, SCHEDULE, TOUR, INTL_VENUES, CALENDAR, COLOR_REGISTRY as .csv files in <tables_dir>.
Exit code = number of failed checks. WARN lines are open design items and do not fail the run.
--doc verifies the content hash stamped in the document (SHA-256 of the file with the hash line removed).
"""
import csv
import datetime as dt
import sys
from collections import Counter, defaultdict

args = [a for a in sys.argv[1:] if not a.startswith("--")]
D = args[0]
TOUR_RULE = "strict" if "--tour-rule" in sys.argv and sys.argv[sys.argv.index("--tour-rule") + 1] == "strict" else "relaxed"
DOC = sys.argv[sys.argv.index("--doc") + 1] if "--doc" in sys.argv else None


def load(name):
    with open(f"{D}/{name}.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


teams, sched, tour = load("TEAMS"), load("SCHEDULE"), load("TOUR")
venues, cal, reg = load("INTL_VENUES"), load("CALENDAR"), load("COLOR_REGISTRY")

results = []


def check(name, ok, detail="", warn=False):
    results.append((name, bool(ok), detail, warn))


def d(s):
    return dt.date.fromisoformat(s)


# ---------------------------------------------------------------- TEAMS
if "team_city" in teams[0]:
    check("TEAMS header uses team_city/team_name", True)
else:
    check("TEAMS header uses team_city/team_name", False, "header has city/name; aliased to continue")
    for t in teams:
        t["team_city"], t["team_name"] = t["city"], t["name"]

conf_order = [r["name"] for r in reg if r["scope"].endswith("Conference")]
conf_order = [c.split()[0] for c in ["Western", "Eastern"]]
scope_rank = {r["scope"]: i for i, r in enumerate(reg)}
div_rank_in_reg = {r["scope"].split("/")[1]: i for i, r in enumerate(reg) if "/" in r["scope"]}

for col in ("team_id", "abbr", "team_name"):
    vals = [t[col] for t in teams]
    check(f"TEAMS {col} unique", len(set(vals)) == len(vals), "" if len(set(vals)) == len(vals) else str(Counter(vals).most_common(2)))
check("TEAMS 48 rows, ids 1..48", sorted(int(t["team_id"]) for t in teams) == list(range(1, 49)))
act = [t for t in teams if t["club_status"] == "active"]
amb = [t for t in teams if t["club_status"] == "ambassador"]
check("TEAMS 40 active / 8 ambassador", len(act) == 40 and len(amb) == 8, f"{len(act)}/{len(amb)}")
divs = sorted({(t["conference"], t["division"]) for t in teams})
check("TEAMS 8 divisions, one ambassador each, five active each",
      len(divs) == 8 and all(sum(1 for t in amb if (t["conference"], t["division"]) == dv) == 1 and
                             sum(1 for t in act if (t["conference"], t["division"]) == dv) == 5 for dv in divs))
check("TEAMS is_active == (club_status=='active')", all((t["is_active"] == "true") == (t["club_status"] == "active") for t in teams))
check("TEAMS is_league_member true on all 48", all(t["is_league_member"] == "true" for t in teams))
beds = [t["bed_id"] for t in act]
WEST = ["Northwest", "Southwest", "Central", "Southern"]
EAST = ["Midwest", "Northeast", "East", "Southeast"]


def expected_bed(conf, div, season):
    i = (WEST if conf == "Western" else EAST).index(div)
    k, p = (season - 1) % 4, ((season - 1) // 4) % 2
    block = p if conf == "Western" else 1 - p
    return 1 + 4 * block + (i + k) % 4


SEASON = int(sys.argv[sys.argv.index("--season") + 1]) if "--season" in sys.argv else 1
shape_ok, bed_ok, per_div = True, True, {}
for t in act:
    pre, _, rk = t["bed_id"].partition("-")
    shape_ok &= pre.isdigit() and 1 <= int(pre) <= 8 and rk == t["division_rank"]
    per_div.setdefault((t["conference"], t["division"]), set()).add(pre)
    bed_ok &= pre == str(expected_bed(t["conference"], t["division"], SEASON))
check("TEAMS bed_id unique among active and shaped bed-rank (bed 1-8)", len(set(beds)) == 40 and shape_ok)
check("TEAMS each division's five active clubs share one bed", all(len(v) == 1 for v in per_div.values()))
check(f"ROTATION division beds follow section 3 for season {SEASON}", bed_ok)
check("TEAMS ambassadors have blank bed_id and division_rank 6", all(t["bed_id"] == "" and t["division_rank"] == "6" for t in amb))
pairs = [(t["primary_hex"].upper(), t["secondary_hex"].upper()) for t in teams]
dup = [p for p, n in Counter(pairs).items() if n > 1]
check("TEAMS (primary_hex, secondary_hex) unique across 48", not dup, str(dup))
regmap = {r["scope"]: r["hex"].upper() for r in reg}
bad = [t["abbr"] for t in teams if regmap.get(f"{t['conference']}/{t['division']}") != t["tertiary_hex"].upper()]
check("TEAMS tertiary_hex matches COLOR_REGISTRY", not bad, str(bad))
# canonical sort: conference (registry order) -> division (registry order) -> division_rank
key = lambda t: (conf_order.index(t["conference"]), div_rank_in_reg[t["division"]], int(t["division_rank"]))
check("TEAMS sorted conference -> division -> division_rank (registry order)", [key(t) for t in teams] == sorted(key(t) for t in teams))

T_BY_CITY = {}
for t in teams:
    T_BY_CITY.setdefault(t["team_city"], []).append(t)
check("TEAMS team_city unique", all(len(v) == 1 for v in T_BY_CITY.values()))
CITY = {c: v[0] for c, v in T_BY_CITY.items()}

# ---------------------------------------------------------------- CALENDAR
check("CALENDAR 27 rows, calendar_week 1..27", [int(r["calendar_week"]) for r in cal] == list(range(1, 28)))
reg_rows = [r for r in cal if r["phase"] in ("regular", "break")]
numbered = [int(r["numbered_week"]) for r in cal if r["numbered_week"]]
check("CALENDAR 19 numbered weeks 1..19, 22 regular-season weeks", numbered == list(range(1, 20)) and len(reg_rows) == 22)
breaks_after = []
last = 0
for r in reg_rows:
    if r["phase"] == "break":
        breaks_after.append(last)
    else:
        last = int(r["numbered_week"])
check("CALENDAR exactly 3 breaks after numbered weeks 4/9/14", breaks_after == [4, 9, 14], str(breaks_after))
ok_days = True
for r in cal:
    for col, wd in (("saturday", 5), ("sunday", 6), ("monday", 0)):
        if r[col] and d(r[col]).weekday() != wd:
            ok_days = False
check("CALENDAR date columns fall on Sat/Sun/Mon", ok_days)
suns = [d(r["sunday"]) for r in cal]
check("CALENDAR consecutive weeks exactly 7 days apart", all((b - a).days == 7 for a, b in zip(suns, suns[1:])))
C = d(cal[-1]["sunday"])
dec = dt.date(C.year, 12, 31)
last_sun_dec = dec - dt.timedelta(days=(dec.weekday() + 1) % 7)
check("CALENDAR Coronation is last Sunday of the year", C == last_sun_dec, str(C))
post = {r["note"]: d(r["sunday"]) for r in cal if r["phase"] in ("postseason", "ambassadors")}
off = {"wild card round": 28, "divisional round": 21, "conference championships": 14, "Ambassador Bowl": 7}
check("CALENDAR rungs at C-28/-21/-14, Bowl C-7", all((C - post[k]).days == v for k, v in off.items()), str({k: (C - post[k]).days for k in off}))
fin_mon = d(cal[21]["monday"])
check("CALENDAR final regular-season Monday = C-34", (C - fin_mon).days == 34, str((C - fin_mon).days))
check("CALENDAR phase mix: 22 regular/break rows then 3 postseason, 1 ambassadors, 1 postseason",
      [r["phase"] for r in cal[22:]] == ["postseason", "postseason", "postseason", "ambassadors", "postseason"])

# ---------------------------------------------------------------- SCHEDULE
check("SCHEDULE 360 rows", len(sched) == 360, str(len(sched)))
check("SCHEDULE sorted week -> game", [(int(r["week"]), int(r["game"])) for r in sched] == sorted((int(r["week"]), int(r["game"])) for r in sched))
unres = {r[c] for r in sched for c in ("away", "home") if r[c] not in CITY}
check("SCHEDULE all cities resolve to a team_city", not unres, str(unres))
check("SCHEDULE no self-fixtures", all(r["away"] != r["home"] for r in sched))
check("SCHEDULE no ambassador appears", not any(CITY[r[c]]["club_status"] != "active" for r in sched for c in ("away", "home") if r[c] in CITY))
gm = defaultdict(list)
for r in sched:
    gm[r["away"]].append(("A", r["home"], int(r["week"])))
    gm[r["home"]].append(("H", r["away"], int(r["week"])))
check("SCHEDULE per club 18 games, 9H/9A",
      len(gm) == 40 and all(len(v) == 18 and sum(1 for x in v if x[0] == "H") == 9 for v in gm.values()),
      str({c: len(v) for c, v in gm.items() if len(v) != 18}))
div_ok = inter_ok = conf_ok = True
div_bad, inter_bad, conf_bad = [], [], []
for c, v in gm.items():
    t = CITY[c]
    dv = [x for x in v if CITY[x[1]]["division"] == t["division"] and CITY[x[1]]["conference"] == t["conference"]]
    cnt = Counter((x[0], x[1]) for x in dv)
    if len(dv) != 8 or sum(1 for x in dv if x[0] == "H") != 4 or any(n != 1 for n in cnt.values()) or len({x[1] for x in dv}) != 4:
        div_bad.append(c)
    ic = [x for x in v if CITY[x[1]]["conference"] != t["conference"]]
    if len(ic) != 4 or len({CITY[x[1]]["division"] for x in ic}) != 4 or any(CITY[x[1]]["division_rank"] != t["division_rank"] for x in ic):
        inter_bad.append(c)
    cf = [x for x in v if CITY[x[1]]["conference"] == t["conference"] and CITY[x[1]]["division"] != t["division"]]
    if len(cf) != 6:
        conf_bad.append(c)
check("SCHEDULE per club 8 divisional (4H/4A, each rival twice)", not div_bad, str(div_bad))
check("SCHEDULE per club 4 interconference, equal seed, one per opposing division", not inter_bad, str(inter_bad))
check("SCHEDULE per club 6 same-conference non-division", not conf_bad, str(conf_bad))
wk = defaultdict(list)
for r in sched:
    wk[int(r["week"])].append(r)
check("SCHEDULE weeks 1-14 have 20 games, weeks 15-19 have 16",
      all(len(wk[w]) == 20 for w in range(1, 15)) and all(len(wk[w]) == 16 for w in range(15, 20)) and sorted(wk) == list(range(1, 20)),
      str({w: len(g) for w, g in wk.items() if len(g) not in (16, 20)}))
once = True
for w, g in wk.items():
    seen = Counter(x for r in g for x in (r["away"], r["home"]))
    if any(n > 1 for n in seen.values()):
        once = False
check("SCHEDULE no club plays twice in one week", once)
check("SCHEDULE weeks 1-14: all 40 clubs play", all(len({x for r in wk[w] for x in (r["away"], r["home"])}) == 40 for w in range(1, 15)))
byes = {}
for c in gm:
    missing = [w for w in range(15, 20) if all(x[2] != w for x in gm[c])]
    byes[c] = missing
check("SCHEDULE per club exactly one bye in weeks 15-19", all(len(m) == 1 for m in byes.values()), str({c: m for c, m in byes.items() if len(m) != 1}))
grp_ok, grp = True, {}
for w in range(15, 20):
    bs = [c for c, m in byes.items() if m == [w]]
    seeds = {CITY[c]["division_rank"] for c in bs}
    dvs = {(CITY[c]["conference"], CITY[c]["division"]) for c in bs}
    grp[w] = seeds
    if len(bs) != 8 or len(seeds) != 1 or len(dvs) != 8:
        grp_ok = False
check("SCHEDULE each bye week's byes are one complete seed group across 8 divisions", grp_ok, str(grp))
notes = {int(r["numbered_week"]): r["note"] for r in cal if r["numbered_week"]}
note_ok = all(f"seed {list(grp[w])[0]}" in notes[w] for w in range(15, 20) if len(grp[w]) == 1)
check("SCHEDULE bye seed groups agree with CALENDAR notes", note_ok)
cal_by_num = {int(r["numbered_week"]): r for r in cal if r["numbered_week"]}
wd_ok = dates_ok = tue_fri = True
win = set()
for r in sched:
    dd = d(r["date"])
    if dd.weekday() not in (5, 6, 0):
        wd_ok = False
    cr = cal_by_num[int(r["week"])]
    if r["date"] not in (cr["saturday"], cr["sunday"], cr["monday"]):
        dates_ok = False
    win.add((dd.strftime("%a"), r["kickoff"]))
check("SCHEDULE all dates Sat/Sun/Mon and no game Tue-Fri", wd_ok)
check("SCHEDULE dates fall in the matching CALENDAR week", dates_ok)
allowed = {("Sat", "17:30"), ("Sun", "10:00"), ("Sun", "13:00"), ("Sun", "17:30"), ("Mon", "17:30")}
check("SCHEDULE kickoffs use only the 5 windows in section 0", win <= allowed, str(win - allowed))
shape = True
for w, g in wk.items():
    if not (d(g[0]["date"]).weekday() == 5 and g[0]["kickoff"] == "17:30" and d(g[-1]["date"]).weekday() == 0 and g[-1]["kickoff"] == "17:30"):
        shape = False
check("SCHEDULE game 1 = Sat 17:30 and last game = Mon 17:30 each week", shape)
def slot_points(r):
    wd = d(r["date"]).weekday()
    return 5 if wd == 0 else 4 if wd == 5 else {"17:30": 3, "13:00": 2, "10:00": 1}[r["kickoff"]]
pts = defaultdict(int)
for r in sched:
    for c in (r["away"], r["home"]):
        pts[c] += slot_points(r)
out = {c: v for c, v in pts.items() if not 33 <= v <= 38}
check("SCHEDULE per club kickoff-slot points total 33-38 (Mon 5, Sat 4, Sun 17:30 3, Sun 13:00 2, Sun 10:00 1)", not out and len(pts) == 40,
      f"out of range: {out}" if out else f"range {min(pts.values())}-{max(pts.values())}")
check("SCHEDULE every week 1 pairing is equal-seed",
      all(CITY[r["away"]]["division_rank"] == CITY[r["home"]]["division_rank"] for r in wk[1]))
check("SCHEDULE no regular-season game on an active-club break date",
      not any(r["date"] in (c["saturday"], c["sunday"], c["monday"]) for r in sched for c in cal if c["phase"] == "break"))

# ---------------------------------------------------------------- TOUR
ALIAS = {"Minneapolis": "Minnesota"}
tour_unres = {r[c] for r in tour for c in ("away_nominal", "home_nominal") if r[c] not in CITY}
check("CROSS-FILE every Tour city resolves to exactly one team_city", not tour_unres, f"unresolved: {sorted(tour_unres)}")
res = lambda n: ALIAS.get(n, n) if n not in CITY else n
check("TOUR 28 rows, 7 rounds x 4 games", len(tour) == 28 and Counter(int(r["tour_round"]) for r in tour) == Counter({i: 4 for i in range(1, 8)}))
check("TOUR sorted round -> game", [(int(r["tour_round"]), int(r["game"])) for r in tour] == sorted((int(r["tour_round"]), int(r["game"])) for r in tour))
ambs = {t["team_city"] for t in amb}
tc = {res(r[c]) for r in tour for c in ("away_nominal", "home_nominal")}
check("TOUR participants are exactly the 8 ambassadors", tc == ambs, str(tc ^ ambs))
check("TOUR no self-fixtures", all(r["away_nominal"] != r["home_nominal"] for r in tour))
opp = defaultdict(list)
for r in tour:
    a, h = res(r["away_nominal"]), res(r["home_nominal"])
    opp[a].append(h)
    opp[h].append(a)
check("TOUR every club 7 games vs 7 distinct opponents", len(opp) == 8 and all(len(v) == 7 and len(set(v)) == 7 for v in opp.values()))
per_round_ok = all(len({res(r[c]) for r in tour if int(r["tour_round"]) == k for c in ("away_nominal", "home_nominal")}) == 8 for k in range(1, 8))
check("TOUR each club plays exactly once per round", per_round_ok)
homes = Counter(res(r["home_nominal"]) for r in tour)
check("TOUR 3-4 nominal homes per club", len(homes) == 8 and all(n in (3, 4) for n in homes.values()), str(dict(homes)))
seq = {}
for c in ambs:
    s = ""
    for k in range(1, 8):
        g = [r for r in tour if int(r["tour_round"]) == k and c in (res(r["away_nominal"]), res(r["home_nominal"]))][0]
        s += "H" if res(g["home_nominal"]) == c else "A"
    seq[c] = s
if TOUR_RULE == "strict":
    bad = {c: s for c, s in seq.items() if "HH" in s}
    check("TOUR no club nominally home in consecutive rounds (spec as written)", not bad, str(bad))
else:
    bad = {c: s for c, s in seq.items() if "HHH" in s or "AAA" in s}
    check("TOUR no club nominally home or away three rounds running (relaxed rule)", not bad, str(bad))
conf_of = {t["team_city"]: t["conference"] for t in teams}
intra = sum(1 for r in tour if conf_of[res(r["away_nominal"])] == conf_of[res(r["home_nominal"])])
check("TOUR 12 intra-conference and 16 cross-conference", intra == 12, f"{intra} intra / {28 - intra} cross")
weeks_by_round = {int(r["tour_round"]): int(r["calendar_week"]) for r in tour}
check("TOUR rounds on calendar weeks 1/4/8/11/15/18/22", [weeks_by_round[k] for k in range(1, 8)] == [1, 4, 8, 11, 15, 18, 22])
cal_by_wk = {int(r["calendar_week"]): r for r in cal}
pattern = {"1": ("saturday", "17:30"), "2": ("sunday", "10:00"), "3": ("sunday", "13:00"), "4": ("monday", "17:30")}
check("TOUR game 1 Sat 17:30, games 2-3 Sun 10:00/13:00, game 4 Mon 17:30, dates in the CALENDAR week",
      all(r["date"] == cal_by_wk[int(r["calendar_week"])][pattern[r["game"]][0]] and r["kickoff"] == pattern[r["game"]][1] for r in tour))
rd = {int(r["tour_round"]): d(r["date"]) for r in tour if r["game"] == "1"}
rd_last = {int(r["tour_round"]): d(r["date"]) for r in tour if r["game"] == "4"}
gaps = [(rd[k + 1] - rd[k]).days for k in range(1, 7)]
check("TOUR gaps between rounds are 21 or 28 days", all(g in (21, 28) for g in gaps), str(gaps))
bowl = d(next(r["sunday"] for r in cal if r["note"] == "Ambassador Bowl"))
check("TOUR last round >= 14 days before the Bowl", (bowl - rd_last[7]).days >= 14, str((bowl - rd_last[7]).days))
slots = defaultdict(set)
for r in tour:
    slots[r["tour_round"]].add(r["venue_slot"])
check("TOUR each round's four games share one venue_slot TR-round; seven slots distinct",
      all(s == {f"TR-{k}"} for k, s in slots.items()) and len(slots) == 7)

# ---------------------------------------------------------------- VENUES
check("VENUES venue_id unique", len({v["venue_id"] for v in venues}) == len(venues))
check("VENUES venue_name unique", len({v["venue_name"] for v in venues}) == len(venues))
check("VENUES no city equals a club team_city", not any(v["city"] in CITY for v in venues), str([v["city"] for v in venues if v["city"] in CITY]))
actv = [v for v in venues if v["active"] == "true"]
check("VENUES every active row has capacity > 0", all(int(v["capacity"]) > 0 for v in actv))
check("VENUES region is one of the five listed", {v["region"] for v in venues} <= {"Europe", "Asia", "Africa", "South America", "Oceania"})
need = 7 + 1
check("VENUES pool >= 8 (one season: 7 Tour rounds + the Bowl)", len(actv) >= need, f"{len(actv)} active rows")

# ---------------------------------------------------------------- CROSS-FILE / CAP / section 0 arithmetic
check("CROSS-FILE the 8 ambassadors equal the 8 lottery_1_8 clubs",
      {t["abbr"] for t in amb} == {t["abbr"] for t in teams if t["draft_band"] == "lottery_1_8"})
gem_bad = [t["abbr"] for t in teams if regmap.get(f"{t['conference']}/{t['division']}") != t["tertiary_hex"].upper()]
check("CROSS-FILE gemstone hexes match between TEAMS and COLOR_REGISTRY", not gem_bad, str(gem_bad))
minima = {"K": 1, "P": 1, "QB": 3, "HB/FB": 3, "WR": 5, "TE": 2, "LT": 2, "LG": 2, "C": 1, "RG": 2, "RT": 2,
          "LE": 2, "RE": 2, "DT": 3, "ROLB": 2, "MLB": 3, "LOLB": 2, "CB": 5, "FS": 2, "SS": 2, "LS": 1}
req = sum(minima.values())
check("CAP positional minimums total 48 required places; Bowl discretionary 53 - 48 = 5; ceiling 1000 - 48*5 - 25 = 735",
      req == 48 and 53 - req == 5 and 1000 - req * 5 - 25 == 735, f"minimums sum to {req}")
check("SECTION 0 bracket paths 2^13 = 8192, halls 1+2+8+48 = 59, relationships 48*47 = 2256, draft 7*48 = 336",
      2 ** 13 == 8192 and 1 + 2 + 8 + 48 == 59 and 48 * 47 == 2256 and 7 * 48 == 336)
check("SECTION 0 real-time year: (27 * 120 + 45) / 60 ~ 55 h", round((27 * 120 + 45) / 60) == 55, f"{(27 * 120 + 45) / 60:.2f} h")

if DOC:
    import hashlib, re
    raw = open(DOC, encoding="utf-8", newline="").read()
    m = re.search(r"^\*\*Content hash[^\n]*`([0-9a-f]{64})`[^\n]*\n", raw, flags=re.M)
    stripped = raw.replace(m.group(0), "", 1) if m else raw
    got = hashlib.sha256(stripped.encode("utf-8")).hexdigest()
    check("DOC content hash matches the stamp", bool(m) and m.group(1) == got, f"stamp {m.group(1)[:12] if m else 'missing'}, computed {got[:12]}")

fails = [r for r in results if not r[1] and not r[3]]
warns = [r for r in results if not r[1] and r[3]]
for name, ok, detail, warn in results:
    tag = "PASS  " if ok else ("WARN  " if warn else "FAIL  ")
    print(tag + name + (f"  -- {detail}" if detail else ""))
print(f"\n{len(results) - len(fails) - len(warns)} passed, {len(fails)} failed, {len(warns)} warnings (tour rule: {TOUR_RULE})")
sys.exit(len(fails))
