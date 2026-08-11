"""
gradeability.py - Which leagues can we actually verify, and how much of the
ledger is unverified?

Why this exists
---------------
A tier percentage is a claim about picks we COULD score. It says nothing about
the ones we couldn't — and those are not a random sample. Bug #8 is the whole
argument: results_espn.py was missing the UEFA qualifying slugs, so half the
ledger went ungraded, and when the backfill finally ran the HIGH tier fell from
57% to 38%. The missing matches were disproportionately LOSSES. The headline
number had been flattering itself for weeks, and nothing in the system said so.

So grading COVERAGE is a first-class number, not an operational detail. It
belongs next to the hit rate, because it is the error bar on the hit rate.

What counts as evidence
-----------------------
The tempting fix is a hardcoded list of "leagues ESPN doesn't publish scores
for". That would be wrong today and more wrong every month: a league with ONE
eligible pick and no result is not proof of anything, it is a sample of one.
Measured on 11 Aug 2026, sixteen leagues had never been scored — but they
covered 24 picks between them, and only one had as many as four.

So the bar is evidence, and it updates itself:

    UNTRACKED = at least MIN_EVIDENCE picks old enough to have been graded,
                and not one of them ever was.

A league that starts publishing scores leaves the list on its own; a league
that quietly stops joins it on its own. Nobody has to maintain a list, which
is the point — a hand-maintained list of third-party behaviour is the same
time bomb as the hardcoded page count in bug #10.

ELIGIBLE means match_date at least GRACE_DAYS ago. Results legitimately arrive
late; counting yesterday's fixtures as "failed to grade" would make every run
look broken every day, which is how a real signal gets ignored (bug #14).

What this module does NOT do
----------------------------
It does not stop those picks being served. That decision needs more evidence
than we have, and hiding a pick is not obviously better than showing it with
an honest label — a punter can still bet a match we cannot verify, they just
deserve to know we won't be counting it. This module supplies the FACT;
callers decide what to do with it.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# A league needs this many gradeable-but-ungraded picks before we will say we
# cannot track it. Below this, "never scored" is indistinguishable from
# "barely seen".
MIN_EVIDENCE = 4

# Results arrive late; don't call a match ungraded until it has had time.
GRACE_DAYS = 2

_ELIGIBLE = f"""
    WITH latest AS (SELECT MAX(id) AS id FROM model_predictions GROUP BY match_id)
    SELECT m.league AS league,
           COUNT(*) AS eligible,
           SUM(CASE WHEN r.match_id IS NOT NULL THEN 1 ELSE 0 END) AS graded
    FROM model_predictions mp
    JOIN latest l ON l.id = mp.id
    JOIN matches m ON m.match_id = mp.match_id
    LEFT JOIN results r ON r.match_id = mp.match_id
    WHERE m.match_date <= date('now', '-{GRACE_DAYS} day')
    GROUP BY m.league
"""


def league_coverage(conn) -> list:
    """Per-league [{league, eligible, graded, pct}], worst coverage first."""
    rows = conn.execute(_ELIGIBLE).fetchall()
    out = []
    for r in rows:
        eligible = r["eligible"] or 0
        graded = r["graded"] or 0
        if not eligible:
            continue
        out.append({"league": r["league"] or "?",
                    "eligible": eligible,
                    "graded": graded,
                    "pct": round(graded / eligible * 100, 1)})
    out.sort(key=lambda d: (d["pct"], -d["eligible"]))
    return out


def untracked_leagues(conn) -> list:
    """Leagues with enough evidence to say we cannot verify results there.

    Returned sorted by how much of the ledger each one costs us, because that
    is the order in which they are worth doing something about.
    """
    bad = [c for c in league_coverage(conn)
           if c["graded"] == 0 and c["eligible"] >= MIN_EVIDENCE]
    bad.sort(key=lambda d: -d["eligible"])
    return bad


def overall_coverage(conn) -> dict:
    """How much of the gradeable ledger has actually been graded.

    `pct` is the honest error bar on every accuracy figure the product
    publishes: at 81% coverage, a stated 60% hit rate is 60% OF THE PICKS WE
    COULD CHECK, and the unchecked fifth is not guaranteed to look the same.
    """
    rows = league_coverage(conn)
    eligible = sum(r["eligible"] for r in rows)
    graded = sum(r["graded"] for r in rows)
    untracked = untracked_leagues(conn)
    return {
        "eligible": eligible,
        "graded": graded,
        "pct": round(graded / eligible * 100, 1) if eligible else None,
        "ungraded": eligible - graded,
        # Split out so the shortfall can be attributed: a pick in a league we
        # KNOW we cannot score is a different problem from one we should have
        # scored and missed. The second kind is a bug; the first is a limit.
        "untracked_leagues": [u["league"] for u in untracked],
        "untracked_picks": sum(u["eligible"] for u in untracked),
    }


def _report() -> int:
    from db.database import get_connection
    conn = get_connection()
    cov = overall_coverage(conn)
    rows = league_coverage(conn)
    conn.close()

    print(f"\nGRADING COVERAGE: {cov['graded']}/{cov['eligible']} "
          f"= {cov['pct']}% of picks old enough to score\n")
    print(f"  {'league':30} {'eligible':>9} {'graded':>7} {'rate':>6}")
    for r in rows:
        flag = ""
        if r["graded"] == 0 and r["eligible"] >= MIN_EVIDENCE:
            flag = "  <-- UNTRACKED (cannot verify)"
        elif r["graded"] == 0:
            flag = "  (too few to judge)"
        print(f"  {r['league'][:30]:30} {r['eligible']:9} {r['graded']:7} "
              f"{r['pct']:5.0f}%{flag}")

    if cov["untracked_leagues"]:
        print(f"\n  {len(cov['untracked_leagues'])} league(s) we cannot verify, "
              f"costing {cov['untracked_picks']} picks:")
        for name in cov["untracked_leagues"]:
            print(f"    - {name}")
    else:
        print("\n  No league has enough evidence to be called untrackable yet.")
    print(f"\n  Read every accuracy figure as '{cov['pct']}% of the ledger "
          f"was actually checked'.\n")
    return 0


if __name__ == "__main__":
    sys.exit(_report())
