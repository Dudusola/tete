"""Download HISTORICAL results for an ESPN league slug into a
football-data.co.uk-shaped CSV that `models/train.py` can read as-is.

WHY THIS EXISTS (24 Aug 2026). Adding a league the model has never seen needs
history, and every other route was blocked:

  * football-data.co.uk publishes no Saudi CSV (nor most non-European leagues).
  * API-Football (`scrapers/fixtures_api.py`) is the project's Cloudflare-immune
    fixtures+odds source, but the account is SUSPENDED — even /status returns
    "Your account is suspended". Its last row in the ledger is 9 Aug 2026.
  * Forebet covers Saudi but is Cloudflare-403'd from datacenter IPs, so it
    cannot run in the cloud at all (docs/history.md bug #26).

ESPN is the one source that is already proven unblocked in GitHub Actions (it
produced rows every single day this month) and it needs no API key. Its
scoreboard endpoint accepts a DATE RANGE, so a whole month costs one request —
four seasons of a league is ~40 requests, not ~1,400.

WHAT THIS DOES NOT GIVE YOU: odds. ESPN's scoreboard carries prices for
UPCOMING fixtures only, not for finished ones. That is survivable and is not a
silent compromise, because:

  * `models/train.py:extract_odds` already handles odds-less rows — it returns
    the flat prior with `odds_is_market=0.0`, and rf_v2 added that flag exactly
    so the forest can quarantine such rows instead of trusting them as prices.
    `InternationalResults.csv` (4,589 rows) has no odds columns at all and is
    already in the training set. This is a shape the pipeline accepts.
  * At SERVE time `models/predict.py` REFUSES to predict a match with no odds
    (bug #7), so a pick built on the flat prior can never reach a user. The
    history here exists to give the teams FORM, which is 9 of the 13 features
    and the thing that currently makes new-league fixtures get skipped outright
    at predict.py:370.

So: this unblocks form. Serving a new league still needs a live odds source for
it — see docs/history.md "What's next" item 0.

COVERAGE IS NOT UNIFORM PER LEAGUE. Ask for 2018 and you may get 2022. Saudi
`ksa.1` returns nothing before 2022/23 no matter what `--start` says — 1,179
matches across four seasons, not eight. The empty months are not errors, so
check the per-season counts this prints rather than assuming `--start` was
honoured.

Usage:
    python scrapers/espn_history.py --league ksa.1 \
        --out data/historical/SaudiProLeague.csv --start 2018-08

    python scrapers/espn_history.py --league ksa.1 --dry-run   # count only
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

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"

# football-data.co.uk's own column names and date format. train.py parses Date
# with dayfirst=True, so DD/MM/YYYY is the unambiguous choice — an ISO date
# would still parse but silently invites a day/month swap on any future edit.
COLUMNS = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]


def _months(start: date, end: date):
    """Inclusive (year, month) pairs from start to end."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def fetch_month(session, league: str, year: int, month: int) -> list | None:
    """Every finished match in one calendar month. None if the request failed."""
    last = monthrange(year, month)[1]
    span = f"{year}{month:02d}01-{year}{month:02d}{last:02d}"
    try:
        r = session.get(BASE.format(league=league),
                        params={"dates": span}, timeout=30)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"{league} {year}-{month:02d}: {e}")
        return None

    out = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        status = (comp.get("status") or {}).get("type") or {}
        # Same gate results_espn.py uses. Never take a score before full time —
        # that rule is why this project's track record is trustworthy at all.
        if not status.get("completed"):
            continue
        teams = {}
        for c in comp.get("competitors", []):
            if c.get("score") is None:
                break
            try:
                teams[c.get("homeAway")] = (c["team"]["displayName"], int(c["score"]))
            except (KeyError, TypeError, ValueError):
                break
        if set(teams) != {"home", "away"}:
            continue
        out.append({
            "event_id": ev.get("id"),
            "iso": (ev.get("date") or "")[:10],
            "home": teams["home"][0], "away": teams["away"][0],
            "hg": teams["home"][1], "ag": teams["away"][1],
        })
    return out


def fetch_history(league: str, start: date, end: date, workers: int = 6) -> list | None:
    jobs = list(_months(start, end))
    session = requests.Session()
    logger.info(f"Fetching {league} across {len(jobs)} months "
                f"({start:%Y-%m} to {end:%Y-%m})...")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda ym: fetch_month(session, league, *ym), jobs))

    if all(r is None for r in results):
        logger.error(f"Every request failed for {league} — network or slug problem.")
        return None

    # Dedupe on ESPN's own event id. A month-range query can return an event
    # twice at boundaries, and the id is authoritative where a name tuple is not.
    seen, rows = set(), []
    for month in results:
        for m in month or []:
            if m["event_id"] in seen:
                continue
            seen.add(m["event_id"])
            rows.append(m)

    rows.sort(key=lambda m: m["iso"])
    logger.success(f"{len(rows)} finished matches for {league}")
    return rows


def write_csv(rows: list, path: str) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    written = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for m in rows:
            y, mo, d = m["iso"].split("-")
            w.writerow({
                "Date": f"{d}/{mo}/{y}",
                "HomeTeam": m["home"], "AwayTeam": m["away"],
                "FTHG": m["hg"], "FTAG": m["ag"],
                "FTR": "H" if m["hg"] > m["ag"] else ("A" if m["ag"] > m["hg"] else "D"),
            })
            written += 1
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", required=True,
                    help="ESPN slug, e.g. ksa.1 (see scrapers/espn_leagues.py)")
    ap.add_argument("--out", help="CSV path; required unless --dry-run")
    ap.add_argument("--start", default="2018-08",
                    help="YYYY-MM. Default 2018-08: train.py drops anything "
                         "before its 2018-01-01 DATE_CUTOFF anyway.")
    ap.add_argument("--end", default=None, help="YYYY-MM, default today")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch and report counts; write nothing")
    args = ap.parse_args()

    if not args.dry_run and not args.out:
        ap.error("--out is required unless --dry-run")

    sy, sm = (int(x) for x in args.start.split("-"))
    start = date(sy, sm, 1)
    if args.end:
        ey, em = (int(x) for x in args.end.split("-"))
        end = date(ey, em, 1)
    else:
        end = date.today()

    rows = fetch_history(args.league, start, end)

    # Project rule: a scraper that gets nothing must exit non-zero. A silent
    # empty result once hid a two-week gap in the ledger (docs/history.md bug #5).
    if not rows:
        logger.error(f"No finished matches for {args.league} — refusing to "
                     f"write an empty or truncated CSV.")
        sys.exit(1)

    by_season = {}
    for m in rows:
        y, mo = int(m["iso"][:4]), int(m["iso"][5:7])
        season = y if mo >= 7 else y - 1        # Aug-May seasons
        by_season[season] = by_season.get(season, 0) + 1
    for s in sorted(by_season):
        logger.info(f"  season {s}/{str(s+1)[2:]}: {by_season[s]} matches")

    teams = sorted({m["home"] for m in rows} | {m["away"] for m in rows})
    logger.info(f"  {len(teams)} distinct teams")

    if args.dry_run:
        # ASCII only in console output: the Windows console is cp1252 and turns
        # an em-dash into a replacement char. Same class of problem docs/history.md
        # records for webapp/*.html.
        logger.info("Dry run - nothing written.")
        print("\nTeams:")
        for t in teams:
            print(f"  {t}")
        return

    n = write_csv(rows, args.out)
    logger.success(f"Wrote {n} matches to {args.out}")
    logger.info("train.py picks this up automatically on the next retrain. In "
                "the cloud, editing data/historical/** invalidates the Actions "
                "model cache key, so the retrain happens by itself.")


if __name__ == "__main__":
    main()
