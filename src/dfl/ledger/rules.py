"""Constants from Design Document 8.33, sections 0 and 11. Money is exact (Fraction), in the document's units."""
from fractions import Fraction as F

CAP, RESERVE, MIN_SALARY = F(1000), F(25), F(5)
REQUIRED_PLACES = 48
ROSTER_MAX, PS_MAX, PS_VETERANS_MAX, PS_VETERAN_AFTER = 53, 16, 6, 2     # veteran = more than 2 credited seasons
CAP_COUNTED_BEFORE_CUT = ROSTER_MAX + PS_MAX                           # Ed 8.9: top-N rule (NFL top 51 of 90, adapted)
VESTED_SEASONS = 4                                                      # Ed 8.9: released at 4+ credited seasons skips waivers
PRESEASON_CEILING = 90                      # roster plus practice squad before the cut
PAID_WEEKS = 22                             # salary prorates over 22 paid regular-season weeks
IR_MIN_GAMES, IR_RETURNS_MAX = 4, 8
ELEVATIONS_PER_GAME, ELEVATIONS_PER_PLAYER = 2, 3
ACTIVE_LIMIT, ACTIVE_LIMIT_FEW_OL, OL_FOR_FULL = 48, 47, 8
MAX_COVERED_YEARS, MAX_PRORATION_YEARS = 5, 5
ROOKIE_SCALE = (F(25), F(18), F(14), F(11), F(9), F(7), F(5))
ROOKIE_YEARS, R1_OPTION = 4, F(35)
POOL_YEARS_MAX = 4                          # Ed 8.12: unsigned this many consecutive years in the pool -> retired for good, removed from the list
TRADE_LAST_PAID_WEEK = 9                    # trades close the Friday before numbered week 9, which is calendar (paid) week 10

# Largest committed (owed-regardless) money a club may carry in any one year:
VIABILITY_MAX_COMMITTED = CAP - REQUIRED_PLACES * MIN_SALARY - RESERVE      # 735
assert VIABILITY_MAX_COMMITTED == 735
