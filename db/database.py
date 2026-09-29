"""
Database handler - SQLite
All match data, predictions, and outcomes stored here
Run this file directly to initialize: python db/database.py
"""

import sqlite3
import os
from loguru import logger

DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # access columns by name
    return conn


def init_db():
    """Create all tables if they don't exist"""
    conn = get_connection()
    cursor = conn.cursor()

    # Core matches table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT UNIQUE,
            sport TEXT NOT NULL,
            league TEXT,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            match_date TEXT NOT NULL,
            kickoff_time TEXT,
            status TEXT DEFAULT 'upcoming',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Scraped predictions from Forebet / other sites
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scraped_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            source TEXT NOT NULL,
            home_win_pct REAL,
            draw_pct REAL,
            away_win_pct REAL,
            predicted_home_goals REAL,
            predicted_away_goals REAL,
            over_2_5_pct REAL,
            btts_pct REAL,
            home_form TEXT,
            away_form TEXT,
            scraped_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        )
    """)

    # Our ML model predictions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS model_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            model_version TEXT,
            home_win_prob REAL,
            draw_prob REAL,
            away_win_prob REAL,
            confidence TEXT,
            best_bet TEXT,
            best_bet_prob REAL,
            gemma_analysis TEXT,
            predicted_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        )
    """)

    # Actual results - filled after match ends
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT UNIQUE NOT NULL,
            home_goals INTEGER,
            away_goals INTEGER,
            outcome TEXT,
            over_2_5 INTEGER,
            btts INTEGER,
            recorded_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        )
    """)

    # Track prediction accuracy over time
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accuracy_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            total_predictions INTEGER,
            correct_predictions INTEGER,
            accuracy_pct REAL,
            sport TEXT,
            confidence_tier TEXT,
            logged_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Schema upgrades on existing DBs (ALTER is a no-op error if column exists)
    # Real bookmaker odds — the model trains on odds-implied probs, so serving
    # must feed real odds, not Forebet's percentages (train/serve mismatch fix).
    for col in ("odds_home", "odds_draw", "odds_away"):
        try:
            cursor.execute(f"ALTER TABLE scraped_predictions ADD COLUMN {col} REAL")
        except sqlite3.OperationalError:
            pass  # already exists

    # Consensus tier adjustment: 'boosted' (MEDIUM->HIGH, sources agree with
    # model), 'demoted' (HIGH->MEDIUM, sources agree against model), or NULL.
    # Stored so the ledger can later answer "do consensus boosts actually win?"
    try:
        cursor.execute("ALTER TABLE model_predictions ADD COLUMN consensus_adj TEXT")
    except sqlite3.OperationalError:
        pass  # already exists

    # The bookmaker's price for OUR pick, captured when the pick was made.
    #
    # This is the only way the project's central question can ever be answered:
    # "when we disagree with the market, are we right?" A model that merely
    # agrees with the odds adds nothing over reading the odds — the value, if
    # any, lives entirely in the disagreements.
    #
    # It MUST be snapshotted at prediction time. Odds move as money arrives, so
    # looking them up later measures a different market than the one we
    # disagreed with, and no amount of backfilling can reconstruct it. Every
    # day this is missing is a day of evidence that cannot be recovered.
    for col in ("book_odds REAL", "book_prob REAL"):
        try:
            cursor.execute(f"ALTER TABLE model_predictions ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # already exists

    # Written analysis for the DRAW view.
    #
    # This column was created only by analysis/draw_analysis.py, which runs as an
    # OPTIONAL pipeline step — but api/server.py:406 SELECTs it unconditionally.
    # On any database where that step has never succeeded (a fresh clone, a
    # rebuilt DB, a machine where the LLM chain is unavailable) /api/picks/draws
    # returns 500 with "no such column: draw_analysis" — the API being broken by
    # a step that is allowed to fail. Schema belongs here, where every other
    # migration lives and where init_db() guarantees it exists; the analysis
    # script fills the column, it should not also define it.
    try:
        cursor.execute("ALTER TABLE model_predictions ADD COLUMN draw_analysis TEXT")
    except sqlite3.OperationalError:
        pass  # already exists

    # Kickoff as a full UTC timestamp ('YYYY-MM-DD HH:MM').
    #
    # kickoff_time alone can't be filtered on: it is a bare 'HH:MM' with no
    # timezone, and PredictZ — the primary fixture source in the cloud — leaves
    # it at '00:00' for ~88% of fixtures, meaning UNKNOWN, not midnight. That is
    # why picks for already-played matches were still being recommended.
    # API-Football provides exact UTC kickoffs and is used to fill this in.
    # NULL means genuinely unknown; never treat NULL as midnight.
    try:
        cursor.execute("ALTER TABLE matches ADD COLUMN kickoff_utc TEXT")
    except sqlite3.OperationalError:
        pass  # already exists

    # Live state from ESPN: 'pre' (not started), 'in' (playing), 'post'
    # (finished). This catches what a kickoff time alone cannot — delayed or
    # rescheduled matches, and anything already in play. NULL = unknown.
    # espn_league is the slug the fixture was matched under (e.g. 'eng.1').
    # Storing it turns "what is the score right now?" from a 66-league sweep
    # into one request per league actually involved, which is what makes an
    # on-demand live score affordable on the free tier.
    for col in ("live_state TEXT", "live_checked_at TEXT", "espn_league TEXT"):
        try:
            cursor.execute(f"ALTER TABLE matches ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # already exists

    # HOW a non-soccer game was decided, and the score when regulation ended.
    #
    # ESPN's final score includes overtime and, in the NHL, the shootout (the
    # winner is credited one goal: NYI @ NSH "Final/SO" 2-1 was 1-1 after
    # regulation). Bookmakers' basic 1X2 for these sports settles on
    # REGULATION only (docs/basketball.md §2), where that game was a DRAW.
    # Grading a regulation market off the final score is bug #1's shape —
    # silent, and wrong only on the games that went to extra time. So the
    # ledger records both. decided_in: 'REG' | 'OT' | 'SO'; NULL = unknown or
    # not applicable (association football, where 90' is already regulation).
    for col in ("decided_in TEXT", "reg_home_goals INTEGER",
                "reg_away_goals INTEGER"):
        try:
            cursor.execute(f"ALTER TABLE results ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # already exists

    # Bookmaker lines for the NON-SOCCER ledgers (27 Sep 2026), one row per
    # OBSERVED price, so opening, movement and close are all kept. A separate
    # table on purpose: scraped_predictions is football's 3-way odds shape and
    # feeds model features; a 2-way moneyline there would be read as football
    # odds (bug #7's shape). ESPN deletes odds once a game is final, so these
    # rows are the ONLY way to later ask "does Elo (models/elo.py) beat the
    # market?" - every game played before recording started is lost to it.
    #   market     'moneyline' = full game INCLUDING overtime/shootout, the
    #              US standard (DraftKings via ESPN). Grade it on the FINAL
    #              score, never regulation (docs/basketball.md section 2).
    #   open_*     ESPN's opening price, as quoted alongside the current one.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            source TEXT NOT NULL,
            provider TEXT,
            market TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            mins_before INTEGER,
            odds_home REAL, odds_away REAL, odds_draw REAL,
            open_home REAL, open_away REAL, open_draw REAL,
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_market_lines_match "
                   "ON market_lines(match_id, id)")
    # Spread and total markets (29 Sep 2026). A price is meaningless without
    # its LINE, and the line itself moves (NYI @ TOR opened o6.5, closed o5.5),
    # so both the current and the opening line are kept.
    #   market 'spread'  odds_home/odds_away are the prices of the home and away
    #                    side AT `line`, which is the HOME side's line (-1.5
    #                    means home gives 1.5). Puck line / run line / spread.
    #   market 'total'   odds_over/odds_under at total `line`. Full game,
    #                    overtime included (and in the NHL the shootout winner
    #                    is credited one goal, as ESPN's final already is).
    # Over/under get their own columns rather than borrowing odds_home/away:
    # a column whose name lies is bug #23's shape.
    for col in ("line REAL", "open_line REAL", "odds_over REAL",
                "odds_under REAL", "open_over REAL", "open_under REAL"):
        try:
            cursor.execute(f"ALTER TABLE market_lines ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # already exists

    # CLOSING-LINE VALUE (27 Sep 2026). book_odds says what the market thought
    # when we picked; these say what it thought just before kickoff, from the
    # SAME feed. If prices keep moving TOWARD our picks, the market is agreeing
    # with us after the fact — evidence of an edge that needs no match result,
    # so it arrives weeks sooner than graded outcomes do.
    #   book_source / book_observed_at  which feed, and WHEN the pick-time
    #       price was observed (a stored ESPN row can be days older than the
    #       pick; without the timestamp "movement" has no known horizon).
    #   close_*  the latest pre-kickoff observation, overwritten by every
    #       snapshot run until kickoff (scrapers/closing_snapshot.py).
    for col in ("book_source TEXT", "book_observed_at TEXT",
                "close_odds REAL", "close_prob REAL", "close_source TEXT",
                "close_at TEXT", "close_mins_before INTEGER"):
        try:
            cursor.execute(f"ALTER TABLE model_predictions ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # already exists

    # SHADOW predictions for the non-soccer sports (models/elo_shadow.py).
    # Created here as well as there because the NHL preview endpoint reads it,
    # and schema belongs to the schema file (bug #21).
    #   markets_json  every market priced for the game (models/nhl_markets.py):
    #                 our probability beside the market's, so each market can be
    #                 graded against the bookmaker later (db/shadow_report.py).
    #   facts_json    the evidence the analysis is written from, stored so the
    #                 prose can be checked against exactly what it was given.
    #   analysis      LLM prose (analysis/nhl_analyst.py). Explanation only.
    cursor.execute("""
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
    for col in ("markets_json TEXT", "facts_json TEXT", "analysis TEXT",
                "analysis_at TEXT"):
        try:
            cursor.execute(f"ALTER TABLE shadow_predictions ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # already exists

    conn.commit()
    conn.close()
    logger.success("✅ Database initialized")


def insert_match(match_data: dict) -> bool:
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO matches 
            (match_id, sport, league, home_team, away_team, match_date, kickoff_time)
            VALUES (:match_id, :sport, :league, :home_team, :away_team, :match_date, :kickoff_time)
        """, match_data)
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error inserting match: {e}")
        return False
    finally:
        conn.close()


def insert_scraped_prediction(prediction_data: dict) -> bool:
    conn = get_connection()
    try:
        data = dict(prediction_data)
        for col in ("odds_home", "odds_draw", "odds_away"):
            data.setdefault(col, None)
        conn.execute("""
            INSERT INTO scraped_predictions
            (match_id, source, home_win_pct, draw_pct, away_win_pct,
             predicted_home_goals, predicted_away_goals, over_2_5_pct,
             btts_pct, home_form, away_form, odds_home, odds_draw, odds_away)
            VALUES (:match_id, :source, :home_win_pct, :draw_pct, :away_win_pct,
                    :predicted_home_goals, :predicted_away_goals, :over_2_5_pct,
                    :btts_pct, :home_form, :away_form,
                    :odds_home, :odds_draw, :odds_away)
        """, data)
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error inserting prediction: {e}")
        return False
    finally:
        conn.close()


def get_upcoming_matches(sport: str = None, date: str = None):
    conn = get_connection()
    query = "SELECT * FROM matches WHERE status = 'upcoming'"
    params = []
    if sport:
        query += " AND sport = ?"
        params.append(sport)
    if date:
        query += " AND match_date = ?"
        params.append(date)
    query += " ORDER BY match_date, kickoff_time"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_match_with_predictions(match_id: str):
    conn = get_connection()
    match = conn.execute(
        "SELECT * FROM matches WHERE match_id = ?", [match_id]
    ).fetchone()
    predictions = conn.execute(
        "SELECT * FROM scraped_predictions WHERE match_id = ?", [match_id]
    ).fetchall()
    conn.close()
    return {
        "match": dict(match) if match else None,
        "predictions": [dict(p) for p in predictions]
    }


if __name__ == "__main__":
    init_db()
    print(f"Database ready at: {DB_PATH}")
