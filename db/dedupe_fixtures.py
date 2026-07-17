"""
dedupe_fixtures.py - Merge duplicate fixtures created by multiple scrapers.

Matches fuzzily: same teams + dates within 1 day -> same fixture.
Keeps earliest-created row as canonical, re-points child rows, deletes dups,
then collapses now-identical child rows.

Handles the results UNIQUE(match_id) constraint: if the canonical already has
a result, the duplicate's result is deleted rather than re-pointed (they're
the same match, so the score is identical anyway).

Run:
    python db/dedupe_fixtures.py            (dry run)
    python db/dedupe_fixtures.py --apply    (do it - back up the .db first)
"""

import sys
import os
import sqlite3
from datetime import datetime
from difflib import SequenceMatcher

DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")


def sim(a, b):
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def dates_close(d1, d2, days=1):
    try:
        a = datetime.strptime(d1, "%Y-%m-%d")
        b = datetime.strptime(d2, "%Y-%m-%d")
        return abs((a - b).days) <= days
    except (ValueError, TypeError):
        return d1 == d2


def same_fixture(m1, m2):
    return (dates_close(m1["match_date"], m2["match_date"]) and
            sim(m1["home_team"], m2["home_team"]) > 0.8 and
            sim(m1["away_team"], m2["away_team"]) > 0.8)


def _repoint(cur, table, canonical, dup):
    """Move child rows from dup to canonical."""
    cur.execute(f"UPDATE {table} SET match_id=? WHERE match_id=?", (canonical, dup))


def _repoint_result_safe(cur, canonical, dup):
    """results has UNIQUE(match_id). If canonical already has a result,
    just delete the duplicate's result (same match = same score)."""
    has_canonical = cur.execute(
        "SELECT 1 FROM results WHERE match_id=?", (canonical,)).fetchone()
    if has_canonical:
        cur.execute("DELETE FROM results WHERE match_id=?", (dup,))
    else:
        cur.execute("UPDATE results SET match_id=? WHERE match_id=?", (canonical, dup))


def run(apply=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    matches = [dict(r) for r in cur.execute("""
        SELECT match_id, home_team, away_team, match_date, created_at
        FROM matches ORDER BY created_at ASC, id ASC
    """).fetchall()]

    clusters = []
    used = set()
    for i, m in enumerate(matches):
        if m["match_id"] in used:
            continue
        cluster = [m]
        used.add(m["match_id"])
        for other in matches[i+1:]:
            if other["match_id"] in used:
                continue
            if same_fixture(m, other):
                cluster.append(other)
                used.add(other["match_id"])
        if len(cluster) > 1:
            clusters.append(cluster)

    print(f"Total matches: {len(matches)}")
    print(f"Duplicate clusters found: {len(clusters)}\n")

    total_merged = 0
    for cluster in clusters:
        canonical = cluster[0]["match_id"]
        dups = [c["match_id"] for c in cluster[1:]]
        print(f"  {cluster[0]['home_team']} vs {cluster[0]['away_team']}  "
              f"(keep {canonical}, merge {len(dups)})")
        total_merged += len(dups)

        if apply:
            for dup in dups:
                _repoint(cur, "scraped_predictions", canonical, dup)
                _repoint(cur, "model_predictions", canonical, dup)
                _repoint_result_safe(cur, canonical, dup)
                cur.execute("DELETE FROM matches WHERE match_id=?", (dup,))

    if apply:
        # Collapse now-identical child rows
        cur.execute("""DELETE FROM model_predictions WHERE id NOT IN (
            SELECT MIN(id) FROM model_predictions GROUP BY match_id, best_bet, confidence)""")
        cur.execute("""DELETE FROM scraped_predictions WHERE id NOT IN (
            SELECT MIN(id) FROM scraped_predictions GROUP BY match_id, source)""")
        conn.commit()
        # no emoji - Windows cp1252 console crashes on it AFTER the commit,
        # which makes a successful merge look like a failure
        print(f"\nApplied. Merged {total_merged} duplicate rows; collapsed child tables.")
    else:
        print(f"\n(DRY RUN) Would merge {total_merged} duplicate rows.")
        print("Re-run with --apply to do it. Back up the .db first!")

    conn.close()


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)