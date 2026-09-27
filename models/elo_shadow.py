"""elo_shadow.py - SHADOW predictions for NFL / NBA / NHL / MLB. Never served.

WHY (27 Sep 2026)
-----------------
The rule for a new sport (docs/basketball.md sections 5 and 7) is: grade on the
ledger for weeks BEFORE serving anything. Elo (models/elo.py) is the baseline,
and the live moneylines are now recorded (market_lines). This joins the two:
every upcoming non-soccer game gets Elo's pre-game home-win probability stored
next to the market's, so in a few weeks db/shadow_report.py can answer "does
Elo beat the bookmaker?" from real graded games instead of backtests.

NEVER SERVED - by construction, not by promise. It writes ONLY to
shadow_predictions, a table no endpoint, page, bot command or stats query
reads. model_predictions (which feeds /api/picks, the Record page and the
published accuracy) is never touched, so an unvalidated engine cannot leak
into the football track record (models/sports.py's whole purpose).

HOW
---
Ratings come from data/sports_history/<feed>.csv with the parameters tuned in
data/sports_history/elo_params.json, walked in date order up to the newest
completed game (point-in-time by construction). Teams are keyed by ESPN
franchise id; the ledger stores ESPN display names, mapped through each
franchise's most recent name. A game whose teams are not yet known ("TBD") is
skipped.

Off-season: run_elo only reverts ratings toward the mean when it SEES a new
season in the data. Before a new season's first game is in the history file,
the same revert is applied here if the game is more than NEW_SEASON_GAP_DAYS
after the last completed game - otherwise opening night would be predicted on
last season's unregressed ratings.

The prediction is UPDATED until kickoff (the latest pre-game view is the one
to grade), and frozen afterwards. The market figure is the latest stored
moneyline with the margin removed: p_home = (1/h) / (1/h + 1/a [+ 1/d]).

Usage:
    python models/elo_shadow.py            # all four leagues
"""

import csv
import json
import os
import sys
from datetime import datetime, timezone

from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from models.elo import HIST, MEAN, load, run_elo, win_prob

MODEL_VERSION = "elo_v1"
NEW_SEASON_GAP_DAYS = 45
FEEDS = {"nfl": "american_football", "nba": "basketball",
         "nhl": "ice_hockey", "mlb": "baseball"}


def ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            model_version TEXT NOT NULL,
            predicted_at TEXT NOT NULL,
            home_rating REAL, away_rating REAL,
            p_home REAL NOT NULL,
            market_p_home REAL,
            market_odds_home REAL, market_odds_away REAL, market_odds_draw REAL,
            market_observed_at TEXT,
            UNIQUE (match_id, model_version),
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        )
    """)


def ratings_for(feed: str, params: dict):
    games = load(feed)
    _, ratings = run_elo(games, params["k"], params["hfa"], params["revert"], params["mov"])
    name_to_id = {}
    for g in games:                     # later games overwrite earlier names
        name_to_id[g["home_name"]] = g["home"]
        name_to_id[g["away_name"]] = g["away"]
    last = max(g["date"] for g in games)[:10]
    return ratings, name_to_id, last


def run() -> int:
    params_all = json.load(open(os.path.join(HIST, "elo_params.json"), encoding="utf-8"))
    conn = get_connection()
    ensure_table(conn)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    now_s = now.strftime("%Y-%m-%d %H:%M")
    total = written = skipped = 0

    for feed, sport in FEEDS.items():
        params = params_all.get(feed, {}).get("params")
        if not params:
            logger.warning(f"{feed}: no tuned Elo params - run models/elo.py")
            continue
        ratings, name_to_id, last_played = ratings_for(feed, params)
        games = conn.execute("""
            SELECT match_id, home_team, away_team, kickoff_utc FROM matches
            WHERE sport = ? AND kickoff_utc > ?
              AND (live_state IS NULL OR live_state = 'pre')
        """, [sport, now_s]).fetchall()
        for g in games:
            total += 1
            hid, aid = name_to_id.get(g["home_team"]), name_to_id.get(g["away_team"])
            if not hid or not aid:
                skipped += 1          # "TBD" playoff slots, or a brand-new team
                continue
            rh, ra = ratings.get(hid, MEAN), ratings.get(aid, MEAN)
            gap = (datetime.strptime(g["kickoff_utc"][:10], "%Y-%m-%d")
                   - datetime.strptime(last_played, "%Y-%m-%d")).days
            if gap > NEW_SEASON_GAP_DAYS:   # history has not reached the new season yet
                rh = rh * (1 - params["revert"]) + MEAN * params["revert"]
                ra = ra * (1 - params["revert"]) + MEAN * params["revert"]
            p_home = win_prob(rh + params["hfa"] - ra)

            line = conn.execute("""
                SELECT odds_home, odds_away, odds_draw, observed_at FROM market_lines
                WHERE match_id = ? AND market = 'moneyline' ORDER BY id DESC LIMIT 1
            """, [g["match_id"]]).fetchone()
            mp = None
            if line and line["odds_home"] and line["odds_away"]:
                inv = [1 / line["odds_home"], 1 / line["odds_away"]] + \
                      ([1 / line["odds_draw"]] if line["odds_draw"] else [])
                mp = round(inv[0] / sum(inv), 4)
            conn.execute("""
                INSERT INTO shadow_predictions
                (match_id, model_version, predicted_at, home_rating, away_rating, p_home,
                 market_p_home, market_odds_home, market_odds_away, market_odds_draw,
                 market_observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (match_id, model_version) DO UPDATE SET
                  predicted_at = excluded.predicted_at, home_rating = excluded.home_rating,
                  away_rating = excluded.away_rating, p_home = excluded.p_home,
                  market_p_home = excluded.market_p_home,
                  market_odds_home = excluded.market_odds_home,
                  market_odds_away = excluded.market_odds_away,
                  market_odds_draw = excluded.market_odds_draw,
                  market_observed_at = excluded.market_observed_at
            """, [g["match_id"], MODEL_VERSION, now.strftime("%Y-%m-%d %H:%M:%S"),
                  round(rh, 1), round(ra, 1), round(p_home, 4), mp,
                  line["odds_home"] if line else None, line["odds_away"] if line else None,
                  line["odds_draw"] if line else None, line["observed_at"] if line else None])
            written += 1
    conn.commit()
    conn.close()
    logger.success(f"Shadow Elo: {written} of {total} upcoming NFL/NBA/NHL/MLB games "
                   f"predicted ({skipped} skipped: teams not known yet). Not served.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
