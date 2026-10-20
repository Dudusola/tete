"""
scrapers/sporty_lines.py - SportyBet's own price for EVERY upcoming fixture,
recorded over time (10 Oct 2026).

WHY. The project's central question is "when we disagree with the market, are
we right?" (market_snapshot.py), and on 8 Oct the answer was: we can't tell,
because the model has largely LEARNED the market. Log loss on 1,103 graded
picks: model 0.678, ESPN's price 0.676. An offline blend of the two was tied
with both (6,671 held-out matches). Re-tuning cannot create information the
odds don't already hold.

The one edge that does not need a cleverer model is PRICE: SportyBet is a soft
book, and when its payout is longer than the fair price implied by another
book's odds, backing it is positive expected value whatever our model thinks.
Testing that needs SportyBet's price beside ESPN's, per fixture, over time.
We had neither: market_snapshot.py stores ESPN's price (1,096 of 1,103 stored
"book" prices are ESPN's), and the SportyBet number on each card is fetched
live and never saved. Like closing prices, a day not recorded is gone for good
- SportyBet removes odds once a game starts.

WHAT. Every upcoming football, NHL and NFL fixture we hold, matched to its
SportyBet event with the SAME matcher booking uses (booking_codes.map_leg -
one matcher per feed, bug #17), and the market's full price written to
`sporty_lines`:
    football  1X2 (market id 1)                 odds_home / odds_draw / odds_away
    NHL / NFL full-game moneyline incl. OT      odds_home / odds_away
A row is appended only when a price CHANGED, so the table holds each game's
first-seen price, every move, and the last price before kickoff - and stays
small. Its own table on purpose: `market_lines` is read as ESPN's moneyline
history and `scraped_predictions` is football's feature path (bug #7); a
SportyBet row in either would be read as something it is not.

Never touches predictions, picks or anything served. Read it with
db/sporty_value_report.py once there is enough of it.

Exit codes: 0 if anything was priced or there was nothing to price; 1 if there
were fixtures and NONE could be priced (feed blocked or changed - bug #5).

Usage:
    python scrapers/sporty_lines.py            # record
    python scrapers/sporty_lines.py --dry-run  # match and print, write nothing
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import DB_PATH, init_db
from scrapers.booking_codes import _EventsBySport, map_leg

SPORTS = {"football": "football", "ice_hockey": "nhl", "american_football": "nfl",
          "basketball": "nba"}
DAYS_AHEAD = 5
MIN_FIXTURES_FOR_ALARM = 10      # a quiet day with 3 fixtures is not an outage


def _fixtures(conn) -> list:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return conn.execute(f"""
        SELECT match_id, sport, league, home_team, away_team, match_date,
               kickoff_time, kickoff_utc
        FROM matches
        WHERE sport IN ({",".join("?" * len(SPORTS))})
          AND match_date BETWEEN date('now', '-1 day') AND date('now', '+{DAYS_AHEAD} day')
          AND (kickoff_utc IS NULL OR kickoff_utc > ?)
          AND (live_state IS NULL OR live_state = 'pre')
          AND match_id NOT IN (SELECT match_id FROM results)
        ORDER BY match_date, kickoff_utc
    """, [*SPORTS, now]).fetchall()


def _price(row, events_for) -> dict | None:
    """SportyBet's full price for one fixture, or None if it isn't listed."""
    code = SPORTS[row["sport"]]
    leg = {"home_team": row["home_team"], "away_team": row["away_team"],
           "match_date": row["match_date"], "kickoff_utc": row["kickoff_utc"],
           "league": row["league"], "sport": code}
    if code == "football":
        leg.update(market="Match Result", selection=row["home_team"])
    else:
        leg.update(market="Moneyline", selection=row["home_team"])
    m = map_leg(leg, events_for)
    if not m:
        return None
    sel = m["selection"]
    ev = next((e for e in events_for(code) if e.get("eventId") == sel["eventId"]), None)
    mkt = next((x for x in (ev or {}).get("markets") or []
                if str(x.get("id")) == sel["marketId"]
                and (x.get("specifier") or "") == sel["specifier"]), None)
    if not mkt:
        return None
    odds = {}
    for out in mkt.get("outcomes") or []:
        if not out.get("isActive"):
            continue
        oid, desc = str(out.get("id")), (out.get("desc") or "").lower()
        try:
            price = float(out.get("odds"))
        except (TypeError, ValueError):
            continue
        if code == "football":
            key = {"home": "home", "draw": "draw", "away": "away"}.get(desc)
        else:
            key = {"4": "home", "5": "away"}.get(oid)      # full-game winner ids
        if key:
            odds[key] = price
    want = ("home", "draw", "away") if code == "football" else ("home", "away")
    if not all(k in odds for k in want):
        return None                                    # a half-suspended market is no price
    return {"market": "1X2" if code == "football" else "ML",
            "event_id": sel["eventId"], **odds}


def _mins_before(kickoff_utc, now) -> int | None:
    if not kickoff_utc:
        return None
    try:
        ko = datetime.fromisoformat(str(kickoff_utc)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int((ko - now).total_seconds() // 60)


def run(dry_run: bool = False) -> int:
    init_db()                                          # sporty_lines lives in the schema
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    fixtures = _fixtures(conn)
    events_for = _EventsBySport()
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    counts = {code: [0, 0, 0] for code in SPORTS.values()}   # fixtures, priced, rows written

    for row in fixtures:
        code = SPORTS[row["sport"]]
        counts[code][0] += 1
        try:
            p = _price(row, events_for)
        except Exception as e:                         # one bad fixture never stops the run
            logger.warning(f"{row['home_team']} v {row['away_team']}: {e}")
            continue
        if not p:
            continue
        counts[code][1] += 1
        last = conn.execute(
            "SELECT odds_home, odds_draw, odds_away FROM sporty_lines "
            "WHERE match_id = ? AND market = ? ORDER BY id DESC LIMIT 1",
            [row["match_id"], p["market"]]).fetchone()
        new = (p["home"], p.get("draw"), p["away"])
        if last and tuple(last) == new:
            continue                                   # unchanged - nothing to record
        counts[code][2] += 1
        if dry_run:
            logger.info(f"[dry] {code} {row['home_team']} v {row['away_team']}: {new}")
            continue
        conn.execute("""
            INSERT INTO sporty_lines (match_id, sport, market, event_id,
                odds_home, odds_draw, odds_away, observed_at, mins_before)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [row["match_id"], code, p["market"], p["event_id"], *new, stamp,
             _mins_before(row["kickoff_utc"], now)])
    if not dry_run:
        conn.commit()
    conn.close()

    total_fx = sum(c[0] for c in counts.values())
    total_priced = sum(c[1] for c in counts.values())
    for code, (n, priced, wrote) in counts.items():
        logger.info(f"{code:8} {n:4} fixtures, {priced:4} on SportyBet, {wrote:4} new price rows")
    if total_fx >= MIN_FIXTURES_FOR_ALARM and total_priced == 0:
        logger.error(f"{total_fx} fixtures and NONE priced - SportyBet feed blocked or changed")
        return 1
    logger.success(f"SportyBet lines: {total_priced}/{total_fx} fixtures priced")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    sys.exit(run(ap.parse_args().dry_run))
