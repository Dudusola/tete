"""
edge_report.py - Does the model know anything the market doesn't?

This is the question the whole project rests on, and it is NOT the same as
"is the model accurate". A model that agrees with the bookmaker can be very
accurate and still worthless — you could read the odds for free. Any edge lives
entirely in the DISAGREEMENTS.

The report groups graded picks by how far our probability sat from the
bookmaker's at pick time, and shows who was right in each band:

  * We disagreed and were RIGHT more often than the market implied -> a real
    edge, and the size of the gap tells you where it lives.
  * We disagreed and were WRONG -> the picks are a repackaging of the odds,
    with noise added. Worth knowing before inviting anyone.
  * Not enough disagreements to tell -> keep accumulating; do not conclude.

Reads model_predictions.book_prob, which scrapers/market_snapshot.py records at
prediction time. Picks made before that step existed have no snapshot and are
skipped — they cannot be reconstructed, because odds move.

Usage:
    python db/edge_report.py
"""

import math
import os
import sqlite3
import sys

DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")
WIN_NAME = {"H": "Home Win", "D": "Draw", "A": "Away Win"}


def wilson(wins: int, n: int, z: float = 1.96) -> tuple:
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((centre - margin) / d * 100, (centre + margin) / d * 100)


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        WITH latest AS (SELECT MAX(id) AS id FROM model_predictions GROUP BY match_id)
        SELECT mp.best_bet, mp.best_bet_prob, mp.book_prob, r.outcome
        FROM model_predictions mp
        JOIN latest l ON l.id = mp.id
        JOIN results r ON r.match_id = mp.match_id
        WHERE mp.book_prob IS NOT NULL AND mp.best_bet_prob IS NOT NULL
    """).fetchall()
    conn.close()

    if not rows:
        print("No graded picks carry a market snapshot yet.")
        print("scrapers/market_snapshot.py records one per pick from now on;")
        print("earlier picks cannot be backfilled because odds move.")
        return 0

    # Bands by how far we sat from the market, in percentage points.
    bands = [("we >> market  (+15pts or more)", lambda g: g >= 0.15),
             ("we >  market  (+5 to +15)",      lambda g: 0.05 <= g < 0.15),
             ("broadly agree (-5 to +5)",       lambda g: -0.05 < g < 0.05),
             ("we <  market  (-5 to -15)",      lambda g: -0.15 < g <= -0.05),
             ("we << market  (-15pts or more)", lambda g: g <= -0.15)]

    print(f"Graded picks carrying a market snapshot: {len(rows)}\n")
    print(f"{'disagreement band':32} {'record':>10} {'we hit':>8} "
          f"{'market implied':>15} {'verdict':>10}")
    print("-" * 80)

    for label, test in bands:
        sub = [r for r in rows
               if test(r["best_bet_prob"] - r["book_prob"])]
        if not sub:
            continue
        n = len(sub)
        wins = sum(1 for r in sub if r["best_bet"] == WIN_NAME.get(r["outcome"]))
        ours = wins / n * 100
        implied = sum(r["book_prob"] for r in sub) / n * 100
        lo, hi = wilson(wins, n)
        # Only call it either way when the interval clears the market's number.
        if lo > implied:
            verdict = "BEAT mkt"
        elif hi < implied:
            verdict = "lost to mkt"
        else:
            verdict = "too few"
        print(f"{label:32} {wins:4}/{n:<5} {ours:7.1f}% {implied:14.1f}% "
              f"{verdict:>10}   [{lo:.0f}-{hi:.0f}%]")

    print()
    disagreements = [r for r in rows
                     if abs(r["best_bet_prob"] - r["book_prob"]) >= 0.10]
    if len(disagreements) < 30:
        print(f"Only {len(disagreements)} picks disagree with the market by 10+ "
              f"points. That is too few to conclude anything — the honest read "
              f"is 'not yet measurable', not 'no edge'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
