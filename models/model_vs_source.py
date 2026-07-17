"""
model_vs_source.py - Measure how much the Random Forest ADDS over just
reading Forebet.

Read-only. For every fixture where we have both a model prediction and a
Forebet scraped prediction, compare their picks:
  - AGREE:    model's best outcome == Forebet's best outcome
  - DISAGREE: they differ (the model overrode the scraped signal)

A high disagreement rate = the model has an independent voice.
A ~100% agreement rate = the model is mostly echoing Forebet.

It also shows, among disagreements, HOW the model moved (e.g. Forebet said
Home, model said Draw).

Usage:
    python models/model_vs_source.py
"""

import sys, os, sqlite3
from collections import Counter

DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")

BET_TO_OUT = {"Home Win": "H", "Draw": "D", "Away Win": "A"}
NAME = {"H": "Home", "D": "Draw", "A": "Away"}


def forebet_best(row):
    """Best outcome from Forebet's scraped 1X2 percentages."""
    h, d, a = row["home_win_pct"], row["draw_pct"], row["away_win_pct"]
    if h is None or d is None or a is None:
        return None
    return max([("H", h), ("D", d), ("A", a)], key=lambda x: x[1])[0]


def run():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT m.home_team, m.away_team,
               mp.best_bet,
               p.home_win_pct, p.draw_pct, p.away_win_pct
        FROM model_predictions mp
        JOIN matches m ON m.match_id = mp.match_id
        JOIN scraped_predictions p ON p.id = (
            SELECT MAX(p2.id) FROM scraped_predictions p2
            WHERE p2.match_id = mp.match_id AND p2.source = 'forebet'
        )
    """).fetchall()
    conn.close()

    if not rows:
        print("No overlapping model + Forebet predictions found.")
        return

    agree = 0
    disagree = 0
    shifts = Counter()
    examples = []

    for r in rows:
        model_out = BET_TO_OUT.get(r["best_bet"])
        fb_out = forebet_best(r)
        if model_out is None or fb_out is None:
            continue
        if model_out == fb_out:
            agree += 1
        else:
            disagree += 1
            shifts[f"Forebet {NAME[fb_out]} -> Model {NAME[model_out]}"] += 1
            if len(examples) < 12:
                examples.append(f"{r['home_team']} vs {r['away_team']}: "
                                f"Forebet={NAME[fb_out]}, Model={NAME[model_out]}")

    total = agree + disagree
    if total == 0:
        print("No comparable rows (missing percentages).")
        return

    print(f"\n{'='*55}")
    print("MODEL vs FOREBET — does the model have its own voice?")
    print(f"{'='*55}")
    print(f"Comparable fixtures: {total}")
    print(f"  AGREE:    {agree:>4}  ({agree/total*100:.1f}%)")
    print(f"  DISAGREE: {disagree:>4}  ({disagree/total*100:.1f}%)")

    if disagree:
        print(f"\nHow the model overrode Forebet:")
        for shift, n in shifts.most_common():
            print(f"  {shift}: {n}")
        print(f"\nExamples:")
        for ex in examples:
            print(f"  {ex}")

    print(f"\n{'-'*55}")
    pct = disagree / total * 100
    if pct < 5:
        print("READ: Model almost always agrees with Forebet. Right now it's\n"
              "mostly echoing the scraped signal - its value is in CALIBRATION\n"
              "(confidence tiers), not in independent picks.")
    elif pct < 25:
        print("READ: Model has a modest independent voice - it overrides\n"
              "Forebet sometimes. Whether those overrides are RIGHT needs the\n"
              "honest ledger to confirm.")
    else:
        print("READ: Model disagrees with Forebet often - it's clearly doing\n"
              "its own thing. The ledger will tell us if that's smart or noisy.")


if __name__ == "__main__":
    run()
