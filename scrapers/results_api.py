"""
results_api.py - Fetch REAL final scores from the football-data.org API
(free tier) and score our predictions. Independent of Forebet, immune to
Cloudflare. Matches fixtures to our DB by fuzzy team name + date.

Setup (one time):
  1. Register free at https://www.football-data.org/client/register
  2. Put the token in .env:   FOOTBALL_DATA_TOKEN=your_token_here

Free tier covers: World Cup, Champions League, Premier League, Championship,
Ligue 1, Bundesliga, Serie A, La Liga, Eredivisie, Primeira Liga,
Brazil Serie A, Euros. (Smaller leagues like Iceland/Lithuania are NOT
covered - Forebet remains the source for those.)

Usage:
    python scrapers/results_api.py
"""

import os
import sys
import time
from datetime import datetime, timedelta
from difflib import SequenceMatcher

import requests
from dotenv import load_dotenv
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()
from db.database import get_connection
# The ONE fixture matcher — see team_match() for why this file no longer has
# its own. consensus.py owns it; dedupe_fixtures.py and build_slip use it too.
from models.consensus import team_sim

TOKEN = os.getenv("FOOTBALL_DATA_TOKEN")
BASE = "https://api.football-data.org/v4"


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def team_match(db_name: str, api_name: str) -> bool:
    """Fuzzy team-name match for GRADING. Delegates to the shared matcher.

    This used to be a private raw-ratio check (>0.75, or >0.78 after stripping
    'FC'/'CF'). It failed on the single most common English naming pattern —
    the short name we store versus the full name the feeds return — because
    the strings genuinely are that different:

        Cardiff    vs Cardiff City      0.65 raw  -> NO MATCH
        Swindon    vs Swindon Town      0.63 raw  -> NO MATCH
        West Ham   vs West Ham United   0.60 raw  -> NO MATCH

    Eleven of fourteen EFL Cup fixtures on 8 Aug 2026 failed this way. ESPN
    HAD every score ("Cardiff City 3-2 Swindon Town", completed) and they sat
    ungraded, showing "score pending" on the Picks page indefinitely.

    That is bug #8's failure mode exactly, and worse than a random gap: the
    unmatched set is not random, it is "clubs whose short name differs from
    the feed's full name" — overwhelmingly English ones. A tier percentage
    computed while those are missing is computed on a biased sample.

    consensus.team_sim scores all three pairs above at 0.92 because it
    normalises names and understands containment on a token boundary. It is
    the same matcher consensus, build_slip and (since today) dedupe use —
    there is no reason grading should hold a weaker private opinion.
    """
    return team_sim(db_name, api_name) >= 0.86


# football-data.org's FREE tier rejects a date range wider than 10 days with a
# bare HTTP 400 — no message naming the limit. Discovered asking for 45 days
# for the weekly deep backfill. So the window is CHUNKED rather than the
# backfill being capped: a limit on one request is not a limit on how far back
# we are allowed to look.
MAX_RANGE_DAYS = 10


def fetch_finished_from_api(days_back: int = 7) -> list | None:
    """Finished matches from the last N days across free-tier competitions.

    Returns None only if EVERY chunk failed. A partial result is still worth
    grading — refusing it would throw away real scores because one window in
    the middle timed out.
    """
    matches, failures, chunks = [], 0, 0
    end = datetime.now()
    remaining = max(1, int(days_back))

    while remaining > 0:
        span = min(remaining, MAX_RANGE_DAYS)
        start = end - timedelta(days=span)
        chunks += 1
        try:
            resp = requests.get(
                f"{BASE}/matches",
                headers={"X-Auth-Token": TOKEN},
                params={"dateFrom": start.strftime("%Y-%m-%d"),
                        "dateTo": end.strftime("%Y-%m-%d"),
                        "status": "FINISHED"},
                timeout=30,
            )
            resp.raise_for_status()
            matches += resp.json().get("matches", [])
        except requests.RequestException as e:
            failures += 1
            logger.warning(f"football-data.org {start:%Y-%m-%d}..{end:%Y-%m-%d} "
                           f"failed: {e}")
        end = start
        remaining -= span
        # The free tier allows ~10 requests/minute and answers a burst with a
        # bare HTTP 429. A 45-day backfill is 5 windows, which is within the
        # quota on its own but not if anything else has just called the API —
        # observed live while testing. Pace them: this runs weekly and
        # unattended, so 6 seconds a window costs nothing and removes the one
        # way a deep backfill can silently return less than it should.
        if remaining > 0:
            time.sleep(6)

    if failures == chunks:
        logger.error(f"football-data.org: all {chunks} request(s) failed")
        return None
    if failures:
        logger.warning(f"football-data.org: {failures}/{chunks} windows failed - "
                       f"grading what came back")
    if chunks > 1:
        logger.info(f"football-data.org: {len(matches)} finished matches "
                    f"across {chunks} windows ({days_back} days)")

    out = []
    for m in matches:
        ft = m.get("score", {}).get("fullTime", {})
        if ft.get("home") is None:
            continue
        out.append({
            "home_team": m["homeTeam"]["name"],
            "away_team": m["awayTeam"]["name"],
            "date": m["utcDate"][:10],
            "home_goals": ft["home"],
            "away_goals": ft["away"],
        })
    logger.info(f"API returned {len(out)} finished matches "
                f"(last {days_back} days)")
    return out


def dates_close(d1: str, d2: str) -> bool:
    try:
        a = datetime.strptime(d1, "%Y-%m-%d")
        b = datetime.strptime(d2, "%Y-%m-%d")
        return abs((a - b).days) <= 1
    except (ValueError, TypeError):
        return d1 == d2


def score_our_fixtures(api_results: list) -> int:
    """Match API results to our unfinished fixtures and record scores."""
    conn = get_connection()
    ours = conn.execute("""
        SELECT match_id, home_team, away_team, match_date
        FROM matches
        WHERE match_id NOT IN (SELECT match_id FROM results)
    """).fetchall()

    inserted = 0
    for db_m in ours:
        for api_m in api_results:
            if not dates_close(db_m["match_date"], api_m["date"]):
                continue
            if not (team_match(db_m["home_team"], api_m["home_team"]) and
                    team_match(db_m["away_team"], api_m["away_team"])):
                continue

            hg, ag = api_m["home_goals"], api_m["away_goals"]
            outcome = "H" if hg > ag else ("A" if ag > hg else "D")
            cur = conn.execute("""
                INSERT OR IGNORE INTO results
                (match_id, home_goals, away_goals, outcome, over_2_5, btts)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [db_m["match_id"], hg, ag, outcome,
                  1 if (hg + ag) > 2.5 else 0,
                  1 if (hg > 0 and ag > 0) else 0])
            if cur.rowcount:
                inserted += 1
                logger.info(f"  scored: {db_m['home_team']} vs "
                            f"{db_m['away_team']} = {hg}-{ag}")
            conn.execute("UPDATE matches SET status='finished' WHERE match_id=?",
                         [db_m["match_id"]])
            break

    conn.commit()
    conn.close()
    logger.success(f"💾 Recorded {inserted} results from football-data.org")
    return inserted


def run(days: int = 7):
    if not TOKEN:
        logger.error("FOOTBALL_DATA_TOKEN missing from .env\n"
                     "  1. Register free: https://www.football-data.org/client/register\n"
                     "  2. Add to .env:   FOOTBALL_DATA_TOKEN=your_token")
        sys.exit(1)
    api_results = fetch_finished_from_api(days)
    if api_results is None:
        sys.exit(1)          # request failed — flag it, don't pretend success
    if api_results:
        score_our_fixtures(api_results)
    else:
        logger.info("API reachable but no finished matches in window (off-season?)")


if __name__ == "__main__":
    # --days exists so the weekly deep backfill can reach further than the
    # daily lookback. Without it, anything ungraded for longer than the
    # default window is never graded at all — the hole that made the HIGH
    # tier read 57% when it was really 38% (bug #8).
    import argparse
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--days", type=int, default=7,
                     help="how far back to look for finished matches")
    run(_ap.parse_args().days)
