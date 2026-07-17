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
from datetime import datetime, timedelta
from difflib import SequenceMatcher

import requests
from dotenv import load_dotenv
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()
from db.database import get_connection

TOKEN = os.getenv("FOOTBALL_DATA_TOKEN")
BASE = "https://api.football-data.org/v4"


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def team_match(db_name: str, api_name: str) -> bool:
    """Fuzzy team-name match, tolerant of 'FC'/'CF' suffixes etc."""
    if _sim(db_name, api_name) > 0.75:
        return True
    # Try stripped common suffixes/prefixes
    strip = lambda s: (s.lower().replace(" fc", "").replace("fc ", "")
                       .replace(" cf", "").replace(" ec", "").strip())
    return _sim(strip(db_name), strip(api_name)) > 0.78


def fetch_finished_from_api(days_back: int = 7) -> list | None:
    """Finished matches from the last N days across free-tier competitions."""
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    date_to = datetime.now().strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            f"{BASE}/matches",
            headers={"X-Auth-Token": TOKEN},
            params={"dateFrom": date_from, "dateTo": date_to,
                    "status": "FINISHED"},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"football-data.org request failed: {e}")
        return None

    out = []
    for m in resp.json().get("matches", []):
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
                f"({date_from} → {date_to})")
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


def run():
    if not TOKEN:
        logger.error("FOOTBALL_DATA_TOKEN missing from .env\n"
                     "  1. Register free: https://www.football-data.org/client/register\n"
                     "  2. Add to .env:   FOOTBALL_DATA_TOKEN=your_token")
        sys.exit(1)
    api_results = fetch_finished_from_api()
    if api_results is None:
        sys.exit(1)          # request failed — flag it, don't pretend success
    if api_results:
        score_our_fixtures(api_results)
    else:
        logger.info("API reachable but no finished matches in window (off-season?)")


if __name__ == "__main__":
    run()
