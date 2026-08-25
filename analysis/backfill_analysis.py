"""
Write AI analysis onto predictions that are missing it.

WHY: a prediction and its written analysis are produced together, but a run
with --no-gemma (or an LLM outage) saves the prediction with gemma_analysis
NULL. Normally the next pipeline run supersedes it. It doesn't when the fixture
can no longer be re-predicted — e.g. Flamengo v Vitoria, whose odds vanished
from every source we can reach, so predict.py rightly refuses to redo it. The
pick stays valid and visible with a blank explanation.

This fills the text in from the stored probabilities. It NEVER changes a
prediction — only the prose that explains one.

Usage:
    python analysis/backfill_analysis.py            # upcoming HIGH/MEDIUM
    python analysis/backfill_analysis.py --dry-run
"""

import argparse
import os
import sys

from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from analysis.gemma_analyst import analyze_match, check_ollama_running


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = get_connection()
    rows = conn.execute("""
        SELECT mp.id, m.home_team, m.away_team, m.league,
               mp.home_win_prob, mp.draw_prob, mp.away_win_prob,
               mp.confidence, mp.best_bet, mp.best_bet_prob,
               sp.predicted_home_goals, sp.predicted_away_goals,
               sp.over_2_5_pct, sp.home_win_pct, sp.draw_pct, sp.away_win_pct
        FROM model_predictions mp
        JOIN matches m ON m.match_id = mp.match_id
        LEFT JOIN scraped_predictions sp ON sp.id = (
            SELECT MAX(id) FROM scraped_predictions WHERE match_id = m.match_id)
        WHERE mp.id = (SELECT MAX(id) FROM model_predictions
                       WHERE match_id = m.match_id)
          AND mp.gemma_analysis IS NULL
          AND mp.confidence IN ('HIGH','MEDIUM')
          AND m.match_date >= date('now')
          AND (m.live_state IS NULL OR m.live_state = 'pre')
    """).fetchall()

    if not rows:
        logger.info("No upcoming HIGH/MEDIUM picks are missing analysis.")
        return 0

    logger.info(f"{len(rows)} pick(s) missing analysis")
    if args.dry_run:
        for r in rows:
            print(f"  {r['home_team']} vs {r['away_team']} [{r['confidence']}]")
        return 0

    if not check_ollama_running():
        logger.error("Analysis provider unavailable — nothing written.")
        return 1

    written = 0
    for r in rows:
        text = analyze_match({
            "home_team": r["home_team"], "away_team": r["away_team"],
            "league": r["league"],
            "home_win_prob": (r["home_win_prob"] or 0) * 100,
            "draw_prob": (r["draw_prob"] or 0) * 100,
            "away_win_prob": (r["away_win_prob"] or 0) * 100,
            "home_win_pct": r["home_win_pct"], "draw_pct": r["draw_pct"],
            "away_win_pct": r["away_win_pct"],
            "predicted_score": (
                f"{int(r['predicted_home_goals'])} - {int(r['predicted_away_goals'])}"
                if r["predicted_home_goals"] is not None else "N/A"),
            "over_2_5_pct": r["over_2_5_pct"],
            "confidence": r["confidence"], "best_bet": r["best_bet"],
            "best_bet_prob": (r["best_bet_prob"] or 0) * 100,
            # No "facts" key on purpose, so build_prompt falls back to the
            # probabilities-only prompt. The fact-based version needs the form
            # features, and reconstructing those means
            # build_current_team_histories() — every historical CSV replayed,
            # minutes of work — for prose on the handful of picks that reach
            # here. The text is thinner than a live run's; that is the trade,
            # and it is why this script exists at all (a blank explanation is
            # worse than a plain one).
        })
        # Don't store the provider's failure string as if it were analysis.
        if not text or text.startswith("Analysis unavailable"):
            logger.warning(f"No text for {r['home_team']} vs {r['away_team']}")
            continue
        conn.execute("UPDATE model_predictions SET gemma_analysis = ? WHERE id = ?",
                     (text, r["id"]))
        written += 1
        logger.info(f"  ✍ {r['home_team']} vs {r['away_team']}")

    conn.commit()
    conn.close()
    logger.success(f"Analysis written for {written}/{len(rows)} pick(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
