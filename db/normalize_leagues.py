"""
normalize_leagues.py - Collapse league labels that are the SAME competition
spelled two ways, so it stops appearing twice in the Builder's league filter.

Different scrapers label the same competition differently: PredictZ writes
slugs ('serie-a', 'premier-league'), other sources write titles ('Serie A',
'Premier League'). prettyLeague() in the web app already renders both as
"Serie A", so the filter showed two chips with IDENTICAL text and different
counts — there was no way to tell them apart or to know which to pick.

**The safety test is the whole point of this script.** Matching on the
prettified name alone is NOT safe: league names are not unique across
countries. Sampling 60 days of fixtures, 'Super League' looked Uzbek while
'super-league' looked Chinese, and 'Serie A' covered both Ecuador and Brazil.
Merging on name similarity would have fused unrelated competitions into one
filter chip — quietly handing users a slip filter that mixes leagues.

So a group is only merged when the two labels demonstrably describe the same
competition: they must SHARE AT LEAST `MIN_SHARED_TEAMS` teams across the
whole history. Over the full ledger the variants share 5-33 teams each, which
is what proves them equivalent; 'Champions League' vs 'champions-league'
shares 1 (different confederations' competitions) and is left alone.

What this does NOT fix, deliberately: one raw label can still cover several
real competitions ('Serie A' is Brazil AND Ecuador; 'Super League' is China
AND Uzbekistan). That ambiguity is in the source data and needs a country
dimension we do not store — it is not something string matching can repair.

Run:
    python db/normalize_leagues.py            (dry run)
    python db/normalize_leagues.py --apply
"""

import os
import re
import sys
import sqlite3
from collections import defaultdict

DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")

# Two labels must share this many teams before they are called one competition.
# 1 is too weak: a single shared club can be a coincidence of a cup tie or a
# relegated side, which is exactly how Champions League variants would have
# been wrongly fused.
MIN_SHARED_TEAMS = 2


def canonical(name: str) -> str:
    """The display form — mirrors prettyLeague() in webapp/js/api.js."""
    cleaned = re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", (name or "")).strip())
    return cleaned.title()


def _teams(cur, label: str) -> set:
    rows = cur.execute(
        "SELECT home_team, away_team FROM matches WHERE league = ?", (label,)
    ).fetchall()
    out = set()
    for home, away in rows:
        out.add((home or "").lower().strip())
        out.add((away or "").lower().strip())
    out.discard("")
    return out


def _preferred(labels: list) -> str:
    """Which spelling to keep: the human one, not the slug."""
    titled = [l for l in labels if not re.search(r"[-_]", l) and l != l.lower()]
    return max(titled, key=len) if titled else max(labels, key=len)


def run(apply: bool = False) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    groups = defaultdict(list)
    for (label,) in cur.execute(
            "SELECT DISTINCT league FROM matches WHERE league IS NOT NULL"):
        groups[canonical(label)].append(label)

    renamed = 0
    for canon, labels in sorted(groups.items()):
        if len(labels) < 2:
            continue
        sets = [_teams(cur, l) for l in labels]
        shared = set.intersection(*sets) if sets else set()
        if len(shared) < MIN_SHARED_TEAMS:
            print(f"  SKIP  {canon!r}: only {len(shared)} shared team(s) — "
                  f"treating {labels} as different competitions")
            continue
        keep = _preferred(labels)
        for label in labels:
            if label == keep:
                continue
            n = cur.execute(
                "SELECT COUNT(*) FROM matches WHERE league = ?", (label,)
            ).fetchone()[0]
            print(f"  {canon:24} {label!r} -> {keep!r}  ({n} rows, "
                  f"{len(shared)} shared teams)")
            if apply:
                cur.execute("UPDATE matches SET league = ? WHERE league = ?",
                            (keep, label))
            renamed += n

    if apply:
        conn.commit()
        print(f"\nApplied. Relabelled {renamed} rows.")
    else:
        print(f"\n(DRY RUN) Would relabel {renamed} rows. "
              f"Re-run with --apply.")
    conn.close()
    return renamed


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
