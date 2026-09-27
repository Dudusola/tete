"""espn_basketball.py - NBA fixtures, kickoff times, live state and FINAL SCORES
from ESPN's public scoreboard API. No key, no Cloudflare.

WHAT THIS IS FOR, AND WHAT IT DELIBERATELY IS NOT
-------------------------------------------------
This is step 2 of `docs/basketball.md` §5, and step 2 only: build a GRADEABLE
LEDGER before a single basketball prediction exists. Coverage first, claims
second — the rule bug #16 paid for.

It therefore does four things and refuses a fifth:

    1. inserts NBA fixtures into `matches` with sport='basketball'
    2. writes kickoff_utc + live_state (the half of kickoff_times.py that ports)
    3. records FINAL scores into `results` once ESPN marks a game completed
    4. distinguishes "the feed is empty" from "no games are scheduled"

    NOT: odds. NOT: predictions. NOT: markets.

**No odds on purpose.** ESPN does carry an NBA moneyline (probed 17 Sept 2026,
`docs/basketball.md` §4a) but a 2-way market is a different shape from a 3-way
one all the way up: `_extract_odds` gates on `if h and d and a`, the feature
vector is `odds_home/draw/away`, and `odds_is_market` assumes that triple. The
tempting fix is `d = 1.0`, which is inventing a prior — exactly what bug #7
exists to forbid. Basketball needs its own extractor and its own feature
layout, and that is step 4, not this file.

WHY THERE IS NO TEAM MATCHER HERE
---------------------------------
Every other results path in this codebase fuzzy-matches names because someone
else inserted the fixture: Forebet writes "Man Utd", ESPN says "Manchester
United", and a matcher has to bridge them. That matcher is where bug #22 lived
(`Barcelona` vs `Barcelona U20` scoring 0.92) and it sits on a path where a
false positive grades a pick against the WRONG GAME — bug #1 territory.

Here, we insert the fixture ourselves from the same feed that later reports the
score, so the two sides share ESPN's own stable event id. The result is joined
on `match_id`, exactly. No similarity threshold, no near-miss band, no chance of
crossing two fixtures. If a future source starts inserting NBA fixtures too,
that assumption breaks and this file needs the matcher back — so the id scheme
is namespaced (`espnbb_<event id>`) to make a second writer obvious.

NBA CANNOT DRAW, AND THIS FILE ASSERTS IT
-----------------------------------------
Overtime is played until someone wins, so an equal final score is not a draw —
it is bad data. Recording it as outcome='D' would quietly poison a ledger whose
whole purpose is verification, so an equal-score "completed" game is logged and
SKIPPED rather than stored.

`over_2_5` and `btts` are written as NULL, not 0/1. They are football semantics:
every NBA game clears 2.5 "goals" and both teams always score, so filling them
would be recording two facts that are true by construction and mean nothing. A
NULL says "this question does not apply to this sport"; a 1 says "we checked".

EMPTY IS NOT FAILURE, BUT A DEAD FEED IS
----------------------------------------
Bug #5's rule is that a scraper which got nothing must fail loudly. The NBA has
a five-month off-season, so "0 events" is the CORRECT answer from June to
October and failing on it would make the pipeline red every day for months —
which is bug #14's noise trap, and noise is not harmless: it hid a real outage.

So this distinguishes the two, the way clubelo.py does:
    every request failed          -> exit 1 (the feed is unreachable)
    requests succeeded, 0 events  -> exit 0 (nothing is scheduled)

Usage:
    python scrapers/espn_basketball.py                # -7 to +3 days
    python scrapers/espn_basketball.py --days-back 45 # backfill results
    python scrapers/espn_basketball.py --dry-run
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from models.sports import BASKETBALL

# The sport is a PATH SEGMENT above the league slug, which is why the four
# football ESPN scrapers could not simply be pointed at a new slug: they all
# hardcode ".../sports/soccer/{league}/...". Same API shape, different sport.
BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/{league}/scoreboard"

# slug -> league label stored in `matches.league`. One entry today; the NBA is
# the only basketball competition docs/basketball.md researched, and adding
# WNBA/NCAA before the NBA path is proven would be widening an unvalidated
# surface. `mens-college-basketball` and `wnba` are the slugs when it is time.
LEAGUES = {"nba": "NBA"}

# ESPN returns one scoreboard per league per day, so a window is N requests.
# Ten threads is what results_espn.py settled on (sequential took 18 minutes
# there and blew the 15-minute step timeout).
MAX_WORKERS = 10

ID_PREFIX = "espnbb"


def _fetch_day(job) -> dict | None:
    slug, day, session = job
    try:
        resp = session.get(BASE.format(league=slug),
                           params={"dates": day}, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"ESPN basketball {slug} {day}: {e}")
        return None


def fetch_window(days_back: int, days_ahead: int) -> tuple[list, int]:
    """(events, successful_requests) across LEAGUES for the date window.

    The second element is what separates "feed down" from "off-season" — see
    the module docstring. Callers must check it before treating [] as failure.
    """
    days = [(datetime.now() + timedelta(days=d)).strftime("%Y%m%d")
            for d in range(-days_back, days_ahead + 1)]
    session = requests.Session()
    jobs = [(slug, day, session) for slug in LEAGUES for day in days]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        responses = list(pool.map(_fetch_day, jobs))

    events, ok = [], 0
    for (slug, day, _), data in zip(jobs, responses):
        if data is None:
            continue
        ok += 1
        for ev in data.get("events", []):
            parsed = _parse_event(ev, slug)
            if parsed:
                events.append(parsed)

    # The same game appears under two adjacent UTC dates when it tips off late
    # US evening. Keyed on ESPN's event id, so this is exact, not heuristic.
    seen, uniq = set(), []
    for e in events:
        if e["match_id"] not in seen:
            seen.add(e["match_id"])
            uniq.append(e)
    return uniq, ok


def _parse_event(ev: dict, slug: str) -> dict | None:
    """One ESPN event -> our row shape, or None if it is unusable."""
    event_id = ev.get("id")
    if not event_id:
        return None
    comp = (ev.get("competitions") or [{}])[0]
    status = (comp.get("status") or {}).get("type", {})

    teams = {}
    for c in comp.get("competitors", []):
        side = c.get("homeAway")
        name = ((c.get("team") or {}).get("displayName") or "").strip()
        if not side or not name:
            continue
        score = c.get("score")
        try:
            score = int(score) if score is not None else None
        except (TypeError, ValueError):
            score = None
        teams[side] = (name, score)
    if set(teams) != {"home", "away"}:
        return None

    kickoff_utc = (ev.get("date") or "").replace("T", " ").replace("Z", "")[:16]
    return {
        "match_id": f"{ID_PREFIX}_{event_id}",
        "league": LEAGUES.get(slug, slug),
        "espn_league": slug,
        "home_team": teams["home"][0],
        "away_team": teams["away"][0],
        "match_date": (ev.get("date") or "")[:10],
        "kickoff_utc": kickoff_utc or None,
        # ESPN's own three-state field, the same one kickoff_times.py stores.
        # It is a SNAPSHOT, not a live feed -- api/server.py ages it past
        # LIVE_MAX_HOURS (bug #13). That expiry is football-shaped (3.5h); a
        # basketball equivalent belongs with the basketball serving path.
        "live_state": (status.get("state") or None),
        "completed": bool(status.get("completed")),
        "home_score": teams["home"][1],
        "away_score": teams["away"][1],
    }


def store(events: list, dry_run: bool = False) -> dict:
    """Upsert fixtures, then record finals. Returns counts for the caller."""
    counts = {"fixtures": 0, "updated": 0, "results": 0, "ties_skipped": 0}
    if not events:
        return counts
    conn = get_connection()
    for e in events:
        if dry_run:
            logger.info(f"  [dry-run] {e['match_date']} {e['home_team']} v "
                        f"{e['away_team']} ({e['live_state']}) "
                        f"{e['home_score']}-{e['away_score']}")
            continue

        cur = conn.execute("""
            INSERT OR IGNORE INTO matches
            (match_id, sport, league, home_team, away_team, match_date,
             kickoff_time, status, kickoff_utc, live_state, live_checked_at,
             espn_league)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [e["match_id"], BASKETBALL, e["league"], e["home_team"],
              e["away_team"], e["match_date"],
              (e["kickoff_utc"] or " ")[11:16] or None,
              "finished" if e["completed"] else "upcoming",
              e["kickoff_utc"], e["live_state"],
              datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
              e["espn_league"]])
        if cur.rowcount:
            counts["fixtures"] += 1
        else:
            # Already known: refresh the volatile fields. live_state is the
            # whole reason this runs more than once for the same fixture.
            conn.execute("""
                UPDATE matches
                   SET kickoff_utc = ?, live_state = ?, live_checked_at = ?,
                       status = CASE WHEN ? THEN 'finished' ELSE status END
                 WHERE match_id = ?
            """, [e["kickoff_utc"], e["live_state"],
                  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                  1 if e["completed"] else 0, e["match_id"]])
            counts["updated"] += 1

        if not e["completed"]:
            continue
        hs, as_ = e["home_score"], e["away_score"]
        if hs is None or as_ is None:
            logger.warning(f"  completed but no score: {e['match_id']}")
            continue
        if hs == as_:
            # Not a draw -- overtime makes one impossible. Bad data, and
            # recording it would put a fake outcome in the ledger (bug #1).
            counts["ties_skipped"] += 1
            logger.warning(f"  equal final score on a completed NBA game, "
                           f"skipped: {e['match_id']} {hs}-{as_}")
            continue
        cur = conn.execute("""
            INSERT OR IGNORE INTO results
            (match_id, home_goals, away_goals, outcome, over_2_5, btts)
            VALUES (?, ?, ?, ?, NULL, NULL)
        """, [e["match_id"], hs, as_, "H" if hs > as_ else "A"])
        if cur.rowcount:
            counts["results"] += 1
            logger.info(f"  scored: {e['home_team']} {hs}-{as_} "
                        f"{e['away_team']}")
    conn.commit()
    conn.close()
    return counts


def run(days_back: int = 7, days_ahead: int = 3, dry_run: bool = False) -> int:
    events, ok_requests = fetch_window(days_back, days_ahead)

    if ok_requests == 0:
        logger.error("Every ESPN basketball request failed - feed unreachable.")
        return 1

    if not events:
        # Correct and expected for most of the year. See the module docstring:
        # failing here would make the pipeline red all summer.
        logger.info(f"ESPN basketball reachable ({ok_requests} responses), "
                    f"0 games scheduled in the window - off-season or rest day.")
        return 0

    counts = store(events, dry_run=dry_run)
    logger.success(
        f"NBA: {len(events)} events -> {counts['fixtures']} new fixtures, "
        f"{counts['updated']} refreshed, {counts['results']} finals recorded"
        + (f", {counts['ties_skipped']} ties SKIPPED" if counts["ties_skipped"] else "")
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="NBA fixtures + results from ESPN")
    ap.add_argument("--days-back", type=int, default=7,
                    help="how far back to look for finals (default 7)")
    ap.add_argument("--days-ahead", type=int, default=3,
                    help="how far ahead to pull fixtures (default 3)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be written, touch nothing")
    args = ap.parse_args()
    return run(args.days_back, args.days_ahead, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
