"""nba_stars.py - is a star player listed out for tonight? (21 Oct 2026)

Boss: "build the star player out warning". The 5-season odds backtest
(models/nba_market_backtest.py) and every public model reviewed agree: what
the bookmaker knows and Elo does not is WHO PLAYS. This does not change a
probability - it is a visible fact on the card, like the NFL QB warning
(models/nfl_qb.py), and us_sports.record() keeps warned picks apart so the
live ledger can show whether they do worse.

  star    = 18+ points a game LAST season, 20+ games (ESPN season stats).
  listed  = on ESPN's NBA injury report as Out or Day-To-Day.
Joined on ESPN's athlete id (the injury entry links /player/_/id/<id>/), never
on names. The injury report gives the player's CURRENT team, so a star traded
in the summer warns for his new team.

shortcut: last season's scoring only - a player who breaks out this season is
missed until next year; switch to the current season's stats from ~December.
"""

import re
from datetime import datetime, timezone

import requests

INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
STATS_URL = ("https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/statistics/"
             "byathlete?isqualified=false&page=1&limit=100&sort=offensive.avgPoints%3Adesc"
             "&season={season}&seasontype=2")
STAR_PPG = 18.0
MIN_GAMES = 20
WARN_STATUS = ("Out", "Day-To-Day")


def last_season(now: datetime | None = None) -> int:
    """ESPN labels a season by the year it ENDS: 2025-26 is 2026."""
    now = now or datetime.now(timezone.utc)
    current = now.year + 1 if now.month >= 8 else now.year
    return current - 1


def stars(season: int) -> dict:
    """ESPN athlete id -> {"name", "ppg"} for last season's 18+ ppg scorers."""
    j = requests.get(STATS_URL.format(season=season), timeout=30).json()
    cats = {c["name"]: c["names"] for c in j.get("categories") or []}
    gi, pi = cats["general"].index("gamesPlayed"), cats["offensive"].index("avgPoints")
    out = {}
    for a in j.get("athletes") or []:
        v = {c["name"]: c["totals"] for c in a.get("categories") or []}
        ppg, gp = float(v["offensive"][pi]), int(float(v["general"][gi]))
        if ppg < STAR_PPG:
            break                                   # sorted by points, descending
        if gp >= MIN_GAMES:
            out[str(a["athlete"]["id"])] = {"name": a["athlete"]["displayName"], "ppg": ppg}
    return out


def _athlete_id(a: dict) -> str | None:
    for link in a.get("links") or []:
        m = re.search(r"/id/(\d+)", link.get("href") or "")
        if m:
            return m.group(1)
    return str(a["id"]) if a.get("id") else None


def live_status() -> dict:
    """Team name -> [{"name", "ppg", "status"}] for stars on the injury report.

    Empty dict when either source is down - the caller then shows nothing
    rather than guessing that everyone is fit.
    """
    try:
        star = stars(last_season())
        d = requests.get(INJURIES_URL, timeout=30).json()
    except Exception:
        return {}
    out = {}
    for t in d.get("injuries") or []:
        for i in t.get("injuries") or []:
            s = star.get(_athlete_id(i.get("athlete") or {}) or "")
            if s and i.get("status") in WARN_STATUS:
                out.setdefault(t.get("displayName"), []).append(dict(s, status=i["status"]))
    return out


if __name__ == "__main__":
    assert last_season(datetime(2026, 10, 21)) == 2026 and last_season(datetime(2027, 3, 1)) == 2026
    assert _athlete_id({"links": [{"href": "https://www.espn.com/nba/player/_/id/4712863/x"}]}) == "4712863"
    for team, ps in sorted(live_status().items()):
        print(team, "-", "; ".join(f"{p['name']} ({p['ppg']} ppg) {p['status']}" for p in ps))
