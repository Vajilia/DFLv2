"""Parity profiles: test beds for how much the league regulates itself (Phase 4).

  tight   the rules as written (the default; changes nothing).
  none    no salary cap, and a returner picks like any club in its second draft (no end-of-block rule). The structure of the league
          (divisions, exile, the lottery for returning ambassadors) stays. IMPORTANT LIMIT: clubs have no money, revenue or ownership
          yet, so removing the cap changes little until the economy exists (people layer); this bed mostly shows the profile machinery.
  rigged  a deliberate control, not a design option: no cap, and two clubs always get first pick of every free-agent class. A league
          that is fair on purpose must fail the bands here, otherwise the bands cannot see unfairness at all.

A profile changes module-level constants, so a process applies one profile and keeps it (the tools run one league per process);
`apply("tight")` restores the shipped values, which is what the tests do after trying another.
"""
from fractions import Fraction as F

from . import draft
from .ledger import rules as R

PROFILES = {
    "tight": {},
    "none": {"cap": F(10 ** 7), "returner_block": False},
    "rigged": {"cap": F(10 ** 7), "returner_block": False, "priority_clubs": 2},
}
NAMES = tuple(PROFILES)
_SHIPPED = {"cap": R.CAP, "returner_block": draft.RETURNER_BLOCK if hasattr(draft, "RETURNER_BLOCK") else True}
_current = "tight"


def apply(name="tight"):
    """Set the profile's constants and return its settings (for the World)."""
    global _current
    if name not in PROFILES:
        raise ValueError(f"unknown parity profile {name!r} (one of {', '.join(NAMES)})")
    spec = PROFILES[name]
    R.CAP = spec.get("cap", _SHIPPED["cap"])
    draft.RETURNER_BLOCK = spec.get("returner_block", _SHIPPED["returner_block"])
    _current = name
    return dict(spec)


def current():
    return _current
