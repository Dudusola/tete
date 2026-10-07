"""nfl_qb.py - how much does a BACKUP quarterback cost, and is it worth adding?

The quarterback is the biggest single thing our NFL Elo cannot see (Boss,
6 Oct 2026: "NFL quarterback and injury news" was one of the two chosen
improvements). nflverse publishes every NFL game's STARTING quarterback
since 1999, with ESPN's own game id (data/nfldata games.csv), so each of our
games joins on the event id exactly - no name matching.

For every game, each team's REGULAR starter is the QB who started most of its
previous REGULAR_WINDOW games (point-in-time: earlier games only). A team is
"on a backup" when someone else starts. The cost of that, in Elo points, is
fitted on Elo's training seasons and scored ONCE on the held-out seasons; it
only ships if the held-out log-loss gain has a bootstrap CI below zero.

Live, nflverse does not know who WILL start, so models/elo_shadow.py asks
ESPN's injury report whether a team's regular starter is listed Out
(scrapers/nfl_injuries.py) and applies the fitted cost then.

Usage:
    python models/nfl_qb.py            # download, fit, report, write params
    python models/nfl_qb.py --refresh  # daily: refresh the starts file only
"""

import csv
import json
import math
import os
import random
import sys

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.elo import HIST, load, run_elo, win_prob

URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
QB_CSV = os.path.join(HIST, "nfl_qb_starts.csv")
PARAMS = os.path.join(HIST, "nfl_qb_params.json")
REGULAR_WINDOW = 6
COSTS = list(range(0, 161, 10))        # Elo points a backup costs, searched


def download() -> int:
    r = requests.get(URL, timeout=120)
    r.raise_for_status()
    rows = list(csv.DictReader(r.text.splitlines()))
    keep = [{"espn": x["espn"], "season": x["season"], "gameday": x["gameday"],
             "home_team": x["home_team"], "away_team": x["away_team"],
             "home_qb_id": x["home_qb_id"], "away_qb_id": x["away_qb_id"],
             "home_qb_name": x["home_qb_name"], "away_qb_name": x["away_qb_name"]}
            for x in rows if x.get("espn") and int(x["season"]) >= 2009]
    with open(QB_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(keep[0]))
        w.writeheader()
        w.writerows(keep)
    return len(keep)


def starts() -> dict:
    """espn event id -> {home_qb, away_qb, home_team, away_team, gameday}."""
    with open(QB_CSV, newline="", encoding="utf-8") as fh:
        return {r["espn"]: r for r in csv.DictReader(fh)}


def regular_flags(games: list, qb: dict) -> dict:
    """espn id -> (home_on_backup, away_on_backup), point-in-time.

    A team's regular starter = most starts in its previous REGULAR_WINDOW games
    (any season, so week 1 uses last season's tail). No history -> not flagged.
    """
    history = {}                  # team abbreviation -> list of starter ids
    flags = {}
    for g in sorted(games, key=lambda x: x["date"]):
        s = qb.get(g["event"])
        if not s or not s["home_qb_id"] or not s["away_qb_id"]:
            continue
        out = []
        for side in ("home", "away"):
            team, starter = s[f"{side}_team"], s[f"{side}_qb_id"]
            past = history.get(team, [])[-REGULAR_WINDOW:]
            if len(past) >= 3:
                regular = max(set(past), key=past.count)
                out.append(starter != regular)
            else:
                out.append(False)
            history.setdefault(team, []).append(starter)
        flags[g["event"]] = tuple(out)
    return flags


def run_with_qb(games, params, flags, cost):
    """Elo whose PREDICTION subtracts `cost` from a side on a backup. The
    ratings themselves update exactly as plain Elo (the QB is a game-day
    adjustment, not a change in the team's underlying strength)."""
    pairs, _ = run_elo(games, params["k"], params["hfa"], params["revert"], params["mov"])
    out = []
    for g, p in pairs:
        hb, ab = flags.get(g["event"], (False, False))
        if hb == ab or cost == 0:
            out.append((g, p))
            continue
        diff = 400 * math.log10(p / (1 - p)) + (cost if ab else 0) - (cost if hb else 0)
        out.append((g, win_prob(diff)))
    return out


def ll(g, p):
    y = 0.5 if g["hs"] == g["as"] else (1.0 if g["hs"] > g["as"] else 0.0)
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
WARN_STATUSES = ("Out", "Doubtful", "Questionable", "Injured Reserve", "Suspension")


def _norm(name: str) -> str:
    import re
    return re.sub(r"[^a-z]", "", (name or "").lower().replace(" jr", "").replace(" sr", ""))


def regular_starters() -> dict:
    """Our ESPN team display name -> name of its regular starting QB (most
    starts in its last REGULAR_WINDOW games in the nflverse file). nflverse
    uses abbreviations, so names are mapped through games both files share."""
    qb = starts()
    with open(os.path.join(HIST, "nfl.csv"), newline="", encoding="utf-8") as fh:
        espn = {r["event_id"]: r for r in csv.DictReader(fh)}
    abbr_to_name, recent = {}, {}
    for eid, s in sorted(qb.items(), key=lambda kv: kv[1]["gameday"]):
        e = espn.get(eid)
        if e:
            abbr_to_name[s["home_team"]] = e["home_team"]
            abbr_to_name[s["away_team"]] = e["away_team"]
        for side in ("home", "away"):
            if s[f"{side}_qb_name"]:
                recent.setdefault(s[f"{side}_team"], []).append(s[f"{side}_qb_name"])
    out = {}
    for abbr, names in recent.items():
        last = names[-REGULAR_WINDOW:]
        if abbr in abbr_to_name and len(last) >= 3:
            out[abbr_to_name[abbr]] = max(set(last), key=last.count)
    return out


def live_status() -> dict:
    """Team name -> {"regular": QB name, "status": ESPN injury status or None}.

    None = the regular starter is not on ESPN's injury report. Empty dict when
    either source is unavailable - the caller shows nothing rather than guess.
    """
    try:
        regular = regular_starters()
        d = requests.get(INJURIES_URL, timeout=30).json()
    except Exception:
        return {}
    listed = {}
    for t in d.get("injuries") or []:
        for i in t.get("injuries") or []:
            a = i.get("athlete") or {}
            if (a.get("position") or {}).get("abbreviation") == "QB":
                listed[(t.get("displayName"), _norm(a.get("displayName")))] = i.get("status")
    return {team: {"regular": qb, "status": listed.get((team, _norm(qb)))}
            for team, qb in regular.items()}


def main() -> int:
    if "--refresh" in sys.argv:
        # Daily pipeline: only refresh who has started, no refit.
        print(f"nflverse starting QBs refreshed: {download()} games")
        return 0
    n = download()
    print(f"nflverse starting QBs: {n} games since 2009")
    elo = json.load(open(os.path.join(HIST, "elo_params.json"), encoding="utf-8"))["nfl"]
    params, (lo, hi), test = elo["params"], elo["seasons"]["train"], set(elo["seasons"]["test"])
    games = load("nfl")
    with open(os.path.join(HIST, "nfl.csv"), newline="", encoding="utf-8") as fh:
        ev = {(r["kickoff_utc"] or r["date"], r["home_id"]): r["event_id"] for r in csv.DictReader(fh)}
    for g in games:
        g["event"] = ev.get((g["date"], g["home"]))
    qb = starts()
    flags = regular_flags(games, qb)
    joined = sum(1 for g in games if g["event"] in flags)
    backups = sum(1 for f in flags.values() for x in f if x)
    print(f"  joined on ESPN id: {joined}/{len(games)} games; team-games on a backup: {backups}")

    best = None
    for cost in COSTS:
        pairs = run_with_qb(games, params, flags, cost)
        tr = [ll(g, p) for g, p in pairs if lo <= g["season"] <= hi]
        m = sum(tr) / len(tr)
        if best is None or m < best[0]:
            best = (m, cost)
    cost = best[1]
    base = run_with_qb(games, params, flags, 0)
    plus = run_with_qb(games, params, flags, cost)
    d = [ll(g, p2) - ll(g, p1) for (g, p1), (_, p2) in zip(base, plus) if g["season"] in test]
    affected = [(g, p1, p2) for (g, p1), (_, p2) in zip(base, plus)
                if g["season"] in test and p1 != p2]
    m = sum(d) / len(d)
    rng = random.Random(7)
    boots = sorted(sum(d[rng.randrange(len(d))] for _ in d) / len(d) for _ in range(2000))
    lo_ci, hi_ci = boots[50], boots[1949]
    verdict = "BETTER" if hi_ci < 0 else ("worse" if lo_ci > 0 else "no measurable difference")
    print(f"  backup QB cost chosen on {lo}-{hi}: {cost} Elo points "
          f"(~{cost / 25:.1f} points of spread)")
    print(f"  held-out {sorted(test)}: {len(d)} games, {len(affected)} with exactly one side on a backup")
    print(f"    log loss Elo {sum(ll(g, p) for g, p in base if g['season'] in test) / len(d):.4f} -> "
          f"with QB {sum(ll(g, p) for g, p in plus if g['season'] in test) / len(d):.4f}  "
          f"diff {m:+.4f} [{lo_ci:+.4f}, {hi_ci:+.4f}] -> {verdict}")
    if affected:
        a1 = sum(ll(g, p1) for g, p1, _ in affected) / len(affected)
        a2 = sum(ll(g, p2) for g, _, p2 in affected) / len(affected)
        print(f"    on the affected games only: {a1:.4f} -> {a2:.4f}")
    json.dump({"backup_cost_elo": cost, "regular_window": REGULAR_WINDOW,
               "held_out": {"games": len(d), "affected": len(affected), "diff": round(m, 5),
                            "ci": [round(lo_ci, 5), round(hi_ci, 5)], "verdict": verdict}},
              open(PARAMS, "w", encoding="utf-8"), indent=1)
    print(f"Wrote {PARAMS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
