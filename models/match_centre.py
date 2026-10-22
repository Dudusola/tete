"""
models/match_centre.py - the free match detail Puntrr shows (22 Oct 2026).

Two free sources, both probed live before this was written:
  * ESPN's match summary (site.api.espn.com/.../soccer/<league>/summary?event=<id>,
    no key). Our football match ids ARE ESPN event ids ("espnfb_<id>") and every
    upcoming fixture is an ESPN one, so there is no name matching here. Gives
    lineups, goals/cards with minute and player, team stats, commentary, the
    league table and each side's last five games.
  * SportyBet's single-event feed (factsCenter/event?eventId=sr:match:..), for
    the correct-score market (Betradar 45). The fixture is found with the same
    map_leg booking uses.

Decoration only: nothing here is a model input or a graded number, and every
function returns None/{} rather than raising, so the match page never breaks
because a feed is down. Memoised so a busy page cannot hammer either source.
"""
import time

import requests

ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/summary?event={event}"
SPORTY_EVENT = "https://www.sportybet.com/api/ng/factsCenter/event?eventId={event}&productId=3"
EVENT_TYPES = {"Goal": "goal", "Goal - Header": "goal", "Goal - Volley": "goal",
               "Goal - Free-kick": "goal", "Penalty - Scored": "goal", "Own Goal": "own goal",
               "Yellow Card": "yellow", "Red Card": "red", "Penalty - Missed": "penalty missed"}
STATS = [("possessionPct", "Possession %"), ("totalShots", "Shots"), ("shotsOnTarget", "On target"),
         ("wonCorners", "Corners"), ("foulsCommitted", "Fouls"), ("yellowCards", "Yellow cards"),
         ("redCards", "Red cards"), ("saves", "Saves")]
_memo: dict = {}


def _remember(key, ttl, compute):
    hit = _memo.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        value = compute()
    except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as e:
        print(f"[match_centre] {key[0]} failed: {e}", flush=True)
        value = hit[1] if hit else None     # a stale answer beats none
    _memo[key] = (time.time(), value)
    return value


def parse_espn(d: dict) -> dict:
    """The parts of ESPN's summary the match page shows, in plain shapes."""
    events = []
    for e in d.get("keyEvents") or []:
        kind = EVENT_TYPES.get((e.get("type") or {}).get("text"))
        if not kind:
            continue
        who = ((e.get("participants") or [{}])[0].get("athlete") or {}).get("displayName")
        events.append({"minute": (e.get("clock") or {}).get("displayValue"), "kind": kind,
                       "team": (e.get("team") or {}).get("displayName"), "player": who})
    stats = []
    teams = ((d.get("boxscore") or {}).get("teams")) or []
    if len(teams) == 2:
        vals = [{s["name"]: s.get("displayValue") for s in t.get("statistics") or []} for t in teams]
        stats = [{"label": label, "home": vals[0].get(k), "away": vals[1].get(k)}
                 for k, label in STATS if vals[0].get(k) is not None and vals[1].get(k) is not None]
    lineups = []
    for r in d.get("rosters") or []:
        starters = [{"name": (p.get("athlete") or {}).get("displayName"),
                     "position": (p.get("position") or {}).get("abbreviation"),
                     "number": p.get("jersey")}
                    for p in r.get("roster") or [] if p.get("starter")]
        if starters:
            lineups.append({"team": (r.get("team") or {}).get("displayName"),
                            "formation": r.get("formation"), "starters": starters})
    last5 = [{"team": (t.get("team") or {}).get("displayName"),
              "games": [{"result": g.get("gameResult"), "score": g.get("score"), "at": g.get("atVs"),
                         "opponent": (g.get("opponent") or {}).get("displayName"),
                         "competition": g.get("leagueName")} for g in t.get("events") or []]}
             for t in d.get("lastFiveGames") or []]
    table = []
    for g in ((d.get("standings") or {}).get("groups")) or []:
        for row in ((g.get("standings") or {}).get("entries")) or []:
            s = {x["name"]: x.get("displayValue") for x in row.get("stats") or []}
            table.append({"team": row.get("team"), "rank": s.get("rank"), "played": s.get("gamesPlayed"),
                          "points": s.get("points"), "gd": s.get("pointDifferential")})
    venue = ((d.get("gameInfo") or {}).get("venue") or {}).get("fullName")
    commentary = [{"minute": (c.get("time") or {}).get("displayValue"), "text": c.get("text")}
                  for c in (d.get("commentary") or []) if c.get("text")][-10:]
    return {"events": events, "stats": stats, "lineups": lineups, "last5": last5,
            "table": table, "venue": venue, "commentary": commentary[::-1]}


def espn_centre(match_id: str, espn_league: str | None, live: bool) -> dict | None:
    if not (match_id or "").startswith("espnfb_") or not espn_league:
        return None
    event = match_id.split("_", 1)[1]
    return _remember(("espn", match_id), 60 if live else 1800,
                     lambda: parse_espn(requests.get(ESPN.format(league=espn_league, event=event),
                                                     timeout=15).json()))


def sporty_correct_scores(fixture: dict) -> dict:
    """{"1:1": 6.4, ...} from SportyBet for this fixture, or {} if it isn't listed."""
    def compute():
        from scrapers.booking_codes import HEADERS, _EventsBySport, map_leg
        probe = {"home_team": fixture["home_team"], "away_team": fixture["away_team"],
                 "league": fixture.get("league"), "match_date": fixture.get("match_date"),
                 "kickoff_utc": fixture.get("kickoff_utc"), "market": "Match Result",
                 "selection": f"{fixture['home_team']} Win"}
        m = map_leg(probe, _EventsBySport())
        if not m:
            return {}
        d = requests.get(SPORTY_EVENT.format(event=m["selection"]["eventId"]),
                         headers=HEADERS, timeout=15).json()
        for mk in (d.get("data") or {}).get("markets") or []:
            if str(mk.get("id")) == "45":
                return {o["desc"]: float(o["odds"]) for o in mk.get("outcomes") or []
                        if o.get("isActive", 1) and o.get("odds")}
        return {}
    return _remember(("sporty_cs", fixture.get("match_id")), 300, compute) or {}


if __name__ == "__main__":
    # Self-check of the parser on a hand-made ESPN payload (no network).
    sample = {"keyEvents": [
        {"type": {"text": "Goal"}, "clock": {"displayValue": "4'"}, "team": {"displayName": "B"},
         "participants": [{"athlete": {"displayName": "Striker"}}]},
        {"type": {"text": "Substitution"}, "clock": {"displayValue": "60'"}},
        {"type": {"text": "Yellow Card"}, "clock": {"displayValue": "70'"}, "team": {"displayName": "A"},
         "participants": [{"athlete": {"displayName": "Mid"}}]}],
        "boxscore": {"teams": [{"statistics": [{"name": "possessionPct", "displayValue": "52.5"}]},
                               {"statistics": [{"name": "possessionPct", "displayValue": "47.5"}]}]},
        "rosters": [{"team": {"displayName": "A"}, "formation": "4-3-3",
                     "roster": [{"starter": True, "athlete": {"displayName": "Keeper"}, "position": {"abbreviation": "G"}},
                                {"starter": False, "athlete": {"displayName": "Sub"}}]}]}
    out = parse_espn(sample)
    assert [(e["minute"], e["kind"], e["player"]) for e in out["events"]] == [("4'", "goal", "Striker"), ("70'", "yellow", "Mid")]
    assert out["stats"] == [{"label": "Possession %", "home": "52.5", "away": "47.5"}]
    assert out["lineups"][0]["starters"] == [{"name": "Keeper", "position": "G", "number": None}]
    print("PASS - ESPN summary parser")
