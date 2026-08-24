"""
Database cleanup utility - removes test rows only.

READ THIS BEFORE RUNNING IT. This script used to do two other things, and both
were catastrophic on the current database:

  1. DELETE FROM model_predictions   (unconditionally, every row)
     The comment said "will be regenerated on next predict run". That was true
     in May 2026, when predictions were regenerated locally from scratch each
     day and the DB was scratch space. It is false now: db/sports_predictor.db
     is committed to the repo and IS the published track record. Every graded
     pick behind the numbers on the website, the /stats command and
     feed/bot_feed.json lives in that table. predict.py only ever writes rows
     for TODAY's fixtures, so nothing regenerates the history — running this
     erased the entire prediction side of the record, permanently, and the next
     daily run would have committed the empty table over the good one.

  2. DELETE FROM scraped_predictions WHERE id NOT IN
         (SELECT MAX(id) ... GROUP BY match_id)
     Grouped by match_id ALONE, with no `source`. Every source but the
     highest-id one gets deleted for each fixture, which destroys the
     cross-source evidence the consensus badge and the protective HIGH->MEDIUM
     demote are computed from. db/dedupe_fixtures.py does this correctly, with
     GROUP BY match_id, source.

Both are gone. What remains only touches rows whose match_id starts with
'test_', and even that needs --apply. If you actually want to reset a scratch
copy of the DB, delete the file and let db/database.py:init_db() rebuild it -
don't truncate tables in the committed one.

Usage:
    python db/cleanup.py              (dry run - counts only, changes nothing)
    python db/cleanup.py --apply      (delete the test rows)
"""

import os
import sqlite3
import sys

# Honour DB_PATH like every other script here. This was hardcoded to
# ./db/sports_predictor.db, so pointing DB_PATH at a scratch copy did nothing
# and the "safe" invocation still hit the real, committed ledger.
DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")

TEST_ROW_DELETES = [
    ("matches", "DELETE FROM matches WHERE match_id LIKE 'test_%'",
     "SELECT COUNT(*) FROM matches WHERE match_id LIKE 'test_%'"),
    ("scraped_predictions", "DELETE FROM scraped_predictions WHERE match_id LIKE 'test_%'",
     "SELECT COUNT(*) FROM scraped_predictions WHERE match_id LIKE 'test_%'"),
    ("model_predictions", "DELETE FROM model_predictions WHERE match_id LIKE 'test_%'",
     "SELECT COUNT(*) FROM model_predictions WHERE match_id LIKE 'test_%'"),
]


def run(apply: bool = False) -> None:
    # sqlite3.connect() CREATES a missing file, so a typo'd DB_PATH would
    # otherwise leave a stray empty database behind and report "0 test rows"
    # as though it had checked the real one.
    if not os.path.exists(DB_PATH):
        print(f"No database at {DB_PATH} - nothing to clean. "
              f"(Set DB_PATH if it lives elsewhere.)")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    print(f"DB: {DB_PATH}")
    total = 0
    for table, delete_sql, count_sql in TEST_ROW_DELETES:
        n = cur.execute(count_sql).fetchone()[0]
        total += n
        if apply and n:
            cur.execute(delete_sql)
        print(f"  {table:22s} {n:>6} test row(s)"
              f"{' - deleted' if apply and n else ''}")

    if apply:
        conn.commit()
        print(f"\nApplied. Removed {total} test row(s).")
    else:
        print(f"\n(DRY RUN) Would remove {total} test row(s). "
              f"Re-run with --apply to do it.")

    conn.close()


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
