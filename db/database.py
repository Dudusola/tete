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
    for col in ("live_state TEXT", "live_checked_at TEXT"):
        try:
            cursor.execute(f"ALTER TABLE matches ADD COLUMN {col}")
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
