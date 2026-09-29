"""nhl_goalies.py - NHL goalies per game from the league's own API (no key).

WHY
---
The goalie is the single biggest input to an NHL game that our preview did not
have - the bookmaker has it, which is most of why the market should beat us.
Probed 29 Sep 2026 (api-web.nhle.com):
  - BEFORE a game, /gamecenter/<id>/landing lists each team's goalies with
    their season numbers (matchup.goalieComparison). It does NOT say who
    starts: teams confirm that around warm-ups, after the day's analysis is
    written. So `started` is stored as NULL = unknown, and the analysis is
    forbidden to name a starter.
  - AFTER a game, /gamecenter/<id>/boxscore flags `starter` per goalie, with
    saves, shots and decision. Boxscores stay up for past seasons, so a missed
    day is recoverable (`--days-back 30`) - unlike ESPN's odds.
Recording real starters is what makes starter PREDICTION possible later
(who plays the second night of a back-to-back, how often the #1 starts).

MATCHING - exact, no fuzzy matcher
----------------------------------
NHL fixtures come from ESPN (espn_ledger.py). The NHL API names all 32 teams
identically once accents and full stops are dropped ("Montréal Canadiens",
"St. Louis Blues") - checked for all 32 on 29 Sep 2026. So a game is joined on
BOTH normalised team names AND the UTC kickoff date, exactly. A game that does
not join is logged by name, never guessed at (bug #22's lesson: a near match
between two fixtures is how the wrong game gets graded).

Only regular season (gameType 2) and playoffs (3); preseason is skipped, as in
espn_ledger (bug #31).

Usage:
    python scrapers/nhl_goalies.py                  # 3 days back, 3 ahead
    python scrapers/nhl_goalies.py --days-back 30   # backfill starters
"""

import argparse
import os
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

import requests
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection, init_db
from models.sports import ICE_HOCKEY, sport_sql

API = "https://api-web.nhle.com/v1"
GAME_TYPES = (2, 3)
FINISHED = ("OFF", "FINAL")


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(s.lower().replace(".", "").split())


def _get(path: str) -> dict | None:
    for attempt in range(3):
        try:
            r = requests.get(f"{API}/{path}", timeout=20)
            if r.status_code == 200:
                return r.json()
            logger.warning(f"  {path}: HTTP {r.status_code}")
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"  {path}: {e}")
        if attempt < 2:
            import time
            time.sleep(2 * (attempt + 1))
    return None


def _team(t: dict) -> str:
    return f"{t['placeName']['default']} {t['commonName']['default']}"


def schedule(dates: list) -> tuple[list, int, int]:
    """NHL games on the given UTC dates. /schedule/<d> returns a whole week,
    so each call covers several dates. Returns (games, ok, failed)."""
    games, seen, ok, failed = [], set(), 0, 0
    pending = sorted(set(dates))
    covered = set()
    for d in pending:
        if d in covered:
            continue
        data = _get(f"schedule/{d}")
        if data is None:
            failed += 1
            continue
        ok += 1
        for day in data.get("gameWeek", []):
            covered.add(day.get("date"))
            for g in day.get("games", []):
                if g["id"] in seen or g.get("gameType") not in GAME_TYPES:
                    continue
                seen.add(g["id"])
                games.append({"id": g["id"], "state": g.get("gameState"),
                              "utc": (g.get("startTimeUTC") or "")[:10],
                              "home": _team(g["homeTeam"]), "away": _team(g["awayTeam"])})
    return games, ok, failed


def ledger_games(conn, days_back: int, days_ahead: int) -> dict:
    now = datetime.now(timezone.utc)
    lo = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
    hi = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d 23:59")
    rows = conn.execute(f"""
        SELECT match_id, home_team, away_team, kickoff_utc FROM matches m
        WHERE kickoff_utc >= ? AND kickoff_utc <= ? {sport_sql(ICE_HOCKEY)}
    """, [lo, hi]).fetchall()
    return {(norm(r["home_team"]), norm(r["away_team"]), r["kickoff_utc"][:10]): r["match_id"]
            for r in rows}


def _pct(v):
    return None if v is None else round(float(v), 3)


def pregame_rows(landing: dict) -> list:
    gc = (landing.get("matchup") or {}).get("goalieComparison") or {}
    season = gc.get("contextSeason")
    out = []
    for side, key in (("home", "homeTeam"), ("away", "awayTeam")):
        for p in (gc.get(key) or {}).get("leaders") or []:
            out.append({"side": side, "player_id": p["playerId"],
                        "name": p["name"]["default"], "stats_season": season,
                        "season_gp": p.get("gamesPlayed"), "season_record": p.get("record"),
                        "season_sv_pct": _pct(p.get("savePctg")),
                        "season_gaa": None if p.get("gaa") is None else round(p["gaa"], 2)})
    return out


def boxscore_rows(box: dict) -> list:
    out = []
    stats = box.get("playerByGameStats") or {}
    for side, key in (("home", "homeTeam"), ("away", "awayTeam")):
        for p in (stats.get(key) or {}).get("goalies") or []:
            out.append({"side": side, "player_id": p["playerId"], "name": p["name"]["default"],
                        "started": 1 if p.get("starter") else 0, "toi": p.get("toi"),
                        "shots_against": p.get("shotsAgainst"), "saves": p.get("saves"),
                        "goals_against": p.get("goalsAgainst"), "decision": p.get("decision")})
    return out


def upsert(conn, match_id: str, row: dict, now: str) -> None:
    cols = [k for k in row if k not in ("side", "player_id", "name")]
    conn.execute(f"""
        INSERT INTO nhl_goalies (match_id, side, player_id, name, updated_at
                                 {"".join(", " + c for c in cols)})
        VALUES (?, ?, ?, ?, ? {", ?" * len(cols)})
        ON CONFLICT (match_id, player_id) DO UPDATE SET
          name = excluded.name, side = excluded.side, updated_at = excluded.updated_at
          {"".join(f", {c} = excluded.{c}" for c in cols)}
    """, [match_id, row["side"], row["player_id"], row["name"], now,
          *[row[c] for c in cols]])


def run(days_back: int = 3, days_ahead: int = 3) -> int:
    init_db()
    conn = get_connection()
    ours = ledger_games(conn, days_back, days_ahead)
    if not ours:
        logger.info("NHL goalies: no NHL fixtures in the window (off-season?).")
        conn.close()
        return 0
    games, ok, failed = schedule([k[2] for k in ours])
    if ok == 0:
        logger.error("NHL goalies: every schedule request failed - API unreachable.")
        conn.close()
        return 1
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    pre = post = unmatched = 0
    for g in games:
        mid = ours.get((norm(g["home"]), norm(g["away"]), g["utc"]))
        if not mid:
            # Only games in our window matter; the schedule returns a week.
            if any(k[2] == g["utc"] for k in ours):
                unmatched += 1
                logger.warning(f"  no ledger fixture for {g['away']} @ {g['home']} {g['utc']}")
            continue
        if g["state"] in FINISHED:
            box = _get(f"gamecenter/{g['id']}/boxscore")
            for row in boxscore_rows(box or {}):
                upsert(conn, mid, row, now)
                post += 1
        elif g["state"] in ("FUT", "PRE"):
            landing = _get(f"gamecenter/{g['id']}/landing")
            for row in pregame_rows(landing or {}):
                upsert(conn, mid, row, now)
                pre += 1
    conn.commit()
    conn.close()
    logger.success(f"NHL goalies: {pre} pre-game goalie rows, {post} post-game "
                   f"(starters known), {unmatched} NHL games with no ledger fixture.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-back", type=int, default=3)
    ap.add_argument("--days-ahead", type=int, default=3)
    a = ap.parse_args()
    sys.exit(run(a.days_back, a.days_ahead))
