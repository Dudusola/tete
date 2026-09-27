"""sports_history.py - historical results for the non-soccer ledgers (NBA, NFL,
NHL, MLB), downloaded once and then kept current, so the training data is already
sitting in the repo on the day a model for those sports is built.

WHERE IT WRITES, AND WHY NOT data/historical/
---------------------------------------------
`models/train.py` globs `data/historical/*.csv` and trains the FOOTBALL model on
every file it finds, and the Actions model-cache key hashes
`data/historical/**`. An NBA CSV there would be read as football matches, and
every daily update would force a cloud retrain. So these live in
`data/sports_history/<feed>.csv`, which nothing trains on until a sport-specific
trainer is written to read it on purpose.

ONE PARSER, SHARED WITH THE LEDGER
----------------------------------
Rows are parsed by `scrapers.espn_ledger._parse_event` - the same function that
writes the live ledger - so history and ledger cannot disagree about what a
game was. In particular both:
  * skip PRESEASON (ESPN season.type 1): split squads, not the real teams;
  * split the final from REGULATION. ESPN's final includes overtime and the
    NHL shootout (the SO winner is credited +1 goal), while a bookmaker's basic
    1X2 for these sports settles on regulation. `decided_in` (REG/OT/SO) and
    `reg_home`/`reg_away` carry that; they stay blank rather than guessed when
    ESPN's per-period scores are missing or don't add up to the final.

NO ODDS. ESPN prices upcoming games only. Same stance as espn_history.py: this
gives teams FORM; serving a pick still needs a live price (bug #7).

HOW IT FETCHES (probed 27 Sept 2026)
------------------------------------
For these sports ESPN rejects the `YYYYMMDD-YYYYMMDD` range the soccer
downloader uses (HTTP 400 with a limit, 0 events without). What works is a
whole month, `dates=YYYYMM`, but ONLY with `limit`: without it the response is
silently capped at 100 events (Jan 2026 NBA stopped on the 14th). With
limit=1000 a month is ~240 games. A response that comes back exactly AT the
limit is treated as truncated and fails the run - bug #10 was a hardcoded page
count that was right until the feed grew, and "not found" hid it.

UPDATING
--------
If the CSV already exists, only the months from (newest game - 1 month) to now
are refetched and merged by ESPN event id - a handful of requests. So a missed
day costs nothing: the next run resumes from whatever the file last saw.
`--full` rebuilds from `--start`.

Usage:
    python scrapers/sports_history.py                      # update every feed
    python scrapers/sports_history.py --feed nhl --full    # rebuild one
    python scrapers/sports_history.py --dry-run
"""

import argparse
import csv
import os
import sys
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import requests
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.espn_ledger import BASE, FEEDS, _parse_event

OUT_DIR = "data/sports_history"
LIMIT = 1000
WORKERS = 6

# Default first season. Rules and scoring environments drift (the NBA's
# three-point era, the NHL's shootout arriving in 2005-06), so older seasons
# are less like today's game; 2010 still gives ~15 seasons per sport. ESPN
# carries these leagues back to at least 2002 if a trainer ever wants more.
DEFAULT_START = "2010-08"

# home_id / away_id are ESPN's FRANCHISE ids, and they are the join key a
# trainer should use, not the name. Names change under one franchise (New
# Jersey Nets -> Brooklyn Nets, Charlotte Bobcats -> Charlotte Hornets,
# Washington Redskins -> Washington -> Commanders) and one name has meant two
# franchises ("Hornets": New Orleans until 2013, Charlotte from 2014).
COLUMNS = ["event_id", "date", "kickoff_utc", "season", "season_type",
           "home_team", "away_team", "home_id", "away_id", "home_score", "away_score", "outcome",
           "decided_in", "reg_home", "reg_away", "neutral_site"]


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _get(session, feed, dates: str):
    """Events for one `dates` value, or None if the request failed/truncated."""
    try:
        r = session.get(BASE.format(path=feed.espn_path, league=feed.slug),
                        params={"dates": dates, "limit": LIMIT}, timeout=60)
        r.raise_for_status()
        events = r.json().get("events", [])
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"{feed.slug} {dates}: {e}")
        return None
    if len(events) >= LIMIT:
        logger.error(f"{feed.slug} {dates}: {len(events)} events = the limit; "
                     f"response is probably truncated")
        return None
    return events


def fetch_month(session, feed, year: int, month: int):
    """(rows, ok). ok=False when the month could not be fetched completely."""
    events = _get(session, feed, f"{year}{month:02d}")
    if events is None:
        # Some whole months 504 on ESPN's side every time ("timeout occurred
        # waiting for core endpoint": NHL Sept 2010/2013/2014, probed 27 Sept
        # 2026) while each single day answers. Fall back to day by day, and
        # still fail the month if any one day fails.
        events, seen = [], set()
        for day in range(1, monthrange(year, month)[1] + 1):
            day_events = _get(session, feed, f"{year}{month:02d}{day:02d}")
            if day_events is None:
                return [], False
            for ev in day_events:
                if ev.get("id") not in seen:
                    seen.add(ev.get("id"))
                    events.append(ev)

    rows = []
    for ev in events:
        p = _parse_event(ev, feed)
        if not p or not p["completed"]:
            continue
        hs, as_ = p["home_score"], p["away_score"]
        if hs is None or as_ is None:
            continue
        if hs == as_ and not feed.draws_possible:
            # Same rule as the ledger: a level final in a sport that cannot
            # draw is bad data (or a pre-2005 NHL tie), not a result.
            continue
        season = ev.get("season") or {}
        comp = (ev.get("competitions") or [{}])[0]
        ids = {c.get("homeAway"): (c.get("team") or {}).get("id")
               for c in comp.get("competitors", [])}
        rows.append({
            "event_id": ev["id"],
            "date": p["match_date"],
            "kickoff_utc": p["kickoff_utc"],
            "season": season.get("year"),
            "season_type": season.get("type"),
            "home_team": p["home_team"], "away_team": p["away_team"],
            "home_id": ids.get("home"), "away_id": ids.get("away"),
            "home_score": hs, "away_score": as_,
            "outcome": "D" if hs == as_ else ("H" if hs > as_ else "A"),
            "decided_in": p["decided_in"],
            "reg_home": p["reg_home"], "reg_away": p["reg_away"],
            "neutral_site": int(bool(comp.get("neutralSite"))),
        })
    return rows, True


# A franchise plays 17 (NFL) to 82 (NBA/NHL) games a season; an exhibition
# "team" (Team LeBron, USA v World, AFC v NFC) plays one to six. This is the
# backstop for exhibitions ESPN labels like real games, which the parser's
# type/notes checks cannot see. Applied to COMPLETED seasons only: three
# weeks into a season every real team is still under the bar.
MIN_TEAM_GAMES = 10


def drop_exhibition_teams(name: str, rows: dict) -> dict:
    current = max((r["season"] for r in rows.values()), default="")
    counts = {}
    for r in rows.values():
        for t in (r["home_team"], r["away_team"]):
            counts[(r["season"], t)] = counts.get((r["season"], t), 0) + 1
    kept, dropped = {}, []
    for k, r in rows.items():
        if r["season"] != current and any(
                counts[(r["season"], t)] < MIN_TEAM_GAMES
                for t in (r["home_team"], r["away_team"])):
            dropped.append(f"{r['date']} {r['home_team']} v {r['away_team']}")
            continue
        kept[k] = r
    if dropped:
        logger.info(f"{name}: dropped {len(dropped)} games with a team that "
                    f"played <{MIN_TEAM_GAMES} that season (exhibitions): "
                    f"{'; '.join(dropped[:8])}{' ...' if len(dropped) > 8 else ''}")
    return kept


def load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["event_id"]: r for r in csv.DictReader(fh)}


def write(rows: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: (r["date"], r["event_id"])):
            w.writerow(r)
    os.replace(tmp, path)   # never leave a half-written CSV behind


def run_feed(name: str, full: bool, start: str, dry_run: bool) -> bool:
    feed = FEEDS[name]
    path = os.path.join(OUT_DIR, f"{name}.csv")
    existing = {} if full else load(path)

    if existing:
        newest = max(r["date"] for r in existing.values())
        y, m = int(newest[:4]), int(newest[5:7])
        first = date(y - 1, 12, 1) if m == 1 else date(y, m - 1, 1)
    else:
        sy, sm = (int(x) for x in start.split("-"))
        first = date(sy, sm, 1)
    months = list(_months(first, date.today()))

    session = requests.Session()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda ym: fetch_month(session, feed, *ym), months))

    failed = [f"{y}-{m:02d}" for (y, m), (_, ok) in zip(months, results) if not ok]
    if failed:
        # A gap in history is silent forever once written, so do not write.
        logger.error(f"{name}: {len(failed)}/{len(months)} months failed "
                     f"({', '.join(failed[:6])}{'...' if len(failed) > 6 else ''})"
                     f" - file left unchanged")
        return False

    merged = dict(existing)
    fetched = 0
    for rows, _ in results:
        for r in rows:
            # Stringified, so fresh rows compare and sort like rows read
            # back from the CSV (an int season beside a str one crashed the
            # first incremental update).
            merged[str(r["event_id"])] = {k: ("" if v is None else str(v))
                                          for k, v in r.items()}
            fetched += 1
    merged = drop_exhibition_teams(name, merged)
    added = len(merged) - len(existing)

    if not merged:
        logger.error(f"{name}: 0 finished games in {len(months)} months - "
                     f"refusing to write an empty file")
        return False

    by_season = {}
    for r in merged.values():
        by_season[r["season"]] = by_season.get(r["season"], 0) + 1
    regs = [r for r in merged.values() if r["decided_in"]]
    extra = sum(1 for r in regs if r["decided_in"] != "REG")
    logger.info(f"{name}: {len(merged)} games, {len(by_season)} seasons "
                f"({min(by_season)}-{max(by_season)}); regulation known for "
                f"{len(regs)} ({extra} went past regulation)")

    if dry_run:
        logger.info(f"{name}: dry run - {fetched} fetched, {added} would be new")
        return True
    write(merged, path)
    logger.success(f"{name}: {added} new games, {len(merged)} total -> {path} "
                   f"({len(months)} months fetched)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feed", action="append", choices=sorted(FEEDS),
                    help="which league (repeatable; default: all)")
    ap.add_argument("--full", action="store_true",
                    help="rebuild from --start instead of updating")
    ap.add_argument("--start", default=DEFAULT_START,
                    help=f"YYYY-MM for a full build (default {DEFAULT_START})")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ok = [run_feed(n, args.full, args.start, args.dry_run)
          for n in (args.feed or sorted(FEEDS))]
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
