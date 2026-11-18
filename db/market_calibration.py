"""market_calibration.py - are the Builder's OTHER markets honest?

WHY (3 Nov 2026). The public record grades one thing: the 1X2 pick. But the
Builder's top legs are Double Chance, Over/Under, BTTS and Handicap at 75-80%
("Wigan v Mansfield, 3 goals or fewer, 78%"), and the slip itself says those
"haven't been checked against results yet". docs/history.md's rule is "grade what we
already show BEFORE making more of it bookable" - this grades it.

HOW. Every graded football match with a pre-kickoff prediction (the latest row;
quarantine_late_predictions.py already moved post-kickoff ones out) is re-priced
with the SAME derive_markets() the Builder uses, then each leg is settled with
the SAME models/settle.py that grades tracked slips.

LOOK-AHEAD. The goal ratings are refit daily from CSVs that contain these very
matches, so grading with today's file would be in-sample. The ratings are refit
here WEEKLY, fit(cutoff=Monday of the match's week), and swapped into
goals_model's cache - close to what live had. One cutoff for the whole window
(the first version) froze the scoring level for months and blamed the model for
a lag production never had: Under 3.5 at 70-80% read 74.0% said / 66.9% won.

Read-only. Never writes the DB or data/goals_ratings.json.

    python db/market_calibration.py                    # since 2026-08-26
    python db/market_calibration.py --since 2026-10-01
    python db/market_calibration.py --no-refit         # today's ratings (in-sample, fast)
"""
import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.calibration_health import MIN_BAND_N, wilson
from models import goals_model
from models.markets import derive_markets
from models.settle import settle, WON, LOST
from models.sports import FOOTBALL, sport_sql

DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")
# goals_model shipped 26 Aug 2026; before that the goals markets were priced
# another way (bug #25), so they are not the markets the Builder serves today.
DEFAULT_SINCE = "2026-08-26"
BANDS = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]


def band(p: float) -> str:
    lo, hi = next(b for b in BANDS if b[0] <= p < b[1])
    return f"{lo * 100:.0f}-{min(hi, 1) * 100:.0f}%"


def rows(conn, since: str) -> list:
    return conn.execute(f"""
        SELECT m.match_id, m.home_team, m.away_team, m.league, m.match_date,
               m.kickoff_time, m.kickoff_utc,
               mp.home_win_prob, mp.draw_prob, mp.away_win_prob,
               r.home_goals, r.away_goals
        FROM matches m
        JOIN model_predictions mp ON mp.id = (
            SELECT MAX(id) FROM model_predictions WHERE match_id = m.match_id)
        JOIN results r ON r.match_id = m.match_id
        WHERE m.match_date >= ? AND r.home_goals IS NOT NULL
          {sport_sql(FOOTBALL)}
        ORDER BY m.match_date""", (since,)).fetchall()


def week_start(d: str) -> str:
    day = date.fromisoformat(d[:10])
    return (day - timedelta(days=day.weekday())).isoformat()


def refit(cutoff: str) -> None:
    goals_model._CACHE = goals_model.fit(cutoff=cutoff, verbose=False)
    goals_model._INDEX, goals_model._RESOLVED = None, {}


def grade(conn, since: str, walk: bool = True) -> tuple[dict, int, int]:
    """{(market, band): [won, n, sum_claim]}, fixtures, fixtures with a goal rating."""
    cells = defaultdict(lambda: [0, 0, 0.0])
    fixtures = rated = 0
    fitted = None
    for r in rows(conn, since):
        if walk and week_start(r["match_date"]) != fitted:
            fitted = week_start(r["match_date"])
            refit(fitted)
        pred = dict(r)
        fixtures += 1
        rated += goals_model.expected_goals(r["home_team"], r["away_team"]) is not None
        for o in derive_markets(pred):
            s = settle(o.market, o.selection, r["home_team"], r["away_team"],
                       r["home_goals"], r["away_goals"])
            if s not in (WON, LOST):       # void (DNB draw) or ungradable
                continue
            c = cells[(o.market, band(o.probability))]
            c[0] += s == WON
            c[1] += 1
            c[2] += o.probability
    return cells, fixtures, rated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=DEFAULT_SINCE)
    ap.add_argument("--no-refit", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(DB_PATH):
        sys.exit(f"no ledger at {DB_PATH}")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    if not a.no_refit:
        print("Refitting goal ratings weekly, each on matches before that week (no look-ahead)...")
    cells, fixtures, rated = grade(conn, a.since, walk=not a.no_refit)
    print("=" * 84)
    print(f"  MARKET CALIBRATION - every market derive_markets() prices, since {a.since}")
    print(f"  {fixtures} graded football fixtures; {rated} had a goal rating for both teams"
          + ("  [IN-SAMPLE: --no-refit]" if a.no_refit else ""))
    print("=" * 84)
    print(f"  {'market':<22}{'band':>9}{'n':>7}{'claim':>8}{'actual':>8}   95% CI          verdict")
    flagged = []
    for market in sorted({m for m, _ in cells}):
        for b in BANDS:
            key = (market, band(b[0]))
            if key not in cells:
                continue
            won, n, claim = cells[key]
            p, lo, hi = wilson(won, n)
            claim /= n
            if n < MIN_BAND_N:
                verdict = f"n<{MIN_BAND_N}"
            elif claim > hi:
                verdict = "OVER-CONFIDENT"
            elif claim < lo:
                verdict = "under-confident"
            else:
                verdict = "within noise"
            if verdict in ("OVER-CONFIDENT", "under-confident"):
                flagged.append(f"{market} {key[1]}: said {claim:.1%}, won {p:.1%} (n={n})")
            print(f"  {market:<22}{key[1]:>9}{n:>7}{claim:>8.1%}{p:>8.1%}"
                  f"   [{lo:5.1%}, {hi:5.1%}]  {verdict}")
        print()
    print("  Interval excludes the claim:" if flagged else "  Every band with n>=15 is within noise.")
    for f in flagged:
        print(f"    - {f}")


if __name__ == "__main__":
    assert week_start("2026-11-08") == "2026-11-02" and week_start("2026-11-02") == "2026-11-02"
    assert band(0.705) == "70-80%" and band(0.95) == "90-100%" and band(0.2) == "0-50%"
    assert settle("Over/Under 2.5", "Under 2.5 Goals", "A", "B", 2, 1) == LOST
    main()
