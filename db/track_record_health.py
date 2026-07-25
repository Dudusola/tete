"""Track-record health & calibration — the honest scoreboard.

Read-only. Answers the only questions that matter before inviting testers:
  1. How many picks are ACTUALLY graded (joined to a real result)?
  2. Do the confidence tiers hold their claimed calibration — and is the
     sample big enough to believe it? (Wilson 95% intervals, not bare %.)
  3. Is the ledger accumulating daily, or is coverage bursty/stalled?
  4. Do consensus boosts actually win?

Grades the model's own best_bet (1X2) against results.outcome, using the
LATEST prediction per match (MAX(id)) so repeated pipeline runs don't
double-count — same rule stats queries use elsewhere.

Usage:  python db/track_record_health.py [--version rf_v1d]
Read-only; changes nothing.
"""
import os, sys, sqlite3, math
from collections import defaultdict
from datetime import date

DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")
BEST_BET_TO_OUTCOME = {"Home Win": "H", "Away Win": "A", "Draw": "D"}
# Claimed calibration from CLAUDE.md, for reality-check.
CLAIMED = {"HIGH": 75, "MEDIUM": 66}


def wilson(wins, n, z=1.96):
    """Wilson score 95% interval for a binomial proportion. Honest error bars
    on tiny samples — a bare 57% on 7 bets hides that the truth is 25%-84%."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def bar(lo, hi, width=30):
    lo_i, hi_i = int(round(lo * width)), int(round(hi * width))
    return "".join("=" if lo_i <= i <= hi_i else " " for i in range(width))


def main():
    version = None
    if "--version" in sys.argv:
        version = sys.argv[sys.argv.index("--version") + 1]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    ver_clause = "AND mp.model_version = :ver" if version else ""
    rows = conn.execute(f"""
        WITH latest AS (
            SELECT mp.* FROM model_predictions mp
            JOIN (SELECT match_id, MAX(id) id
                  FROM model_predictions GROUP BY match_id) x ON mp.id = x.id
            WHERE 1=1 {ver_clause}
        )
        SELECT l.confidence, l.best_bet, l.consensus_adj,
               l.model_version, r.outcome
        FROM latest l JOIN results r ON l.match_id = r.match_id
    """, {"ver": version}).fetchall()

    scope = f" (model_version={version})" if version else " (all model versions)"
    print(f"\n{'='*64}\n  TRACK-RECORD HEALTH{scope}\n{'='*64}")

    # ---- Tier calibration --------------------------------------------------
    tier = defaultdict(lambda: [0, 0])
    adj = defaultdict(lambda: [0, 0])
    graded = 0
    for r in rows:
        pick = BEST_BET_TO_OUTCOME.get(r["best_bet"])
        if pick is None:
            continue
        graded += 1
        win = 1 if pick == r["outcome"] else 0
        tier[r["confidence"]][0] += win
        tier[r["confidence"]][1] += 1
        if r["consensus_adj"]:
            adj[r["consensus_adj"]][0] += win
            adj[r["consensus_adj"]][1] += 1

    print(f"\nGraded 1X2 picks: {graded}   "
          f"(a tier needs ~30+ before its % means much)\n")
    print(f"  {'TIER':<8}{'RECORD':<9}{'HIT':<6}{'95% CI':<16}  0%          "
          f"50%         100%")
    for k in ["HIGH", "MEDIUM", "LOW"]:
        w, n = tier[k]
        if n == 0:
            print(f"  {k:<8}{'-':<9}")
            continue
        p, lo, hi = wilson(w, n)
        ci = f"{lo*100:.0f}-{hi*100:.0f}%"
        flag = ""
        if k in CLAIMED:
            # Claimed rate outside the CI => data doesn't support the claim.
            if not (lo <= CLAIMED[k] / 100 <= hi):
                flag = f"  <- claim {CLAIMED[k]}% NOT in CI"
            if n < 30:
                flag += "  (sample too small)" if flag else "  (sample too small)"
        print(f"  {k:<8}{f'{w}/{n}':<9}{p*100:>4.0f}% {ci:<16} |{bar(lo,hi)}|{flag}")

    allw = sum(v[0] for v in tier.values())
    alln = sum(v[1] for v in tier.values())
    if alln:
        p, lo, hi = wilson(allw, alln)
        print(f"\n  OVERALL {f'{allw}/{alln}':<9}{p*100:>4.0f}% "
              f"{f'{lo*100:.0f}-{hi*100:.0f}%':<16} |{bar(lo,hi)}|")

    # ---- Consensus boosts --------------------------------------------------
    print("\n" + "-"*64 + "\n  DO CONSENSUS BOOSTS WIN?")
    if not adj:
        print("  No graded picks carry a consensus adjustment yet.")
    for k, (w, n) in sorted(adj.items()):
        p, lo, hi = wilson(w, n)
        print(f"  {k:<10}{w}/{n} = {p*100:.0f}%  (95% CI {lo*100:.0f}-{hi*100:.0f}%)")

    # ---- Scoring health ----------------------------------------------------
    # NOTE: results use INSERT OR IGNORE on UNIQUE(match_id), so recorded_at is
    # the FIRST-seen date. Counting results-per-day therefore looks bursty even
    # when scoring runs daily (off-season sparsity + already-recorded matches
    # add no row). The honest signals are: (a) how promptly finished matches get
    # scored, and (b) how many predicted past matches were never graded at all.
    print("\n" + "-"*64 + "\n  SCORING PROMPTNESS (days from match date -> recorded)")
    lag = conn.execute("""
        SELECT CAST(julianday(substr(r.recorded_at,1,10))
                   - julianday(substr(m.match_date,1,10)) AS INT) lag,
               COUNT(*) n
        FROM results r JOIN matches m ON m.match_id = r.match_id
        WHERE m.match_date IS NOT NULL AND r.recorded_at IS NOT NULL
        GROUP BY lag ORDER BY lag
    """).fetchall()
    total_lag = sum(r["n"] for r in lag)
    within2 = sum(r["n"] for r in lag if r["lag"] is not None and r["lag"] <= 2)
    for r in lag:
        print(f"  +{r['lag']:>2} day  {r['n']:>3}  {'#'*min(r['n'],40)}")
    if total_lag:
        print(f"\n  {within2}/{total_lag} ({100*within2/total_lag:.0f}%) scored "
              f"within 2 days of the match. Prompt scoring = healthy pipeline.")

    # ---- The real leak: ungraded predictions -------------------------------
    print("\n" + "-"*64 + "\n  GRADING COVERAGE (the metric that actually gates launch)")
    total_pred = conn.execute(
        "SELECT COUNT(DISTINCT match_id) FROM model_predictions").fetchone()[0]
    ungraded = conn.execute("""
        SELECT COUNT(DISTINCT mp.match_id)
        FROM model_predictions mp
        JOIN matches m ON m.match_id = mp.match_id
        LEFT JOIN results r ON r.match_id = mp.match_id
        WHERE r.match_id IS NULL AND m.match_date < date('now','-2 day')
    """).fetchone()[0]
    graded_matches = conn.execute("""
        SELECT COUNT(DISTINCT mp.match_id) FROM model_predictions mp
        JOIN results r ON r.match_id = mp.match_id
    """).fetchone()[0]
    print(f"  Distinct predicted matches : {total_pred}")
    print(f"  Graded (have a result)     : {graded_matches}")
    print(f"  Predicted, >2 days old, NO result (ungraded) : {ungraded}")
    if ungraded:
        print(f"  -> {ungraded} predicted matches over 2 days old were never scored.")
        print(f"     Root cause (verified July 2026): these leagues have NO working")
        print(f"     results source - the fixture is predicted but no scraper covers")
        print(f"     it (WC national-team naming, Europa Ldg qualifiers, Brazil B,")
        print(f"     Superettan, Veikkausliiga, Copa Sudamericana...). Fix is to")
        print(f"     align predicted leagues with scoreable ones, not re-key.")
        by_league = conn.execute("""
            SELECT m.league, COUNT(DISTINCT mp.match_id) n
            FROM model_predictions mp JOIN matches m ON m.match_id = mp.match_id
            LEFT JOIN results r ON r.match_id = mp.match_id
            WHERE r.match_id IS NULL AND m.match_date < date('now','-2 day')
              AND NOT EXISTS (SELECT 1 FROM results r2 JOIN matches m2
                    ON m2.match_id = r2.match_id WHERE m2.league = m.league)
            GROUP BY m.league ORDER BY n DESC LIMIT 8
        """).fetchall()
        if by_league:
            print("     Leagues we predict but have NEVER scored:")
            for r in by_league:
                print(f"       {(r['league'] or '?')[:24]:<24} {r['n']} ungraded")

    log = "./data/daily_pipeline.log"
    if os.path.exists(log):
        stale = (date.today()
                 - date.fromtimestamp(os.path.getmtime(log))).days
        msg = "OK" if stale <= 1 else f"!! {stale} days old — did the daily task run?"
        print(f"\n  Pipeline log last written {stale} day(s) ago: {msg}")

    # ---- Verdict -----------------------------------------------------------
    print("\n" + "="*64 + "\n  READINESS VERDICT")
    high_n = tier["HIGH"][1]
    if graded < 100 or high_n < 30:
        print("  NOT READY for testers. Need ~100+ graded picks overall and")
        print(f"  ~30+ in the HIGH tier to claim calibration. Have {graded} "
              f"total / {high_n} HIGH.")
    else:
        print("  Sample size is sufficient to start evaluating calibration.")
    print("="*64 + "\n")
    conn.close()


if __name__ == "__main__":
    main()
