"""The DFL year (Design Document 8.9, section 2): Jan 1 to Dec 31.

The offseason (Jan to June) is the front of the same year as the season it leads into. The season is anchored backwards
from the Diamond Coronation (last Sunday of the year). Offseason dates come from data/OFFSEASON.csv (editable); in-season
events (breaks, byes, trade close, playoffs, Tour rounds, Bowl, Coronation) come from the 27-week calendar.
"""
import csv
import datetime as dt
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SEASON1_CALENDAR = os.path.join(ROOT, "data", "season1", "CALENDAR.csv")
OFFSEASON_TABLE = os.path.join(ROOT, "data", "OFFSEASON.csv")
TOUR_CALENDAR_WEEKS = (1, 4, 8, 11, 15, 18, 22)
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def coronation(year):
    d = dt.date(year, 12, 31)
    return d - dt.timedelta(days=(d.weekday() - 6) % 7)


def _template():
    with open(SEASON1_CALENDAR, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def season_weeks(year):
    """The 27 calendar-week rows for `year`, same shape as CALENDAR.csv, dates shifted so week 27's Sunday is the Coronation."""
    c = coronation(year)
    rows = []
    for r in _template():
        w = int(r["calendar_week"])
        sunday = c - dt.timedelta(days=7 * (27 - w))
        sat = sunday - dt.timedelta(days=1)
        mon = sunday + dt.timedelta(days=1)
        row = dict(r)
        row["sunday"] = sunday.isoformat()
        row["saturday"] = sat.isoformat() if r["saturday"] else ""
        row["monday"] = mon.isoformat() if r["monday"] else ""
        rows.append(row)
    return rows


def week1_saturday(year):
    return dt.date.fromisoformat(season_weeks(year)[0]["saturday"])


def _last_weekday(year, month, wd):
    d = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - wd) % 7)


def offseason_table():
    with open(OFFSEASON_TABLE, encoding="utf-8", newline="") as f:
        return sorted(csv.DictReader(f), key=lambda r: int(r["order"]))


def offseason_events(year):
    out, at = [], {}
    for r in offseason_table():
        parts = r["rule"].split()
        if parts[0] == "fixed":
            m, d = map(int, parts[1].split("-"))
            date = dt.date(year, m, d)
        elif parts[0] == "last":
            date = _last_weekday(year, int(parts[2]), WEEKDAYS[parts[1]])
        elif parts[0] == "after":
            date = at[parts[1]] + dt.timedelta(days=int(parts[2]))
        elif parts[0] == "before" and parts[1] == "week1":
            d = week1_saturday(year) - dt.timedelta(days=1)
            while d.weekday() != WEEKDAYS[parts[2]]:
                d -= dt.timedelta(days=1)
            date = d
        else:
            raise ValueError(f"unknown rule {r['rule']!r}")
        at[r["event"]] = date
        out.append((date, r["event"], "offseason", r["note"]))
    return out


def season_events(year):
    out = []
    weeks = season_weeks(year)
    first_sat = {}
    for r in weeks:
        w = int(r["calendar_week"])
        if r["phase"] == "regular":
            nw = int(r["numbered_week"])
            first_sat[nw] = dt.date.fromisoformat(r["saturday"])
            out.append((first_sat[nw], f"week_{nw}", "regular", r["note"]))
        elif r["phase"] == "break":
            out.append((dt.date.fromisoformat(r["saturday"]), f"break_{w}", "break", r["note"]))
        elif r["note"] == "wild card round":
            out.append((dt.date.fromisoformat(r["sunday"]), "wild_card", "postseason", ""))
        elif r["note"] == "divisional round":
            out.append((dt.date.fromisoformat(r["sunday"]), "divisional", "postseason", ""))
        elif r["note"] == "conference championships":
            out.append((dt.date.fromisoformat(r["sunday"]), "conference_championships", "postseason", ""))
        elif r["note"] == "Ambassador Bowl":
            out.append((dt.date.fromisoformat(r["sunday"]), "ambassador_bowl", "ambassadors", ""))
        elif r["note"] == "Diamond Coronation":
            out.append((dt.date.fromisoformat(r["sunday"]), "coronation", "postseason", ""))
    # trading ends Friday 23:59 before the first game of numbered week 9
    out.append((first_sat[9] - dt.timedelta(days=1), "trade_close", "regular", "Friday 23:59 before week 9"))
    for i, w in enumerate(TOUR_CALENDAR_WEEKS, 1):
        out.append((dt.date.fromisoformat(weeks[w - 1]["saturday"]), f"tour_round_{i}", "ambassadors", ""))
    out.append((dt.date(year, 7, 4), "venue_announce", "regular", "Coronation venue announced on 4 July"))
    out.append((dt.date(year, 12, 31), "year_close", "regular", "League year closes"))
    return out


PRIORITY = {"offseason": 0, "regular": 1, "break": 1, "ambassadors": 2, "postseason": 3}


def year_events(year):
    """Every dated event of the year in firing order (date, then the order they are declared in the tables)."""
    ev = offseason_events(year) + season_events(year)
    keyed = [(d, i, k, ph, note) for i, (d, k, ph, note) in enumerate(ev)]
    keyed.sort(key=lambda t: (t[0], t[1]))
    return [(d, k, ph, note) for d, _, k, ph, note in keyed]
