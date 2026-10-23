"""Engine parameters: data tables in data/engine/ (PLACEHOLDER values until calibrated against the bands in BANDS.csv)."""
import csv
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA = os.path.join(ROOT, "data", "engine")
RULE_VERSION = "engine-1.0 (Ed 8.16, play-level)"


def _rows(name, path=DATA):
    with open(os.path.join(path, name), encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(l for l in f if not l.startswith("#"))]


def load(path=DATA):
    P = {r["name"]: float(r["value"]) for r in _rows("PARAMS.csv", path)}
    pen = []
    for r in _rows("PENALTIES.csv", path):
        pen.append({"name": r["name"], "side": r["side"], "rate": float(r["rate"]),
                    "yards": r["yards"] if r["yards"] == "spot" else int(r["yards"]),
                    "auto_first": r["auto_first"] == "1", "when": r["when"]})
    return P, pen


P, PENALTIES = load()
