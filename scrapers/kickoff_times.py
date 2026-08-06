"""
Fill in real UTC kickoff times, so picks for already-started matches can be hidden.

THE PROBLEM: matches.kickoff_time is a bare 'HH:MM' with no timezone, and
PredictZ — the primary fixture source in the cloud — leaves it at '00:00' for
~88% of fixtures. '00:00' means UNKNOWN, not midnight. With no reliable time,
the app kept recommending matches that had already kicked off.

SOURCE: ESPN's public scoreboard API. Chosen over API-Football deliberately —
it needs no key, has no quota, already works from GitHub's IPs, and returns both
an exact UTC kickoff and a state field ('pre' / 'in' / 'post'). API-Football
suspended this project's account mid-session, which is exactly the fragility we
don't want under a correctness fix.

Fixtures we can't match keep kickoff_utc = NULL, and downstream MUST treat NULL
as "unknown", never as "already played" — hiding a match we merely failed to
time would be worse than showing it.

Usage:
    python scrapers/kickoff_times.py            # today + tomorrow
    python scrapers/kickoff_times.py --days 3
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import requests
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from models.consensus import _sim
from scrapers.results_espn import LEAGUES

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"

# Same threshold consensus.py uses for "these are the same two teams".
SIM_THRESHOLD = 0.80

# ESPN marks a fixture 'pre' (not started), 'in' (live) or 'post' (finished).
NOT_STARTED = "pre"


def _fetch(args) -> list:
    league, day = args
    try:
        r = requests.get(BASE.format(league=league), params={"dates": day},
                         timeout=20)
        r.raise_for_status()
        events = r.json().get("events") or []
    except (requests.RequestException, ValueError):
        return []

    out = []
    for ev in events:
        iso = ev.get("date") or ""          # e.g. 2026-08-06T22:00Z (UTC)
        comps = (ev.get("competitions") or [{}])[0].get("competitors") or []
        home = away = None
        for c in comps:
            name = (c.get("team") or {}).get("displayName")
            if c.get("homeAway") == "home":
                home = name
            elif c.get("homeAway") == "away":
                away = name
        if not (iso and home and away):
            continue
        try:
            dt = datetime.strptime(iso, "%Y-%m-%dT%H:%MZ")
        except ValueError:
            continue
        state = ((ev.get("status") or {}).get("type") or {}).get("state", "")
        out.append((home, away, dt.strftime("%Y-%m-%d %H:%M"), state))
    return out


def fetch_reference(days: int) -> list:
    """(home, away, kickoff_utc, state) across every ESPN league for N days."""
    jobs = [(lg, (date.today() + timedelta(days=i)).strftime("%Y%m%d"))
            for i in range(days) for lg in LEAGUES]
    # Same 10-thread pattern results_espn.py uses — sequential took ~18 min
    # there and would blow the pipeline's per-step timeout.
    with ThreadPoolExecutor(max_workers=10) as pool:
        per_job = list(pool.map(_fetch, jobs))
    ref = [row for job in per_job for row in job]
    logger.info(f"ESPN reference: {len(ref)} fixtures across {len(LEAGUES)} "
                f"leagues over {days} day(s)")
    return ref


def enrich(days: int = 2) -> int:
    reference = fetch_reference(days)
    if not reference:
        logger.error("No reference kickoffs fetched — nothing to enrich.")
        return 0

    conn = get_connection()
    # Every upcoming match is re-checked, not just untimed ones: live state
    # changes through the day, and a match that was 'pre' an hour ago may be
    # 'in' now. The kickoff itself is only written once (it doesn't move).
    rows = conn.execute("""
        SELECT match_id, home_team, away_team, match_date, kickoff_utc
        FROM matches
        WHERE match_date >= date('now')
    """).fetchall()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    updated = live = 0
    for row in rows:
        best, best_state, best_score = None, None, 0.0
        our_date = datetime.strptime(row["match_date"], "%Y-%m-%d").date()
        for ref_home, ref_away, ref_utc, state in reference:
            ref_date = datetime.strptime(ref_utc[:10], "%Y-%m-%d").date()
            # +/-1 day: sources disagree on date near midnight (bug #2's rule).
            if abs((ref_date - our_date).days) > 1:
                continue
            score = min(_sim(row["home_team"], ref_home),
                        _sim(row["away_team"], ref_away))
            if score > best_score:
                best, best_state, best_score = ref_utc, state, score

        if not (best and best_score >= SIM_THRESHOLD):
            continue

        if row["kickoff_utc"] is None:
            conn.execute("UPDATE matches SET kickoff_utc = ? WHERE match_id = ?",
                         (best, row["match_id"]))
            updated += 1
        conn.execute(
            "UPDATE matches SET live_state = ?, live_checked_at = ? "
            "WHERE match_id = ?", (best_state, now, row["match_id"]))
        if best_state in ("in", "post"):
            live += 1

    conn.commit()
    remaining = conn.execute("""
        SELECT COUNT(*) FROM matches
        WHERE match_date >= date('now') AND kickoff_utc IS NULL
    """).fetchone()[0]
    conn.close()

    logger.success(f"Kickoff times set for {updated} matches; {live} marked "
                   f"in-play/finished ({remaining} still without a time — these "
                   f"stay visible, because 'unknown' must never be read as "
                   f"'already played')")
    return updated


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    args = ap.parse_args()
    enrich(args.days)
    # Never fatal: NULL kickoffs are handled downstream as "unknown".
    sys.exit(0)
