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
from models.sports import FOOTBALL, sport_sql


def same_fixture(m1, m2):
    return (_dates_close(m1["match_date"], m2["match_date"]) and
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

    matches = [dict(r) for r in cur.execute(f"""
        -- `league` is read by _best_league(). It was NOT selected here, so
        -- _best_league raised KeyError on the FIRST cluster and the whole
        -- script exited 1 — on every run since that helper was added. The
        -- pipeline step is required=False, so the run stayed green while no
        -- deduping happened at all (verified across 6 consecutive cloud runs).
        --
        -- Football only. A merge here DELETES a row, and NBA/NFL fixtures carry
        -- ESPN's exact event id (scrapers/espn_ledger.py) - they cannot have a
        -- cross-source duplicate, only a false one.
        SELECT m.match_id, m.home_team, m.away_team, m.match_date, m.league,
               m.created_at
        FROM matches m WHERE 1=1 {sport_sql(FOOTBALL)}
        ORDER BY m.created_at ASC, m.id ASC
    """).fetchall()]

    # Compare only fixtures whose dates are within a day of each other, by
    # bucketing on the date first.
    #
    # This is not an optimisation for its own sake, it is a deadline. The old
    # loop compared EVERY pair in the table -- and the table is the whole
    # history, not the upcoming fixtures -- so the cost grows with the square
    # of a number that only ever increases. Measured in the cloud on 23 Sept
    # 2026 at 6,066 rows: 4 minutes 46 seconds, against run_daily's 15-minute
    # step timeout. A third of the budget spent, and quadratic: ~9,000 rows is
    # roughly 10 minutes and ~12,000 blows the timeout outright. That failure
    # would arrive on an ordinary day months from now, with no change to blame,
    # and it would stop the deduper -- which is the only thing standing between
    # cross-source duplicates and a double-counted track record (bug #2).
    #
    # Exactly equivalent, not merely close: same_fixture() requires
    # _dates_close(), which is |d1 - d2| <= 1 day, so a pair in non-adjacent
    # buckets could never have matched. Rows whose date will not parse keep
    # _dates_close's own fallback (d1 == d2) by bucketing on the raw string.
    # Candidates are still visited in ascending original order, so the clusters
    # built are identical, member for member.
    def _bucket(raw):
        try:
            return ("d", datetime.strptime(raw, "%Y-%m-%d").toordinal())
        except (ValueError, TypeError):
            return ("raw", raw)

    by_bucket = {}
    for i, m in enumerate(matches):
        by_bucket.setdefault(_bucket(m["match_date"]), []).append(i)

    def _candidates(i, m):
        kind, key = _bucket(m["match_date"])
        if kind == "raw":
            positions = by_bucket.get((kind, key), [])
        else:
            positions = []
            for offset in (-1, 0, 1):
                positions.extend(by_bucket.get(("d", key + offset), []))
            positions.sort()
        return (matches[p] for p in positions if p > i)

    clusters = []
    used = set()
    for i, m in enumerate(matches):
        if m["match_id"] in used:
            continue
        cluster = [m]
        used.add(m["match_id"])
        for other in _candidates(i, m):
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
        # Collapse now-identical child rows.
        #
        # KEEP MAX(id), NOT MIN(id). These rows are not interchangeable even
        # when the grouped columns match: the newer row is the one carrying the
        # payload columns added later in the pipeline. model_predictions rows
        # gain book_odds and draw_analysis after they are first inserted, and
        # scraped_predictions rows gain odds as a source's odds scrape lands. A
        # row grouped as "identical" on (match_id, best_bet, confidence) can
        # still be the only row holding today's price.
        #
        # This ran with MIN(id) daily (run_daily.py, before predict and
        # market_snapshot). The damage was not yet visible when this was
        # changed — only 20 rows had book_odds at all and 19 of them were
        # already MAX(id) — but that is a property of the feature being new,
        # not of the query being safe. Every serving and stats query in the
        # codebase reads MAX(id) per group (CLAUDE.md bug #3); a cleanup step
        # that keeps MIN is deleting precisely the row the rest of the system
        # is about to read.
        cur.execute("""DELETE FROM model_predictions WHERE id NOT IN (
            SELECT MAX(id) FROM model_predictions GROUP BY match_id, best_bet, confidence)""")
        cur.execute("""DELETE FROM scraped_predictions WHERE id NOT IN (
            SELECT MAX(id) FROM scraped_predictions GROUP BY match_id, source)""")
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