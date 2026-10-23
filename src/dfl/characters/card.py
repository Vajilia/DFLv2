"""The character card.

Identity is frozen at creation and never changes: id, role, name, birth year, official position, creation body and creation look.
State is what lives and changes: hidden true attributes, a personal growth profile, traits, the 32-slot memory and the sparse,
directed relationship map (the last two are filled by the people layer; the card only provides the slots).

Hidden ability is never exposed: `public()` shows only what anyone could see. Everything else is for the simulation.
There is deliberately no stored overall rating; `true_overall` is derived from the hidden attributes for the engine's use only.
"""
import json
from dataclasses import dataclass, field, asdict

MEMORY_SLOTS = 32
GROWTH_KEYS = ("peak_age", "ceiling", "volatility")     # personal development profile (the curve itself is data, item 3)


@dataclass(frozen=True)
class Identity:
    id: int
    role: str                  # player | ceo | gm | hc | oc | dc | stc
    first: str
    last: str
    birth_year: int
    pos: str                   # official position for players (permanent); the role code for leaders
    height_cm: int
    weight_kg: int
    look: tuple                # ((feature, value), ...) the creation portrait genome, immutable
    created_year: int
    origin: str                # founding | draft | hire

    @property
    def name(self):
        return f"{self.first} {self.last}"


@dataclass
class Card:
    identity: Identity
    attrs: dict                                    # hidden true attributes, 1-99
    growth: dict                                   # hidden: peak_age, ceiling (points still to gain at peak), volatility
    traits: dict                                   # 0-100: AMB LOY WRK TMP GRE LDR
    memory: list = field(default_factory=list)     # at most MEMORY_SLOTS detailed memories (people layer)
    relations: dict = field(default_factory=dict)  # other id -> directed summary, recorded only for actual contacts

    # ---- views
    def age(self, year):
        return year - self.identity.birth_year

    def public(self, year):
        """What anyone can see. No attributes, growth, traits or memory."""
        i = self.identity
        return {"id": i.id, "name": i.name, "role": i.role, "pos": i.pos, "age": self.age(year),
                "height_cm": i.height_cm, "weight_kg": i.weight_kg, "look": dict(i.look)}

    def true_overall(self, tables):
        """Hidden true overall from the position profile weights. For the engine and for tests; never shown or stored."""
        prof = tables.profile[self.identity.pos if self.identity.role == "player" else self.identity.role]
        tot = sum(w for _, _, _, _, w in prof)
        return sum(self.attrs[a] * w for a, _, _, _, w in prof) / tot

    def remember(self, event):
        """Append a detailed memory; when full, the oldest routine one merges away (people layer decides what is routine)."""
        if len(self.memory) >= MEMORY_SLOTS:
            raise OverflowError("memory full: the people layer must merge before adding")
        self.memory.append(event)

    # ---- storage
    def to_dict(self):
        d = asdict(self)
        d["identity"]["look"] = [list(x) for x in self.identity.look]
        return d

    @classmethod
    def from_dict(cls, d):
        i = dict(d["identity"])
        i["look"] = tuple(tuple(x) for x in i["look"])
        return cls(Identity(**i), dict(d["attrs"]), dict(d["growth"]), dict(d["traits"]),
                   list(d.get("memory", [])), {int(k): v for k, v in d.get("relations", {}).items()})

    def dumps(self):
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def loads(cls, s):
        return cls.from_dict(json.loads(s))

    def describe(self, year, hidden=False):
        """A readable card. With hidden=True it also lists the true attributes, growth and traits (developer view)."""
        i = self.identity
        out = [f"#{i.id}  {i.name}   {i.role.upper()} {i.pos if i.role == 'player' else ''}".rstrip(),
               f"age {self.age(year)} (born {i.birth_year})  {i.height_cm} cm  {i.weight_kg} kg  [{i.origin} {i.created_year}]",
               "look: " + ", ".join(f"{k} {v}" for k, v in i.look)]
        if hidden:
            out.append("attrs: " + " ".join(f"{k}{v}" for k, v in self.attrs.items()))
            out.append("growth: " + " ".join(f"{k}={v}" for k, v in self.growth.items()))
            out.append("traits: " + " ".join(f"{k}{v}" for k, v in self.traits.items()))
        return "\n".join(out)
