"""shrink_backtest.py - would pulling the model toward the market price help?

Read-only. Answers one question on the graded football ledger: if every
served probability were a blend

    p = w * model + (1 - w) * market        (w = 1.0 is what we serve today)

would the picks be better forecasts, and would the served tiers still hold?

Why ask: on 10 Oct 2026 the model's log loss (0.6715) was WORSE than the price
at pick time (0.6645), and where we rated a side 3+ points below the market the
closing line moved away from us (db/clv_report.py). A model that is a noisier
copy of the market should be shrunk toward it.

Honesty rules this script keeps:
- The market is the 3-way price we had AT PICK TIME: the newest priced
  scraped_predictions row observed at or before predicted_at (no hindsight),
  margin removed by normalising.
- Latest prediction per match (bug #3); post-kickoff rows are already
  quarantined (bug #33). Football only.
- w is CHOSEN on the earlier part of the ledger and SCORED on the later part,
  chronologically, so the reported gain is not fitted to its own test set.
- Tiers are recomputed from the probabilities for every w, including 1.0, so
  the comparison is like for like (stored tiers include demotions).

    python db/shrink_backtest.py [--train 0.6] [--boot 2000]
"""

import argparse
import math
import os
import random
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.tiers import TIER_HIGH, TIER_MEDIUM

OUTCOMES = ("H", "D", "A")
BET = {"H": "Home Win", "D": "Draw", "A": "Away Win"}
WEIGHTS = [round(i / 10, 1) for i in range(11)]


def load(conn):
    rows = conn.execute("""
        WITH latest AS (SELECT MAX(id) AS id FROM model_predictions GROUP BY match_id)
        SELECT p.match_id, p.predicted_at, p.home_win_prob, p.draw_prob, p.away_win_prob,
               p.best_bet, p.book_odds, p.book_prob, p.close_prob, p.book_source, p.close_source,
               r.outcome,
               (SELECT s.odds_home || '|' || s.odds_draw || '|' || s.odds_away
                  FROM scraped_predictions s
                 WHERE s.match_id = p.match_id
                   AND s.odds_home > 1 AND s.odds_draw > 1 AND s.odds_away > 1
                   AND s.scraped_at <= p.predicted_at
                 ORDER BY s.scraped_at DESC, s.id DESC LIMIT 1) AS mkt
        FROM latest l
        JOIN model_predictions p ON p.id = l.id
        JOIN results r ON r.match_id = p.match_id
        JOIN matches m ON m.match_id = p.match_id
        WHERE m.sport = 'football' AND r.outcome IN ('H','D','A')
          AND p.home_win_prob IS NOT NULL
        ORDER BY p.predicted_at, p.id
    """).fetchall()
    out, no_mkt = [], 0
    for (mid, at, ph, pd, pa, best, bodds, bprob, cprob, bsrc, csrc, res, mkt) in rows:
        if not mkt:
            no_mkt += 1
            continue
        inv = [1 / float(x) for x in mkt.split("|")]
        s = sum(inv)
        tot = ph + pd + pa
        out.append({
            "at": at, "res": res,
            "model": dict(zip(OUTCOMES, (ph / tot, pd / tot, pa / tot))),
            "market": dict(zip(OUTCOMES, (v / s for v in inv))),
            "stored_bet": best, "book_odds": bodds, "book_prob": bprob,
            "close_prob": cprob if (bsrc and csrc and bsrc == csrc) else None,
        })
    return out, no_mkt


def blend(r, w):
    return {o: w * r["model"][o] + (1 - w) * r["market"][o] for o in OUTCOMES}


def tier(p):
    return "HIGH" if p >= TIER_HIGH else "MEDIUM" if p >= TIER_MEDIUM else "LOW"


def logloss(rows, w):
    return [-math.log(max(blend(r, w)[r["res"]], 1e-12)) for r in rows]


def wilson(k, n):
    if n == 0:
        return (float("nan"),) * 2
    z, ph = 1.96, k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


def served(rows, w):
    """Per served tier: n, won, mean claim; plus flat-stake ROI where the
    blended pick is the same selection we stored a SportyBet price for."""
    t = {"HIGH": [0, 0, 0.0], "MEDIUM": [0, 0, 0.0]}
    pl, clv, changed = [], [], 0
    for r in rows:
        p = blend(r, w)
        o = max(p, key=p.get)
        tr = tier(p[o])
        if tr == "LOW":
            continue
        won = o == r["res"]
        t[tr][0] += 1
        t[tr][1] += won
        t[tr][2] += p[o]
        if BET[o] != r["stored_bet"]:
            changed += 1
            continue
        if r["book_odds"] and r["book_odds"] > 1:
            pl.append(r["book_odds"] - 1 if won else -1.0)
        if r["close_prob"] is not None and r["book_prob"] is not None:
            clv.append(100 * (r["close_prob"] - r["book_prob"]))
    return t, pl, clv, changed


def mean_ci(v):
    n = len(v)
    if n < 2:
        return None
    m = sum(v) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1))
    return m, m - 1.96 * sd / math.sqrt(n), m + 1.96 * sd / math.sqrt(n)


def boot_diff(rows, w, boot, seed=7):
    """Paired bootstrap of mean log loss(w) - log loss(1.0). Negative = w better."""
    a, b = logloss(rows, w), logloss(rows, 1.0)
    d = [x - y for x, y in zip(a, b)]
    rnd = random.Random(seed)
    n = len(d)
    means = sorted(sum(d[rnd.randrange(n)] for _ in range(n)) / n for _ in range(boot))
    return sum(d) / n, means[int(0.025 * boot)], means[int(0.975 * boot)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=float, default=0.6, help="earliest share used to choose w")
    ap.add_argument("--boot", type=int, default=2000)
    a = ap.parse_args()

    conn = sqlite3.connect(os.getenv("DB_PATH", "./db/sports_predictor.db"))
    rows, no_mkt = load(conn)
    cut = int(len(rows) * a.train)
    train, test = rows[:cut], rows[cut:]
    line = "=" * 78
    print(line)
    print("  SHRINK BACKTEST - p = w*model + (1-w)*market at pick time  (w=1.0 = today)")
    print(line)
    print(f"  Graded football predictions with a pre-pick 3-way price: {len(rows)}"
          f"  (skipped, no price: {no_mkt})")
    print(f"  Choose w on the first {len(train)} ({train[0]['at'][:10]} .. {train[-1]['at'][:10]}),"
          f" score on the last {len(test)} ({test[0]['at'][:10]} .. {test[-1]['at'][:10]})")

    print("\n  3-WAY LOG LOSS, every graded prediction (lower is better)")
    print(f"  {'w':>4} {'train':>8} {'test':>8}")
    best_w, best_ll = None, 9
    for w in WEIGHTS:
        tr = sum(logloss(train, w)) / len(train)
        te = sum(logloss(test, w)) / len(test)
        if tr < best_ll:
            best_w, best_ll = w, tr
        mark = "  <- today" if w == 1.0 else ("  <- pure market" if w == 0.0 else "")
        print(f"  {w:>4} {tr:8.4f} {te:8.4f}{mark}")
    print(f"\n  Best w on TRAIN: {best_w}")
    d, lo, hi = boot_diff(test, best_w, a.boot)
    verdict = ("BETTER than today" if hi < 0 else "WORSE than today" if lo > 0
               else "not distinguishable from today")
    print(f"  On TEST, w={best_w} minus today: {d:+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]  -> {verdict}")

    print("\n  SERVED PICKS on TEST (tiers recomputed from the probabilities)")
    print(f"  {'w':>4} {'tier':<7} {'record':>9} {'actual':>7} {'claim':>6}  95% CI"
          f"          ROI same-pick (n)            CLV pts (n)          picks changed")
    for w in sorted({1.0, best_w, 0.5, 0.0}, reverse=True):
        t, pl, clv, changed = served(test, w)
        roi, cl = mean_ci(pl), mean_ci(clv)
        for i, name in enumerate(("HIGH", "MEDIUM")):
            n, k, c = t[name]
            lo_, hi_ = wilson(k, n)
            act = f"{100 * k / n:6.1f}%" if n else "    - "
            clm = f"{100 * c / n:5.1f}%" if n else "   - "
            extra = ""
            if i == 0:
                rs = (f"{100 * roi[0]:+5.1f}% [{100 * roi[1]:+5.1f},{100 * roi[2]:+5.1f}] ({len(pl)})"
                      if roi else "-")
                cs = f"{cl[0]:+.2f} [{cl[1]:+.2f},{cl[2]:+.2f}] ({len(clv)})" if cl else "-"
                extra = f"  {rs:<28} {cs:<22} {changed}"
            print(f"  {w:>4} {name:<7} {k:>4}/{n:<4} {act} {clm}  [{lo_:4.1f}, {hi_:4.1f}]{extra}")
    print("\n  ROI/CLV count only picks whose selection is unchanged by the blend -")
    print("  we hold no stored price for a selection we did not make. 'picks changed'")
    print("  are served picks whose side flips; those are not priced, not guessed.")
    print(line)


if __name__ == "__main__":
    main()
