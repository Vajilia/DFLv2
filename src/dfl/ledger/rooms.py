"""Meeting rooms (people layer, Edition 8.29): the places where independent parties each have a say.

A room has a kind (contract, trade, interview), its parties, a transcript of messages (round, speaker, move, terms) and an outcome.
Rules the ledger holds every room to: only a party may speak; a room has a round limit by kind and a message beyond it is refused;
a closed room is never reopened. Rooms record what happened; the engine never reads a transcript, but a run is reproducible from
the same seeds because every number a room uses is drawn from a keyed stream.

Three rooms are built:
- the contract room: a player asks, the club's general manager counters, the player answers (up to three rounds);
- the trade room: the three-round protocol of Edition 8.26, now recorded as a room;
- the interview: a club invites a candidate for a leadership post, the candidate answers; an interview that is accepted shrinks the
  club's error in judging her.
All numbers are data (ROOMS.csv), placeholders.
"""
from fractions import Fraction as F

from ..characters.creator import rng_for
from . import rules as R

KINDS = ("contract", "trade", "interview")
OUTCOMES = ("deal", "no_deal", "lapsed", "accepted", "declined")


class RoomError(Exception):
    pass


def open_room(world, kind, parties):
    assert kind in KINDS
    world.room_seq += 1
    room = {"id": world.room_seq, "kind": kind, "year": world.L.year, "week": world.L.week, "parties": tuple(parties), "msgs": [], "outcome": None}
    world.rooms.append(room)
    world.stats["rooms"] = world.stats.get("rooms", 0) + 1
    return room


def say(world, room, rnd, by, move, **terms):
    if room["outcome"] is not None:
        raise RoomError("room is closed")
    if by not in room["parties"]:
        raise RoomError(f"{by} is not in this room")
    if rnd > int(world.tables.rooms["max_rounds_" + room["kind"]]):
        raise RoomError("round limit")
    room["msgs"].append((rnd, by, move, terms))


def close(world, room, outcome, **terms):
    if room["outcome"] is not None:
        raise RoomError("room is closed")
    assert outcome in OUTCOMES
    room["outcome"] = outcome
    room["terms"] = terms
    return room


def prune(world):
    keep = world.L.year - int(world.tables.rooms["keep_years"])
    world.rooms[:] = [r for r in world.rooms if r["year"] > keep]


# ---------------------------------------------------------------- the contract room
def contract(world, cid, p):
    """Her yearly price with this club, or None. She asks a little above her demand; the general manager counters, her negotiating rating
    deciding how much she takes back; the player answers, and in the last round holds at the least she will take. A club that cannot
    reach that least price has no deal. At the middle negotiating rating the deal is struck at her demand."""
    from .world import ask
    Rm, N = world.tables.rooms, world.tables.negotiation
    card = world.cards[p.pid]
    d, w = float(world.demand(p, cid)), float(world.willing(cid, p))
    gm = world.leaders.get(cid, {}).get("gm")
    neg = (gm.attrs.get("NEG", 50) - 50) / 50 if gm is not None else 0.0
    greed = (card.traits["GRE"] - 50) / 50
    least = max(float(R.MIN_SALARY), d * (1 - Rm["flex"] * (1 - 0.5 * greed)))
    opening = d * (1 + Rm["open_markup"])
    push = max(0.0, min(0.5, Rm["open_markup"] / (1 + Rm["open_markup"]) * (1 + Rm["push_neg"] * neg)))
    room = open_room(world, "contract", (f"club:{cid}", f"player:{p.pid}"))
    me, you = f"player:{p.pid}", f"club:{cid}"
    say(world, room, 1, me, "ask", price=round(opening, 1))
    offer = min(w, opening * (1 - push))
    say(world, room, 2, you, "offer", price=round(offer, 1))
    if offer >= least:
        price, how = offer, "accept"
    else:
        say(world, room, 3, me, "final", price=round(least, 1))
        price, how = (least, "accept") if least <= w else (None, "reject")
        say(world, room, 3, you, how, price=round(least, 1))
    if price is None:
        close(world, room, "no_deal")
        world.stats["refusals"] = world.stats.get("refusals", 0) + 1
        return None
    price = max(R.MIN_SALARY, F(int(price)))
    close(world, room, "deal", price=float(price))
    world.stats["deals"] = world.stats.get("deals", 0) + 1
    world.stats["paid_over_market"] = world.stats.get("paid_over_market", 0) + float(price - ask(p))
    return price


# ---------------------------------------------------------------- the trade room
class TradeTape:
    """Collects the messages of one trade negotiation into a room; the trade module calls `note` where it logs. The proposer is `a`."""

    def __init__(self, world, a, b):
        self.world = world
        self.room = open_room(world, "trade", (f"club:{a}", f"club:{b}"))
        self.a, self.b = f"club:{a}", f"club:{b}"

    def note(self, kind, rnd, **terms):
        if self.room["outcome"] is not None:
            return
        last = self.room["msgs"][-1] if self.room["msgs"] else None
        if kind == "propose":
            rnd, by = 1, self.a
        elif kind == "counter":
            rnd, by = 2, self.b
        elif kind == "accept":
            rnd, by = (3, self.a) if last and last[2] == "counter" else (2, self.b)
        elif kind == "reject":
            rnd = rnd or 2
            by = self.b if rnd == 2 else self.a
        else:                                       # illegal at the ledger: the proposer's deal fell through
            rnd, by = min(3, (last[0] if last else 0) + 1), self.a
        say(self.world, self.room, rnd, by, kind, **terms)

    def finish(self, outcome):
        if self.room["outcome"] is None:
            close(self.world, self.room, outcome)


# ---------------------------------------------------------------- the interview
def decline_chance(world, cid, card):
    Rm = world.tables.rooms
    below = max(0.0, 0.5 - world.form.get(cid, 0.5))
    return Rm["decline_base"] + Rm["decline_fortune"] * (card.traits["AMB"] / 100) * (below / 0.5)


def interview(world, cid, card, post):
    """The club invites; she answers. Her answer depends on her own ambition and how badly the club did last season, and on a draw of her
    own (the same draw whenever the question is asked again)."""
    room = open_room(world, "interview", (f"club:{cid}", f"candidate:{card.identity.id}"))
    say(world, room, 1, f"club:{cid}", "invite", post=post)
    z = rng_for(world.creator.seed, "interview", cid, card.identity.id, world.L.year, post).random()
    yes = z >= decline_chance(world, cid, card)
    say(world, room, 2, f"candidate:{card.identity.id}", "accept" if yes else "decline")
    close(world, room, "accepted" if yes else "declined")
    world.stats["interviews"] = world.stats.get("interviews", 0) + 1
    if not yes:
        world.stats["interview_declines"] = world.stats.get("interview_declines", 0) + 1
    return yes


def choose(world, cid, post, candidates, judge):
    """Fill a post. The club ranks the candidates by its own first impression (`judge(card, 1.0)`), interviews them in that order until
    enough have accepted, then picks the one it rates highest after the interview (`judge(card, interview_factor)`, a smaller error)."""
    Rm = world.tables.rooms
    ranked = sorted(candidates, key=lambda c: (-judge(c, 1.0), c.identity.id))
    seen = []
    for c in ranked:
        if len(seen) >= int(Rm["interviews_per_hire"]):
            break
        if interview(world, cid, c, post):
            seen.append(c)
    if not seen:                                    # everyone declined: the club takes its first choice, who must still sign
        world.stats["forced_hires"] = world.stats.get("forced_hires", 0) + 1
        return ranked[0]
    return max(seen, key=lambda c: (judge(c, Rm["interview_factor"]), -c.identity.id))
