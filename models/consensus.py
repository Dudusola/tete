"""
consensus.py - Link predictions across sources (Forebet + PredictZ) for the
same fixture, and compute agreement.

Sources name matches slightly differently, use different IDs, and may file the
same fixture under dates a day apart (PredictZ stamps scrape-date, Forebet uses
real match date). So we match on fuzzy team-name within a +/-1 day window.

When both agree on an outcome -> stronger signal.
When they split -> caution flag.

Usage:
    python models/consensus.py
"""

import sys
import os
from datetime import datetime
from difflib import SequenceMatcher
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _dates_close(d1: str, d2: str) -> bool:
    """True if the two dates are within 1 day of each other."""
    try:
        a = datetime.strptime(d1, "%Y-%m-%d")
        b = datetime.strptime(d2, "%Y-%m-%d")
        return abs((a - b).days) <= 1
    except (ValueError, TypeError):
        return d1 == d2


def _same_match(m1: dict, m2: dict) -> bool:
    """Are these two scraped rows the same real fixture?"""
    if not _dates_close(m1["match_date"], m2["match_date"]):
        return False
    return (_sim(m1["home_team"], m2["home_team"]) > 0.8 and
            _sim(m1["away_team"], m2["away_team"]) > 0.8)


def _best_outcome(pred: dict) -> str | None:
    """Return 'H'/'D'/'A' for the highest-probability outcome, or None."""
    h = pred.get("home_win_pct")
    d = pred.get("draw_pct")
    a = pred.get("away_win_pct")
    if h is None or d is None or a is None:
        return None
    return max([("H", h), ("D", d), ("A", a)], key=lambda x: x[1])[0]


def get_latest_predictions_by_source() -> dict:
    """{source: [prediction_rows]} for upcoming matches, latest per match."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT m.home_team, m.away_team, m.match_date, m.league,
               p.source, p.home_win_pct, p.draw_pct, p.away_win_pct,
               p.predicted_home_goals, p.predicted_away_goals, p.match_id
        FROM matches m
        JOIN scraped_predictions p ON p.match_id = m.match_id
        -- latest per (match, source): after dedupe_fixtures merges
        -- cross-source rows onto one match_id, both sources' predictions
        -- live under the same match - taking one row per match would
        -- silently erase the second source and kill consensus.
        -- (GROUP BY subquery, not a correlated one - correlated was ~15s)
        JOIN (SELECT MAX(id) AS latest_id FROM scraped_predictions
              GROUP BY match_id, source) k ON k.latest_id = p.id
        WHERE m.match_date >= date('now')
    """).fetchall()
    conn.close()

    by_source = {}
    for r in rows:
        d = dict(r)
        by_source.setdefault(d["source"], []).append(d)
    return by_source


def build_consensus() -> list:
    """
    Cross-reference sources. Returns list of:
        home_team, away_team, match_date, league,
        sources: {source: outcome}, agree: bool, agreed_outcome: str|None
    """
    by_source = get_latest_predictions_by_source()
    sources = list(by_source.keys())

    if len(sources) < 2:
        logger.warning(f"Only {len(sources)} source(s) present "
                       f"({sources}) - run both scrapers for consensus")

    spine_source = max(by_source, key=lambda s: len(by_source[s])) if by_source else None
    if not spine_source:
        return []

    consensus = []
    for base in by_source[spine_source]:
        entry = {
            "home_team": base["home_team"],
            "away_team": base["away_team"],
            "match_date": base["match_date"],
            "league": base["league"],
            "sources": {},
        }
        for src, preds in by_source.items():
            for p in preds:
                if _same_match(base, p):
                    out = _best_outcome(p)
                    if out:
                        entry["sources"][src] = out
                    break

        outcomes = list(entry["sources"].values())
        if len(outcomes) >= 2 and len(set(outcomes)) == 1:
            entry["agree"] = True
            entry["agreed_outcome"] = outcomes[0]
        else:
            entry["agree"] = False
            entry["agreed_outcome"] = None

        consensus.append(entry)

    return consensus


def run():
    consensus = build_consensus()
    agreed = [c for c in consensus if c["agree"]]
    multi = [c for c in consensus if len(c["sources"]) >= 2]

    print(f"\n{'='*60}")
    print(f"CROSS-SOURCE CONSENSUS")
    print(f"{'='*60}")
    print(f"Total fixtures: {len(consensus)}")
    print(f"Covered by 2+ sources: {len(multi)}")
    print(f"Both sources AGREE: {len(agreed)}")

    names = {"H": "Home Win", "D": "Draw", "A": "Away Win"}

    if agreed:
        print(f"\n--- STRONG PICKS (sources agree) ---")
        for c in agreed:
            srcs = ", ".join(c["sources"].keys())
            print(f"\n{c['home_team']} vs {c['away_team']} [{c['match_date']}]")
            print(f"  Both say: {names[c['agreed_outcome']]}  ({srcs})")

    disagree = [c for c in multi if not c["agree"]]
    if disagree:
        print(f"\n--- SPLIT (sources disagree - caution) ---")
        for c in disagree[:10]:
            detail = ", ".join(f"{s}:{names[o]}" for s, o in c["sources"].items())
            print(f"  {c['home_team']} vs {c['away_team']}: {detail}")


if __name__ == "__main__":
    run()