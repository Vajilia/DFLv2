"""Box scores, the scoring summary and play-by-play text from a finished game (Design Document section 16, Edition 8.47).

Everything here is read from a `GameResult` and the two squads; nothing is drawn and nothing is changed. The box score shows only what a
viewer could see: names, positions, counts and yards. No attribute, rating or hidden state reaches it.

`box_score(result, home, away)` returns a plain dict (JSON-safe). `format_box(box)` lays it out as text. `play_by_play(result, home, away)`
narrates the structured play table (`result.table`, present when the game was played with `log=True`).
"""

KINDS = {"run": "rush", "scramble": "rush"}


def short(card):
    i = card.identity
    return f"{i.first[0]}. {i.last}"


class Roster:
    """Display names, positions and side of every card in the two squads."""

    def __init__(self, home, away):
        self.name, self.pos, self.side = {}, {}, {}
        for side, sq in enumerate((home, away)):
            for c in sq.cards.values():
                self.name[c.identity.id] = short(c)
                self.pos[c.identity.id] = c.identity.pos
                self.side[c.identity.id] = side

    def n(self, pid):
        return self.name.get(pid, "?")


def clock(secs):
    return f"{int(secs) // 60}:{int(secs) % 60:02d}"


def period_name(p):
    return f"Q{p}" if p <= 4 else ("OT" if p == 5 else f"OT{p - 4}")


# ------------------------------------------------------------------------------------------------------------------------- box score
def line_score(table, periods):
    n = max(4, periods)
    lines = [[0] * n, [0] * n]
    for e in table:
        if e.get("pts"):
            lines[e["team"]][e["period"] - 1] += e["pts"]
    return lines


def scoring_summary(table, ros, ids):
    out, run = [], [0, 0]
    for k, e in enumerate(table):
        if not e.get("pts"):
            continue
        run[e["team"]] += e["pts"]
        kind = e["kind"]
        prev = table[k - 1] if k else {}
        if kind == "touchdown":
            who = ros.n(e.get("scorer"))
            if prev.get("kind") in ("run", "scramble"):
                text = f"{who} {100 - prev['yl']}-yard run"
            elif prev.get("kind") == "complete":
                text = f"{who} {100 - prev['yl']}-yard pass from {ros.n(prev.get('qb'))}"
            elif e["detail"] and e["detail"][0] == "defense":
                text = f"{who} defensive touchdown"
            else:
                text = f"{who} return touchdown"
        elif kind == "field goal":
            text = f"{ros.n(e.get('kicker'))} {e['detail'][1]}-yard field goal"
        elif kind == "extra point":
            text = f"{ros.n(e.get('kicker'))} extra point"
        elif kind == "two-point try":
            text = "two-point try"
        else:
            text = "safety"
        out.append({"period": e["period"], "clock": clock(e["clock"]), "team": ids[e["team"]], "text": text, "score": list(run)})
    return out


def _sum(players, ids, key):
    return sum(players.get(i, {}).get(key, 0) for i in ids)


def team_block(result, side, ros):
    s, o = result.stats[side], result.stats[1 - side]
    ids = [i for i, sd in ros.side.items() if sd == side]
    pl = result.players
    punts = _sum(pl, ids, "punts")
    net_pass = s["pass_yds"] - s["sack_yds"]
    return {
        "first_downs": s["first_downs"],
        "total_yards": s["rush_yds"] + net_pass,
        "plays": s["plays"],
        "rushing": f"{s['rush_att']}-{s['rush_yds']}",
        "passing": f"{s['comp']}-{s['pass_att']}-{net_pass}",
        "sacked": f"{o['sacks']}-{s['sack_yds']}",
        "third_down": f"{s['third_conv']}-{s['third_att']}",
        "fourth_down": f"{s['fourth_conv']}-{s['fourth_att']}",
        "turnovers": s["ints"] + s["fumbles_lost"],
        "penalties": f"{s['penalties']}-{s['pen_yds']}",
        "punts": f"{punts}-{round(_sum(pl, ids, 'punt_yds') / punts, 1) if punts else 0}",
        "time_of_possession": clock(s["top"]),
        "yards_per_play": round((s["rush_yds"] + net_pass) / max(1, s["plays"]), 1),
    }


def _rows(result, ros, side, keys, order):
    rows = []
    for pid, st in result.players.items():
        if ros.side.get(pid) != side or not any(st.get(k, 0) for k in keys):
            continue
        rows.append((pid, st))
    rows.sort(key=lambda r: (-r[1].get(order, 0), r[0]))
    return rows


def player_blocks(result, ros, side):
    pl = {}
    pl["passing"] = [{"name": ros.n(i), "pos": ros.pos[i], "line": f"{st.get('comp', 0)}/{st.get('att', 0)}, {st.get('pass_yds', 0)} yds, "
                      f"{st.get('pass_td', 0)} TD, {st.get('int', 0)} INT" + (f", sacked {st['sacked']} ({st.get('sack_yds', 0)})" if st.get("sacked") else "")}
                     for i, st in _rows(result, ros, side, ("att",), "pass_yds")]
    pl["rushing"] = [{"name": ros.n(i), "pos": ros.pos[i], "line": f"{st['car']} car, {st.get('rush_yds', 0)} yds, {st.get('rush_td', 0)} TD"}
                     for i, st in _rows(result, ros, side, ("car",), "rush_yds")]
    pl["receiving"] = [{"name": ros.n(i), "pos": ros.pos[i], "line": f"{st.get('rec', 0)} rec on {st['tgt']} tgt, {st.get('rec_yds', 0)} yds, {st.get('rec_td', 0)} TD"}
                       for i, st in _rows(result, ros, side, ("tgt",), "rec_yds")]
    pl["defense"] = [{"name": ros.n(i), "pos": ros.pos[i],
                      "line": f"{st.get('tkl', 0)} tkl, {st.get('ast', 0)} ast, {st.get('sack', 0):g} sack, {st.get('tfl', 0):g} TFL, {st.get('pd', 0)} PD, "
                              f"{st.get('def_int', 0)} INT, {st.get('ff', 0)} FF, {st.get('fr', 0)} FR"}
                     for i, st in _rows(result, ros, side, ("tkl", "ast", "sack", "pd", "def_int", "ff", "fr"), "tkl")]
    pl["kicking"] = [{"name": ros.n(i), "pos": ros.pos[i],
                      "line": f"FG {st.get('fg_made', 0)}/{st.get('fg_att', 0)} (long {st.get('fg_long', 0)}), XP {st.get('xp_made', 0)}/{st.get('xp_att', 0)}, "
                              f"{st.get('ko', 0)} kickoffs, {st.get('ko_tb', 0)} touchbacks"}
                     for i, st in _rows(result, ros, side, ("fg_att", "xp_att", "ko"), "fg_made")]
    pl["punting"] = [{"name": ros.n(i), "pos": ros.pos[i],
                      "line": f"{st['punts']} punts, {st.get('punt_yds', 0)} yds ({st.get('punt_yds', 0) / st['punts']:.1f} avg), "
                              f"{st.get('punt_in20', 0)} inside 20, {st.get('punt_tb', 0)} TB"}
                     for i, st in _rows(result, ros, side, ("punts",), "punt_yds")]
    pl["returns"] = [{"name": ros.n(i), "pos": ros.pos[i],
                      "line": f"KR {st.get('kr', 0)}-{st.get('kr_yds', 0)}, PR {st.get('pr', 0)}-{st.get('pr_yds', 0)}"}
                     for i, st in _rows(result, ros, side, ("kr", "pr"), "kr_yds")]
    pl["penalties"] = [{"name": n, "line": f"{c} for {y} yds"} for n, (c, y) in sorted(result.pen_types[side].items())] if result.pen_types else []
    return pl


def box_score(result, home, away):
    ros = Roster(home, away)
    ids = (result.home, result.away)
    box = {
        "home": result.home, "away": result.away, "score": list(result.score), "periods": result.periods, "tie": result.tie,
        "winner": result.winner,
        "line": line_score(result.table, result.periods) if result.table else None,
        "scoring": scoring_summary(result.table, ros, ids) if result.table else None,
        "team": [team_block(result, 0, ros), team_block(result, 1, ros)],
        "players": [player_blocks(result, ros, 0), player_blocks(result, ros, 1)],
        "injuries": {c: [(ros.n(i), w) for i, w in v] for c, v in result.injuries.items()},
    }
    return box


TEAM_LABELS = (("first_downs", "First downs"), ("total_yards", "Total yards"), ("plays", "Plays"), ("yards_per_play", "Yards per play"),
               ("rushing", "Rushing (att-yds)"), ("passing", "Passing (comp-att-net yds)"), ("sacked", "Sacked (no-yds)"),
               ("third_down", "Third down"), ("fourth_down", "Fourth down"), ("turnovers", "Turnovers"), ("penalties", "Penalties (no-yds)"),
               ("punts", "Punts (no-avg)"), ("time_of_possession", "Time of possession"))


def format_box(box):
    h, a = box["home"], box["away"]
    out = [f"{a} {box['score'][1]}  at  {h} {box['score'][0]}" + ("  (tie)" if box["tie"] else f"  - {box['winner']} win"), ""]
    if box["line"]:
        n = len(box["line"][0])
        out.append(f"{'':12s}" + "".join(f"{period_name(p + 1):>4s}" for p in range(n)) + "   Final")
        out.append(f"{a:12s}" + "".join(f"{x:4d}" for x in box["line"][1]) + f"{box['score'][1]:8d}")
        out.append(f"{h:12s}" + "".join(f"{x:4d}" for x in box["line"][0]) + f"{box['score'][0]:8d}")
        out.append("")
    if box["scoring"]:
        out.append("Scoring")
        for s in box["scoring"]:
            out.append(f"  {period_name(s['period'])} {s['clock']:>5s}  {s['team']:8s} {s['text']}  ({box['away']} {s['score'][1]}, {box['home']} {s['score'][0]})")
        out.append("")
    out.append(f"{'Team statistics':30s}{a:>14s}{h:>14s}")
    for key, label in TEAM_LABELS:
        out.append(f"  {label:28s}{str(box['team'][1][key]):>14s}{str(box['team'][0][key]):>14s}")
    for side, name in ((1, a), (0, h)):
        out.append("")
        out.append(name)
        for sect in ("passing", "rushing", "receiving", "defense", "kicking", "punting", "returns", "penalties"):
            rows = box["players"][side][sect]
            if rows:
                out.append(f"  {sect.capitalize()}")
                for r in rows:
                    out.append(f"    {r['name']:16s}{r.get('pos', ''):>4s}  {r['line']}")
    return "\n".join(out)


# --------------------------------------------------------------------------------------------------------------------- play by play
def spot(e, ids):
    yl = e["yl"]
    me, other = ids[e["off"]], ids[1 - e["off"]]
    return "the 50" if yl == 50 else (f"its own {yl}" if yl < 50 else f"{other} {100 - yl}")


def ordinal(n):
    return {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}.get(n, f"{n}th")


def yards_text(y):
    return "no gain" if y == 0 else (f"for {y} yard{'s' if y != 1 else ''}" if y > 0 else f"for a loss of {-y}")


def narrate(e, ros, ids):
    k, d = e["kind"], e.get("detail", ())
    n = ros.n
    team = ids[e["off"]]
    if k == "kickoff":
        if d and d[0] == "return touchdown":
            return f"{n(e.get('kicker'))} kicks off; returned for a touchdown."
        recv = ids[1 - ros.side.get(e.get("kicker"), 0)]
        return f"{n(e.get('kicker'))} kicks off; {recv} start at their own {d[0]}."
    if k == "onside":
        return f"Onside kick {d[0]}."
    if k in ("run", "scramble"):
        who = n(e.get("car"))
        text = f"{who} {'scrambles' if k == 'scramble' else 'rushes'} {yards_text(d[0])}"
        if e.get("tkl"):
            text += f" (tackle: {n(e['tkl'])}" + (f", {n(e['ast'])}" if e.get("ast") else "") + ")"
        return text + (". First down." if e.get("first") else ".")
    if k == "complete":
        text = f"{n(e.get('qb'))} pass complete to {n(e.get('tgt'))} {yards_text(d[0])}"
        if e.get("tkl"):
            text += f" (tackle: {n(e['tkl'])}" + (f", {n(e['ast'])}" if e.get("ast") else "") + ")"
        return text + (". First down." if e.get("first") else ".")
    if k == "incomplete":
        return f"{n(e.get('qb'))} pass incomplete to {n(e.get('tgt'))}" + (f", broken up by {n(e['pd'])}." if e.get("pd") else ".")
    if k == "sack":
        who = " and ".join(n(i) for i in e.get("sack", []))
        return f"{n(e.get('qb'))} sacked by {who} {yards_text(d[0])}."
    if k == "interception":
        return f"{n(e.get('qb'))} pass intercepted by {n(e.get('int_by'))}."
    if k == "fumble lost":
        return f"{n(e.get('car') or e.get('qb'))} fumbles, recovered by {n(e.get('fr'))}."
    if k == "kneel":
        return "Kneel down."
    if k == "punt":
        return f"{n(e.get('punter'))} punts {d[0]} yards."
    if k == "field goal":
        return f"{n(e.get('kicker'))} {d[1]}-yard field goal is {'good' if d[0] == 'good' else 'no good'}."
    if k == "extra point":
        return f"{n(e.get('kicker'))} extra point is {'good' if e.get('good') else 'no good'}."
    if k == "two-point try":
        return f"Two-point try by {ids[e['team']]} is {'good' if e.get('good') else 'no good'}."
    if k == "touchdown":
        return f"TOUCHDOWN {ids[e['team']]}, {n(e.get('scorer'))}."
    if k == "safety":
        return f"SAFETY: {ids[e['team']]} score two points."
    if k == "penalty":
        extra = ", automatic first down" if len(d) > 3 and d[3] == "first down" else (f" ({d[3]})" if len(d) > 3 else "")
        return f"PENALTY on {ids[e['team']]}, {n(e.get('player'))}: {d[0]}, {d[1]} yards{extra}."
    if k == "timeout":
        return f"Timeout, {ids[e['team']]}."
    if k == "two-minute warning":
        return "Two-minute warning."
    if k == "injury":
        return f"{n(d[0])} is injured and out for the game ({d[1]} weeks)."
    if k == "ejection":
        return f"{n(d[0])} is ejected."
    return k


def play_by_play(result, home, away):
    ros = Roster(home, away)
    ids = (result.home, result.away)
    out = []
    for e in result.table:
        head = f"{period_name(e['period'])} {clock(e['clock']):>5s}"
        situ = ""
        if e["kind"] in ("run", "scramble", "complete", "incomplete", "sack", "interception", "fumble lost", "punt", "field goal", "kneel"):
            situ = f" {ids[e['off']]} {ordinal(e['down'])} & {e['dist']} at {spot(e, ids)}."
        out.append(f"{head}{situ} {narrate(e, ros, ids)}")
    return out
