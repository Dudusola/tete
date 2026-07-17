"""
Random Forest trainer for match outcome prediction (H / D / A) - v2
Trains on football-data.co.uk CSVs dropped into data/historical/

v2 changes:
- Handles both football-data.co.uk CSV formats (main + extra leagues)
- Date cutoff: only matches from 2018 onwards (recent era)
- Removed class_weight="balanced" (was forcing bad draw predictions)
- matches_played used only as a filter, not as a model feature
- Added confidence-tier evaluation (the metric that matters for betting)

Usage:
    python models/train.py
"""

import os
import sys
import glob
import pandas as pd
import numpy as np
import joblib
from loguru import logger
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HISTORICAL_DIR = "./data/historical"
MODEL_PATH = "./models/random_forest_v1.joblib"
FORM_WINDOW = 5      # last N matches for form features
DATE_CUTOFF = "2018-01-01"  # ignore older matches


# ─── Data Loading ────────────────────────────────────────────────────────────

def load_all_csvs() -> pd.DataFrame:
    """Load and combine every CSV in data/historical/ - handles both
    football-data.co.uk formats (main leagues + extra leagues)."""
    files = glob.glob(os.path.join(HISTORICAL_DIR, "*.csv"))
    if not files:
        logger.error(f"No CSVs found in {HISTORICAL_DIR}. Download from football-data.co.uk first.")
        sys.exit(1)

    # Extra-league format -> main format column mapping
    RENAME_MAP = {
        "Home": "HomeTeam",
        "Away": "AwayTeam",
        "HG": "FTHG",
        "AG": "FTAG",
        "Res": "FTR",
        "PH": "B365H", "PD": "B365D", "PA": "B365A",
    }

    required = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]
    frames = []

    for f in files:
        try:
            df = pd.read_csv(f, encoding="utf-8", on_bad_lines="skip")
        except UnicodeDecodeError:
            df = pd.read_csv(f, encoding="latin-1", on_bad_lines="skip")

        df = df.rename(columns=RENAME_MAP)

        if not all(col in df.columns for col in required):
            logger.warning(f"Skipping {os.path.basename(f)} - unrecognized format")
            continue

        df = df.dropna(subset=required)
        df = df.copy()  # defragment
        df["source_file"] = os.path.basename(f)
        frames.append(df)
        logger.info(f"Loaded {os.path.basename(f)}: {len(df)} matches")

    combined = pd.concat(frames, ignore_index=True)

    combined["Date"] = pd.to_datetime(combined["Date"], dayfirst=True, format="mixed", errors="coerce")
    combined = combined.dropna(subset=["Date"])
    combined = combined[combined["Date"] >= DATE_CUTOFF]  # recent era only
    combined = combined.sort_values("Date").reset_index(drop=True)

    logger.success(f"✅ Combined dataset: {len(combined)} matches from {len(frames)} files (since {DATE_CUTOFF})")
    return combined


# ─── Feature Engineering ────────────────────────────────────────────────────

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each match, compute features using ONLY data available before kickoff.
    No data leakage from the future.
    """
    logger.info("Building features (this takes a minute on large datasets)...")

    team_history = {}  # team -> list of past match records (date-ordered)
    features = []

    for idx, row in df.iterrows():
        home, away = row["HomeTeam"], row["AwayTeam"]

        h_hist = team_history.get(home, [])
        a_hist = team_history.get(away, [])

        feat = {
            # Form: points per game over last N matches
            "home_form_ppg": form_ppg(h_hist, FORM_WINDOW),
            "away_form_ppg": form_ppg(a_hist, FORM_WINDOW),

            # Goals scored/conceded per game over last N
            "home_goals_for_avg": goals_avg(h_hist, FORM_WINDOW, "for"),
            "home_goals_against_avg": goals_avg(h_hist, FORM_WINDOW, "against"),
            "away_goals_for_avg": goals_avg(a_hist, FORM_WINDOW, "for"),
            "away_goals_against_avg": goals_avg(a_hist, FORM_WINDOW, "against"),

            # Venue-specific form
            "home_home_ppg": venue_ppg(h_hist, FORM_WINDOW, "home"),
            "away_away_ppg": venue_ppg(a_hist, FORM_WINDOW, "away"),

            # Head to head
            "h2h_home_wins": h2h_wins(h_hist, away, 5),

            # Used for filtering only - dropped before training
            "home_matches_played": len(h_hist),
            "away_matches_played": len(a_hist),
        }

        # Odds features (closing odds are highly predictive)
        feat.update(extract_odds(row))

        feat["target"] = row["FTR"]
        features.append(feat)

        # Update history AFTER computing features (no leakage)
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

    feat_df = pd.DataFrame(features)

    # Filter out early-season matches with thin form data
    feat_df = feat_df[
        (feat_df["home_matches_played"] >= FORM_WINDOW) &
        (feat_df["away_matches_played"] >= FORM_WINDOW)
    ].reset_index(drop=True)

    # Drop the filter columns - not model features
    feat_df = feat_df.drop(columns=["home_matches_played", "away_matches_played"])

    logger.success(f"✅ Feature matrix: {len(feat_df)} matches x {len(feat_df.columns)-1} features")
    return feat_df


def form_ppg(history: list, n: int) -> float:
    recent = history[-n:]
    if not recent:
        return 0.0
    pts = sum(3 if m["result"] == "W" else (1 if m["result"] == "D" else 0) for m in recent)
    return pts / len(recent)


def goals_avg(history: list, n: int, direction: str) -> float:
    recent = history[-n:]
    if not recent:
        return 0.0
    key = "goals_for" if direction == "for" else "goals_against"
    return sum(m[key] for m in recent) / len(recent)


def venue_ppg(history: list, n: int, venue: str) -> float:
    venue_matches = [m for m in history if m["venue"] == venue][-n:]
    if not venue_matches:
        return 0.0
    pts = sum(3 if m["result"] == "W" else (1 if m["result"] == "D" else 0) for m in venue_matches)
    return pts / len(venue_matches)


def h2h_wins(history: list, opponent: str, n: int) -> int:
    meetings = [m for m in history if m["opponent"] == opponent][-n:]
    return sum(1 for m in meetings if m["result"] == "W")


def extract_odds(row) -> dict:
    """Pull odds - try Bet365 first, then average odds columns"""
    for h, d, a in [("B365H", "B365D", "B365A"), ("AvgH", "AvgD", "AvgA"),
                    ("BbAvH", "BbAvD", "BbAvA")]:
        if h in row.index and pd.notna(row.get(h)):
            try:
                oh, od, oa = float(row[h]), float(row[d]), float(row[a])
                ih, idr, ia = 1/oh, 1/od, 1/oa
                total = ih + idr + ia
                return {
                    "odds_home_prob": ih / total,
                    "odds_draw_prob": idr / total,
                    "odds_away_prob": ia / total,
                }
            except (ValueError, ZeroDivisionError):
                continue
    return {"odds_home_prob": 0.33, "odds_draw_prob": 0.33, "odds_away_prob": 0.33}


# ─── Training ────────────────────────────────────────────────────────────────

def train():
    df = load_all_csvs()
    feat_df = build_features(df)

    X = feat_df.drop(columns=["target"])
    y = feat_df["target"]

    # Time-aware split: train on older matches, test on most recent 20%
    split_idx = int(len(X) * 0.8)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    logger.info(f"Training on {len(X_train)} matches, testing on {len(X_test)}")

    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=12,
        min_samples_leaf=20,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    probs = model.predict_proba(X_test)
    acc = accuracy_score(y_test, preds)

    print("\n" + "=" * 60)
    print(f"OVERALL ACCURACY: {acc:.1%}")
    print("=" * 60)
    print("\nBaselines:")
    print("  Always predict Home Win: ~43-46%")
    print("  Bookmaker favourite:     ~53-55%")
    print()
    print(classification_report(y_test, preds, target_names=model.classes_))

    # ── The number that actually matters: accuracy by confidence tier ──
    max_probs = probs.max(axis=1)
    print("ACCURACY BY CONFIDENCE TIER (this is your product):")
    print(f"{'Threshold':>10} {'Matches':>9} {'% of all':>9} {'Accuracy':>9}")
    for thresh in [0.40, 0.45, 0.50, 0.55, 0.60, 0.65]:
        mask = max_probs >= thresh
        n = mask.sum()
        if n == 0:
            continue
        tier_acc = accuracy_score(y_test[mask], preds[mask])
        print(f"{thresh:>10.0%} {n:>9} {n/len(y_test):>8.1%} {tier_acc:>9.1%}")

    importance = pd.Series(model.feature_importances_, index=X.columns).sort_values(ascending=False)
    print("\nTOP FEATURES:")
    for name, score in importance.head(8).items():
        print(f"  {name:30s} {score:.3f}")

    joblib.dump({"model": model, "columns": list(X.columns)}, MODEL_PATH)
    logger.success(f"💾 Model saved to {MODEL_PATH}")


if __name__ == "__main__":
    train()