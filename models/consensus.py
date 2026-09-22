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
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from models.sports import FOOTBALL, sport_sql


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# ─── Team-name matching across sources ──────────────────────────────────────
# Raw _sim is too blunt for cross-source names. Measured failures:
#   "Club Puebla"              vs "Puebla"        -> 0.71  (same club)
#   "Club Tijuana de Caliente" vs "Club Tijuana"  -> 0.67  (same club)
# At a 0.75 threshold both were rejected, which is why the slip checker
# reported "0 of your games covered" on fixtures we had actually predicted.
#
# Lives here so every caller shares ONE implementation — this logic was
# already duplicated once (scrapers/kickoff_times.py) and a second copy would
# drift.

# Words that carry no identity. NOT "united"/"city": dropping those made
# "Manchester United" and "Manchester City" both normalise to "manchester"
# and score a perfect 1.00.
_NOISE_WORDS = {"fc", "cf", "sc", "afc", "ac", "club", "de", "the",
                "sv", "bk", "if", "fk", "cd", "ca", "us", "ss",
                # Honorifics one feed spells out and another abbreviates:
                # "Union St.-Gilloise" vs "Union Saint Gilloise". Punctuation is
                # already stripped in _norm_team, so "st" and "saint" both land
                # here and the two names normalise identically. models/
                # team_match.py has carried these since it was written; this set
                # did not, which is why GRADING (scrapers/results_api.py, via
                # team_sim) could fail to score a fixture that kickoff_times.py
                # had already matched.
                "st", "saint", "sainte", "santo"}

# Squad qualifiers: tokens that make an otherwise-identical name a DIFFERENT
# team. These are not noise and must never be stripped — they are the only thing
# distinguishing the senior side from the academy or the women's side.
#
# Every containment rule below says yes to these pairs, because the shorter name
# sits inside the longer one and the first word agrees:
#
#   "Barcelona"  vs "Barcelona U20"      -> 0.92
#   "Bayern"     vs "Bayern II"          -> 0.92
#   "Chelsea"    vs "Chelsea Women"      -> 0.92
#
# SportyBet's events feed lists senior, youth and women's fixtures side by side,
# so a bet built on that 0.92 lands on the wrong match with the user's money on
# it. Compared as SETS and required to be equal, so women-vs-women and
# U20-vs-U20 still match each other — only mismatches are rejected.
#
# "jr" is deliberately absent: too short to be safely distinguished from an
# initial. "junior" is kept, and clubs actually named Junior (Junior
# Barranquilla) are unaffected because both sides carry the token equally.
_SQUAD_MARKERS = re.compile(
    r"\b(u\s?\d{2}|under\s?\d{2}|youth|junior|academy"
    r"|women|womens|ladies|fem(?:inino|enino|inina|enina)?"
    r"|reserves?|ii|iii|b)\b"
)


def _squad_qualifiers(name: str) -> frozenset:
    """The squad markers present in a name, as a set for exact comparison."""
    s = (name or "").lower()
    return frozenset(m.group(1).replace(" ", "") for m in _SQUAD_MARKERS.finditer(s))


def _norm_team(name: str) -> str:
    """Lowercase, strip accents and punctuation, drop noise words."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    tokens = [t for t in s.split() if t and t not in _NOISE_WORDS]
    return " ".join(tokens) or s.strip()


def team_sim(a: str, b: str) -> float:
    """Similarity that understands how bookmakers and scrapers differ.

    Adds two signals to raw string similarity:
      - containment on a token boundary ("Puebla" inside "Club Puebla")
      - a shared leading stem when one side is a single word
        ("Wolves" / "Wolverhampton")

    The stem rule is restricted to single-word names on purpose: applied
    generally it matched "Real Madrid" to "Real Sociedad" and "Inter Turku"
    to "Inter Milan" on the shared first word.

    Returns a hard 0.0 when the two names carry different squad qualifiers
    (U20 / Women / II / reserves), because those are different teams however
    similar the strings are. See _SQUAD_MARKERS.
    """
    # Before any similarity work: senior vs academy vs women's are not the same
    # club, and every rule below would happily score them 0.92.
    if _squad_qualifiers(a) != _squad_qualifiers(b):
        return 0.0

    na, nb = _norm_team(a), _norm_team(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    best = _sim(na, nb)

    ta, tb = set(na.split()), set(nb.split())
    # Containment, but only when the FIRST word agrees. Without that check
    # "AC Milan" (noise word stripped -> "milan") is contained in "Inter Milan"
    # and the two score 0.92 — different clubs entirely. "Santos" / "Santos
    # Laguna" and "Club Tijuana" / "Club Tijuana de Caliente" both keep their
    # leading word, which is what makes them safe.
    if ta and tb and (ta <= tb or tb <= ta) and na.split()[0] == nb.split()[0]:
        best = max(best, 0.92)

    if len(ta) == 1 or len(tb) == 1:
        fa, fb = na.split()[0], nb.split()[0]
        stem = 0
        for ca, cb in zip(fa, fb):
            if ca != cb:
                break
            stem += 1
        if stem >= 5 and stem >= min(len(fa), len(fb)) * 0.6:
            best = max(best, 0.88)
        return best

    # Multi-word names that SHARE a word but differ on the rest are the
    # dangerous case: "Manchester United" vs "Manchester City" scores 0.81 on
    # raw similarity purely from the shared "manchester", and paired with an
    # exact away team that was enough to match the WRONG fixture. Attaching
    # another match's prediction to a user's leg is worse than admitting we
    # have no view, so the distinguishing words must agree too.
    only_a, only_b = ta - tb, tb - ta
    if only_a and only_b:
        best_distinct = max(_sim(x, y) for x in only_a for y in only_b)
        if best_distinct < 0.6:
            best = min(best, 0.65)

    return best


def same_teams(home_a: str, away_a: str, home_b: str, away_b: str) -> bool:
    """Do these two (home, away) pairs describe the same fixture?

    Both sides must be plausible AND the pair must be strong on average, so one
    exact name plus one abbreviation matches while two vague names don't.
    0.86 rather than 0.82 because "Manchester United" vs "Manchester City"
    scores 0.81 on raw similarity alone — a wrong fixture would need the OTHER
    team to match strongly too.
    """
    sh = team_sim(home_a, home_b)
    sa = team_sim(away_a, away_b)
    if sh < 0.55 or sa < 0.55:
        return False
    return (sh + sa) / 2 >= 0.86


def _dates_close(d1: str, d2: str) -> bool:
    """True if the two dates are within 1 day of each other."""
    try:
        a = datetime.strptime(d1, "%Y-%m-%d")
        b = datetime.strptime(d2, "%Y-%m-%d")
        return abs((a - b).days) <= 1
    except (ValueError, TypeError):
        return d1 == d2


def _same_match(m1: dict, m2: dict) -> bool:
    """Are these two scraped rows the same real fixture?

    Uses same_teams, not raw _sim. This function had its own inline test —
    `_sim(home) > 0.8 and _sim(away) > 0.8` on UNNORMALISED names — which is
    the blunt comparison the whole block above exists to replace. It misses
    every difference these sources actually have:

        "Cardiff"     vs "Cardiff City"     -> 0.65   (same club, rejected)
        "Club Puebla" vs "Puebla"           -> 0.71   (same club, rejected)

    Two callers depend on this: the consensus badge on the web app, and
    apply_consensus_modifier's protective HIGH -> MEDIUM demote in predict.py.
    A missed link is not neutral in either — the badge under-reports agreement,
    and the demote silently fails to fire, leaving a pick in HIGH that two
    sources contradict. same_teams applies the pair floor and mean, so it is
    both looser on abbreviations and stricter on the dangerous cases.
    """
    if not _dates_close(m1["match_date"], m2["match_date"]):
        return False
    return same_teams(m1["home_team"], m1["away_team"],
                      m2["home_team"], m2["away_team"])


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
    rows = conn.execute(f"""
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
        -- ...and only among rows that actually CARRY an opinion. PredictZ
        -- writes a row per fixture whether or not it found odds, leaving
        -- home/draw/away pct NULL when it didn't (190 of 228 upcoming rows on
        -- 9 Aug 2026). A NULL-pct row is not a newer verdict, it is the
        -- absence of one -- but MAX(id) happily picked it, so _best_outcome
        -- returned None and PredictZ vanished from every entry. Result: 19
        -- upcoming matches each covered by TWO sources reported
        -- covered_by_multiple = 0, and the page looked like a data drought
        -- rather than a bug. Filter first, then take the latest.
        JOIN (SELECT MAX(id) AS latest_id FROM scraped_predictions
              WHERE home_win_pct IS NOT NULL
                AND draw_pct IS NOT NULL
                AND away_win_pct IS NOT NULL
              GROUP BY match_id, source) k ON k.latest_id = p.id
        -- Football only: consensus compares SOURCES on one fixture, and the
        -- fuzzy team matcher below is name-based with no sport attached, so a
        -- basketball fixture could be merged with a football one on a shared
        -- club name -- bug #2's duplicate-fixture shape, across sports.
        WHERE m.match_date >= date('now')
          {sport_sql(FOOTBALL)}
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