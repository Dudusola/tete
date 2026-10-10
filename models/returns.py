"""returns.py - would backing our served picks have made money?

Hit rate says how often a pick won; it says nothing about whether following
the picks pays, because short odds need a high hit rate just to break even.
Measured 28 Sep 2026: served picks won 71% at average odds 1.45 (break-even
69%) - a flat-stake return of +0.4%, 95% interval -7.4% to +8.2%. Accurate,
calibrated, and so far roughly break-even: the model mostly agrees with the
bookmaker, whose margin takes the difference.

The Record and Picks pages publish this next to the hit rate so no reader
mistakes a calibrated forecast for a proven profit. Prices are the SportyBet
odds stored AT PICK TIME (market_snapshot.py, `book_odds`) - never a later
price, which would flatter or punish with hindsight. Picks with no stored
price are counted in `unpriced`, not guessed.
"""

import math

SERVED = ("HIGH", "MEDIUM")


def served_returns(conn) -> dict:
    """Flat 1-unit stake on every graded served 1X2 pick that has a stored price.

    Uses the latest prediction per match (bug #3), football only.
    """
    rows = conn.execute(f"""
        WITH latest AS (SELECT MAX(id) AS id FROM model_predictions GROUP BY match_id)
        SELECT p.book_odds,
               CASE WHEN (p.best_bet = 'Home Win' AND r.outcome = 'H')
                      OR (p.best_bet = 'Away Win' AND r.outcome = 'A')
                      OR (p.best_bet = 'Draw'     AND r.outcome = 'D') THEN 1 ELSE 0 END AS won
        FROM latest l
        JOIN model_predictions p ON p.id = l.id
        JOIN results r ON r.match_id = p.match_id
        JOIN matches m ON m.match_id = p.match_id
        WHERE m.sport = 'football'
          AND p.confidence IN ({",".join("?" * len(SERVED))})
    """, SERVED).fetchall()
    priced = [(float(o), int(w)) for o, w in rows if o is not None and float(o) > 1.0]
    n = len(priced)
    out = {"graded": len(rows), "priced": n, "unpriced": len(rows) - n}
    if n < 2:
        return {**out, "roi_pct": None}
    pl = [(o - 1.0) if w else -1.0 for o, w in priced]
    mean = sum(pl) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in pl) / (n - 1))
    half = 1.96 * sd / math.sqrt(n)
    avg_odds = sum(o for o, _ in priced) / n
    won = sum(w for _, w in priced)
    # Verdict from the interval, not the point estimate: a +0.4% mean with a
    # -7..+8 interval is "not distinguishable from break-even", not "profit".
    lo, hi = mean - half, mean + half
    verdict = "profit" if lo > 0 else "loss" if hi < 0 else "break_even"
    return {
        **out,
        "won": won,
        "hit_pct": round(won / n * 100, 1),
        "avg_odds": round(avg_odds, 2),
        "breakeven_pct": round(100 / avg_odds, 1),
        "roi_pct": round(mean * 100, 1),
        "roi_lo": round(lo * 100, 1),
        "roi_hi": round(hi * 100, 1),
        "verdict": verdict,
        "price_source": "SportyBet odds stored when each pick was made",
    }


if __name__ == "__main__":
    import json
    import os
    import sqlite3
    c = sqlite3.connect(os.getenv("DB_PATH", "./db/sports_predictor.db"))
    print(json.dumps(served_returns(c), indent=2))
