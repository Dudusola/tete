"""
Fixtures + real bookmaker odds from API-Football (v3, free tier).

WHY THIS EXISTS: Forebet is Cloudflare-blocked from datacenter IPs (verified
6 Aug 2026 — plain HTTP, Chromium AND Firefox all get HTTP 403 "just a
moment"), so it cannot run in the cloud. This is a Cloudflare-immune fixture
source, which restores a second source for consensus when the pipeline runs on
GitHub Actions.

WHY ODDS MATTER: the model trains on odds-implied probabilities. A fixture
source without odds is worse than useless — predict.py would have to invent a
prior (it now refuses instead; see bug #7). football-data.org's free tier
returns "Activate Odds-Package" instead of odds, which is exactly why it isn't
used here. So: fixtures WITHOUT odds are skipped, not stored.

FREE-TIER BUDGET: 100 requests/day.
  fixtures?date=X          -> 1 request per day fetched (151 fixtures, 41
                              leagues in a single unpaginated response)
  odds?date=X              -> ~10 requests per day fetched (10 per page)
A today+tomorrow run costs ~22 requests. The cap is enforced in code below —
running out of quota mid-run must never look like "no fixtures today".

Usage:
    python scrapers/fixtures_api.py            # today + tomorrow
    python scrapers/fixtures_api.py --days 1   # today only
"""

import argparse
import os
import sys
from datetime import date, timedelta

import requests
from dotenv import load_dotenv
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import insert_match, insert_scraped_prediction

load_dotenv()

API_KEY = os.getenv("APIFOOTBALL_KEY", "").strip()
BASE = "https://v3.football.api-sports.io"
SOURCE = "apifootball"

# Stay well inside the free tier. Exceeding it returns empty responses that
# look identical to "no matches", which is the silent-failure pattern this
# project keeps getting bitten by (bugs #5, #8, #9).
MAX_REQUESTS = int(os.getenv("APIFOOTBALL_MAX_REQUESTS", "60"))
_requests_used = 0

# The FREE plan rejects page > 3 outright ("Free plans are limited to a maximum
# value of 3 for the Page parameter"), so a date-wide odds sweep tops out around
# 30 fixtures/day. That is by design here: this source exists to give consensus
# a second opinion on the big leagues, while PredictZ supplies fixture volume.
MAX_ODDS_PAGES = int(os.getenv("APIFOOTBALL_MAX_ODDS_PAGES", "3"))

# 1X2 market. API-Football calls it "Match Winner" (id 1).
MATCH_WINNER = "Match Winner"

# Leagues we hold historical training data for (data/historical/*.csv). Querying
# ONLY these is the whole point: a date-wide sweep returns fixtures ordered by
# id, which handed us Copa Ecuador and Bolivian Primera División — leagues whose
# teams aren't in the model, so 45 of 60 fixtures were unusable.
#
# Ids resolved live from /leagues?current=true on 6 Aug 2026 (all 34 matched).
# Regenerate with:  python scrapers/fixtures_api.py --discover
LEAGUE_IDS = {
    39:  "England Premier League",   40:  "England Championship",
    41:  "England League One",       42:  "England League Two",
    140: "Spain La Liga",            141: "Spain Segunda Division",
    78:  "Germany Bundesliga",       79:  "Germany 2. Bundesliga",
    135: "Italy Serie A",            136: "Italy Serie B",
    61:  "France Ligue 1",           62:  "France Ligue 2",
    88:  "Netherlands Eredivisie",   144: "Belgium Jupiler Pro League",
    94:  "Portugal Primeira Liga",   203: "Turkey Super Lig",
    197: "Greece Super League 1",    179: "Scotland Premiership",
    128: "Argentina Liga Profesional", 71: "Brazil Serie A",
    253: "USA Major League Soccer",  262: "Mexico Liga MX",
    169: "China Super League",       98:  "Japan J1 League",
    103: "Norway Eliteserien",       218: "Austria Bundesliga",
    119: "Denmark Superliga",        106: "Poland Ekstraklasa",
    283: "Romania Liga I",           235: "Russia Premier League",
    207: "Switzerland Super League", 113: "Sweden Allsvenskan",
    244: "Finland Veikkausliiga",    357: "Ireland Premier Division",
}


def _get(path: str, params: dict) -> dict:
    """One API call, with budget accounting and loud failure."""
    global _requests_used
    if _requests_used >= MAX_REQUESTS:
        raise RuntimeError(
            f"Request budget exhausted ({MAX_REQUESTS}). Refusing to continue — "
            "a partial fetch must not be mistaken for an empty day."
        )
    r = requests.get(f"{BASE}{path}", headers={"x-apisports-key": API_KEY},
                     params=params, timeout=40)
    _requests_used += 1
    r.raise_for_status()
    data = r.json()

    # API-Football returns HTTP 200 with an `errors` payload for quota and auth
    # problems — never trust the status code alone.
    errs = data.get("errors")
    if errs:
        raise RuntimeError(f"API-Football error on {path}: {errs}")
    return data


def fetch_fixtures(day: str) -> list:
    """All fixtures for a date. One request — the response is not paginated."""
    data = _get("/fixtures", {"date": day})
    fixtures = data.get("response") or []
    logger.info(f"[{day}] {len(fixtures)} fixtures "
                f"({len({f['league']['name'] for f in fixtures})} leagues)")
    return fixtures


def fetch_odds(day: str, league_id: int = None, season: int = None) -> dict:
    """1X2 odds keyed by fixture id, for one league-day.

    Scoped to a league on purpose. A date-wide sweep is paginated and the free
    plan rejects page > 3, so it returned an arbitrary ~30 fixtures — mostly
    leagues the model has never seen. Per-league queries fit in one page and
    hit the fixtures we can actually predict.

    Takes the FIRST bookmaker offering Match Winner: we need a market consensus
    for the odds-implied probability feature, not the best available price.
    """
    odds_by_fixture = {}
    page = 1
    while True:
        params = {"date": day, "page": page}
        if league_id:
            # The API rejects `league` without `season` ("The Season field is
            # required"), and the season year is NOT always the calendar year
            # (J1 League is on 2027) — so it's taken from the fixture itself,
            # never assumed.
            params["league"] = league_id
            params["season"] = season
        data = _get("/odds", params)
        for entry in data.get("response") or []:
            fid = entry["fixture"]["id"]
            if fid in odds_by_fixture:
                continue
            for bm in entry.get("bookmakers") or []:
                bet = next((b for b in bm.get("bets") or []
                            if b.get("name") == MATCH_WINNER), None)
                if not bet:
                    continue
                vals = {v["value"]: v["odd"] for v in bet.get("values") or []}
                try:
                    h, d, a = (float(vals["Home"]), float(vals["Draw"]),
                               float(vals["Away"]))
                except (KeyError, TypeError, ValueError):
                    continue
                if h > 1 and d > 1 and a > 1:
                    odds_by_fixture[fid] = (h, d, a)
                    break

        paging = data.get("paging") or {}
        total_pages = paging.get("total") or 1
        if page >= min(total_pages, MAX_ODDS_PAGES):
            if total_pages > MAX_ODDS_PAGES:
                logger.info(
                    f"[{day}] odds truncated at page {MAX_ODDS_PAGES} of "
                    f"{total_pages} (free plan caps the page parameter at 3). "
                    "This source is a consensus second opinion, not the bulk "
                    "fixture feed — PredictZ supplies volume."
                )
            break
        page += 1

    return odds_by_fixture


def build_rows(fixtures: list, odds: dict) -> list:
    """Join fixtures to odds. Fixtures without odds are DROPPED, not stored —
    they'd be unpredictable (no odds feature) and would only pad the tables."""
    rows = []
    for f in fixtures:
        fid = f["fixture"]["id"]
        if fid not in odds:
            continue
        h, d, a = odds[fid]

        # Odds -> implied probabilities, normalised to remove the bookmaker's
        # margin. Same treatment PredictZ rows get, so the feature is
        # comparable across sources.
        ih, idr, ia = 1 / h, 1 / d, 1 / a
        total = ih + idr + ia

        kickoff = (f["fixture"].get("date") or "")  # ISO8601 with offset
        match_date = kickoff[:10]
        kickoff_time = kickoff[11:16]

        rows.append({
            "match": {
                "match_id": f"af_{fid}",
                "sport": "football",
                "league": f["league"]["name"],
                "home_team": f["teams"]["home"]["name"],
                "away_team": f["teams"]["away"]["name"],
                "match_date": match_date,
                "kickoff_time": kickoff_time,
            },
            "prediction": {
                "match_id": f"af_{fid}",
                "source": SOURCE,
                "home_win_pct": round(ih / total * 100, 1),
                "draw_pct": round(idr / total * 100, 1),
                "away_win_pct": round(ia / total * 100, 1),
                "predicted_home_goals": None,
                "predicted_away_goals": None,
                "over_2_5_pct": None,
                "btts_pct": None,
                "home_form": None,
                "away_form": None,
                "odds_home": h,
                "odds_draw": d,
                "odds_away": a,
            },
        })
    return rows


def save(rows: list) -> int:
    saved = 0
    for row in rows:
        if insert_match(row["match"]) and insert_scraped_prediction(row["prediction"]):
            saved += 1
    logger.success(f"💾 Saved {saved}/{len(rows)} API-Football fixtures")
    return saved


def run(days: int = 2) -> int:
    if not API_KEY:
        logger.error("APIFOOTBALL_KEY not set — get a free key at "
                     "https://dashboard.api-football.com/register")
        return 0

    all_rows = []
    for i in range(days):
        day = (date.today() + timedelta(days=i)).isoformat()
        fixtures = fetch_fixtures(day)
        if not fixtures:
            continue

        # Keep only leagues the model has history for — everything else would
        # be skipped downstream anyway ("teams not in historical data") and
        # would pad the fixtures table with matches we can neither predict nor
        # reliably grade (the coverage trap of bug #8).
        ours = [f for f in fixtures if f["league"]["id"] in LEAGUE_IDS]
        # (league id, season) — season comes from the fixture, see fetch_odds.
        leagues_today = sorted({(f["league"]["id"], f["league"]["season"])
                                for f in ours})
        logger.info(f"[{day}] {len(ours)} fixtures in {len(leagues_today)} "
                    f"of our leagues (from {len(fixtures)} total)")
        if not ours:
            continue

        # Odds must be fetched date-wide, NOT per league. The free plan rejects
        # any season-scoped query for the current season ("Free plans do not
        # have access to this season, try from 2022 to 2024"), and `league`
        # requires `season` — so per-league targeting is impossible here.
        # We therefore sweep by date and filter to our leagues ourselves. The
        # sweep is capped at 3 pages by the plan, so coverage is partial and
        # essentially arbitrary: treat whatever arrives as a bonus second
        # opinion, never as the fixture feed.
        odds = fetch_odds(day)
        logger.info(f"[{day}] odds sweep returned {len(odds)} fixtures "
                    f"(capped at {MAX_ODDS_PAGES} pages by the free plan)")

        all_rows.extend(build_rows(ours, odds))

    logger.info(f"API requests used: {_requests_used}/{MAX_REQUESTS}")
    if not all_rows:
        # Exit non-zero on nothing: a source that silently returns zero is how
        # the ledger stalled for two weeks (bug #5).
        logger.error("0 fixtures with odds — treating as failure so the "
                     "pipeline flags it rather than reporting success.")
        return 0
    return save(all_rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2,
                    help="how many days from today (default 2 = today+tomorrow)")
    ap.add_argument("--discover", action="store_true",
                    help="re-resolve LEAGUE_IDS from the API and print them")
    args = ap.parse_args()

    if args.discover:
        data = _get("/leagues", {"current": "true"})
        for x in data.get("response") or []:
            lg, co = x["league"], x["country"]["name"]
            if lg["id"] in LEAGUE_IDS:
                seasons = [s for s in x["seasons"] if s.get("current")]
                yr = seasons[0]["year"] if seasons else "?"
                print(f"{lg['id']:<5} {co} {lg['name']} (season {yr})")
        sys.exit(0)

    saved = run(args.days)
    sys.exit(0 if saved else 1)
