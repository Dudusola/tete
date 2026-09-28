"""quarantine_late_predictions.py - keep the track record to predictions made BEFORE kickoff.

CLAUDE.md bug #33. Every stats query in the codebase counts the LATEST
prediction per match (MAX(id), bug #3). predict.py used to predict anything
dated today or later, so a late kickoff (23:00 UTC, dated "tomorrow" by one
source) was predicted again the next morning - after the final whistle, and
often after its own result was in `results`, where it feeds the form
features. That row then became "the" prediction. Measured 28 Sep 2026: 341 of
1,439 graded football picks (83 served), 226 made with the result already
recorded.

This MOVES such rows to `model_predictions_after_kickoff` - never deletes
them, the evidence is kept - so MAX(id) everywhere falls back to the latest
PRE-kickoff prediction. A match predicted only after kickoff leaves the record:
nobody could have bet on that pick.

A row is late if it was predicted at/after the fixture's kickoff_utc, OR after
its result was recorded (covers fixtures with no kickoff time).

Dry run by default; --apply moves rows. Runs daily in the pipeline because
kickoff_times.py can CORRECT a kickoff after the prediction was made.
"""

import argparse
import os
import sqlite3
import sys

DB = os.getenv("DB_PATH", "./db/sports_predictor.db")

LATE = """
    SELECT p.id FROM model_predictions p
    JOIN matches m ON m.match_id = p.match_id
    LEFT JOIN results r ON r.match_id = p.match_id
    WHERE (m.kickoff_utc IS NOT NULL
           AND replace(p.predicted_at, 'T', ' ') >= m.kickoff_utc)
       OR (r.recorded_at IS NOT NULL
           AND replace(p.predicted_at, 'T', ' ') >= r.recorded_at)
"""

SERVED = """
    WITH latest AS (SELECT MAX(id) id FROM model_predictions GROUP BY match_id)
    SELECT COUNT(*),
           SUM(CASE WHEN (p.best_bet = 'Home Win' AND r.outcome = 'H')
                      OR (p.best_bet = 'Away Win' AND r.outcome = 'A')
                      OR (p.best_bet = 'Draw' AND r.outcome = 'D') THEN 1 ELSE 0 END)
    FROM latest l JOIN model_predictions p ON p.id = l.id
    JOIN results r ON r.match_id = p.match_id
    JOIN matches m ON m.match_id = p.match_id
    WHERE m.sport = 'football' AND p.confidence IN ({tiers})
"""


def record(conn, tiers):
    n, won = conn.execute(SERVED.format(tiers=",".join(f"'{t}'" for t in tiers))).fetchone()
    return n or 0, won or 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="move the rows (default: report only)")
    args = ap.parse_args()
    if not os.path.exists(DB):
        # sqlite3.connect would CREATE an empty file and report "0 late rows".
        print(f"ERROR: no database at {DB}")
        return 1

    conn = sqlite3.connect(DB)
    ids = [r[0] for r in conn.execute(LATE)]
    served_before = record(conn, ("HIGH", "MEDIUM"))
    all_before = record(conn, ("HIGH", "MEDIUM", "LOW"))
    print(f"Predictions made at/after kickoff (or after the result): {len(ids)}")
    if not ids:
        print("Nothing to move.")
        return 0

    conn.execute("SAVEPOINT q")
    conn.execute("""CREATE TABLE IF NOT EXISTS model_predictions_after_kickoff
                    AS SELECT * FROM model_predictions WHERE 0""")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(model_predictions_after_kickoff)")]
    if "quarantined_at" not in cols:
        conn.execute("ALTER TABLE model_predictions_after_kickoff ADD COLUMN quarantined_at TEXT")
    src = [r[1] for r in conn.execute("PRAGMA table_info(model_predictions)")]
    missing = [c for c in src if c not in cols]
    for c in missing:            # model_predictions gained a column since the table was made
        conn.execute(f"ALTER TABLE model_predictions_after_kickoff ADD COLUMN {c}")
    q = ",".join("?" * len(ids))
    conn.execute(f"""INSERT INTO model_predictions_after_kickoff ({",".join(src)}, quarantined_at)
                     SELECT {",".join(src)}, datetime('now') FROM model_predictions WHERE id IN ({q})""", ids)
    conn.execute(f"DELETE FROM model_predictions WHERE id IN ({q})", ids)
    served_after = record(conn, ("HIGH", "MEDIUM"))
    all_after = record(conn, ("HIGH", "MEDIUM", "LOW"))

    pct = lambda n, w: f"{w}/{n} = {w / n * 100:.1f}%" if n else "0/0"
    print(f"Served (HIGH+MEDIUM) record: {pct(*served_before)}  ->  {pct(*served_after)}")
    print(f"All tiers record:            {pct(*all_before)}  ->  {pct(*all_after)}")
    if args.apply:
        conn.execute("RELEASE q")
        conn.commit()
        print(f"Moved {len(ids)} rows to model_predictions_after_kickoff.")
    else:
        conn.execute("ROLLBACK TO q")
        conn.execute("RELEASE q")
        print("Dry run - nothing changed. Re-run with --apply.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
