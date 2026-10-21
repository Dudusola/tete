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
from db.database import get_connection, init_db
from models import nfl_markets, nhl_form, nhl_markets
from models.elo import HIST, MEAN, load, run_elo, win_prob

MODEL_VERSION = "elo_v1"
# NBA Elo extensions, chosen on 2012-2024 and held out on 2025-26 by
# models/elo_plus.py: log loss -0.0038 [-0.0061, -0.0013]. Elo points.
NBA_PLUS = {"early_k": 1.5, "early_n": 10, "b2b": -45}
NEW_SEASON_GAP_DAYS = 45
FEEDS = {"nfl": "american_football", "nba": "basketball",
         "nhl": "ice_hockey", "mlb": "baseball"}


def ensure_table(conn) -> None:
    init_db()   # the NHL columns (markets_json, facts_json...) live there
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


def ratings_for(feed: str, params: dict, plus: dict | None = None):
    games = load(feed)
    extra = {"early_k": plus["early_k"], "early_n": plus["early_n"]} if plus else {}
    _, ratings = run_elo(games, params["k"], params["hfa"], params["revert"], params["mov"],
                         **extra)
    name_to_id = {}
    for g in games:                     # later games overwrite earlier names
        name_to_id[g["home_name"]] = g["home"]
        name_to_id[g["away_name"]] = g["away"]
    last = max(g["date"] for g in games)[:10]
    return ratings, name_to_id, last


def _fair(a: float | None, b: float | None) -> float | None:
    """Margin-free probability of side a from a two-way pair of decimal odds."""
    if not a or not b:
        return None
    return round((1 / a) / (1 / a + 1 / b), 4)


def _latest(conn, match_id: str, market: str):
    return conn.execute("""
        SELECT * FROM market_lines WHERE match_id = ? AND market = ?
        ORDER BY id DESC LIMIT 1
    """, [match_id, market]).fetchone()


def nhl_markets_for(conn, match_id: str, p_home: float, exp_total: float,
                    params: dict) -> tuple[list, dict]:
    """Every priced market for one NHL game, ours beside the bookmaker's.

    Returns (items, movement). A market the book is not quoting at a line we
    priced keeps market_p = None - it is never matched to a neighbouring line.
    """
    ours = nhl_markets.price(p_home, exp_total, params)
    book = {}                       # key -> (decimal odds, fair probability)
    movement = {}
    ml = _latest(conn, match_id, "moneyline")
    if ml:
        book["ml_home"] = (ml["odds_home"], _fair(ml["odds_home"], ml["odds_away"]))
        book["ml_away"] = (ml["odds_away"], _fair(ml["odds_away"], ml["odds_home"]))
        movement["moneyline"] = {"open": [ml["open_home"], ml["open_away"]],
                                 "now": [ml["odds_home"], ml["odds_away"]]}
    sp = _latest(conn, match_id, "spread")
    if sp and sp["line"] in (-1.5, 1.5):
        h = "pl_home_-1.5" if sp["line"] == -1.5 else "pl_home_+1.5"
        a = "pl_away_+1.5" if sp["line"] == -1.5 else "pl_away_-1.5"
        book[h] = (sp["odds_home"], _fair(sp["odds_home"], sp["odds_away"]))
        book[a] = (sp["odds_away"], _fair(sp["odds_away"], sp["odds_home"]))
        movement["puck_line"] = {"home_line": sp["line"], "open_home_line": sp["open_line"],
                                 "open": [sp["open_home"], sp["open_away"]],
                                 "now": [sp["odds_home"], sp["odds_away"]]}
    tot = _latest(conn, match_id, "total")
    if tot and tot["line"] in nhl_markets.TOTAL_LINES:
        o, u = f"over_{tot['line']}", f"under_{tot['line']}"
        book[o] = (tot["odds_over"], _fair(tot["odds_over"], tot["odds_under"]))
        book[u] = (tot["odds_under"], _fair(tot["odds_under"], tot["odds_over"]))
    if tot:
        movement["total"] = {"line": tot["line"], "open_line": tot["open_line"],
                             "open": [tot["open_over"], tot["open_under"]],
                             "now": [tot["odds_over"], tot["odds_under"]]}
    items = []
    for key, p in ours.items():
        odds, fair = book.get(key, (None, None))
        items.append({"key": key, "p": p, "market_p": fair, "odds": odds})
    return items, movement


def nfl_markets_for(conn, match_id: str, p_home: float, mu_total: float,
                    params: dict) -> tuple[list, dict]:
    """NFL moneyline, spread and total - priced at the BOOK's own lines only."""
    ml = _latest(conn, match_id, "moneyline")
    sp = _latest(conn, match_id, "spread")
    tot = _latest(conn, match_id, "total")
    home_line = sp["line"] if sp and sp["line"] is not None else None
    total_line = tot["line"] if tot and tot["line"] is not None else None
    ours = nfl_markets.price(p_home, mu_total, params, home_line, total_line)
    book, movement = {}, {}
    if ml:
        book["ml_home"] = (ml["odds_home"], _fair(ml["odds_home"], ml["odds_away"]))
        book["ml_away"] = (ml["odds_away"], _fair(ml["odds_away"], ml["odds_home"]))
        movement["moneyline"] = {"open": [ml["open_home"], ml["open_away"]],
                                 "now": [ml["odds_home"], ml["odds_away"]]}
    if home_line is not None:
        h = f"spread_home_{nfl_markets._fmt(home_line)}"
        a = f"spread_away_{nfl_markets._fmt(-home_line)}"
        book[h] = (sp["odds_home"], _fair(sp["odds_home"], sp["odds_away"]))
        book[a] = (sp["odds_away"], _fair(sp["odds_away"], sp["odds_home"]))
        movement["spread"] = {"home_line": home_line, "open_home_line": sp["open_line"],
                              "open": [sp["open_home"], sp["open_away"]],
                              "now": [sp["odds_home"], sp["odds_away"]]}
    if total_line is not None:
        book[f"over_{total_line:g}"] = (tot["odds_over"], _fair(tot["odds_over"], tot["odds_under"]))
        book[f"under_{total_line:g}"] = (tot["odds_under"], _fair(tot["odds_under"], tot["odds_over"]))
        movement["total"] = {"line": total_line, "open_line": tot["open_line"],
                             "open": [tot["open_over"], tot["open_under"]],
                             "now": [tot["odds_over"], tot["odds_under"]]}
    items = []
    for key, p in ours.items():
        if key.startswith("_"):
            continue                    # push probabilities, carried separately
        odds, fair = book.get(key, (None, None))
        items.append({"key": key, "p": p, "market_p": fair, "odds": odds})
    pushes = {k[1:]: v for k, v in ours.items() if k.startswith("_")}
    return items, movement, pushes


def run() -> int:
    params_all = json.load(open(os.path.join(HIST, "elo_params.json"), encoding="utf-8"))
    conn = get_connection()
    ensure_table(conn)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    now_s = now.strftime("%Y-%m-%d %H:%M")
    total = written = skipped = 0
    nhl_params = nhl_markets.load_params()
    if not nhl_params:
        logger.warning("nhl: no fitted market params - run models/nhl_markets.py")
    nfl_params = nfl_markets.load_params()
    if not nfl_params:
        logger.warning("nfl: no fitted market params - run models/nfl_markets.py")
    nba_params = nfl_markets.load_params("nba")
    if not nba_params:
        logger.warning("nba: no fitted market params - run models/nfl_markets.py --feed nba")

    for feed, sport in FEEDS.items():
        params = params_all.get(feed, {}).get("params")
        if not params:
            logger.warning(f"{feed}: no tuned Elo params - run models/elo.py")
            continue
        ratings, name_to_id, last_played = ratings_for(feed, params,
                                                       NBA_PLUS if feed == "nba" else None)
        nhl = feed == "nhl" and nhl_params
        nfl = feed == "nfl" and nfl_params
        nba = feed == "nba" and nba_params
        if nhl or nfl or nba:
            if nhl:
                nhl_state = nhl_markets.current_rates(nhl_params, params)
            elif nba:
                nba_state = nfl_markets.current_rates(nba_params, params)
                # Stars (18+ ppg last season) on ESPN's injury report, once per
                # run (models/nba_stars.py). {} if a source is down: no warning.
                from models.nba_stars import live_status as nba_stars_out
                stars_out = nba_stars_out()
            elif nfl:
                nfl_state = nfl_markets.current_rates(nfl_params, params)
                # Regular starting QB per team + ESPN injury status, once per run
                # (models/nfl_qb.py). {} if a source is down: no warning, no guess.
                from models.nfl_qb import live_status
                qb_status = live_status()
            nhl_games = nhl_form.load_games(feed)
            # Ranks on the ratings a game is actually predicted with: after the
            # off-season revert when the history has not reached the new season.
            gap = (now - datetime.strptime(last_played, "%Y-%m-%d")).days
            ranked = {t: (r * (1 - params["revert"]) + MEAN * params["revert"]
                          if gap > NEW_SEASON_GAP_DAYS else r) for t, r in ratings.items()}
            nhl_ranks = nhl_form.rating_ranks(ranked, nhl_games)
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
            adj = 0.0
            if nba:     # second night of a back-to-back (elo_plus.py, held out 2025-26)
                for team, sign in ((hid, 1), (aid, -1)):
                    rest = nhl_form.team_facts(team, g["kickoff_utc"][:10], nhl_games,
                                               "home")["rest_days"]
                    if rest is not None and rest <= 1:
                        adj += sign * NBA_PLUS["b2b"]
            p_home = win_prob(rh + params["hfa"] - ra + adj)

            line = conn.execute("""
                SELECT odds_home, odds_away, odds_draw, observed_at FROM market_lines
                WHERE match_id = ? AND market = 'moneyline' ORDER BY id DESC LIMIT 1
            """, [g["match_id"]]).fetchone()
            mp = None
            if line and line["odds_home"] and line["odds_away"]:
                inv = [1 / line["odds_home"], 1 / line["odds_away"]] + \
                      ([1 / line["odds_draw"]] if line["odds_draw"] else [])
                mp = round(inv[0] / sum(inv), 4)
            markets_json = facts_json = None
            if nhl:
                exp_total = nhl_markets.expected_total(nhl_state, hid, aid, g["kickoff_utc"][:10])
                items, movement = nhl_markets_for(conn, g["match_id"], p_home, exp_total, nhl_params)
                markets_json = json.dumps({"version": nhl_params.get("version", "nhl_mk_v1"),
                                           "exp_total": round(exp_total, 2), "items": items})
                facts = nhl_form.build(hid, aid, g["kickoff_utc"][:10], nhl_games,
                                       ranked, nhl_ranks)
                facts["market_movement"] = movement
                facts["goalies"] = nhl_form.goalie_facts(conn, g["match_id"], g["kickoff_utc"])
                facts_json = json.dumps(facts)
            elif nfl:
                mu_total = nfl_markets.expected_total(nfl_state, nfl_params, hid, aid,
                                                      g["kickoff_utc"][:10])
                items, movement, pushes = nfl_markets_for(conn, g["match_id"], p_home,
                                                          mu_total, nfl_params)
                markets_json = json.dumps({"version": "nfl_mk_v1", "exp_total": round(mu_total, 1),
                                           "pushes": pushes, "items": items})
                facts = nhl_form.build(hid, aid, g["kickoff_utc"][:10], nhl_games,
                                       ranked, nhl_ranks)
                facts["market_movement"] = movement
                facts["qb"] = {side: qb_status.get(team) for side, team in
                               (("home", g["home_team"]), ("away", g["away_team"]))}
                facts_json = json.dumps(facts)
            elif nba:
                mu_total = nfl_markets.expected_total(nba_state, nba_params, hid, aid,
                                                      g["kickoff_utc"][:10])
                items, movement, pushes = nfl_markets_for(conn, g["match_id"], p_home,
                                                          mu_total, nba_params)
                # Spread/total legs carry the BOOK's fair probability, not ours:
                # on 5 past seasons our spread/total scored WORSE than a flat 50%
                # at the book's line (models/nba_market_backtest.py). Moneyline
                # stays Elo - that is the pick, and it is labelled experimental.
                items = [it if it["key"].startswith("ml_")
                         else dict(it, model_p=it["p"], p=it["market_p"])
                         for it in items
                         if it["key"].startswith("ml_") or it["market_p"] is not None]
                markets_json = json.dumps({"version": "nba_mk_v2", "exp_total": round(mu_total, 1),
                                           "pushes": pushes, "items": items})
                facts = nhl_form.build(hid, aid, g["kickoff_utc"][:10], nhl_games,
                                       ranked, nhl_ranks)
                facts["market_movement"] = movement
                facts["stars_out"] = {side: stars_out.get(team, []) for side, team in
                                      (("home", g["home_team"]), ("away", g["away_team"]))}
                facts_json = json.dumps(facts)
            conn.execute("""
                INSERT INTO shadow_predictions
                (match_id, model_version, predicted_at, home_rating, away_rating, p_home,
                 market_p_home, market_odds_home, market_odds_away, market_odds_draw,
                 market_observed_at, markets_json, facts_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (match_id, model_version) DO UPDATE SET
                  predicted_at = excluded.predicted_at, home_rating = excluded.home_rating,
                  away_rating = excluded.away_rating, p_home = excluded.p_home,
                  market_p_home = excluded.market_p_home,
                  market_odds_home = excluded.market_odds_home,
                  market_odds_away = excluded.market_odds_away,
                  market_odds_draw = excluded.market_odds_draw,
                  market_observed_at = excluded.market_observed_at,
                  markets_json = excluded.markets_json,
                  facts_json = excluded.facts_json
            """, [g["match_id"], MODEL_VERSION, now.strftime("%Y-%m-%d %H:%M:%S"),
                  round(rh, 1), round(ra, 1), round(p_home, 4), mp,
                  line["odds_home"] if line else None, line["odds_away"] if line else None,
                  line["odds_draw"] if line else None, line["observed_at"] if line else None,
                  markets_json, facts_json])
            written += 1
    conn.commit()
    conn.close()
    logger.success(f"Shadow Elo: {written} of {total} upcoming NFL/NBA/NHL/MLB games "
                   f"predicted ({skipped} skipped: teams not known yet). Not served.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
