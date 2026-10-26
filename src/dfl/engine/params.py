"""Engine parameters: data tables in data/engine/ (PLACEHOLDER values until calibrated against the bands in BANDS.csv)."""
import csv
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA = os.path.join(ROOT, "data", "engine")
RULE_VERSION = "engine-1.1 (Ed 8.46, play-level)"


def _rows(name, path=DATA):
    with open(os.path.join(path, name), encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(l for l in f if not l.startswith("#"))]


def _yards(v):
    if v.startswith("spot"):
        f = v.split(":")
        return ("spot", float(f[1]), float(f[2])) if len(f) == 3 else ("spot", 13.0, 8.0)
    return int(v)


def _who(v):
    out = []
    for part in v.split(","):
        g, _, w = part.partition(":")
        out.append((g, float(w) if w else 1.0))
    return out


def load(path=DATA):
    P = {r["name"]: float(r["value"]) for r in _rows("PARAMS.csv", path)}
    pen = []
    for r in _rows("PENALTIES.csv", path):
        eff, _, kinds = r["effect"].partition(":")
        pen.append({"name": r["name"], "side": r["side"], "rate": float(r["rate"]), "yards": _yards(r["yards"]),
                    "auto_first": r["auto_first"] == "1", "loss_down": r["loss_down"] == "1", "when": r["when"],
                    "who": _who(r["who"]), "effect": eff, "kinds": kinds, "nfl": float(r["nfl_per_game"])})
    return P, pen


P, PENALTIES = load()
