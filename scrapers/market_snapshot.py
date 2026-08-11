"""
market_snapshot.py - Record the bookmaker's price for our pick, at pick time.

Why this exists
---------------
The central unanswered question about this project is not "is the model
accurate?" — it is "does the model know anything the market doesn't?"

Those are different questions with very different answers. The market check on
the Picks page shows our probabilities tracking SportyBet within about eight
points. That is reassuring for correctness and quietly damning for value: a
model that agrees with the odds adds nothing over reading the odds. Whatever
edge exists lives entirely in the DISAGREEMENTS — and nobody has ever measured
whether ours win.

This step makes that measurable, by storing two numbers per pick:

    book_odds  - the bookmaker's decimal odds for the exact selection we made
    book_prob  - those odds as an implied probability (margin left in; see below)

Once graded, the ledger can answer: when we said 70% and the market said 50%,
who was right? Group by the size of the disagreement and the answer is either
"we have a product" or "these picks are a nicely packaged version of the odds".

WHY IT MUST RUN AT PREDICTION TIME
----------------------------------
Odds move as money arrives. Looking them up after the match measures a
different market than the one we disagreed with, and no backfill can recover
it. **Every day this does not run is a day of evidence that cannot be
reconstructed.** That is the whole reason it is a separate step that runs
immediately after predictions rather than an analysis written later.

On the margin: book_prob is the raw 1/odds, which includes the bookmaker's
overround, so it is systematically a point or two high. That is deliberate —
storing the raw observable rather than a derived, de-margined number means the
stored value never depends on an assumption that might later be revised. Remove
the margin at analysis time, when the whole three-way price is in view.

Usage:
    python scrapers/market_snapshot.py            (stamp today's un-stamped picks)
    python scrapers/market_snapshot.py --days 2   (how far ahead to look)
"""

import argparse
import os
import sys

from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from scrapers.booking_codes import attach_sporty_odds


def _selection_for(row) -> str:
    """Phrase our pick the way the SportyBet mapper expects.

    The mapper identifies a 1X2 outcome by finding the TEAM NAME in the
    selection text, so the generic "Home Win" matches neither side and silently
    falls through to the DRAW branch — which is exactly the bug that made every
    pick look like a 40-point value edge when the market check was first wired
    up. Phrase it the way build_slip does.
    """
    bet = row["best_bet"] or ""
    if bet == "Home Win":
        return f"{row['home_team']} Win"
    if bet == "Away Win":
        return f"{row['away_team']} Win"
    return bet                      # "Draw" maps as-is


def run(days: int = 2) -> int:
    conn = get_connection()
    rows = conn.execute("""
        SELECT mp.id, mp.match_id, mp.best_bet, mp.best_bet_prob,
               m.home_team, m.away_team
        FROM model_predictions mp
        JOIN matches m ON m.match_id = mp.match_id
        WHERE mp.book_odds IS NULL
          AND m.match_date >= date('now')
          AND m.match_date <= date('now', ?)
          -- Only picks that have not kicked off: an in-play price is a
          -- different thing from the pre-match one we predicted against.
          AND (m.kickoff_utc IS NULL
               OR m.kickoff_utc > strftime('%Y-%m-%d %H:%M', 'now'))
          AND mp.id = (SELECT MAX(mp2.id) FROM model_predictions mp2
                       WHERE mp2.match_id = mp.match_id)
    """, [f"+{int(days)} day"]).fetchall()

    if not rows:
        logger.info("No un-stamped upcoming picks — nothing to snapshot.")
        conn.close()
        return 0

    # ── Where the price comes from ──────────────────────────────────────────
    #
    # STORED odds first, live SportyBet only as a top-up.
    #
    # SportyBet's events feed is IP-blocked from GitHub Actions — every page
    # returns HTML instead of JSON, the same shape as the Forebet block. Asking
    # it first meant this step priced 14/15 picks on a laptop and 0/15 in the
    # cloud, which is where it actually has to run. The odds we already store
    # (ESPN, written by kickoff_times.py, which DOES work from Actions) are a
    # real bookmaker line and cost no extra request.
    #
    # The live call is kept as a top-up because it is worth having where it
    # works, but nothing depends on it.
    stored = {}
    for row in conn.execute("""
        SELECT sp.match_id, sp.odds_home, sp.odds_draw, sp.odds_away
        FROM scraped_predictions sp
        WHERE sp.odds_home IS NOT NULL
          AND sp.id = (SELECT MAX(sp2.id) FROM scraped_predictions sp2
                       WHERE sp2.match_id = sp.match_id
                         AND sp2.odds_home IS NOT NULL)
    """):
        stored[row["match_id"]] = (row["odds_home"], row["odds_draw"],
                                   row["odds_away"])

    def stored_odds_for(r):
        trio = stored.get(r["match_id"])
        if not trio:
            return None
        home, draw, away = trio
        return {"Home Win": home, "Draw": draw, "Away Win": away}.get(r["best_bet"])

    odds = [stored_odds_for(r) for r in rows]

    missing = [i for i, o in enumerate(odds) if not o]
    if missing:
        try:
            legs = [{"home_team": rows[i]["home_team"],
                     "away_team": rows[i]["away_team"],
                     "market": "Match Result",
                     "selection": _selection_for(rows[i])} for i in missing]
            live = attach_sporty_odds(legs)["odds"]
            for i, o in zip(missing, live):
                if o:
                    odds[i] = o
        except Exception as e:
            # Never fatal: the stored odds already carry the measurement.
            logger.warning(f"SportyBet top-up unavailable ({e}) - "
                           f"using stored odds only")

    stamped = 0
    for r, o in zip(rows, odds):
        if not o or o <= 1.0:
            continue
        conn.execute(
            "UPDATE model_predictions SET book_odds = ?, book_prob = ? WHERE id = ?",
            (float(o), round(1.0 / float(o), 4), r["id"]))
        stamped += 1

    conn.commit()

    # A quick read on today's disagreements, so the log itself is informative
    # rather than just a count.
    gaps = []
    for r, o in zip(rows, odds):
        if o and o > 1.0 and r["best_bet_prob"]:
            gaps.append(r["best_bet_prob"] - (1.0 / float(o)))
    conn.close()

    if gaps:
        big = [g for g in gaps if abs(g) >= 0.15]
        logger.success(
            f"Market snapshot: {stamped}/{len(rows)} picks priced; "
            f"mean gap {sum(gaps)/len(gaps)*100:+.1f} pts, "
            f"{len(big)} disagreements of 15+ pts")
    else:
        # Pricing NOTHING when there were picks to price means the feed is
        # unreachable or the selection mapping has broken — not "the bookmaker
        # doesn't list these". Return non-zero so it shows up, because a
        # snapshot that quietly stops is indistinguishable from one that runs,
        # and the evidence it loses cannot be recovered afterwards.
        logger.error(f"Market snapshot priced 0 of {len(rows)} picks - "
                     f"no stored odds and no live feed. Evidence for those "
                     f"picks is lost permanently, so this is not tolerated.")
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    args = ap.parse_args()
    sys.exit(run(args.days))
