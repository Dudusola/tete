"""
predict.py - Run the trained Random Forest on today's scraped matches.
v2: Gemma analysis wired in for HIGH/MEDIUM picks (saved to DB -> Telegram).

Usage:
    python models/predict.py
    python models/predict.py --no-gemma   (skip AI analysis, faster)
"""

import os
import sys
import argparse
import joblib
import pandas as pd
from datetime import datetime
from difflib import get_close_matches
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from models.train import load_all_csvs, form_ppg, goals_avg, venue_ppg, h2h_wins, FORM_WINDOW
from models.consensus import build_consensus, _same_match

MODEL_PATH = "./models/random_forest_v1.joblib"
# rf_v1b = same trained model, but serving now feeds REAL bookmaker odds to the
# odds features (was Forebet's percentages - train/serve mismatch) and team
# form includes our own recorded results. Bumped so the ledger separates eras.
# rf_v1c = same model, but the confidence TIER is now consensus-adjusted
# (see apply_consensus_modifier). Probabilities are untouched.
MODEL_VERSION = "rf_v1d"  # rf_v1d = July 2026 data refresh (+SWE/FIN/IRL leagues)

# Confidence tiers calibrated from training evaluation (12 June 2026):
#   >=65% model prob -> ~75% historical accuracy -> HIGH
#   >=55%            -> ~66%                     -> MEDIUM
TIER_HIGH = 0.65
TIER_MEDIUM = 0.55

# Forebet name -> historical dataset name
# Grows over time as skip logs reveal mismatches
TEAM_ALIASES = {
    # International
    "USA": "United States",
    "South Korea": "South Korea",
    "North Korea": "North Korea",
    "Ireland": "Republic of Ireland",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Trinidad & Tobago": "Trinidad and Tobago",
    "UAE": "United Arab Emirates",
    "DR Congo": "DR Congo",
    "Cape Verde Islands": "Cape Verde",
    "China": "China PR",
    # Clubs (add as skip logs reveal them)
    # "Man Utd": "Man United",
}


def build_current_team_histories() -> dict:
    """Replay all historical matches to get every team's form as of today."""
    logger.info("Rebuilding current team form from historical data...")
    df = load_all_csvs()

    team_history = {}
    for _, row in df.iterrows():
        home, away = row["HomeTeam"], row["AwayTeam"]
        h_result = "W" if row["FTR"] == "H" else ("D" if row["FTR"] == "D" else "L")
        a_result = "W" if row["FTR"] == "A" else ("D" if row["FTR"] == "D" else "L")

        team_history.setdefault(home, []).append({
            "venue": "home", "result": h_result, "opponent": away,
            "goals_for": row["FTHG"], "goals_against": row["FTAG"],
        })
        team_history.setdefault(away, []).append({
            "venue": "away", "result": a_result, "opponent": home,
            "goals_for": row["FTAG"], "goals_against": row["FTHG"],
        })

    # Append OUR recorded real results (matches + results tables) so form
    # reflects games played after the historical CSVs end. Only genuine
    # finished scores live in results, so this can't leak predictions.
    conn = get_connection()
    rows = conn.execute("""
        SELECT m.home_team, m.away_team, r.home_goals, r.away_goals, r.outcome
        FROM results r JOIN matches m ON m.match_id = r.match_id
        ORDER BY m.match_date, r.recorded_at
    """).fetchall()
    conn.close()
    known = list(team_history.keys())
    appended = 0
    for r in rows:
        home = match_team_name(r["home_team"], known) or r["home_team"]
        away = match_team_name(r["away_team"], known) or r["away_team"]
        h_result = "W" if r["outcome"] == "H" else ("D" if r["outcome"] == "D" else "L")
        a_result = "W" if r["outcome"] == "A" else ("D" if r["outcome"] == "D" else "L")
        team_history.setdefault(home, []).append({
            "venue": "home", "result": h_result, "opponent": away,
            "goals_for": r["home_goals"], "goals_against": r["away_goals"],
        })
        team_history.setdefault(away, []).append({
            "venue": "away", "result": a_result, "opponent": home,
            "goals_for": r["away_goals"], "goals_against": r["home_goals"],
        })
        appended += 1

    logger.success(f"✅ Form data ready for {len(team_history)} teams "
                   f"(+{appended} of our own recorded results)")
    return team_history


def match_team_name(scraped_name: str, known_teams: list) -> str | None:
    """Match a Forebet team name to a historical team name."""
    # 1. Manual alias map (exact known mismatches)
    if scraped_name in TEAM_ALIASES:
        aliased = TEAM_ALIASES[scraped_name]
        if aliased in known_teams:
            return aliased
    # 2. Exact match
    if scraped_name in known_teams:
        return scraped_name
    # 3. Fuzzy fallback
    matches = get_close_matches(scraped_name, known_teams, n=1, cutoff=0.85)
    return matches[0] if matches else None


def get_todays_scraped_matches() -> list:
    """Latest scraped prediction per match, today onwards."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT m.match_id, m.home_team, m.away_team, m.league,
               m.match_date, m.kickoff_time,
               p.home_win_pct, p.draw_pct, p.away_win_pct,
               p.predicted_home_goals, p.predicted_away_goals,
               p.odds_home, p.odds_draw, p.odds_away
        FROM matches m
        JOIN scraped_predictions p ON p.id = (
            SELECT MAX(p2.id) FROM scraped_predictions p2
            WHERE p2.match_id = m.match_id
        )
        WHERE m.match_date >= date('now')
        ORDER BY m.match_date, m.kickoff_time
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def build_live_features(home_hist: list, away_hist: list,
                        home_team: str, away_team: str,
                        forebet: dict) -> dict:
    """Build the exact feature set the model was trained on.

    Odds features MUST come from real bookmaker odds when available - the
    model was trained on odds-implied probabilities. Source percentages are
    only a fallback (for PredictZ rows they are odds-derived anyway).
    """
    oh, od, oa = (forebet.get("odds_home"), forebet.get("odds_draw"),
                  forebet.get("odds_away"))
    if oh and od and oa:
        h, d, a = 1 / oh, 1 / od, 1 / oa
    else:
        h = (forebet.get("home_win_pct") or 33.3) / 100
        d = (forebet.get("draw_pct") or 33.3) / 100
        a = (forebet.get("away_win_pct") or 33.3) / 100
    total = h + d + a

    return {
        "home_form_ppg": form_ppg(home_hist, FORM_WINDOW),
        "away_form_ppg": form_ppg(away_hist, FORM_WINDOW),
        "home_goals_for_avg": goals_avg(home_hist, FORM_WINDOW, "for"),
        "home_goals_against_avg": goals_avg(home_hist, FORM_WINDOW, "against"),
        "away_goals_for_avg": goals_avg(away_hist, FORM_WINDOW, "for"),
        "away_goals_against_avg": goals_avg(away_hist, FORM_WINDOW, "against"),
        "home_home_ppg": venue_ppg(home_hist, FORM_WINDOW, "home"),
        "away_away_ppg": venue_ppg(away_hist, FORM_WINDOW, "away"),
        "h2h_home_wins": h2h_wins(home_hist, away_team, 5),
        "odds_home_prob": h / total,
        "odds_draw_prob": d / total,
        "odds_away_prob": a / total,
    }


def save_prediction(match_id: str, probs: dict, confidence: str,
                    best_bet: str, best_prob: float, gemma_analysis: str = None,
                    consensus_adj: str = None):
    conn = get_connection()
    conn.execute("""
        INSERT INTO model_predictions
        (match_id, model_version, home_win_prob, draw_prob, away_win_prob,
         confidence, best_bet, best_bet_prob, gemma_analysis, consensus_adj)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [match_id, MODEL_VERSION, probs["H"], probs["D"], probs["A"],
          confidence, best_bet, best_prob, gemma_analysis, consensus_adj])
    conn.commit()
    conn.close()


def apply_consensus_modifier(match: dict, best_outcome: str,
                             confidence: str, consensus: list) -> tuple[str, str | None]:
    """Adjust the confidence tier using cross-source agreement.

    Rules (deliberately conservative — the tier calibration story depends on
    these staying rare and defensible):
      - boost:  MEDIUM -> HIGH when 2+ sources agree WITH the model's outcome.
      - demote: HIGH -> MEDIUM when 2+ sources agree on a DIFFERENT outcome.
      - LOW is never promoted: sources agreeing on a coin-flip doesn't make it
        bettable. Probabilities are never changed, only the tier label.

    Returns (confidence, consensus_adj) where consensus_adj is
    'boosted' / 'demoted' / None — stored in the ledger so we can later
    measure whether these adjustments actually win.
    """
    for entry in consensus:
        if not _same_match(match, entry):
            continue
        if len(entry["sources"]) < 2 or not entry["agree"]:
            return confidence, None
        if entry["agreed_outcome"] == best_outcome and confidence == "MEDIUM":
            return "HIGH", "boosted"
        if entry["agreed_outcome"] != best_outcome and confidence == "HIGH":
            return "MEDIUM", "demoted"
        return confidence, None
    return confidence, None


def run(use_gemma: bool = True):
    # Load model
    if not os.path.exists(MODEL_PATH):
        logger.error(f"No trained model at {MODEL_PATH}. Run: python models/train.py")
        sys.exit(1)
    bundle = joblib.load(MODEL_PATH)
    model, columns = bundle["model"], bundle["columns"]
    logger.info(f"Loaded model {MODEL_VERSION}")

    # Gemma availability check (only if requested)
    gemma_ok = False
    if use_gemma:
        from analysis.gemma_analyst import check_ollama_running, analyze_match
        gemma_ok = check_ollama_running()
        if not gemma_ok:
            logger.warning("Ollama not running - predictions will have no AI analysis")

    # Current form for all known teams
    team_history = build_current_team_histories()
    known_teams = list(team_history.keys())

    # Today's scraped fixtures
    scraped = get_todays_scraped_matches()
    if not scraped:
        logger.warning("No scraped matches in DB. Run: python scrapers/forebet.py")
        sys.exit(0)
    logger.info(f"Found {len(scraped)} scraped matches to evaluate")

    # Cross-source agreement, used to adjust confidence tiers
    try:
        consensus = build_consensus()
    except Exception as e:
        logger.warning(f"Consensus unavailable ({e}) — tiers won't be adjusted")
        consensus = []

    predictions = []
    skipped = 0
    boosted = demoted = 0

    for match in scraped:
        home_match = match_team_name(match["home_team"], known_teams)
        away_match = match_team_name(match["away_team"], known_teams)

        if not home_match or not away_match:
            skipped += 1
            if match.get("league") in ("WC", "INT"):
                logger.warning(f"Skipped [{match['league']}]: "
                               f"'{match['home_team']}' vs '{match['away_team']}'")
            continue

        feats = build_live_features(
            team_history[home_match], team_history[away_match],
            home_match, away_match, match
        )

        X = pd.DataFrame([feats])[columns]
        probs_arr = model.predict_proba(X)[0]
        probs = dict(zip(model.classes_, probs_arr))

        best_outcome = max(probs, key=probs.get)
        best_prob = probs[best_outcome]

        if best_prob >= TIER_HIGH:
            confidence = "HIGH"
        elif best_prob >= TIER_MEDIUM:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        bet_names = {"H": "Home Win", "D": "Draw", "A": "Away Win"}
        best_bet = bet_names[best_outcome]

        # Consensus tier adjustment (before Gemma, so boosted picks get analysis)
        confidence, consensus_adj = apply_consensus_modifier(
            match, best_outcome, confidence, consensus)
        if consensus_adj == "boosted":
            boosted += 1
            logger.info(f"⬆ Consensus boost MEDIUM→HIGH: "
                        f"{match['home_team']} vs {match['away_team']}")
        elif consensus_adj == "demoted":
            demoted += 1
            logger.info(f"⬇ Consensus demote HIGH→MEDIUM: "
                        f"{match['home_team']} vs {match['away_team']} "
                        f"(sources back a different outcome)")

        # Gemma analysis only for picks worth showing (saves time)
        gemma_text = None
        if gemma_ok and confidence in ("HIGH", "MEDIUM"):
            logger.info(f"🧠 Gemma analyzing: {match['home_team']} vs {match['away_team']}")
            gemma_text = analyze_match({
                "home_team": match["home_team"],
                "away_team": match["away_team"],
                "league": match["league"],
                "home_win_prob": probs["H"] * 100,
                "draw_prob": probs["D"] * 100,
                "away_win_prob": probs["A"] * 100,
                "home_win_pct": match.get("home_win_pct"),
                "draw_pct": match.get("draw_pct"),
                "away_win_pct": match.get("away_win_pct"),
                "predicted_score": (
                    f"{match['predicted_home_goals']:.0f} - {match['predicted_away_goals']:.0f}"
                    if match.get("predicted_home_goals") is not None else "N/A"
                ),
                "over_2_5_pct": match.get("over_2_5_pct", "N/A"),
                "confidence": confidence,
                "best_bet": best_bet,
                "best_bet_prob": best_prob * 100,
            })

        save_prediction(match["match_id"], probs, confidence,
                        best_bet, best_prob, gemma_text, consensus_adj)

        predictions.append({
            **match,
            "model_probs": probs,
            "confidence": confidence,
            "best_bet": best_bet,
            "best_prob": best_prob,
            "gemma_analysis": gemma_text,
        })

    logger.success(f"✅ Predicted {len(predictions)} matches "
                   f"({skipped} skipped - teams not in historical data; "
                   f"consensus: {boosted} boosted, {demoted} demoted)")

    # Display, best picks first
    predictions.sort(key=lambda x: x["best_prob"], reverse=True)

    print(f"\n{'='*70}")
    print(f"MODEL PICKS - {datetime.now().strftime('%A, %d %B %Y')}")
    print(f"{'='*70}")

    for tier in ["HIGH", "MEDIUM"]:
        tier_preds = [p for p in predictions if p["confidence"] == tier]
        if not tier_preds:
            continue
        print(f"\n--- {tier} CONFIDENCE ({len(tier_preds)} picks) ---")
        for p in tier_preds:
            probs = p["model_probs"]
            print(f"\n{p['home_team']} vs {p['away_team']}  [{p['league']}] "
                  f"{p['match_date']} {p['kickoff_time']}")
            print(f"  Model: H {probs['H']:.0%} | D {probs['D']:.0%} | A {probs['A']:.0%}")
            print(f"  >> {p['best_bet']} ({p['best_prob']:.0%})")
            if p.get("gemma_analysis"):
                print(f"  💬 {p['gemma_analysis'][:150]}...")

    low_count = sum(1 for p in predictions if p["confidence"] == "LOW")
    if low_count:
        print(f"\n({low_count} LOW confidence matches hidden - not worth betting)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-gemma", action="store_true", help="Skip Gemma analysis")
    args = parser.parse_args()
    run(use_gemma=not args.no_gemma)