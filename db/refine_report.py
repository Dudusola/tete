"""refine_report.py - is there anything in the record worth refining YET?

Boss (6 Oct 2026): "look at the records we have and try to refine better".
The answer that day was no - and this script exists so the question can be
re-asked in one command as the ledger grows, instead of re-derived by hand
(which is how two earlier "findings" were acted on and then reversed - see
docs/history.md "What's next" item 4).

It asks three things of the SERVED football picks (HIGH + MEDIUM, 1X2):

  1. SEGMENTS - by tier, home/away, price band, agreement with the
     bookmaker, league. Hit rate vs the tier's claim, and flat-stake return
     at the SportyBet price stored when the pick was made.
  2. STABILITY - the record split in half by prediction date. A segment is
     only a CANDIDATE if it is on the same side of its claim / break-even in
     BOTH halves. One half alone is how noise gets promoted to a rule.
  3. BLEND - does mixing the model with the bookmaker's pick-time price beat
     the model on picks it was not fitted to? Logistic blend fitted on the
     earlier half of ALL priced predictions, scored once on the later half,
     paired bootstrap CI on the log-loss difference.

What it does NOT do: change anything. A candidate here is a lead to re-check
at the next doubling of the record, not an instruction.

Findings on 6 Oct 2026 (395 served graded, 1,084 priced predictions):
  - HIGH 77.5% vs claim 75.0%, MEDIUM 61.0% vs 60.6%; ROI -0.2% overall.
  - Only stable lead: price 1.50-1.80 below claim in both halves (53.5 vs
    59.8, 57.3 vs 62.1) - not significant in either.
  - MEDIUM ROI +6.0% then -15.9% by half: noise, NOT a reason to drop it.
  - Blend weights book 0.90 / model 0.32; blend vs model -0.0042
    [-0.0101, +0.0020]: no measurable gain. Not shipped.

Usage:
    python db/refine_report.py
"""

import math
import os
import sqlite3
import sys
from collections import defaultdict

DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")
MIN_HALF = 15          # per half, for the stability table


def wilson(k, n):
    if n == 0:
        return 0.0, 0.0
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


def load(conn, served_only: bool):
    where = "AND p.confidence IN ('HIGH','MEDIUM') AND p.best_bet IN ('Home Win','Away Win')" \
        if served_only else "AND p.book_prob > 0 AND p.best_bet IN ('Home Win','Away Win','Draw')"
    return conn.execute(f"""
        WITH latest AS (SELECT MAX(id) id FROM model_predictions GROUP BY match_id)
        SELECT p.match_id, p.best_bet, p.confidence, p.model_version, p.predicted_at,
               p.home_win_prob, p.draw_prob, p.away_win_prob, p.book_odds, p.book_prob,
               m.league, r.outcome
        FROM latest l JOIN model_predictions p ON p.id = l.id
        JOIN matches m ON m.match_id = p.match_id
        JOIN results r ON r.match_id = p.match_id
        WHERE m.sport = 'football' {where}
        ORDER BY p.predicted_at
    """).fetchall()


def prob(r):
    v = {"Home Win": r["home_win_prob"], "Away Win": r["away_win_prob"],
         "Draw": r["draw_prob"]}[r["best_bet"]]
    return v / 100 if v and v > 1 else v


def won(r):
    return int({"Home Win": "H", "Away Win": "A", "Draw": "D"}[r["best_bet"]] == r["outcome"])


def roi(rs):
    pl = [(r["book_odds"] - 1) if won(r) else -1.0 for r in rs
          if r["book_odds"] and r["book_odds"] > 1]
    n = len(pl)
    if n < 2:
        return None
    m = sum(pl) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in pl) / (n - 1))
    h = 1.96 * sd / math.sqrt(n)
    return n, 100 * m, 100 * (m - h), 100 * (m + h)


def price_band(r):
    o = r["book_odds"]
    if not o:
        return "no price"
    return "<1.30" if o < 1.30 else "1.30-1.50" if o < 1.5 else "1.50-1.80" if o < 1.8 else "1.80+"


def agreement(r):
    if not r["book_prob"]:
        return "no price"
    g = 100 * (prob(r) - r["book_prob"])
    return "we > book 5+" if g >= 5 else "we < book 5+" if g <= -5 else "agree +-5"


SEGMENTS = {
    "tier": lambda r: r["confidence"],
    "pick": lambda r: r["best_bet"],
    "price": price_band,
    "vs book": agreement,
    "league": lambda r: r["league"],
}


def segments(rows):
    mid = rows[len(rows) // 2]["predicted_at"]
    print(f"\n  Served graded 1X2 picks: {len(rows)}   (halves split at {mid[:10]})")
    print(f"  {'segment':34} {'n':>4} {'hit%':>6} {'claim':>6} {'95% CI':>13}  "
          f"{'ROI at pick-time price':>26}   both halves?")
    candidates = []
    for name, fn in SEGMENTS.items():
        groups = defaultdict(list)
        for r in rows:
            groups[fn(r)].append(r)
        for key, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            if len(rs) < 20:
                continue
            n, k = len(rs), sum(map(won, rs))
            claim = 100 * sum(map(prob, rs)) / n
            lo, hi = wilson(k, n)
            rr = roi(rs)
            rtxt = f"{rr[1]:+6.1f}% [{rr[2]:+.1f},{rr[3]:+.1f}]" if rr else "-"
            a = [r for r in rs if r["predicted_at"] < mid]
            b = [r for r in rs if r["predicted_at"] >= mid]
            stable = "-"
            if len(a) >= MIN_HALF and len(b) >= MIN_HALF:
                da = 100 * sum(map(won, a)) / len(a) - 100 * sum(map(prob, a)) / len(a)
                db_ = 100 * sum(map(won, b)) / len(b) - 100 * sum(map(prob, b)) / len(b)
                if da < 0 and db_ < 0:
                    stable = f"BELOW claim both ({da:+.1f}, {db_:+.1f})"
                    candidates.append(f"{name}={key}")
                elif da > 0 and db_ > 0:
                    stable = f"above claim both ({da:+.1f}, {db_:+.1f})"
                else:
                    stable = f"flips ({da:+.1f}, {db_:+.1f})"
            sig = "  *" if hi < claim or lo > claim else ""
            print(f"  {name + ': ' + str(key):34.34} {n:4} {100*k/n:6.1f} {claim:6.1f} "
                  f"[{lo:4.1f},{hi:4.1f}]  {rtxt:>26}   {stable}{sig}")
    print("  (* = 95% CI excludes the claim on the whole record)")
    return candidates


def blend(rows):
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        print("\n  BLEND: needs numpy + scikit-learn (the pipeline venv has both).")
        return None

    def lg(p):
        p = min(max(p, 1e-4), 1 - 1e-4)
        return math.log(p / (1 - p))

    def nll(t, p):
        p = min(max(p, 1e-9), 1 - 1e-9)
        return -(t * math.log(p) + (1 - t) * math.log(1 - p))

    half = len(rows) // 2
    fit, test = rows[:half], rows[half:]
    X = lambda rs: np.array([[lg(prob(r)), lg(r["book_prob"])] for r in rs])
    Y = lambda rs: np.array([won(r) for r in rs])
    m = LogisticRegression(C=1e6).fit(X(fit), Y(fit))
    pb = m.predict_proba(X(test))[:, 1]
    yt = Y(test)
    d = np.array([nll(t, b) - nll(t, prob(r)) for t, b, r in zip(yt, pb, test)])
    rng = np.random.default_rng(7)
    boots = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(4000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    verdict = "BLEND BETTER" if hi < 0 else ("blend worse" if lo > 0 else "no measurable gain")
    print(f"\n  BLEND (fit {len(fit)} priced predictions, scored on the next {len(test)}):")
    print(f"    weights: model {m.coef_[0][0]:+.3f}, bookmaker {m.coef_[0][1]:+.3f}")
    print(f"    log loss blend - model: {d.mean():+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]  -> {verdict}")
    return verdict


def main() -> int:
    if not os.path.exists(DB_PATH):
        print(f"No database at {DB_PATH}")
        return 1
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    print("=" * 96)
    print("  REFINE REPORT - is anything in the football record worth changing yet?")
    print("=" * 96)
    candidates = segments(load(conn, served_only=True))
    verdict = blend(load(conn, served_only=False))
    print("\n  VERDICT")
    if candidates:
        print(f"    Below claim in BOTH halves: {', '.join(candidates)}.")
        print("    A lead, not a rule: re-check when the record has doubled before acting.")
    else:
        print("    No segment is below its claim in both halves.")
    if verdict == "BLEND BETTER":
        print("    The model+bookmaker blend beat the model out of sample - worth a proposal.")
    print("    Calibration of the tiers themselves: python db/calibration_health.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
