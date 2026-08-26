"""Form-depth calibration — does a tier still mean what it claims when the
model has barely seen one of the teams?

Read-only. Answers one question `track_record_health.py` cannot:

    Our confidence tiers are reported as if every pick rests on the same
    quality of evidence. It does not. A third of the teams in `team_history`
    exist there because of ONE recorded result of our own, and the form
    features are averaged over `len(recent)` — not over the window — so a
    single game produces a FULL-MAGNITUDE signal. One win reads exactly like
    five straight wins (`form_ppg` = 3.0); one loss reads exactly like five
    straight defeats (0.0).

Why this is a separate script and not another section of
`track_record_health.py`: answering it needs `build_current_team_histories()`
(every historical CSV replayed) plus the fuzzy matcher on both teams of every
graded pick. That is minutes, not milliseconds, and the point of the other
script is that it is cheap enough to run before every trust decision. Keep that
one fast; run this one when the sample has grown.

What it found on first run (24 Aug 2026, 635 graded picks) — see CLAUDE.md
bug #27. The headline is that thin history is NOT uniformly bad, which is why
the obvious fix (refuse to predict without N games) would be a mistake:

    thin   HIGH    29/34  85.3%   <- the BEST bucket in the entire ledger
    deep   HIGH    32/51  62.7%
    deep   MEDIUM  63/96  65.6%
    thin   MEDIUM  23/47  48.9%   <- below the floor MEDIUM promises
    thin   LOW     64/145 44.1%
    deep   LOW    115/262 43.9%

Overall the two bands are identical (51.3% each). Three things the first pass
at this got WRONG, recorded here so the next reader does not repeat them:

  * Tier ordering within thin picks is INTACT (85 > 49 > 44). It is *deep* that
    shows a mild inversion (HIGH 63% < MEDIUM 66%), and those intervals overlap
    heavily, so that is most likely noise.
  * The real defect is a LABELLING failure: thin MEDIUM's 95% interval is
    35-63%, which excludes the claimed 66%. The badge is not supported.
  * Hit rate does NOT decline with less history. By depth of the shallower team
    the curve is flat: 1g 62%, 2g 50%, 3-5g 51%, 6-10g 52%, 26+g 51%. So the
    `len(recent)` mechanism is real in the code but its effect on accuracy is
    NOT demonstrated. Do not claim that it is.

The thin-HIGH 85% (n=34) is an unexplained outlier worth watching. The tempting
story - a one-game club is usually a minnow in a European qualifier, so "no
record of this team" correlates with "out of its depth" - is undercut by the
flat depth curve above.

Do NOT act on a single run of this. n=47 for thin MEDIUM, and this project has
already been burned once by treating a 13-sample tier inversion as real
(CLAUDE.md "What's next" item 4). Re-run as the ledger grows.

Above all, vary `--depth` before believing anything. On the same 635 picks,
moving the cut from 5 to 3 flips every conclusion except one:

    --depth 5:  thin MEDIUM 23/47  49%  CI 35-63%  excludes the 66% claim
    --depth 3:  thin MEDIUM 18/31  58%  CI 41-74%  CONTAINS the 66% claim
    --depth 5:  deep ordering BROKEN (HIGH 63% < MEDIUM 66%)
    --depth 3:  deep ordering OK     (HIGH 64% > MEDIUM 61%)

The only finding stable across both cuts is thin HIGH being extraordinary
(85% at <=5, 92% at <=3). A conclusion that depends on an arbitrary cut-off is
not a conclusion yet - which is exactly why this script takes the threshold as
a flag rather than hard-coding one.

Usage:  python db/form_depth_health.py [--depth 5] [--version rf_v2]
Read-only; changes nothing. Takes a few minutes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import get_connection                      # noqa: E402
from db.track_record_health import wilson, bar              # noqa: E402
from models.predict import (build_current_team_histories,   # noqa: E402
                            match_team_name)

BEST_BET_TO_OUTCOME = {"Home Win": "H", "Away Win": "A", "Draw": "D"}
# Claimed calibration, same source of truth as track_record_health.py — see the
# comment there for why MEDIUM is 62 and not 66 (the old value was a cumulative
# >=55% figure being graded against a band, so the alarm always fired).
CLAIMED = {"HIGH": 74, "MEDIUM": 62}
TIERS = ["HIGH", "MEDIUM", "LOW"]


def _arg(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def main():
    thin_max = int(_arg("--depth", 5))
    version = _arg("--version", None)

    team_history = build_current_team_histories()
    known = list(team_history.keys())

    conn = get_connection()
    sql = """
        SELECT m.home_team, m.away_team, m.league,
               mp.confidence, mp.best_bet, mp.model_version, r.outcome
        FROM model_predictions mp
        JOIN matches m ON m.match_id = mp.match_id
        JOIN results r ON r.match_id = mp.match_id
        -- Latest prediction per match (MAX(id)), the universal serving rule.
        -- Without it a re-predicted match is scored once per row. Bug #3.
        WHERE mp.id = (SELECT MAX(mp2.id) FROM model_predictions mp2
                       WHERE mp2.match_id = mp.match_id)
    """
    params = []
    if version:
        sql += " AND mp.model_version = ?"
        params.append(version)
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    # The matcher is the expensive part; memoise per distinct raw name.
    cache = {}

    def depth(name):
        if name not in cache:
            hit = match_team_name(name, known)
            cache[name] = len(team_history[hit]) if hit else None
        return cache[name]

    buckets = {}      # (band, tier) -> [wins, n]
    bands = {}        # band -> [wins, n]
    unmatched = 0
    hist_of = []      # (min_depth, hit) for the depth-curve table

    for r in rows:
        dh, da = depth(r["home_team"]), depth(r["away_team"])
        if dh is None or da is None:
            # Neither team is in history at all — predict.py:370 skips these,
            # so a graded pick here would mean something has gone wrong.
            unmatched += 1
            continue
        shallow = min(dh, da)
        band = f"thin (<={thin_max}g)" if shallow <= thin_max else f"deep (>{thin_max}g)"
        hit = 1 if BEST_BET_TO_OUTCOME.get(r["best_bet"]) == r["outcome"] else 0
        hist_of.append((shallow, hit))
        for key, store in ((band, bands), ((band, r["confidence"]), buckets)):
            t = store.setdefault(key, [0, 0])
            t[0] += hit
            t[1] += 1

    scope = f"  [{version}]" if version else "  [all versions]"
    print(f"\n{'='*72}\n  FORM-DEPTH CALIBRATION{scope}\n{'='*72}")
    print(f"\nGraded picks examined: {len(hist_of)}"
          f"   (thin = the SHALLOWER of the two teams has <={thin_max} games)")
    if unmatched:
        print(f"  {unmatched} skipped: a team matched no history at all.")

    print(f"\n  {'BAND':<14}{'TIER':<8}{'RECORD':<9}{'HIT':<6}{'95% CI':<12}"
          f"  0%          50%         100%")
    for band in sorted(bands):
        for tier in TIERS:
            w, n = buckets.get((band, tier), [0, 0])
            if not n:
                continue
            p, lo, hi = wilson(w, n)
            flag = ""
            if tier in CLAIMED and not (lo <= CLAIMED[tier] / 100 <= hi):
                flag = f"  <- claim {CLAIMED[tier]}% NOT in CI"
            if n < 30:
                flag += "  (n<30)"
            print(f"  {band:<14}{tier:<8}{f'{w}/{n}':<9}{p*100:>4.0f}% "
                  f"{f'{lo*100:.0f}-{hi*100:.0f}%':<12} |{bar(lo, hi)}|{flag}")
        w, n = bands[band]
        p, lo, hi = wilson(w, n)
        print(f"  {band:<14}{'ALL':<8}{f'{w}/{n}':<9}{p*100:>4.0f}% "
              f"{f'{lo*100:.0f}-{hi*100:.0f}%':<12} |{bar(lo, hi)}|")
        print()

    # ---- Is the tier ORDER intact within each band? ------------------------
    # This, not the raw hit rate, is the thing that breaks. A band whose MEDIUM
    # does not beat its own LOW has no MEDIUM tier — it has two LOW tiers and a
    # label that misleads whoever is reading the app.
    print("-"*72 + "\n  TIER ORDERING WITHIN EACH BAND (HIGH > MEDIUM > LOW ?)")
    for band in sorted(bands):
        rates = []
        for tier in TIERS:
            w, n = buckets.get((band, tier), [0, 0])
            rates.append(w / n if n else None)
        have = [(t, r) for t, r in zip(TIERS, rates) if r is not None]
        ok = all(a[1] >= b[1] for a, b in zip(have, have[1:]))
        shown = "  >  ".join(f"{t} {r*100:.0f}%" for t, r in have)
        print(f"  {band:<14}{'OK  ' if ok else 'BROKEN'}  {shown}")

    # ---- Hit rate as a function of how much history we have ----------------
    print("\n" + "-"*72 + "\n  HIT RATE BY DEPTH OF THE SHALLOWER TEAM")
    edges = [(1, 1), (2, 2), (3, 5), (6, 10), (11, 25), (26, 10**9)]
    for lo_e, hi_e in edges:
        sel = [h for d, h in hist_of if lo_e <= d <= hi_e]
        if not sel:
            continue
        w, n = sum(sel), len(sel)
        p, lo, hi = wilson(w, n)
        label = f"{lo_e}g" if lo_e == hi_e else (
            f"{lo_e}+g" if hi_e > 10**8 else f"{lo_e}-{hi_e}g")
        print(f"  {label:<10}{f'{w}/{n}':<10}{p*100:>4.0f}% "
              f"{f'{lo*100:.0f}-{hi*100:.0f}%':<12} |{bar(lo, hi)}|")

    print(f"\n{'='*72}")
    # Plain ASCII on purpose: this prints to a Windows console (cp1252), where
    # an em-dash comes out as a replacement char. CLAUDE.md already records the
    # same class of problem for webapp/*.html.
    print("  Reminder: one game gives a full-magnitude form feature, because\n"
          "  form_ppg/goals_avg/venue_ppg divide by len(recent), not by the\n"
          "  window. That mechanism is real in the code - but the depth curve\n"
          "  above is FLAT, so it is not yet shown to cost accuracy. The fix is\n"
          "  a has_form_history feature (let the forest tell 'no data' from\n"
          "  'dreadful'), NOT a minimum-games refusal: thin HIGH is currently\n"
          "  the best-performing bucket in the ledger.")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    main()
