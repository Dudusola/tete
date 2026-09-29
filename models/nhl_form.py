"""nhl_form.py - the FACTS an NHL analysis is allowed to use.

Bug #28 in CLAUDE.md is the reason this exists as its own step: an LLM given
nothing but probabilities writes prose that fits every game ever played, and an
LLM asked for specifics without being given them invents injuries and goalies.
So the analysis is written ONLY from this dict, which is stored beside it
(shadow_predictions.facts_json) so any sentence can be checked against what the
writer was actually handed.

Everything here comes from data/sports_history/nhl.csv - real finals, keyed by
ESPN franchise id - and is point-in-time: only games before the kickoff count.

Three rules carried over from bug #28, each of which broke once in football:
  - A count must say how many games it is out of. "3-0-0" over 3 games is not
    "3 straight wins in the last 10", and n travels with every record.
  - Absence is not zero. No head-to-head is "never met in the file", not 0-0.
  - The new season is flagged. On opening night every "recent" figure is from
    last April, and the summer's trades are not in the data at all.

NHL records are W-L-OTL: a loss in overtime or a shootout is its own column,
because it earned a standings point and is not the same result as a
regulation defeat. Goals exclude the shootout "goal" (ESPN credits the
shootout winner +1 in the final; that is a tiebreak, not scoring).
"""

import csv
import os
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.elo import HIST

FORM_GAMES = 10
H2H_GAMES = 5
NEW_SEASON_GAP_DAYS = 45


def load_games() -> list:
    with open(os.path.join(HIST, "nhl.csv"), newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    games = []
    for r in rows:
        hs, as_ = int(r["home_score"]), int(r["away_score"])
        if r["decided_in"] == "SO":          # take the shootout "goal" back off
            hs, as_ = (hs - 1, as_) if hs > as_ else (hs, as_ - 1)
        games.append({"date": r["date"], "season": int(r["season"]),
                      "playoff": r["season_type"] == "3",
                      "home": r["home_id"], "away": r["away_id"],
                      "home_name": r["home_team"], "away_name": r["away_team"],
                      "hs": hs, "as": as_, "decided_in": r["decided_in"] or None,
                      "winner_home": int(r["home_score"]) > int(r["away_score"])})
    games.sort(key=lambda g: g["date"])
    return games


def _record(team: str, games: list) -> dict:
    w = l = otl = gf = ga = 0
    for g in games:
        home = g["home"] == team
        won = g["winner_home"] == home
        f, a = (g["hs"], g["as"]) if home else (g["as"], g["hs"])
        gf, ga = gf + f, ga + a
        if won:
            w += 1
        elif g["decided_in"] in ("OT", "SO"):
            otl += 1
        else:
            l += 1
    n = len(games)
    return {"n": n, "w": w, "l": l, "otl": otl,
            "playoff": sum(g["playoff"] for g in games),
            "gf_pg": round(gf / n, 2) if n else None,
            "ga_pg": round(ga / n, 2) if n else None}


def _streak(team: str, games: list) -> str | None:
    """'W3' / 'L2' - an OT/SO loss counts as a loss for the streak."""
    if not games:
        return None
    kind, n = None, 0
    for g in reversed(games):
        won = g["winner_home"] == (g["home"] == team)
        k = "W" if won else "L"
        if kind is None:
            kind = k
        if k != kind:
            break
        n += 1
    return f"{kind}{n}"


def team_facts(team: str, before: str, games: list, venue: str) -> dict:
    """Form for one side, using only games strictly before `before` (a date)."""
    played = [g for g in games if (g["home"] == team or g["away"] == team)
              and g["date"] < before]
    last = played[-1] if played else None
    gap = ((datetime.strptime(before, "%Y-%m-%d")
            - datetime.strptime(last["date"], "%Y-%m-%d")).days if last else None)
    new_season = gap is None or gap > NEW_SEASON_GAP_DAYS
    this_season = [] if new_season else [g for g in played if g["season"] == last["season"]
                                         and not g["playoff"]]
    at_venue = [g for g in played if (g["home"] == team) == (venue == "home")]
    recent = played[-FORM_GAMES:]
    return {
        "last10": _record(team, recent),
        "last10_through": recent[-1]["date"] if recent else None,
        "venue10": _record(team, at_venue[-FORM_GAMES:]),
        "venue": venue,
        "season": _record(team, this_season),
        "streak": _streak(team, recent),
        # Rest only means something inside a season; after a summer it is noise.
        "rest_days": None if new_season else gap,
        "back_to_back": (gap == 1) if not new_season else False,
        "new_season": new_season,
        "last_game": last["date"] if last else None,
        "last_playoff_game": next((g["date"] for g in reversed(played) if g["playoff"]), None),
    }


def head_to_head(home: str, away: str, before: str, games: list) -> list:
    met = [g for g in games if {g["home"], g["away"]} == {home, away} and g["date"] < before]
    out = []
    for g in met[-H2H_GAMES:][::-1]:
        out.append({"date": g["date"], "home": g["home_name"], "away": g["away_name"],
                    "score": f"{g['hs']}-{g['as']}",
                    "decided_in": g["decided_in"], "playoff": g["playoff"],
                    "winner": g["home_name"] if g["winner_home"] else g["away_name"]})
    return out


def rating_ranks(ratings: dict, games: list) -> dict:
    """Rank among the franchises that played last season (Arizona is gone)."""
    last_season = max(g["season"] for g in games)
    active = {t for g in games if g["season"] == last_season for t in (g["home"], g["away"])}
    order = sorted((t for t in active if t in ratings), key=lambda t: -ratings[t])
    return {t: i + 1 for i, t in enumerate(order)}, len(order)


def build(home_id: str, away_id: str, kickoff_date: str, games: list,
          ratings: dict, ranks: tuple) -> dict:
    rank, of = ranks
    home = team_facts(home_id, kickoff_date, games, "home")
    away = team_facts(away_id, kickoff_date, games, "away")
    home.update(rating=round(ratings.get(home_id, 0), 1), rank=rank.get(home_id), of=of)
    away.update(rating=round(ratings.get(away_id, 0), 1), rank=rank.get(away_id), of=of)
    return {"home": home, "away": away,
            "h2h": head_to_head(home_id, away_id, kickoff_date, games),
            "history_through": games[-1]["date"] if games else None}
