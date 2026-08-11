"""
draw_analysis.py - Written analysis for the DRAW view specifically.

Why this cannot reuse the normal analysis
-----------------------------------------
Every prediction already carries `gemma_analysis`, but it explains the model's
BEST BET — and the model has never once rated a draw as most likely (0 of 360
predictions). So the stored text on a draw-leaning fixture says something like
"Tigres are the heavy favourite to win at home". Rendering that under a draw
suggestion would not merely be unhelpful, it would CONTRADICT the thing the
user is looking at, in the app's own voice.

The choice was: show nothing, or write the right explanation. Showing nothing
leaves the Draws tab visibly poorer than every other view. So this writes text
for the actual question — why might this one end level? — and stores it in its
own column, leaving `gemma_analysis` untouched for the 1X2 view.

The constraint that does NOT change
-----------------------------------
Analysis is EXPLANATION ONLY and can never move a probability. This module
reads the numbers and describes them; it does not choose fixtures (draws.py
does, from market balance) and it does not rank them. If every provider is
down, the draw list still ships with the structural reason from
`draw_lean.why` — the prose is a bonus, never the substance.

It is also written NOT to oversell. The honest finding is that draws in these
fixtures land ~30-34% against a 26.2% baseline and the bookmaker prices that
accurately, so the prompt explicitly forbids "value" or "underpriced" framing.
A model that agrees with the odds must not be made to sound like it disagrees.

Usage:
    python analysis/draw_analysis.py            # fill upcoming draw-leaning
    python analysis/draw_analysis.py --dry-run
"""

import argparse
import os
import sys

from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from models.draws import draw_lean, RATE_BASELINE
from analysis.gemma_analyst import (analyze_match, active_provider, UNAVAILABLE)


def ensure_column(conn) -> None:
    """Add draw_analysis if it isn't there. Idempotent, safe on every run.

    A separate column rather than overwriting gemma_analysis: the same fixture
    legitimately appears in both views and needs a different sentence in each.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(model_predictions)")}
    if "draw_analysis" not in cols:
        conn.execute("ALTER TABLE model_predictions ADD COLUMN draw_analysis TEXT")
        conn.commit()
        logger.info("Added model_predictions.draw_analysis")


def build_context(row, lean: dict) -> dict:
    """Shape a draw-framed prompt for the shared provider chain.

    Reuses analyze_match() so the Gemini -> Groq -> Ollama failover, retries
    and quota handling are the SAME code path as the main analysis. A second
    private copy of that chain would drift, and drift in a fallback is only
    discovered during an outage.
    """
    home, away = row["home_team"], row["away_team"]
    pct = lean["expected_pct"]
    return {
        "home_team": home,
        "away_team": away,
        "league": row["league"],
        # Percentages, not fractions — the scale every other caller uses
        # (predict.py:396, backfill_analysis.py:73). Passing 0-1 here would
        # print "Draw: 0.3%" into the prompt.
        "home_win_prob": (row["home_win_prob"] or 0) * 100,
        "draw_prob": (row["draw_prob"] or 0) * 100,
        "away_win_prob": (row["away_win_prob"] or 0) * 100,
        "best_bet": "Draw",
        "best_bet_prob": (row["draw_prob"] or 0) * 100,
        "confidence": "DRAW-LEANING",
        # Carried into the prompt by build_prompt's generic fields; the
        # instruction below is what actually reframes the task.
        "custom_instruction": (
            f"This is the DRAW view. Do NOT argue for either side to win. "
            f"Explain in 3 sentences why {home} vs {away} could plausibly end "
            f"level: the bookmaker rates these teams almost identically, and "
            f"historically about {pct:.0f}% of such evenly-matched fixtures "
            f"finish as draws, against {RATE_BASELINE:.0f}% of matches "
            f"overall. Be clear that a draw is still the less likely single "
            f"outcome — roughly one in three, not a favourite. Do NOT call it "
            f"value, an edge, underpriced, or a banker: the odds already "
            f"reflect this pattern."
        ),
    }


def run(dry_run: bool = False) -> int:
    conn = get_connection()
    ensure_column(conn)

    rows = conn.execute("""
        SELECT mp.id, mp.match_id, mp.home_win_prob, mp.draw_prob,
               mp.away_win_prob, m.home_team, m.away_team, m.league,
               od.odds_home, od.odds_away
        FROM model_predictions mp
        JOIN matches m ON m.match_id = mp.match_id
        JOIN scraped_predictions od ON od.id = (
            SELECT MAX(sp2.id) FROM scraped_predictions sp2
            WHERE sp2.match_id = m.match_id AND sp2.odds_home IS NOT NULL)
        WHERE m.match_date >= date('now')
          AND (mp.draw_analysis IS NULL OR TRIM(mp.draw_analysis) = '')
          AND mp.id = (SELECT MAX(mp2.id) FROM model_predictions mp2
                       WHERE mp2.match_id = mp.match_id)
    """).fetchall()

    todo = [(r, draw_lean(dict(r))) for r in rows]
    todo = [(r, l) for r, l in todo if l]

    if not todo:
        logger.info("No draw-leaning fixtures need analysis.")
        conn.close()
        return 0

    logger.info(f"{len(todo)} draw-leaning fixture(s) need analysis "
                f"(provider: {active_provider()})")
    if dry_run:
        for r, l in todo:
            logger.info(f"  would write: {r['home_team']} vs {r['away_team']} "
                        f"(balance {l['balance']})")
        conn.close()
        return 0

    written = 0
    for r, lean in todo:
        text = analyze_match(build_context(r, lean))
        if not text or text == UNAVAILABLE:
            logger.warning(f"  no analysis for {r['home_team']} vs {r['away_team']}")
            continue
        conn.execute("UPDATE model_predictions SET draw_analysis = ? WHERE id = ?",
                     (text, r["id"]))
        written += 1
        logger.info(f"  wrote {r['home_team']} vs {r['away_team']}")
    conn.commit()
    conn.close()

    # Not fatal when the providers are down: the draw list still carries the
    # structural reason. Prose is a bonus here, never the substance.
    if written == 0:
        logger.error(f"Draw analysis wrote 0 of {len(todo)} - providers down?")
        return 1
    logger.success(f"Draw analysis written for {written}/{len(todo)}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.exit(run(args.dry_run))
