# Trade mechanism: design for owner review (not built)

Status: stage 1 built (Edition 8.26, 2026-10-24); stages 2 to 4 are proposals. The Design Document is the authority. Origin: the owner's idea of 48 persistent general-manager agents polled by a control agent, with a common searchable player list. Builder's amendments are marked **(builder)**.

## What the owner proposed
- 48 persistent agents, one per club, acting as that club's general manager and instructed to show no emotion.
- Every agent can see every player on every roster and practice squad, ranked by how much a club values the player. The owner then clarified: agents need not each hold this; a single, updated, searchable common list is enough.
- A control agent polls the 48 for who wants or will accept a trade and passes proposals on. The other GM(s) accept, reject or counter. Every GM's priority is to get the better deal.
- Discretion (the "staff meter") feeds the narrative only; the narrative never reaches the engine.

## Design

### 1. One common list, owned by the ledger **(builder)**
A table that the ledger updates on every move (signing, release, trade, injury, contract change). Agents hold nothing, so nothing goes stale.
- **Public fields only:** club, position, age, credited seasons, contract (years, cap hit, guarantees), roster slot, injury status, public production once the engine records it.
- **Never in it:** hidden attributes, true overall, any club's private valuation.
- **Searchable:** filter by position, age, cap hit, contract years, club; sort by any public field.
- **Market value from public evidence.** Today the placeholder price curve reads each player's true overall rounded. That leaks hidden ability into a public number. Before agents use the list, market value must come from production, age and seasons (spec §14: evaluate using public production only). Until the engine records stats, a noisy public estimate stands in.

### 2. Private valuation on demand
A tool call returns the asking club's own valuation (its scouting view, Edition 8.21, adjusted for positional need, age and cap hit) for any row. An agent never sees another club's valuation or true ratings. Clubs disagree because their views differ; that disagreement is what makes a trade worth doing.

### 3. The trading block **(builder)**
A GM willing to deal publishes the players she would move (and optionally what she is looking for). The control step polls the block, not 48 agents, and only inside the legal trading windows (the spec fixes the trading windows; a trade outside one is illegal anyway).

### 4. The protocol
- A proposal names both clubs, the players and picks each side gives, and any cash-equivalent cap effects.
- The other club accepts, rejects or counters. **At most three rounds** per negotiation, then it lapses (no endless haggling when every GM maximises its own deal).
- **Deal score (fixed, data):** the club's valuation of what it receives minus what it gives, plus positional need, age and cap effects. A GM accepts if the score clears a threshold set by the GM's own traits (greed, loyalty, negotiation skill, relationship with the counterpart). The GM's NEG rating shapes what concessions she can extract; a weak negotiator cannot haggle like a strong one.
- **The ledger validates every proposal** (cap, roster limits, window, no-trade rules). An illegal proposal is rejected before it reaches the other club.
- **Every proposal, response and counter is an event in the log.** The engine replays the log; a run is reproducible even when the deciders were not.

### 5. Deciders are pluggable **(builder)**
The decision "propose / accept / reject / counter" sits behind one interface. The autopilot GM is the default and the one all tests use. Language-model agents plug into the same interface.

## Why not just 48 free-running agents
- Language models are not reproducible, slow and cost money for every call; the roadmap's own risk 3.
- 48 copies of one model can collude without meaning to, and their skill varies from run to run. Both are channels the Phase 4 fairness bands would have to detect.
- Handing each agent 3,300 players is very expensive; a query tool is cheap and lets agents ask only for what they need.
- Polling is a loop; it does not need a model.

## Build order
1. **Stage 1: deterministic protocol with autopilot GMs.** Common list, trading block, deal score, three-round limit, ledger validation, event log. Measured against the Phase 4 bands.
2. **Stage 2: public market value** from production (needs the engine's stats).
3. **Stage 3: pluggable deciders**, then language-model agents on a few clubs with record-and-replay; measure them against autopilot clubs on trade volume, deal balance and the bands.
4. **Stage 4: scale up** only if the bands hold.

## Owner decisions (2026-10-24)
1. The staging is approved: autopilot first, language-model agents later.
2. Picks may be traded; **no cash and no trading of cap space**; no limit on the number of trades.
3. At most three players a side (the builder also capped picks at three a side).
4. Discretion and relationships generate story material from the trade log; none of it flows back into the engine.

## Stage 1 as built
`src/dfl/ledger/trading.py`, `data/characters/TRADING.csv`, `tests/test_trading.py` (14 tests). Entry points: `public_list` (the searchable list), `value` and `score` (a club's private valuation), `block`, `negotiate` (the three rounds), `window` (one run of the trading step, called from the offseason trading step and weekly through the last open paid week), and `World.trade_log`. Each deal is dry-run through the ledger before it is proposed. About 0.7 trades per club per year in an eight-club test world; mostly one-for-one swaps.

Since Edition 8.29 each negotiation is also held as a meeting room (`rooms.TradeTape`): the same messages, round limit enforced by the ledger.

Known limits: the market price still derives from the hidden overall (stage 2 fixes this); the proposer estimates the other club's valuation from that public price; counters add one asset only; no three-way trades; no relationship effects yet.
