"""
purge_results.py - Wipe the contaminated results table and restart the ledger.

The old results.py recorded Forebet's PREDICTED scores as final results,
so every row in `results` is untrustworthy. This deletes them all and
resets match statuses, so the fixed results.py can rebuild the track
record from real final scores only.

Run:
    python db/purge_results.py           (dry run - shows counts)
    python db/purge_results.py --apply   (actually wipes)
"""

import sys, os, sqlite3

DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")

def run(apply=False):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    n_results = cur.execute("SELECT COUNT(*) FROM results").fetchone()[0]
    n_finished = cur.execute(
        "SELECT COUNT(*) FROM matches WHERE status='finished'").fetchone()[0]

    print(f"results rows to delete: {n_results}")
    print(f"matches marked finished to reset: {n_finished}")

    if apply:
        cur.execute("DELETE FROM results")
        cur.execute("UPDATE matches SET status='scheduled' WHERE status='finished'")
        conn.commit()
        print("\n[OK] Purged. The ledger restarts clean - run the fixed results.py "
              "after today's matches finish.")
    else:
        print("\n(DRY RUN) Re-run with --apply to wipe. This is the honest reset.")
    conn.close()

if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
