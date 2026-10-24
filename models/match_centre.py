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

  * FotMob (www.fotmob.com/api/data/..., no key, unofficial - 23 Oct 2026). ONLY
    for team stats when ESPN's are empty: ESPN sends all-zero stats (0% possession
    each) for games it isn't tracking live, e.g. Al Ittihad v Al Diriyah at 38'
    where FotMob had 54-46 possession, 6-5 shots and xG. Found by date + both team
    names through models/team_match.pair_score (the live-score matcher, bug #17).
    Sofascore was probed too: 403 to plain requests. Since 24 Oct 2026 also the
    match page's xG, momentum (FotMob's per-minute -100..100 swing) and shot map,
    for every started game FotMob has, not only when ESPN's stats are empty.

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
FOTMOB_DAY = "https://www.fotmob.com/api/data/matches?date={day}"
FOTMOB_MATCH = "https://www.fotmob.com/api/data/matchDetails?matchId={id}"
FOTMOB_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/128 Safari/537.36"}
# FotMob title -> our label, in the order the page shows them (ESPN's labels, then xG).
FOTMOB_STATS = [("Ball possession", "Possession %"), ("Total shots", "Shots"),
                ("Shots on target", "On target"), ("Corners", "Corners"), ("Fouls committed", "Fouls"),
                ("Yellow cards", "Yellow cards"), ("Red cards", "Red cards"), ("Keeper saves", "Saves"),
                ("Expected goals (xG)", "Expected goals (xG)")]
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


def espn_stats_empty(stats: list) -> bool:
    """ESPN's 'not tracked' shape: no stats, or possession 0 for both sides."""
    poss = next((x for x in stats or [] if x["label"].startswith("Possession")), None)
    if not poss:
        return not stats
    try:
        return float(poss["home"] or 0) + float(poss["away"] or 0) == 0
    except ValueError:
        return True


def parse_fotmob_stats(d: dict) -> list:
    seen = {}
    for g in (((d.get("content") or {}).get("stats") or {}).get("Periods") or {}).get("All", {}).get("stats") or []:
        for x in g.get("stats") or []:
            v = x.get("stats") or []
            if len(v) == 2 and None not in v:
                seen.setdefault(x.get("title"), v)
    return [{"label": label, "home": str(seen[t][0]), "away": str(seen[t][1])}
            for t, label in FOTMOB_STATS if t in seen]


def parse_fotmob(d: dict) -> dict:
    """Stats, xG, momentum and shots, home/away as FotMob's header has them."""
    content = d.get("content") or {}
    teams = (d.get("header") or {}).get("teams") or [{}]
    home_id = teams[0].get("id")
    stats = parse_fotmob_stats(d)
    momentum = [[p["minute"], p["value"]] for p in
                ((content.get("momentum") or {}).get("main") or {}).get("data") or []
                if p.get("minute") is not None and p.get("value") is not None]
    # Coordinates: both sides attack the goal at x=105 (pitch 105 x 68).
    shots = [{"home": s.get("teamId") == home_id, "x": round(s["x"], 1), "y": round(s["y"], 1),
              "xg": round(s.get("expectedGoals") or 0, 2),
              "goal": s.get("eventType") == "Goal" and not s.get("isOwnGoal"),
              "on_target": bool(s.get("isOnTarget")), "min": s.get("min"), "player": s.get("playerName")}
             for s in (content.get("shotmap") or {}).get("shots") or []
             if s.get("x") is not None and s.get("y") is not None]
    xg = next(([x["home"], x["away"]] for x in stats if x["label"] == "Expected goals (xG)"), None)
    return {"stats": stats, "xg": xg, "momentum": momentum, "shots": shots}


def fotmob_detail(fixture: dict, live: bool) -> dict | None:
    """FotMob's stats, xG, momentum and shots for this fixture, or None."""
    from models.team_match import pair_score
    def find():
        day = (fixture.get("kickoff_utc") or fixture.get("match_date") or "")[:10].replace("-", "")
        if len(day) != 8:
            return None
        listing = _remember(("fotmob_day", day), 1800, lambda: requests.get(
            FOTMOB_DAY.format(day=day), headers=FOTMOB_HEADERS, timeout=15).json()) or {}
        best = None
        for lg in listing.get("leagues") or []:
            for m in lg.get("matches") or []:
                sc = pair_score(fixture["home_team"], fixture["away_team"],
                                (m.get("home") or {}).get("name", ""), (m.get("away") or {}).get("name", ""))
                if sc and (not best or sc > best[0]):
                    best = (sc, m.get("id"))
        return best and best[1]
    fm_id = _remember(("fotmob_id", fixture.get("match_id")), 1800, find)
    if not fm_id:
        return None
    return _remember(("fotmob_detail", fm_id), 60 if live else 1800, lambda: parse_fotmob(
        requests.get(FOTMOB_MATCH.format(id=fm_id), headers=FOTMOB_HEADERS, timeout=15).json()))


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
    assert espn_stats_empty([{"label": "Possession %", "home": "0", "away": "0"}])
    assert not espn_stats_empty(out["stats"]) and espn_stats_empty([])
    fm = {"content": {"stats": {"Periods": {"All": {"stats": [{"stats": [
        {"title": "Ball possession", "stats": [54, 46]}, {"title": "Expected goals (xG)", "stats": ["1.04", "0.16"]},
        {"title": "Shots", "stats": [None, None]}]}]}}}}}
    assert parse_fotmob_stats(fm) == [{"label": "Possession %", "home": "54", "away": "46"},
                                      {"label": "Expected goals (xG)", "home": "1.04", "away": "0.16"}]
    print("PASS - FotMob stats parser")
    full = {"header": {"teams": [{"id": 1}, {"id": 2}]}, **fm}
    full["content"]["momentum"] = {"main": {"data": [{"minute": 1, "value": 33}, {"minute": 2, "value": -14}]}}
    full["content"]["shotmap"] = {"shots": [
        {"teamId": 1, "x": 100.9, "y": 34.6, "expectedGoals": 0.53, "eventType": "Goal", "isOnTarget": True, "min": 30},
        {"teamId": 2, "x": 90.0, "y": 30.0, "expectedGoals": 0.1, "eventType": "Goal", "isOwnGoal": True, "min": 50}]}
    p = parse_fotmob(full)
    assert p["xg"] == ["1.04", "0.16"] and p["momentum"] == [[1, 33], [2, -14]]
    assert [(s["home"], s["goal"]) for s in p["shots"]] == [(True, True), (False, False)]
    print("PASS - FotMob detail parser")
