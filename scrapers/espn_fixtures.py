"""espn_fixtures.py - football fixtures from ESPN for the days PredictZ cannot reach.

WHY THIS EXISTS
---------------
PredictZ has exactly two pages, `/predictions/` and `/predictions/tomorrow/`
(`OFFSETS = {"today": 0, "tomorrow": 1}`), and it is the only source that
INSERTS football fixtures. So `matches` has never held more than two days of
football, and everything downstream inherits that ceiling.

That ceiling went from invisible to painful during the September 2026
international break. Measured 23 Sept, the big five and UCL had zero fixtures
for ten days, but club football was still being played in Argentina, MLS,
Brazil, Colombia and the lower English and Spanish divisions -- 87 fixtures on
the Saturday alone. The app showed nothing, honestly but uselessly, because
Saturday was four days away and nothing had ever looked that far.

The odds are ALREADY THERE for those days. Counted the same morning, per day
ahead, across all 67 league slugs:

    +0 Wed    4 fixtures   4 with odds  (100%)   <- the old window
    +1 Thu    1            1            (100%)   <- the old window ended here
    +2 Fri   17           17            (100%)
    +3 Sat   87           62            ( 71%)
    +4 Sun   38           27            ( 71%)
    +6 Tue    3            1            ( 33%)
    +7 Wed    9            1            ( 11%)

111 priced fixtures sitting one HTTP call away, against the 5 the pipeline
could see. Past +5 the books have not posted yet, so DEFAULT_DAYS stops at 5:
fetching further would add fixtures with no odds, which predict.py correctly
refuses to serve anyway (bug #7), at the cost of 67 more requests a day.

WHY IT ONLY INSERTS FROM +2
---------------------------
PredictZ owns +0 and +1. Inserting ESPN rows there too would manufacture a
cross-source duplicate for every fixture, every day -- bug #2, deliberately,
at scale. Starting at +2 means the two sources never write the same day.

Duplicates still arise at the day boundary: a fixture inserted here as +2
becomes PredictZ's "tomorrow" the next morning, and PredictZ inserts its own
row. That is the exact case `db/dedupe_fixtures.py` exists for (fuzzy teams,
+/-1 day) and it runs daily, before predict. This scraper must therefore run
BEFORE the deduper in the pipeline, never after.

WHY IT WRITES ODDS ITSELF
-------------------------
`kickoff_times.py` attaches ESPN odds by fuzzy-matching OUR fixtures back to
ESPN's feed -- necessary when PredictZ inserted the row under a different
spelling. Here the fixture and the odds come from the same payload, so the
odds are written directly from the event we already hold. No matcher, no
SIM_FLOOR, no near-miss band on a path that feeds model FEATURES (bug #22's
warning about odds being features, not decoration).

Odds go in as a `scraped_predictions` row with source='espn', the same shape
kickoff_times writes, so predict.py reads them through the existing path and
nothing downstream needs to know where they came from. A fixture with no odds
yet is still inserted -- it will be priced by a later run as kickoff nears,
and until then predict.py skips it, which is correct.

Usage:
    python scrapers/espn_fixtures.py               # +2..+5
    python scrapers/espn_fixtures.py --days 7
    python scrapers/espn_fixtures.py --dry-run
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
from models.sports import FOOTBALL
from scrapers.espn_leagues import ALL_LEAGUES
from scrapers.kickoff_times import BASE, _extract_odds

# PredictZ owns +0 and +1; see the module docstring.
FIRST_DAY = 2
# Odds coverage collapses past +5 (33% at +6, 11% at +7), so fetching further
# buys fixtures predict.py cannot serve. Measured, not guessed -- and worth
# re-measuring if the window is ever changed (bug #10's rule).
DEFAULT_DAYS = 5

MAX_WORKERS = 16
ID_PREFIX = "espnfb"


def _fetch(job):
    slug, day, session = job
    try:
        resp = session.get(BASE.format(league=slug),
                           params={"dates": day.strftime("%Y%m%d")}, timeout=20)
        resp.raise_for_status()
        return slug, resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"ESPN {slug} {day}: {e}")
        return slug, None


def fetch_fixtures(last_day: int = DEFAULT_DAYS,
                   first_day: int = FIRST_DAY) -> tuple[list, int]:
    """(fixtures, ok_requests) for days first_day..last_day inclusive."""
    days = [date.today() + timedelta(days=d)
            for d in range(first_day, last_day + 1)]
    session = requests.Session()
    jobs = [(slug, day, session) for slug in sorted(ALL_LEAGUES) for day in days]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        responses = list(pool.map(_fetch, jobs))

    out, ok = [], 0
    for slug, data in responses:
        if data is None:
            continue
        ok += 1
        # ESPN names the competition once, at the top of the response -- not on
        # each event. Reading it per-event silently yields the raw slug
        # ("usa.usl.l1"), which would then reach the UI and gradeability's
        # GROUP BY m.league as a league name.
        league_name = ((data.get("leagues") or [{}])[0].get("name") or slug)
        for ev in data.get("events", []):
            parsed = _parse(ev, slug, league_name)
            if parsed:
                out.append(parsed)

    seen, uniq = set(), []
    for f in out:
        if f["match_id"] not in seen:
            seen.add(f["match_id"])
            uniq.append(f)
    return uniq, ok


def _parse(ev: dict, slug: str, league_name: str) -> dict | None:
    event_id = ev.get("id")
    if not event_id:
        return None
    comp = (ev.get("competitions") or [{}])[0]
    status = (comp.get("status") or {}).get("type", {})
    # Only fixtures that have not started. A finished game arriving through a
    # FIXTURE source would be a prediction opportunity that never existed, and
    # results_espn.py already owns scoring.
    if status.get("state") != "pre":
        return None

    teams = {}
    for c in comp.get("competitors", []):
        side = c.get("homeAway")
        name = ((c.get("team") or {}).get("displayName") or "").strip()
        if side and name:
            teams[side] = name
    if set(teams) != {"home", "away"}:
        return None

    kickoff_utc = (ev.get("date") or "").replace("T", " ").replace("Z", "")[:16]
    if not kickoff_utc:
        return None
    return {
        "match_id": f"{ID_PREFIX}_{event_id}",
        "espn_league": slug,
        "league": league_name,
        "home_team": teams["home"],
        "away_team": teams["away"],
        "match_date": kickoff_utc[:10],
        "kickoff_utc": kickoff_utc,
        "odds": _extract_odds(comp),
    }


def store(fixtures: list, dry_run: bool = False) -> dict:
    counts = {"inserted": 0, "existing": 0, "priced": 0}
    if not fixtures:
        return counts
    conn = get_connection()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for f in fixtures:
        if dry_run:
            logger.info(f"  [dry-run] {f['match_date']} {f['home_team']} v "
                        f"{f['away_team']} [{f['league']}] "
                        f"odds={f['odds']}")
            continue
        cur = conn.execute("""
            INSERT OR IGNORE INTO matches
            (match_id, sport, league, home_team, away_team, match_date,
             kickoff_time, status, kickoff_utc, live_state, live_checked_at,
             espn_league)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'upcoming', ?, 'pre', ?, ?)
        """, [f["match_id"], FOOTBALL, f["league"], f["home_team"],
              f["away_team"], f["match_date"], f["kickoff_utc"][11:16],
              f["kickoff_utc"], now, f["espn_league"]])
        counts["inserted" if cur.rowcount else "existing"] += 1

        if not f["odds"]:
            continue
        # One odds row per match per run at most. predict.py reads the LATEST
        # scraped_predictions row, so re-writing an identical row every run
        # would churn the table for nothing.
        h, d, a = f["odds"]
        already = conn.execute("""
            SELECT 1 FROM scraped_predictions
            WHERE match_id = ? AND source = 'espn' AND odds_home IS NOT NULL
        """, [f["match_id"]]).fetchone()
        if already:
            continue
        conn.execute("""
            INSERT INTO scraped_predictions
            (match_id, source, odds_home, odds_draw, odds_away)
            VALUES (?, 'espn', ?, ?, ?)
        """, [f["match_id"], h, d, a])
        counts["priced"] += 1
    conn.commit()
    conn.close()
    return counts


def run(last_day: int = DEFAULT_DAYS, dry_run: bool = False,
        first_day: int = FIRST_DAY) -> int:
    fixtures, ok = fetch_fixtures(last_day, first_day)
    if ok == 0:
        logger.error("Every ESPN fixture request failed - feed unreachable.")
        return 1
    if not fixtures:
        # Distinguishable from a dead feed, same rule as espn_basketball.py:
        # a genuinely empty schedule is not a scraper failure.
        logger.warning(f"ESPN reachable ({ok} responses) but no upcoming "
                       f"fixtures in +{first_day}..+{last_day}.")
        return 0
    counts = store(fixtures, dry_run=dry_run)
    logger.success(
        f"ESPN fixtures +{first_day}..+{last_day}: {len(fixtures)} found, "
        f"{counts['inserted']} new, {counts['existing']} already known, "
        f"{counts['priced']} priced")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"last day ahead to fetch (default {DEFAULT_DAYS})")
    # --first-day 0: take TODAY and TOMORROW as well. The +2 default exists to
    # leave those days to PredictZ, and since ~30 Sep 2026 PredictZ blocks
    # GitHub's IPs outright (probed at 06:00 and 19:15 UTC - not time of day),
    # so in the cloud those days were simply empty. run_daily.py passes 0
    # whenever PredictZ is skipped (PP_SKIP_PREDICTZ=1).
    ap.add_argument("--first-day", type=int, default=FIRST_DAY,
                    help=f"first day ahead to fetch (default {FIRST_DAY})")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return run(args.days, args.dry_run, args.first_day)


if __name__ == "__main__":
    sys.exit(main())
