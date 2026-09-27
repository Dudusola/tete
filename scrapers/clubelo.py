"""
clubelo.py - An INDEPENDENT opinion, from a rating model rather than a market.

Why this source exists
----------------------
Every other opinion in this ledger traces back to bookmaker prices. PredictZ
publishes odds-derived percentages; the ESPN rows are odds converted to
probabilities. So when the Consensus page said "2 sources agree" it was really
saying "two views of the same market" — which is not the cross-check the
product claims to offer. Forebet was the one genuinely independent model, and
it is permanently unreachable from the cloud (re-verified 11 Aug 2026: HTTP 403
from GitHub Actions on plain requests, Chromium AND Firefox — IP reputation,
not fingerprint).

ClubElo (http://api.clubelo.com) publishes an Elo rating model built from
match RESULTS. It has never seen a bookmaker's line. That is precisely the
missing ingredient: two sources that disagree for different reasons.

Free, no key, no quota, not behind Cloudflare.

What it gives us
----------------
GET /Fixtures returns CSV covering roughly the next five days, one row per
fixture, with a full goal-difference distribution:

    Date,Country,Home,Away,GD<-5,...,GD=0,...,GD>5,R:0-0,R:0-1,...

Summing those columns gives real H/D/A probabilities:
    home = every GD > 0     draw = GD=0     away = every GD < 0

Coverage and its limits
-----------------------
ClubElo covers top European club football plus the UEFA competitions. It does
NOT cover South America, MLS, Scottish lower cups or German regional leagues.
Measured against our fixture list on 11 Aug 2026 the overlap was 7 of 76 (9%),
because that day was Scottish Challenge Cup and Regionalliga North. Overlap
rises sharply once Europe's leagues restart — which is also when fixture volume
arrives.

**This is an OPINION source, not a fixture source.** It only ever attaches a
view to matches we already have; it never inserts a match. Letting a second
source create fixtures is how the cross-source duplicates of bug #2 happened.

Usage:
    python scrapers/clubelo.py            (attach opinions for upcoming matches)
    python scrapers/clubelo.py --days 5   (how far ahead to match)
"""

import argparse
import csv
import io
import os
import sys
import time
from datetime import datetime, timedelta

import requests
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from models.consensus import team_sim
from models.sports import FOOTBALL, sport_sql

FIXTURES_URL = "http://api.clubelo.com/Fixtures"
HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36")}

# Same bar the rest of the codebase uses for "is this the same fixture".
# One shared matcher: three private copies of this logic have already been
# found and removed (dedupe, grading, consensus).
SIM_FLOOR = 0.80          # per side
SIM_MEAN = 0.86           # and the pair must average at least this


def fetch_fixtures(attempts: int = 4) -> list:
    """ClubElo's upcoming fixtures with their goal-difference distribution.

    Retries, because the service is genuinely flaky: three consecutive probes
    on 11 Aug 2026 returned 502, then 200, then a connection failure. It is a
    small hobby API, not a commercial feed, and a single 502 says nothing about
    whether it is actually down. Without retries this source would look broken
    most days and get switched off for the wrong reason.
    """
    for attempt in range(1, attempts + 1):
        try:
            r = requests.get(FIXTURES_URL, headers=HEADERS, timeout=40)
            r.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(r.text)))
            if rows:
                logger.info(f"ClubElo returned {len(rows)} fixtures "
                            f"(attempt {attempt})")
                return rows
            logger.warning(f"ClubElo returned an empty body (attempt {attempt})")
        except requests.RequestException as e:
            logger.warning(f"ClubElo attempt {attempt}/{attempts} failed: {e}")
        if attempt < attempts:
            time.sleep(3 * attempt)          # 3s, 6s, 9s
    logger.error("ClubElo unreachable after retries")
    return []


def outcome_probs(row: dict) -> tuple | None:
    """(home, draw, away) as percentages from the GD distribution.

    Returns None rather than guessing when the columns are missing — a source
    that changes shape must go quiet, not start emitting invented numbers.
    """
    try:
        home = draw = away = 0.0
        for col, value in row.items():
            if not col or not col.startswith("GD"):
                continue
            v = float(value or 0)
            if col == "GD=0":
                draw += v
            elif col.startswith("GD>") or (col.startswith("GD=") and int(col[3:]) > 0):
                home += v
            elif col.startswith("GD<") or (col.startswith("GD=") and int(col[3:]) < 0):
                away += v
        total = home + draw + away
        if total <= 0:
            return None
        return (round(home / total * 100, 1),
                round(draw / total * 100, 1),
                round(away / total * 100, 1))
    except (ValueError, TypeError):
        return None


def attach(days: int = 5) -> tuple:
    """Attach a ClubElo opinion to every upcoming match we can match.

    Returns (matched, considered) so the caller can tell "no European fixtures
    today" apart from "the feed broke".
    """
    fixtures = fetch_fixtures()
    if not fixtures:
        return (0, 0)

    conn = get_connection()
    horizon = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    # Football only: the rows below are fuzzy-matched by team name against a
    # FOOTBALL feed. NBA/NFL fixtures (scrapers/espn_ledger.py) must never
    # reach a soccer matcher - see models/sports.py and results_espn.py.
    ours = conn.execute(f"""
        SELECT m.match_id, m.home_team, m.away_team, m.match_date
        FROM matches m
        WHERE m.match_date >= date('now') AND m.match_date <= ?
          {sport_sql(FOOTBALL)}
    """, [horizon]).fetchall()

    matched = 0
    for row in ours:
        best, best_score = None, 0.0
        for fx in fixtures:
            # +/-1 day: sources disagree on the date either side of midnight.
            # Same rule as everywhere else in this codebase (bug #2).
            try:
                d1 = datetime.strptime(row["match_date"], "%Y-%m-%d")
                d2 = datetime.strptime(fx.get("Date", ""), "%Y-%m-%d")
                if abs((d1 - d2).days) > 1:
                    continue
            except (ValueError, TypeError):
                continue
            sh = team_sim(row["home_team"], fx.get("Home", ""))
            sa = team_sim(row["away_team"], fx.get("Away", ""))
            if sh < SIM_FLOOR or sa < SIM_FLOOR:
                continue
            score = (sh + sa) / 2
            if score > best_score:
                best, best_score = fx, score

        if not best or best_score < SIM_MEAN:
            continue
        probs = outcome_probs(best)
        if not probs:
            continue

        h, d, a = probs
        # No odds columns: ClubElo has no market view, which is the entire
        # point. Leaving them NULL keeps predict.py's odds features honest —
        # it must never mistake a rating model for a bookmaker line.
        conn.execute("""
            INSERT INTO scraped_predictions
            (match_id, source, home_win_pct, draw_pct, away_win_pct,
             predicted_home_goals, predicted_away_goals, over_2_5_pct,
             btts_pct, home_form, away_form, odds_home, odds_draw, odds_away)
            VALUES (?, 'clubelo', ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL,
                    NULL, NULL, NULL)
        """, (row["match_id"], h, d, a))
        matched += 1
        logger.debug(f"  {row['home_team']} v {row['away_team']}: "
                     f"H {h}% D {d}% A {a}%")

    conn.commit()
    conn.close()
    return (matched, len(fixtures))


def run(days: int = 5) -> int:
    matched, considered = attach(days)

    # The project rule is "a scraper that got nothing must fail loudly". It
    # needs a carve-out here, and the distinction is real:
    #
    #   feed returned NOTHING          -> broken. Fail, so it is noticed.
    #   feed fine, nothing OVERLAPS    -> legitimate. ClubElo covers European
    #                                     club football; on a day of Copa
    #                                     Libertadores and MLS there is simply
    #                                     nothing for it to say.
    #
    # Failing on the second case would paint the run red most of the summer and
    # teach everyone to ignore it — the exact trap that hid a real outage in
    # bug #14.
    if considered == 0:
        logger.error("ClubElo returned no fixtures at all - treating as a failure")
        return 1
    if matched == 0:
        logger.warning(f"ClubElo had {considered} fixtures but none overlap our "
                       f"list (expected outside the European season)")
        return 0
    logger.success(f"ClubElo opinions attached to {matched} matches "
                   f"(from {considered} fixtures offered)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5,
                    help="how far ahead to look for matchable fixtures")
    args = ap.parse_args()
    sys.exit(run(args.days))
