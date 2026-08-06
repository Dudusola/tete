"""
Fill in real UTC kickoff times, so picks for already-started matches can be hidden.

THE PROBLEM: matches.kickoff_time is a bare 'HH:MM' with no timezone, and
PredictZ — the primary fixture source in the cloud — leaves it at '00:00' for
~88% of fixtures. '00:00' means UNKNOWN, not midnight. With no reliable time,
the app kept recommending matches that had already kicked off.

SOURCE: ESPN's public scoreboard API. Chosen over API-Football deliberately —
it needs no key, has no quota, already works from GitHub's IPs, and returns both
an exact UTC kickoff and a state field ('pre' / 'in' / 'post'). API-Football
suspended this project's account mid-session, which is exactly the fragility we
don't want under a correctness fix.

Fixtures we can't match keep kickoff_utc = NULL, and downstream MUST treat NULL
as "unknown", never as "already played" — hiding a match we merely failed to
time would be worse than showing it.

Usage:
    python scrapers/kickoff_times.py            # today + tomorrow
    python scrapers/kickoff_times.py --days 3
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import requests
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from models.consensus import _sim
from scrapers.results_espn import LEAGUES as RESULT_LEAGUES

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"

# results_espn.py's list exists to cover leagues football-data.org MISSES, so it
# deliberately omits the big European leagues and the domestic cups. For kickoff
# times we want the opposite: every competition we actually predict. Omitting
# them is why "time TBC" dominated — EFL Cup, Conference League and the European
# top flights all fell outside the results-oriented list.
EXTRA_LEAGUES = {
    # England
    "eng.1", "eng.2", "eng.3", "eng.4", "eng.league_cup", "eng.fa",
    # Europe's big five + others we hold data for
    "esp.1", "esp.2", "ger.1", "ger.2", "ita.1", "ita.2",
    "fra.1", "fra.2", "ned.1", "por.1", "bel.1", "tur.1", "gre.1",
    # Cups and continental competitions
    "concacaf.leagues.cup", "uefa.super_cup", "concacaf.champions",
    "conmebol.libertadores", "conmebol.sudamericana",
    # Domestic cups for leagues already in the results list
    "esp.copa_del_rey", "ger.dfb_pokal", "ita.coppa_italia", "fra.coupe_de_france",
}

LEAGUES = set(RESULT_LEAGUES) | EXTRA_LEAGUES

# Matching thresholds. A pure min(sim) >= 0.80 rule rejected real matches:
# ESPN writes "Wolverhampton Wanderers" where PredictZ writes "Wolves", and
# "Santos" where we hold "Santos Laguna". Both are unambiguous to a human.
# So: BOTH sides must be plausible, and the PAIR must be strong on average —
# one exact name plus one abbreviation now matches, but two vague names don't.
SIM_FLOOR = 0.55        # neither side may be worse than this
SIM_MEAN = 0.86         # ...and the two together must average this
# 0.86 not 0.82: "Manchester United" vs "Manchester City" scores 0.81 on
# raw string similarity, which is unavoidable. Requiring a high PAIR mean
# means a wrong fixture would need the OTHER team to match strongly too.

# Noise words that carry no identity ("FC Thun" vs "Thun").
# NOTE: "united" and "city" are NOT here. Dropping them made
# "Manchester United" and "Manchester City" both normalise to "manchester"
# and score 1.00 — they carry identity, they are not noise.
_NOISE = {"fc", "cf", "sc", "afc", "ac", "club", "de", "the",
          "sv", "bk", "if", "fk", "cd", "ca", "us", "ss"}


def _norm(name: str) -> str:
    """Lowercase, strip accents and punctuation, drop noise words."""
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    tokens = [t for t in s.split() if t and t not in _NOISE]
    return " ".join(tokens) or s.strip()


def team_sim(a: str, b: str) -> float:
    """Similarity that understands abbreviations.

    Beyond raw string similarity, treat a shared leading stem of >=4 chars as
    strong evidence ("wolves" / "wolverhampton"), and full containment as near
    certain ("santos" inside "santos laguna"). Both are how these feeds
    actually differ; neither matches two genuinely different clubs.
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    best = _sim(na, nb)

    # One name fully contained in the other, on a token boundary.
    ta, tb = set(na.split()), set(nb.split())
    if ta and tb and (ta <= tb or tb <= ta):
        best = max(best, 0.92)

    # Shared leading stem: wolves / wolverhampton. Restricted to cases where
    # one side is a SINGLE word, otherwise shared first words fire wrongly —
    # "Real Madrid" vs "Real Sociedad" and "Inter Turku" vs "Inter Milan" both
    # scored 0.88 on the stem "real" / "inter" alone.
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

# ESPN marks a fixture 'pre' (not started), 'in' (live) or 'post' (finished).
NOT_STARTED = "pre"


def _fetch(args) -> list:
    league, day = args
    try:
        r = requests.get(BASE.format(league=league), params={"dates": day},
                         timeout=20)
        r.raise_for_status()
        events = r.json().get("events") or []
    except (requests.RequestException, ValueError):
        return []

    out = []
    for ev in events:
        iso = ev.get("date") or ""          # e.g. 2026-08-06T22:00Z (UTC)
        comps = (ev.get("competitions") or [{}])[0].get("competitors") or []
        home = away = None
        for c in comps:
            name = (c.get("team") or {}).get("displayName")
            if c.get("homeAway") == "home":
                home = name
            elif c.get("homeAway") == "away":
                away = name
        if not (iso and home and away):
            continue
        try:
            dt = datetime.strptime(iso, "%Y-%m-%dT%H:%MZ")
        except ValueError:
            continue
        state = ((ev.get("status") or {}).get("type") or {}).get("state", "")
        out.append((home, away, dt.strftime("%Y-%m-%d %H:%M"), state))
    return out


def fetch_reference(days: int) -> list:
    """(home, away, kickoff_utc, state) across every ESPN league for N days."""
    jobs = [(lg, (date.today() + timedelta(days=i)).strftime("%Y%m%d"))
            for i in range(days) for lg in LEAGUES]
    # Same 10-thread pattern results_espn.py uses — sequential took ~18 min
    # there and would blow the pipeline's per-step timeout.
    with ThreadPoolExecutor(max_workers=10) as pool:
        per_job = list(pool.map(_fetch, jobs))
    ref = [row for job in per_job for row in job]
    logger.info(f"ESPN reference: {len(ref)} fixtures across {len(LEAGUES)} "
                f"leagues over {days} day(s)")
    return ref


def enrich(days: int = 2) -> int:
    reference = fetch_reference(days)
    if not reference:
        logger.error("No reference kickoffs fetched — nothing to enrich.")
        return 0

    conn = get_connection()
    # Every upcoming match is re-checked, not just untimed ones: live state
    # changes through the day, and a match that was 'pre' an hour ago may be
    # 'in' now. The kickoff itself is only written once (it doesn't move).
    rows = conn.execute("""
        SELECT match_id, home_team, away_team, match_date, kickoff_utc
        FROM matches
        WHERE match_date >= date('now')
    """).fetchall()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    updated = live = 0
    for row in rows:
        best, best_state, best_score = None, None, 0.0
        our_date = datetime.strptime(row["match_date"], "%Y-%m-%d").date()
        for ref_home, ref_away, ref_utc, state in reference:
            ref_date = datetime.strptime(ref_utc[:10], "%Y-%m-%d").date()
            # +/-1 day: sources disagree on date near midnight (bug #2's rule).
            if abs((ref_date - our_date).days) > 1:
                continue
            sh = team_sim(row["home_team"], ref_home)
            sa = team_sim(row["away_team"], ref_away)
            if sh < SIM_FLOOR or sa < SIM_FLOOR:
                continue          # one side is clearly a different club
            score = (sh + sa) / 2
            if score > best_score:
                best, best_state, best_score = ref_utc, state, score

        if not (best and best_score >= SIM_MEAN):
            continue

        if row["kickoff_utc"] is None:
            conn.execute("UPDATE matches SET kickoff_utc = ? WHERE match_id = ?",
                         (best, row["match_id"]))
            updated += 1
        conn.execute(
            "UPDATE matches SET live_state = ?, live_checked_at = ? "
            "WHERE match_id = ?", (best_state, now, row["match_id"]))
        if best_state in ("in", "post"):
            live += 1

    conn.commit()
    remaining = conn.execute("""
        SELECT COUNT(*) FROM matches
        WHERE match_date >= date('now') AND kickoff_utc IS NULL
    """).fetchone()[0]
    conn.close()

    logger.success(f"Kickoff times set for {updated} matches; {live} marked "
                   f"in-play/finished ({remaining} still without a time — these "
                   f"stay visible, because 'unknown' must never be read as "
                   f"'already played')")
    return updated


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    args = ap.parse_args()
    enrich(args.days)
    # Never fatal: NULL kickoffs are handled downstream as "unknown".
    sys.exit(0)
