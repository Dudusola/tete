"""
clv_report.py - Does the market move TOWARD our picks after we make them?

Closing-line value (CLV) per pick = close_prob - book_prob, the implied
probability of OUR selection just before kickoff minus the same at pick time,
from the same feed. Positive means the price shortened: money arriving after we
picked agreed with us. It is the standard test of a betting edge, and it needs
no match result, so it is measurable weeks before edge_report.py's graded
answer is.

How to read it:
  * mean CLV reliably ABOVE zero (the 95% interval excludes 0) -> the model
    finds value before the market does. The strongest evidence available here.
  * interval straddles 0 -> no evidence either way yet; keep accumulating.
  * reliably BELOW zero -> the market moves AWAY from our picks: we are late,
    picking what it has already abandoned.

Only picks whose opening and closing price come from the SAME feed are scored:
across two bookmakers, "movement" would just be their disagreement. Picks
stamped before book_source existed (27 Sep 2026) are listed separately.

Raw 1/odds on both sides, margin included. The overround barely changes
between two observations of one book, so it largely cancels in the difference.

Usage:
    python db/clv_report.py
"""

import math
import os
import sqlite3
import sys

DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")


def wilson(wins: int, n: int, z: float = 1.96) -> tuple:
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((centre - half) / d, (centre + half) / d)


def mean_ci(xs: list, z: float = 1.96) -> tuple:
    n = len(xs)
    m = sum(xs) / n
    if n < 2:
        return m, m, m
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    half = z * sd / math.sqrt(n)
    return m, m - half, m + half


def line(label: str, rows: list) -> str:
    clv = [r["clv"] for r in rows]
    m, lo, hi = mean_ci(clv)
    pos = sum(1 for c in clv if c > 0)
    plo, phi = wilson(pos, len(clv))
    verdict = ("n<30, too few" if len(clv) < 30 else
               "EDGE: market moves toward us" if lo > 0 else
               "market moves AWAY from us" if hi < 0 else
               "within noise")
    return (f"{label:26} {len(clv):5} {m*100:+7.2f} [{lo*100:+6.2f},{hi*100:+6.2f}]"
            f"  {pos/len(clv):6.1%} [{plo:.0%}-{phi:.0%}]  {verdict}")


def main() -> int:
    if not os.path.exists(DB_PATH):
        print(f"No database at {DB_PATH}")
        return 1
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("""
        SELECT mp.confidence, mp.best_bet_prob, mp.book_prob, mp.close_prob,
               mp.book_source, mp.close_source, mp.book_observed_at,
               mp.close_at, mp.close_mins_before, r.outcome, mp.best_bet
        FROM model_predictions mp
        JOIN matches m ON m.match_id = mp.match_id
        LEFT JOIN results r ON r.match_id = mp.match_id
        WHERE mp.book_prob IS NOT NULL AND mp.close_prob IS NOT NULL
          AND m.sport = 'football'
          AND mp.id = (SELECT MAX(mp2.id) FROM model_predictions mp2
                       WHERE mp2.match_id = mp.match_id)
    """)]
    conn.close()

    for r in rows:
        r["clv"] = r["close_prob"] - r["book_prob"]
    same = [r for r in rows if r["book_source"] and r["book_source"] == r["close_source"]]
    unknown = [r for r in rows if not r["book_source"]]
    mixed = [r for r in rows if r["book_source"] and r["book_source"] != r["close_source"]]

    print("=" * 84)
    print("  CLOSING-LINE VALUE — does the price move toward our picks before kickoff?")
    print("=" * 84)
    print(f"  Picks with both prices: {len(rows)}  |  same feed: {len(same)}  |  "
          f"source unrecorded (pre-27 Sep): {len(unknown)}  |  different feeds "
          f"(excluded): {len(mixed)}")
    if not same and not unknown:
        print("\n  Nothing to score yet. closing.yml stamps closes hourly; give it days.")
        return 0

    head = f"  {'':26} {'n':>5} {'CLV pts':>7} {'95% CI':>16}  {'% +CLV':>7}"
    print("\n" + head)
    if same:
        print("  " + line("ALL (same feed)", same))
        for tier in ("HIGH", "MEDIUM", "LOW"):
            sub = [r for r in same if (r["confidence"] or "").upper() == tier]
            if sub:
                print("  " + line(f"  {tier}", sub))
        bands = [("  we >> market (+10 pts)", lambda g: g >= 0.10),
                 ("  we > market (+3..+10)", lambda g: 0.03 <= g < 0.10),
                 ("  agree (-3..+3)", lambda g: -0.03 < g < 0.03),
                 ("  we < market (-3 or less)", lambda g: g <= -0.03)]
        print("\n  by how far we disagreed with the market at pick time:")
        for label, test in bands:
            sub = [r for r in same if r["best_bet_prob"] is not None
                   and test(r["best_bet_prob"] - r["book_prob"])]
            if sub:
                print("  " + line(label, sub))
        mins = sorted(r["close_mins_before"] for r in same
                      if r["close_mins_before"] is not None)
        if mins:
            print(f"\n  Close taken a median {mins[len(mins)//2]} min before kickoff "
                  f"(range {mins[0]}-{mins[-1]}).")
    if unknown:
        print("\n  Unverified source (stamped before book_source existed — every "
              "September\n  pick was ESPN-priced, but that is not recorded per pick):")
        print("  " + line("ALL (unverified)", unknown))

    graded = [r for r in same + unknown if r["outcome"] in ("H", "D", "A")
              and r["best_bet"] in ("Home Win", "Draw", "Away Win")]
    if len(graded) >= 30:
        won = lambda r: {"Home Win": "H", "Draw": "D", "Away Win": "A"}[r["best_bet"]] == r["outcome"]
        def logloss(key):
            eps = 1e-6
            return sum(-math.log(max(eps, r[key])) if won(r)
                       else -math.log(max(eps, 1 - r[key])) for r in graded) / len(graded)
        print(f"\n  On {len(graded)} graded picks, log loss of our selection's probability"
              f" (lower is better):\n    model {logloss('best_bet_prob'):.4f}  |  "
              f"price at pick {logloss('book_prob'):.4f}  |  closing price "
              f"{logloss('close_prob'):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
