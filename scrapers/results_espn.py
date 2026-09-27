"""
results_espn.py - Fetch REAL final scores from ESPN's public scoreboard API
(no key needed) and score our predictions. Third results source, Cloudflare-
immune, covering leagues football-data.org's free tier misses (MLS, Liga MX,
Argentina, ...). Matches fixtures to our DB by fuzzy team name + ±1 day.

Only events ESPN marks completed (STATUS_FULL_TIME etc.) are recorded —
never grade before full-time.

Usage:
    python scrapers/results_espn.py [--days N]
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from scrapers.results_api import team_match, dates_close
from models.sports import FOOTBALL, sport_sql

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"

# ESPN league slugs → label (leagues the football-data free tier does NOT cover,
# plus UEFA comps whose qualifiers football-data misses). Slugs verified July 2026;
# an unknown slug returns HTTP 400 and is just logged as a warning.
# ONE shared league list — see scrapers/espn_leagues.py. This used to be a
# 35-entry dict here while kickoff_times.py queried 66, and the 31 missing
# slugs meant 184 finished EFL Cup matches were never graded: we knew they had
# kicked off and finished, but never asked ESPN for the score.
# Values are display labels only; the slug is what matters.
from scrapers.espn_leagues import ALL_LEAGUES

LEAGUES = {slug: slug for slug in sorted(ALL_LEAGUES)}


def fetch_finished(days_back: int = 7) -> list | None:
    """Finished matches from ESPN across LEAGUES for the last N days.
    Returns None only if every request failed (network/API down)."""
    out, ok_requests = [], 0
    days = [(datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
            for i in range(days_back + 1)]
    session = requests.Session()

    def fetch_one(job):
        slug, label, day = job
        try:
            resp = session.get(BASE.format(league=slug),
                               params={"dates": day}, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"ESPN {label} {day}: {e}")
            return None

    jobs = [(slug, label, day)
            for slug, label in LEAGUES.items() for day in days]
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(fetch_one, jobs))

    for data in responses:
        if data is None:
            continue
        ok_requests += 1
        for ev in data.get("events", []):
                comp = ev.get("competitions", [{}])[0]
                status = comp.get("status", {}).get("type", {})
                if not status.get("completed"):
                    continue  # never grade before full-time
                teams = {}
                for c in comp.get("competitors", []):
                    if c.get("score") is None:
                        break
                    teams[c.get("homeAway")] = (c["team"]["displayName"],
                                                int(c["score"]))
                if set(teams) != {"home", "away"}:
                    continue
                out.append({
                    "home_team": teams["home"][0],
                    "away_team": teams["away"][0],
                    "date": ev.get("date", "")[:10],
                    "home_goals": teams["home"][1],
                    "away_goals": teams["away"][1],
                })
    if ok_requests == 0:
        return None
    # dedupe (same event can appear under adjacent UTC dates)
    seen, uniq = set(), []
    for m in out:
        key = (m["home_team"], m["away_team"], m["date"])
        if key not in seen:
            seen.add(key)
            uniq.append(m)
    logger.info(f"ESPN returned {len(uniq)} finished matches "
                f"across {len(LEAGUES)} leagues, last {days_back} days")
    return uniq


def score_our_fixtures(api_results: list) -> int:
    conn = get_connection()
    # Football only, and this one is load-bearing rather than tidy: the rows
    # below are matched to ESPN's FOOTBALL scoreboard by FUZZY TEAM NAME. Leave
    # basketball fixtures in this set and a name that scores above the floor in
    # both sports grades an NBA game against a football scoreline -- bug #1,
    # arrived at through bug #22's matcher. scrapers/espn_ledger.py records
    # its own finals by exact ESPN event id and never needs this path.
    ours = conn.execute(f"""
        SELECT m.match_id, m.home_team, m.away_team, m.match_date
        FROM matches m
        WHERE m.match_id NOT IN (SELECT match_id FROM results)
          {sport_sql(FOOTBALL)}
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
    logger.success(f"Recorded {inserted} results from ESPN")
    return inserted


def run(days_back: int = 7):
    api_results = fetch_finished(days_back)
    if api_results is None:
        logger.error("Every ESPN request failed — exiting non-zero")
        sys.exit(1)
    if api_results:
        score_our_fixtures(api_results)
    else:
        logger.info("ESPN reachable but no finished matches in window")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7)
    run(p.parse_args().days)
