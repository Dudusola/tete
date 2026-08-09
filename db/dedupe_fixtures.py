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


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Use the SHARED fixture matcher rather than a second, weaker one.
#
# This file used to compare raw SequenceMatcher ratios with a >0.8 threshold on
# each team. That was both too loose and too tight, in the worst way round for
# a script whose entire job is MERGING rows:
#
#   "Manchester United" vs "Manchester City" -> 0.81   ** above the threshold **
#   "Union"             vs "Union Santa Fe"  -> 0.53   (a real duplicate, missed)
#   "Instituto"         vs "Instituto Cordoba" -> 0.69 (a real duplicate, missed)
#
# So it could merge two DIFFERENT Manchester fixtures into one — silently
# destroying a real match and its result — while leaving genuine cross-source
# duplicates unmerged. consensus.team_sim scores those 0.65 / 0.92 / 0.92: it
# normalises names, understands containment on a token boundary, and refuses
# multi-word names whose distinguishing words disagree. It is already trusted
# for consensus and slip building; there is no reason for a second opinion here.
from models.consensus import same_teams, _dates_close


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
            same_teams(m1["home_team"], m1["away_team"],
                       m2["home_team"], m2["away_team"]))


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


def _is_code(name: str) -> bool:
    """Is this one of Forebet's short league codes rather than a real name?

    Forebet labels leagues 'LC', 'Us4', 'Kz2', 'ECL', 'BrC' — fine as an
    internal key, meaningless in a filter dropdown. PredictZ gives the full
    name for the same fixture, so a merged cluster usually holds both.
    """
    n = (name or "").strip()
    return bool(n) and len(n) <= 4 and " " not in n


def _best_league(cluster) -> str | None:
    """Pick the most human-readable league name in a duplicate cluster.

    Preferring the readable name here fixes it once, at merge time, instead of
    maintaining a lookup table of Forebet's codes forever — and it also stops
    the same competition appearing twice in the league filter (once as 'LC',
    once as 'Leagues Cup').
    """
    names = [c["league"] for c in cluster if (c["league"] or "").strip()]
    if not names:
        return None
    readable = [n for n in names if not _is_code(n)]
    if not readable:
        return None                      # every source gave a code: keep as-is
    # Longest readable name — "Europa Conference League" over "Conference".
    return max(readable, key=len)


def run(apply=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    matches = [dict(r) for r in cur.execute("""
        -- `league` is read by _best_league(). It was NOT selected here, so
        -- _best_league raised KeyError on the FIRST cluster and the whole
        -- script exited 1 — on every run since that helper was added. The
        -- pipeline step is required=False, so the run stayed green while no
        -- deduping happened at all (verified across 6 consecutive cloud runs).
        SELECT match_id, home_team, away_team, match_date, league, created_at
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

        # Keep the readable league name from whichever source supplied one.
        better = _best_league(cluster)
        if better and better != cluster[0]["league"]:
            print(f"      league: {cluster[0]['league']!r} -> {better!r}")

        if apply:
            for dup in dups:
                _repoint(cur, "scraped_predictions", canonical, dup)
                _repoint(cur, "model_predictions", canonical, dup)
                _repoint_result_safe(cur, canonical, dup)
                cur.execute("DELETE FROM matches WHERE match_id=?", (dup,))
            if better:
                cur.execute("UPDATE matches SET league=? WHERE match_id=?",
                            (better, canonical))

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