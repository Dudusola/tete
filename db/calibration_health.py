"""calibration_health.py - is the model's stated probability the truth?

WHY THIS FILE EXISTS
--------------------
A tier percentage answers "how often does HIGH win?". Calibration answers a
harder and more useful question: when the model says 62%, does it win 62% of
the time? A model can have a correctly ORDERED set of tiers and still be
systematically wrong about the numbers it prints next to them, and those
numbers are what a punter acts on.

docs/history.md item 4 has been through this analysis twice, by hand, and the
conclusion REVERSED both times:

    9 Aug 2026, 184 graded : HIGH 68.0% > MEDIUM 59.1% > LOW 56.5%
                             "the inversion resolved itself"
    11 Aug 2026, 265 graded: HIGH 76.9% > MEDIUM 61.5% > LOW 52.8%
                             "DO NOT raise the HIGH threshold to 70%" -- the
                             standing recommendation from the previous pass was
                             now unsupported, because the 65-70%% band had gone
                             57.1%% (n=7) -> 75.0%% (n=12) as the sample grew.

Both passes were recomputed from scratch, by hand, with the numbers pasted into
docs/history.md. That is how a finding gets stale without anyone noticing it has, and
it is why `form_depth_health.py` exists for bug #27. This is the same idea for
the calibration question: run it, do not re-derive it.

WHAT IT REPORTS
---------------
1. TIERS - the headline record per confidence tier, with Wilson intervals.
2. BANDS - the real calibration test. Picks bucketed by the probability the
   model CLAIMED, actual hit rate against that claim, and whether the 95%
   interval contains the claim. A band whose interval EXCLUDES its own claim is
   the only kind of miscalibration worth acting on; everything else is noise
   wearing a suggestive shape.
3. SIGN TEST - the pattern no single band can show. If nearly every band lands
   on the same side of its claim, the model is systematically over- or
   under-confident even when each band alone is within noise. This is what
   found the under-confidence lead on 11 Aug.
4. ERAS - per `model_version`, so a retrain can be judged against its
   predecessor rather than against a vibe.

THE TRAP THIS FILE IS BUILT AROUND
----------------------------------
Every "actual" here is computed over GRADED picks only, and bug #8 established
that ungraded picks are NOT a random sample -- when that backfill landed, HIGH
fell 57%% -> 38%% because what was missing was mostly losses. So coverage is
printed at the top, next to the numbers it qualifies, and a low figure means
every conclusion below it is provisional. Apparent under-confidence and a
coverage shortfall look IDENTICAL from inside this report. Distinguish them
before touching a threshold.

    python db/calibration_health.py            # served picks (HIGH+MEDIUM)
    python db/calibration_health.py --all      # every tier, including LOW
    python db/calibration_health.py --era rf_v2
"""

import os
import sys
import math
import sqlite3
import argparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.sports import FOOTBALL, sport_sql

DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")
BEST_BET_TO_OUTCOME = {"Home Win": "H", "Away Win": "A", "Draw": "D"}

# Band edges for the calibration table. Deliberately 5-point buckets: wide
# enough that a band reaches a usable n within months, narrow enough that the
# claimed probability inside one is roughly constant.
BANDS = [(0.35, 0.40), (0.40, 0.45), (0.45, 0.50), (0.50, 0.55),
         (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.01)]

# A band below this is reported but never interpreted. 11 Aug's reversal came
# from reading a 7-sample band as a finding.
MIN_BAND_N = 15


def wilson(wins, n, z=1.96):
    """Wilson score 95% interval. Honest error bars on small samples."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def sign_test_p(above: int, total: int) -> float:
    """Two-sided exact binomial p for `above` of `total` landing one way.

    No scipy in the pipeline image, and this is one line of combinatorics.
    """
    if total == 0:
        return 1.0
    def tail(k):
        return sum(math.comb(total, i) for i in range(k, total + 1)) / 2 ** total
    lo = min(above, total - above)
    return min(1.0, 2 * tail(max(above, total - above)) if lo != above else
               2 * tail(total - lo))


def load_picks(conn, tiers=None, era=None) -> list:
    """Latest graded 1X2 prediction per match, with claim and outcome.

    MAX(id) per match is not optional -- model_predictions is append-only and a
    re-predicted match carries several rows (bug #3, bug #20). Football only:
    a blended calibration curve would be meaningless (see models/sports.py).
    """
    where = []
    params = []
    if tiers:
        where.append(f"AND mp.confidence IN ({','.join('?' * len(tiers))})")
        params.extend(tiers)
    if era:
        where.append("AND mp.model_version = ?")
        params.append(era)
    rows = conn.execute(f"""
        SELECT mp.confidence, mp.best_bet, mp.best_bet_prob,
               mp.model_version, r.outcome
        FROM model_predictions mp
        JOIN results r ON r.match_id = mp.match_id
        JOIN matches m ON m.match_id = mp.match_id
        WHERE mp.id = (SELECT MAX(mp2.id) FROM model_predictions mp2
                       WHERE mp2.match_id = mp.match_id)
          {sport_sql(FOOTBALL)}
          AND mp.best_bet_prob IS NOT NULL
          {' '.join(where)}
    """, params).fetchall()
    out = []
    for r in rows:
        pick = BEST_BET_TO_OUTCOME.get(r["best_bet"])
        if not pick:
            continue
        out.append({"confidence": r["confidence"],
                    "prob": float(r["best_bet_prob"]),
                    "era": r["model_version"],
                    "won": 1 if pick == r["outcome"] else 0})
    return out


def tier_calibration(conn, tiers=("HIGH", "MEDIUM")) -> list:
    """Per-tier calibration as data, for the Record page (/api/stats).

    Same picks and same Wilson interval as the printed report, so the page and
    `python db/calibration_health.py` cannot disagree. `claim` is the mean
    probability the model stated for the tier's graded picks; `verdict` uses
    the report's wording rules, including "too few" below MIN_BAND_N.
    """
    picks = load_picks(conn, list(tiers))
    out = []
    for name in tiers:
        got = [p for p in picks if p["confidence"] == name]
        if not got:
            continue
        wins, n = sum(g["won"] for g in got), len(got)
        claim = sum(g["prob"] for g in got) / n
        p, lo, hi = wilson(wins, n)
        verdict = ("too_few" if n < MIN_BAND_N else
                   "under_confident" if claim < lo else
                   "over_confident" if claim > hi else "within_noise")
        out.append({"tier": name, "n": n, "won": wins,
                    "actual": round(p * 100, 1), "claim": round(claim * 100, 1),
                    "lo": round(lo * 100, 1), "hi": round(hi * 100, 1),
                    "verdict": verdict})
    return out


def _row(label, wins, n, claim=None, note_width=0):
    p, lo, hi = wilson(wins, n)
    claim_s = f"{claim*100:5.1f}%" if claim is not None else "    - "
    verdict = ""
    if claim is not None and n >= MIN_BAND_N:
        if claim < lo:
            verdict = "  UNDER-confident (CI excludes claim)"
        elif claim > hi:
            verdict = "  OVER-confident (CI excludes claim)"
        else:
            verdict = "  within noise"
    elif claim is not None:
        verdict = f"  n<{MIN_BAND_N}, not interpretable"
    print(f"  {label:12} {wins:4}/{n:<5} {p*100:5.1f}%  {claim_s}   "
          f"[{lo*100:4.1f}, {hi*100:4.1f}]{verdict}")


def report(conn, tiers, era) -> int:
    picks = load_picks(conn, tiers, era)
    if not picks:
        print("No graded picks match that filter.")
        return 1

    from models.gradeability import overall_coverage
    cov = overall_coverage(conn)

    scope = "ALL TIERS" if tiers is None else "+".join(tiers)
    print("\n" + "=" * 72)
    print(f"  CALIBRATION HEALTH - {scope}" + (f", era {era}" if era else ""))
    print("=" * 72)
    print(f"\n  Graded picks: {len(picks)}")
    print(f"  Grading coverage: {cov['pct']}%  "
          f"({cov['graded']}/{cov['eligible']})")
    if cov["pct"] is not None and cov["pct"] < 90:
        print("  !! Coverage is low. Bug #8: ungraded picks are NOT a random")
        print("     sample -- they skewed to LOSSES last time. Under-confidence")
        print("     below and a coverage shortfall look identical from here.")

    print("\n" + "-" * 72)
    print("  TIERS (is the ordering right?)")
    print(f"  {'tier':12} {'record':>10} {'actual':>7} {'claim':>7}   95% CI")
    by_tier = {}
    for p in picks:
        by_tier.setdefault(p["confidence"], []).append(p)
    for name in ("HIGH", "MEDIUM", "LOW"):
        got = by_tier.get(name)
        if not got:
            continue
        claim = sum(g["prob"] for g in got) / len(got)
        _row(name, sum(g["won"] for g in got), len(got), claim)

    print("\n" + "-" * 72)
    print("  BANDS (does a stated probability mean what it says?)")
    print(f"  {'band':12} {'record':>10} {'actual':>7} {'claim':>7}   95% CI")
    above = total_bands = 0
    for lo_b, hi_b in BANDS:
        got = [p for p in picks if lo_b <= p["prob"] < hi_b]
        if not got:
            continue
        wins = sum(g["won"] for g in got)
        claim = sum(g["prob"] for g in got) / len(got)
        label = f"{lo_b*100:.0f}-{hi_b*100:.0f}%" if hi_b <= 1 else f">={lo_b*100:.0f}%"
        _row(label, wins, len(got), claim)
        if len(got) >= MIN_BAND_N:
            total_bands += 1
            if wins / len(got) > claim:
                above += 1

    if total_bands >= 4:
        p_sign = sign_test_p(above, total_bands)
        print(f"\n  SIGN TEST: {above}/{total_bands} interpretable bands came in "
              f"ABOVE their claim (p = {p_sign:.3f}).")
        if p_sign < 0.05 and above > total_bands / 2:
            print("  -> Systematic UNDER-confidence. Individually each band may")
            print("     be noise; one-directional across bands is not. But read")
            print("     the coverage caveat above before acting: a loss-skewed")
            print("     ungraded set produces this exact pattern.")
        elif p_sign < 0.05:
            print("  -> Systematic OVER-confidence. This is the dangerous")
            print("     direction: we are publishing numbers we do not hit.")
        else:
            print("  -> No directional bias. Bands scatter either side of their")
            print("     claim, which is what a calibrated model looks like.")

    print("\n" + "-" * 72)
    print("  ERAS (did the last retrain help?)")
    print(f"  {'version':12} {'record':>10} {'actual':>7} {'claim':>7}   95% CI")
    by_era = {}
    for p in picks:
        by_era.setdefault(p["era"] or "?", []).append(p)
    for name in sorted(by_era):
        got = by_era[name]
        claim = sum(g["prob"] for g in got) / len(got)
        _row(name, sum(g["won"] for g in got), len(got), claim)
    print("\n  Eras are not comparable on equal footing: each ran over a")
    print("  different slate, and an era with few picks is a different")
    print("  question from one that ran for weeks. Compare CIs, not centres.\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true",
                    help="include LOW (default: served tiers only)")
    ap.add_argument("--era", help="restrict to one model_version")
    args = ap.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"No database at {DB_PATH} -- set DB_PATH.")
        return 1
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return report(conn, None if args.all else ["HIGH", "MEDIUM"], args.era)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
