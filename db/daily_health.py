"""daily_health.py - a short health report sent to Boss after the daily picks.

WHY (8 Oct 2026). Two silent failures in one week were only found by digging:
  - PredictZ blocked GitHub's IPs (bug #34): five red mornings, unnoticed.
  - ESPN's full club names failed the strict matcher: 120 PRICED fixtures in
    10 days were never predicted, and served football picks fell to 0-2 a day.
Neither broke a step; both showed up in NUMBERS nobody was looking at. This
puts those numbers in front of Boss every morning, with a warning mark on
anything outside its normal range - so the next collapse is seen the next day.

The thresholds are deliberately loose: measured normal, minus a margin. A
health line that warns every day is noise (bug #14), and noise hides the one
warning that matters.

Usage:
    python db/daily_health.py          # print the report
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")

# Measured normal on 6-8 Oct 2026, minus a margin.
MIN_PREDICTED_SHARE = 0.80     # of priced football fixtures (was 0.69 when broken)
MIN_BOOKABLE_SHARE = 0.80      # served picks with a SportyBet price (~0.98 after fix)
MIN_GRADED_SHARE = 0.85        # served picks graded within the last week (~0.96)
MAX_DATA_AGE_HOURS = 30        # newest fixture/odds row


def _pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else f"{a}/0"


def report(conn) -> tuple[list, int]:
    """(lines, number of warnings)."""
    conn.row_factory = sqlite3.Row      # us_sports reads columns by name; indexes still work
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    end = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    lines, warns = [], 0

    def line(ok: bool, text: str):
        nonlocal warns
        warns += not ok
        lines.append(("" if ok else "⚠️ ") + text)

    latest = """WITH latest AS (SELECT MAX(id) id FROM model_predictions GROUP BY match_id)"""
    # 1. The football funnel, today + tomorrow.
    f = conn.execute(f"""{latest}
        SELECT COUNT(*),
               SUM(EXISTS(SELECT 1 FROM model_predictions p WHERE p.match_id = m.match_id)),
               SUM(EXISTS(SELECT 1 FROM latest l JOIN model_predictions p ON p.id = l.id
                          WHERE p.match_id = m.match_id AND p.confidence IN ('HIGH','MEDIUM')))
        FROM matches m
        WHERE m.sport = 'football' AND m.match_date BETWEEN ? AND ?
          AND EXISTS (SELECT 1 FROM scraped_predictions s WHERE s.match_id = m.match_id
                      AND s.odds_home IS NOT NULL AND s.odds_draw IS NOT NULL
                      AND s.odds_away IS NOT NULL)
    """, [today, end]).fetchone()
    priced, predicted, served = f[0] or 0, f[1] or 0, f[2] or 0
    line(not priced or predicted / priced >= MIN_PREDICTED_SHARE,
         f"Football today+tomorrow: {priced} priced -> {predicted} predicted "
         f"({100 * predicted / priced:.0f}%) -> {served} served"
         if priced else "Football today+tomorrow: no priced fixtures")
    if priced and predicted / priced < MIN_PREDICTED_SHARE:
        lines.append("   (priced games not predicted = usually team names not matching "
                     "history: check the 'skipped - teams not in historical data' log line)")

    # 2. Bookable served picks (SportyBet price stored at pick time).
    b = conn.execute(f"""{latest}
        SELECT SUM(p.book_odds IS NOT NULL), COUNT(*)
        FROM latest l JOIN model_predictions p ON p.id = l.id
        JOIN matches m ON m.match_id = p.match_id
        WHERE m.sport = 'football' AND p.confidence IN ('HIGH','MEDIUM')
          AND m.match_date BETWEEN ? AND ?
    """, [today, end]).fetchone()
    bk, bn = b[0] or 0, b[1] or 0
    line(not bn or bk / bn >= MIN_BOOKABLE_SHARE, f"Bookable on SportyBet: {_pct(bk, bn)} served picks")

    # 3. Grading coverage, served football picks of the last 7 days (2+ days old).
    g = conn.execute(f"""{latest}
        SELECT SUM(r.match_id IS NOT NULL), COUNT(*)
        FROM latest l JOIN model_predictions p ON p.id = l.id
        JOIN matches m ON m.match_id = p.match_id
        LEFT JOIN results r ON r.match_id = p.match_id
        WHERE m.sport = 'football' AND p.confidence IN ('HIGH','MEDIUM')
          AND m.match_date BETWEEN date('now','-9 day') AND date('now','-2 day')
    """).fetchone()
    gk, gn = g[0] or 0, g[1] or 0
    line(not gn or gk / gn >= MIN_GRADED_SHARE, f"Graded (last week): {_pct(gk, gn)}")

    # 4. Freshness.
    newest = conn.execute("SELECT MAX(scraped_at) FROM scraped_predictions").fetchone()[0]
    if newest:
        age = (datetime.now(timezone.utc).replace(tzinfo=None)
               - datetime.fromisoformat(newest[:19])).total_seconds() / 3600
        line(age <= MAX_DATA_AGE_HOURS, f"Newest odds/fixture data: {age:.0f}h old")
    else:
        line(False, "No odds/fixture data at all")

    # 5. NHL / NFL served picks coming up (counts only - no thresholds yet).
    try:
        from models.us_sports import picks as us_picks
        codes = ["nhl", "nfl", "nba"]
        d = us_picks(conn, codes)
        rec = d["records"]
        lines.append(" | ".join(f"{c.upper()}: {sum(p['sport'] == c for p in d['picks'])} picks upcoming, "
                                f"record {rec[c]['won']}-{rec[c]['lost']}" for c in codes))
    except Exception as e:                     # never let the report itself fail
        lines.append(f"NHL/NFL: unavailable ({type(e).__name__})")
    return lines, warns


def message(conn) -> str:
    lines, warns = report(conn)
    head = ("🩺 *Daily health* - all normal" if not warns
            else f"🩺 *Daily health* - {warns} warning{'s' if warns > 1 else ''}")
    return "\n".join([head, *lines])


def main() -> int:
    if not os.path.exists(DB_PATH):
        print(f"No database at {DB_PATH}")
        return 1
    conn = sqlite3.connect(DB_PATH)
    print(message(conn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
