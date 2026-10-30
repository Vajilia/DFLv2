"""Contracts (section 11): up to five covered years, steady/rising/declining base salary, signing bonus paid at
signing and prorated evenly (at most five years), guarantees, release and trade charges.

All years are absolute league years. A contract signed in-season starts at `start_week` (paid weeks already elapsed) and
pays only the remaining weeks of its first year.
"""
from dataclasses import dataclass, field
from fractions import Fraction as F

from . import rules as R


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class Contract:
    start_year: int
    years: tuple                 # annual base salary rate, one per covered year
    guaranteed: tuple = ()       # guaranteed part of each year's base rate (same length as years; default none)
    bonuses: tuple = ()          # (amount, first_year_index, proration_years)
    start_week: int = 0
    shape: str = "steady"
    option: object = None        # an unexercised club option on one more year at this base salary (the first-round fifth-year option); not a covered year until exercised
    _memo: dict = field(default_factory=dict, compare=False, repr=False, hash=False)

    def __post_init__(self):
        n = len(self.years)
        object.__setattr__(self, "years", tuple(F(x) for x in self.years))
        g = tuple(F(x) for x in (self.guaranteed or (0,) * n))
        object.__setattr__(self, "guaranteed", g)
        object.__setattr__(self, "bonuses", tuple((F(a), int(i), int(p)) for a, i, p in self.bonuses))
        if not 1 <= n <= R.MAX_COVERED_YEARS:
            raise ContractError("a contract covers 1 to 5 years")
        if len(g) != n or any(x < 0 or x > b for x, b in zip(g, self.years)):
            raise ContractError("guarantee must be between 0 and the year's base")
        if any(b < R.MIN_SALARY for b in self.years):
            raise ContractError("base salary below the minimum")
        if self.option is not None:
            object.__setattr__(self, "option", F(self.option))
            if self.option < R.MIN_SALARY or n >= R.MAX_COVERED_YEARS:
                raise ContractError("an option year needs a room for a further covered year and a salary of at least the minimum")
        if not 0 <= self.start_week <= R.PAID_WEEKS:
            raise ContractError("start week out of range")
        for a, i, p in self.bonuses:
            if a < 0 or not 1 <= p <= R.MAX_PRORATION_YEARS or not 0 <= i < n or i + p > n:
                raise ContractError("bonus must prorate over 1 to 5 covered years")
        ys = self.years
        ok = {"steady": all(x == ys[0] for x in ys), "rising": all(a <= b for a, b in zip(ys, ys[1:])),
              "declining": all(a >= b for a, b in zip(ys, ys[1:]))}
        if not ok.get(self.shape):
            raise ContractError(f"base salaries are not {self.shape}")

    # --- timing
    @property
    def end_year(self):
        return self.start_year + len(self.years) - 1

    def index(self, year):
        return year - self.start_year

    def covers(self, year):
        return 0 <= self.index(year) < len(self.years)

    def _share(self, i):
        """Share of year i's annual rate that this contract pays at all (first year may be partial)."""
        return F(R.PAID_WEEKS - self.start_week, R.PAID_WEEKS) if i == 0 else F(1)

    # --- cap
    def base_charge(self, year):
        i = self.index(year)
        return self.years[i] * self._share(i) if self.covers(year) else F(0)

    def guarantee_in(self, year):
        i = self.index(year)
        return self.guaranteed[i] * self._share(i) if self.covers(year) else F(0)

    def bonus_charge(self, year):
        i = self.index(year)
        return sum((a / p for a, j, p in self.bonuses if j <= i < j + p), F(0))

    def cap_hit(self, year):
        return self.base_charge(year) + self.bonus_charge(year)

    def accelerated_bonus(self, year):
        """Every bonus portion from `year` on (this year's charge and all later ones)."""
        i = self.index(year)
        return sum((a * max(0, j + p - max(i, j)) / p for a, j, p in self.bonuses), F(0))

    # --- release and trade
    def release_charges(self, year, week, split=False, keep_guarantees=True):
        key = (year, week, split, keep_guarantees)
        hit = self._memo.get(key)
        if hit is None:
            hit = self._memo[key] = self._release_charges(year, week, split, keep_guarantees)
        return dict(hit)

    def _release_charges(self, year, week, split=False, keep_guarantees=True):
        """Dead money by year if the player is let go in `year` after `week` paid weeks.
        Earned base stays this year (guaranteed money in full); later guarantees stay in their own years;
        every uncharged bonus accelerates onto this year (split 50/50 with next year's uncharged part if `split`)."""
        i = self.index(year)
        if not self.covers(year):
            raise ContractError("contract does not cover that year")
        earned_weeks = week - (self.start_week if i == 0 else 0)
        earned = self.years[i] * F(max(0, earned_weeks), R.PAID_WEEKS)
        cur = max(earned, self.guarantee_in(year)) if keep_guarantees else earned
        out = {year: cur}
        if keep_guarantees:
            for j in range(i + 1, len(self.years)):
                if self.guaranteed[j]:
                    out[self.start_year + j] = self.guaranteed[j]
        accel = self.accelerated_bonus(year)
        current_portion = self.bonus_charge(year)
        future = accel - current_portion
        if split and future:
            out[year] = out.get(year, F(0)) + current_portion + future / 2
            out[year + 1] = out.get(year + 1, F(0)) + future / 2
        else:
            out[year] = out.get(year, F(0)) + accel
        return {y: v for y, v in out.items() if v}

    def assumed_by_receiver(self, year, week):
        """The contract a trade partner or waiver claimant takes on: unpaid base and attached guarantees, never the bonus."""
        i = self.index(year)
        ys, gs = self.years[i:], self.guaranteed[i:]
        # the first (current) year is paid only for the weeks that remain
        shape = "steady" if all(x == ys[0] for x in ys) else self.shape
        return Contract(year, ys, gs, (), week, shape, self.option)

    def restructured(self, year, week, amount):
        """Convert `amount` of this year's remaining base into signing bonus prorated over the remaining covered years (max 5)."""
        i = self.index(year)
        left = self.years[i] * (self._share(i) if i == 0 else F(1))
        if amount <= 0 or amount > left - R.MIN_SALARY * (self._share(i) if i == 0 else 1):
            raise ContractError("cannot convert that much base")
        rate = (self.years[i] * self._share(i) - amount) / self._share(i) if i == 0 else self.years[i] - amount
        ys = list(self.years)
        ys[i] = rate
        n = min(R.MAX_PRORATION_YEARS, len(self.years) - i)
        shape = self.shape
        if shape == "rising" and i + 1 < len(ys) and ys[i] > ys[i + 1]:
            shape = "declining" if all(a >= b for a, b in zip(ys, ys[1:])) else None
        if shape is None or not self._shape_ok(ys, shape):
            shape = next((s for s in ("steady", "rising", "declining") if self._shape_ok(ys, s)), None)
            if shape is None:
                raise ContractError("restructure would leave a non-monotone contract")
        gs = [min(g, y) for g, y in zip(self.guaranteed, ys)]
        return Contract(self.start_year, tuple(ys), tuple(gs), self.bonuses + ((amount, i, n),), self.start_week, shape, self.option)

    def without_option(self):
        """The same contract with the option gone (declined or lapsed)."""
        return Contract(self.start_year, self.years, self.guaranteed, self.bonuses, self.start_week, self.shape)

    def with_option_exercised(self, guaranteed=None):
        """This contract with its option year added as one more covered year (guaranteed in full by default, rules.R1_OPTION_GUARANTEED)."""
        if self.option is None:
            raise ContractError("no option to exercise")
        guaranteed = R.R1_OPTION_GUARANTEED if guaranteed is None else guaranteed
        ys = self.years + (self.option,)
        if not self._shape_ok(ys, "rising"):
            raise ContractError("the option year would leave the base salaries out of order")
        return Contract(self.start_year, ys, self.guaranteed + (self.option if guaranteed else F(0),), self.bonuses, self.start_week,
                        "rising" if not self._shape_ok(ys, "steady") else "steady")

    @staticmethod
    def _shape_ok(ys, shape):
        return {"steady": all(x == ys[0] for x in ys), "rising": all(a <= b for a, b in zip(ys, ys[1:])),
                "declining": all(a >= b for a, b in zip(ys, ys[1:]))}[shape]


def rookie_contract(year, rnd, first_round_option=False):
    """Four fixed years at the round's scale salary, no bonus. With `first_round_option` a first-round contract carries the club's option on a
    fifth year at $35: not a covered year, so no commitment, until the club exercises it (Ledger.exercise_option, decided by the draft of
    the player's fourth contract year)."""
    base = R.ROOKIE_SCALE[rnd - 1]
    ys = (base,) * R.ROOKIE_YEARS
    return Contract(year, ys, (), (), 0, "steady", R.R1_OPTION if first_round_option and rnd == 1 else None)
