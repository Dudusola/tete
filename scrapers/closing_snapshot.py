"""closing_snapshot.py - the bookmaker's price for our pick, just before kickoff.

WHY (27 Sep 2026)
-----------------
`market_snapshot.py` stores what the market thought when we picked. This stores
what the SAME market thought just before kickoff. The difference is closing-line
value (CLV): if prices keep moving TOWARD our picks after we make them, the
market is agreeing with us after the fact. That is the most widely used test of
whether a bettor has an edge, and it needs no match result — so it answers
"do we know something the market doesn't?" weeks sooner, and with far less
noise, than waiting for graded outcomes (`db/edge_report.py`).

The idea came from auditing gmalbert/baseball-predictions (CLAUDE.md item 10).

HOW
---
Runs hourly from `.github/workflows/closing.yml`. Each run takes football picks
kicking off within the next WINDOW minutes and re-reads their price from ESPN,
the feed the pick-time price came from (every September pick was priced from
ESPN; `book_source` now records it). Each run OVERWRITES the close, so the last
observation before kickoff is what remains. ESPN removes odds from finished
games (probed: 0 odds on completed MLS fixtures), which is why this cannot be
done afterwards — every missed hour is a close that is gone for good.

Matching reuses kickoff_times' fetcher and team matcher (bug #17: one matcher
per feed), plus a guard that ESPN's kickoff is within 3 hours of ours.

Usage:
    python scrapers/closing_snapshot.py               # picks kicking off in 75 min
    python scrapers/closing_snapshot.py --window 240
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from models.sports import FOOTBALL, sport_sql
from scrapers.kickoff_times import SIM_FLOOR, SIM_MEAN, _fetch, team_sim

# Hourly schedule + 15 minutes of slack for GitHub's cron lag, so no kickoff
# falls between two runs.
WINDOW_MINUTES = 75
MAX_KICKOFF_DRIFT = timedelta(hours=3)
OUTCOME_INDEX = {"Home Win": 0, "Draw": 1, "Away Win": 2}


def candidates(conn, now: datetime, window: int) -> list:
    fmt = "%Y-%m-%d %H:%M"
    return conn.execute(f"""
        SELECT mp.id, mp.best_bet, m.home_team, m.away_team, m.kickoff_utc,
               m.espn_league
        FROM model_predictions mp
        JOIN matches m ON m.match_id = mp.match_id
        WHERE mp.book_odds IS NOT NULL
          AND m.espn_league IS NOT NULL
          AND m.kickoff_utc > ? AND m.kickoff_utc <= ?
          AND mp.id = (SELECT MAX(mp2.id) FROM model_predictions mp2
                       WHERE mp2.match_id = mp.match_id)
          {sport_sql(FOOTBALL)}
    """, [now.strftime(fmt), (now + timedelta(minutes=window)).strftime(fmt)]
    ).fetchall()


def run(window: int = WINDOW_MINUTES) -> int:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn = get_connection()
    picks = candidates(conn, now, window)
    if not picks:
        logger.info(f"No priced picks kick off in the next {window} min.")
        conn.close()
        return 0

    # ESPN files a late-evening UTC kickoff under the previous US date, so ask
    # for the kickoff's date AND the day before.
    jobs = set()
    for p in picks:
        day = datetime.strptime(p["kickoff_utc"], "%Y-%m-%d %H:%M")
        for d in (day, day - timedelta(days=1)):
            jobs.add((p["espn_league"], d.strftime("%Y%m%d")))
    with ThreadPoolExecutor(max_workers=10) as pool:
        reference = [row for rows in pool.map(_fetch, sorted(jobs)) for row in rows]

    if not reference:
        # Every pick here was matched to ESPN earlier, so its scoreboard should
        # list it. Nothing at all means the feed is down, not "no odds".
        logger.error(f"ESPN returned nothing for {len(jobs)} league-days while "
                     f"{len(picks)} picks await a close - feed unreachable. "
                     f"These closes are lost if the next run also misses.")
        conn.close()
        return 1

    stamped, no_odds, unmatched = 0, 0, 0
    now_s = now.strftime("%Y-%m-%d %H:%M:%S")
    for p in picks:
        ours = datetime.strptime(p["kickoff_utc"], "%Y-%m-%d %H:%M")
        best, best_score = None, 0.0
        for ref_home, ref_away, ref_utc, state, odds, _league in reference:
            if state != "pre":
                continue          # an in-play price is not a closing price
            ref_kick = datetime.strptime(ref_utc, "%Y-%m-%d %H:%M")
            if abs(ref_kick - ours) > MAX_KICKOFF_DRIFT:
                continue
            sh = team_sim(p["home_team"], ref_home)
            sa = team_sim(p["away_team"], ref_away)
            if sh < SIM_FLOOR or sa < SIM_FLOOR:
                continue
            if (sh + sa) / 2 > best_score:
                best, best_score = odds, (sh + sa) / 2
        if best_score < SIM_MEAN:
            unmatched += 1
            continue
        idx = OUTCOME_INDEX.get(p["best_bet"])
        if not best or idx is None or not best[idx] or best[idx] <= 1.0:
            no_odds += 1
            continue
        price = float(best[idx])
        conn.execute("""
            UPDATE model_predictions
               SET close_odds = ?, close_prob = ?, close_source = 'espn',
                   close_at = ?, close_mins_before = ?
             WHERE id = ?
        """, [price, round(1.0 / price, 4), now_s,
              int((ours - now).total_seconds() // 60), p["id"]])
        stamped += 1

    conn.commit()
    conn.close()
    logger.success(f"Closing snapshot: {stamped}/{len(picks)} picks priced"
                   + (f", {no_odds} without an ESPN price" if no_odds else "")
                   + (f", {unmatched} not found on ESPN" if unmatched else ""))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--window", type=int, default=WINDOW_MINUTES,
                    help=f"minutes ahead to look (default {WINDOW_MINUTES})")
    sys.exit(run(ap.parse_args().window))
