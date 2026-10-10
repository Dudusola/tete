"""
db/sporty_value_report.py - does SportyBet's price beat the fair price? Read-only.

The test scrapers/sporty_lines.py exists for. For every FINISHED football match
with both a SportyBet 1X2 (its last price before kickoff) and an ESPN 3-way
price, each outcome's expected value is

    EV = SportyBet odds x ESPN fair probability - 1
         (fair = ESPN's implied probabilities with ESPN's margin removed)

and the outcomes are bucketed by EV and backed at 1 unit flat. If backing
the +EV bucket returns money on enough matches - with an interval that
excludes zero - SportyBet is mispriced against the market and the app can
point at it honestly. If not, the gaps are noise and nothing is claimed.

Note, measured 10 Oct 2026: SportyBet's median margin is 4.8% against ESPN's
8.2%, so ESPN is the LESS efficient book of the two. A "fair" price from it is
noisy; that is why the bar below is an interval, not a point estimate.

Usage:  python db/sporty_value_report.py [--min-ev 0.03]
"""

import argparse
import math
import os
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")

QUERY = """
SELECT r.outcome,
       s.odds_home sh, s.odds_draw sd, s.odds_away sa,
       e.odds_home eh, e.odds_draw ed, e.odds_away ea
FROM sporty_lines s
JOIN results r ON r.match_id = s.match_id
JOIN matches m ON m.match_id = s.match_id AND m.sport = 'football'
JOIN scraped_predictions e ON e.id = (
     SELECT MAX(id) FROM scraped_predictions
     WHERE match_id = s.match_id AND source = 'espn'
       AND odds_home IS NOT NULL AND odds_draw IS NOT NULL AND odds_away IS NOT NULL)
WHERE s.market = '1X2'
  -- the LAST SportyBet price seen before kickoff, one row per match
  AND s.id = (SELECT MAX(id) FROM sporty_lines s2
              WHERE s2.match_id = s.match_id AND s2.market = '1X2'
                AND (s2.mins_before IS NULL OR s2.mins_before >= 0))
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-ev", type=float, default=0.03)
    args = ap.parse_args()
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(QUERY).fetchall()
    except sqlite3.OperationalError as e:
        print(f"No sporty_lines yet ({e}). Run scrapers/sporty_lines.py first.")
        return 0

    buckets = {}
    for outcome, sh, sd, sa, eh, ed, ea in rows:
        inv = [1 / eh, 1 / ed, 1 / ea]
        fair = [x / sum(inv) for x in inv]
        for i, (price, key) in enumerate(((sh, "H"), (sd, "D"), (sa, "A"))):
            ev = price * fair[i] - 1
            b = ("+EV" if ev >= args.min_ev else "fair" if ev > -args.min_ev else "-EV")
            buckets.setdefault(b, []).append(price - 1 if outcome == key else -1.0)

    print(f"Finished football matches with both prices: {len(rows)}  "
          f"(EV threshold {args.min_ev:+.0%})\n")
    print(f"{'bucket':6} {'bets':>6} {'ROI':>8}   95% CI")
    for b in ("+EV", "fair", "-EV"):
        pl = buckets.get(b, [])
        if not pl:
            continue
        n = len(pl)
        mean = sum(pl) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in pl) / (n - 1)) if n > 1 else 0
        lo, hi = mean - 1.96 * sd / math.sqrt(n), mean + 1.96 * sd / math.sqrt(n)
        verdict = ("too few" if n < 200 else "PROFITABLE" if lo > 0
                   else "losing" if hi < 0 else "within noise")
        print(f"{b:6} {n:6} {mean:+8.1%}   [{lo:+.1%}, {hi:+.1%}]  {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
