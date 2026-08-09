"""
live_scores.py - Current score for matches that have started but aren't graded.

The Picks page used to show "score pending" for these, which conflated two
completely different situations:

  * the match is being played RIGHT NOW  -> we should show the running score
  * the match finished hours ago and the pipeline hasn't graded it yet
    -> we should show the final score

"Score pending" was also frequently a lie by omission. Most of those rows were
finished matches whose score ESPN had all along; they were never graded because
the grading matcher couldn't match "Cardiff" to "Cardiff City" (see
results_api.team_match). Fixing that matcher removes the backlog. This module
removes the WINDOW — the hours between kickoff and the next pipeline run.

Why on demand rather than a cron: a private repo's Actions minutes are finite,
and polling every 15 minutes to keep a score fresh for a page nobody is looking
at would spend them for nothing. This costs zero when idle and one request per
league when someone opens the page, memoised for CACHE_SECONDS so a refresh
does not re-fetch.

It is decoration, never data: results recorded in the ledger still come only
from the results scrapers, which require an explicit FINISHED marker. Nothing
here is ever written to the results table — that is bug #1's rule (never grade
before full time, never read a non-final number as a result).
"""

import time
from concurrent.futures import ThreadPoolExecutor

import requests
from loguru import logger

from models.consensus import team_sim

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
CACHE_SECONDS = 60
SIM_FLOOR = 0.80          # per side; the pair must average higher still
SIM_MEAN = 0.86           # same bar the rest of the codebase uses

_cache: dict = {}         # (league, yyyymmdd) -> (fetched_at, events)


def _fetch(job) -> list:
    league, day = job
    key = (league, day)
    hit = _cache.get(key)
    if hit and (time.time() - hit[0]) < CACHE_SECONDS:
        return hit[1]
    try:
        r = requests.get(SCOREBOARD.format(league=league),
                         params={"dates": day}, timeout=8)
        r.raise_for_status()
        events = r.json().get("events") or []
    except (requests.RequestException, ValueError) as e:
        # A live score is decoration. If ESPN is slow or down the page must
        # still render, so this degrades to "no score" rather than an error.
        logger.warning(f"live score fetch failed for {league} {day}: {e}")
        events = []

    out = []
    for ev in events:
        comp = (ev.get("competitions") or [{}])[0]
        status = (ev.get("status") or {}).get("type") or {}
        home = away = hs = as_ = None
        for c in comp.get("competitors") or []:
            name = (c.get("team") or {}).get("displayName")
            if c.get("homeAway") == "home":
                home, hs = name, c.get("score")
            elif c.get("homeAway") == "away":
                away, as_ = name, c.get("score")
        if not (home and away) or hs is None or as_ is None:
            continue
        out.append({
            "home": home, "away": away,
            "home_score": hs, "away_score": as_,
            "state": status.get("state"),          # pre / in / post
            "completed": bool(status.get("completed")),
            # e.g. "45'+2", "HT", "FT". ESPN's own words beat anything we
            # could infer from a clock.
            "detail": status.get("shortDetail") or status.get("description"),
        })
    _cache[key] = (time.time(), out)
    return out


def attach(matches: list) -> None:
    """Add live_score/live_detail to started, ungraded matches, in place.

    `matches` are dicts with home_team, away_team, espn_league, match_date.
    Only rows WITHOUT a recorded result are looked up — a graded match already
    has the authoritative score and must not be second-guessed by a feed.
    """
    jobs = set()
    for m in matches:
        if m.get("result_outcome") is not None:
            continue
        league, date = m.get("espn_league"), m.get("match_date")
        if league and date:
            jobs.add((league, date.replace("-", "")))
    if not jobs:
        return

    with ThreadPoolExecutor(max_workers=min(6, len(jobs))) as pool:
        fetched = list(pool.map(_fetch, jobs))
    events = [e for batch in fetched for e in batch]
    if not events:
        return

    for m in matches:
        if m.get("result_outcome") is not None or not m.get("espn_league"):
            continue
        best, best_score = None, 0.0
        for e in events:
            sh = team_sim(m["home_team"], e["home"])
            sa = team_sim(m["away_team"], e["away"])
            if sh < SIM_FLOOR or sa < SIM_FLOOR:
                continue
            avg = (sh + sa) / 2
            if avg > best_score:
                best, best_score = e, avg
        if best and best_score >= SIM_MEAN:
            m["live_score"] = f"{best['home_score']}-{best['away_score']}"
            m["live_detail"] = best["detail"]
            m["live_final"] = best["completed"]
