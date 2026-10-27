"""Reading a play's trace back: the second per-play table for the 2D view (Edition 8.48).

A trace is what `PlaySim` keeps when asked: every player's position every 0.05 s, the ball's (x, y, height), the events (who engaged, shed,
threw, caught, tackled) and each player's intent at the steps where it changed. Positions are stored as whole hundredths of a yard,
zlib-compressed, which is about 20 KB for a five-second play.
"""
import zlib

import numpy as np


def decode(blob):
    """Return a dict: pos (steps, 22, 2) yards, ball (steps, 3) yards (x, y, height), t (steps) seconds, and the lists the trace carries."""
    n = blob["steps"]
    pos = np.frombuffer(zlib.decompress(blob["pos"]), dtype=np.int16).reshape(n, 22, 2).astype(np.float64) / 100.0
    ball = np.frombuffer(zlib.decompress(blob["ball"]), dtype=np.int16).reshape(n, 3).astype(np.float64) / 100.0
    return {"pos": pos, "ball": ball, "t": np.arange(n) * blob["dt"], "roles": blob["roles"], "ids": blob["ids"], "events": blob["events"],
            "intents": blob["intents"], "los": blob["los"], "formation": blob["formation"], "play": blob["play"], "coverage": blob["coverage"],
            "dt": blob["dt"]}


def to_json(blob, every=2):
    """A plain structure for a viewer: one frame every `every` steps (0.10 s by default), positions to the hundredth of a yard."""
    d = decode(blob)
    frames = []
    for k in range(0, d["pos"].shape[0], every):
        frames.append({"t": round(float(d["t"][k]), 2), "p": [[round(float(x), 2), round(float(y), 2)] for x, y in d["pos"][k]],
                       "b": [round(float(v), 2) for v in d["ball"][k]]})
    return {"roles": d["roles"], "ids": d["ids"], "los": d["los"], "formation": d["formation"], "play": d["play"], "coverage": d["coverage"],
            "dt": d["dt"] * every, "frames": frames,
            "events": [[round(n * d["dt"], 2), kind, a, b] for n, kind, a, b in d["events"]]}
