"""Parity profiles: test beds for how much the league regulates itself (Phase 4).

  tight   the rules as written (the default; changes nothing).
  none    no salary cap, and a returner picks like any club in its second draft (no end-of-block rule). The structure of the league
          (divisions, exile, the lottery for returning ambassadors) stays. IMPORTANT LIMIT: clubs have no money, revenue or ownership
          yet, so removing the cap changes little until the economy exists (people layer); this bed mostly shows the profile machinery.
  rigged  a deliberate control, not a design option: no cap, and two clubs always get first pick of every free-agent class. A league
          that is fair on purpose must fail the bands here, otherwise the bands cannot see unfairness at all.

A profile belongs to the league that was created with it. `settings(name)` only *returns* the profile's values; nothing here changes a
module constant, so creating, restoring or running another league in the same process cannot alter an existing one (before Edition 8.54's
persistence work a profile rewrote `rules.CAP` and `draft.RETURNER_BLOCK` for the whole process, and a league restored from a file did not
re-apply its profile). The values are copied into the league's own state: the cap onto its ledger (`Ledger.cap`), the returner rule onto
the universe and its season run (`returner_block`), the priority clubs onto its world (`World.priority`).
"""
from fractions import Fraction as F

from .ledger import rules as R

PROFILES = {
    "tight": {},
    "none": {"cap": F(10 ** 7), "returner_block": False},
    "rigged": {"cap": F(10 ** 7), "returner_block": False, "priority_clubs": 2},
}
NAMES = tuple(PROFILES)
SHIPPED = {"cap": R.CAP, "returner_block": True}      # the rules as written; a profile overrides some of these


def settings(name="tight"):
    """The profile's complete settings: {"cap", "returner_block", "priority_clubs"}. Pure; changes nothing."""
    if name not in PROFILES:
        raise ValueError(f"unknown parity profile {name!r} (one of {', '.join(NAMES)})")
    spec = PROFILES[name]
    return {"cap": spec.get("cap", SHIPPED["cap"]), "returner_block": spec.get("returner_block", SHIPPED["returner_block"]),
            "priority_clubs": spec.get("priority_clubs", 0)}
