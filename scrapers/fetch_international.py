"""
fetch_international.py
Downloads the open international football results dataset (1872-present,
updated continuously) and converts it into football-data.co.uk format so
the existing train/predict pipeline handles national teams automatically.

Source: github.com/martj42/international_results (open dataset)

Usage:
    python scrapers/fetch_international.py
    # then retrain:
    python models/train.py
"""

import os
import sys
import pandas as pd
import requests
from loguru import logger

DATASET_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
OUTPUT_PATH = "./data/historical/InternationalResults.csv"
DATE_CUTOFF = "2014-01-01"  # keep ~3 World Cup cycles of history

# Only competitive matches - friendlies are poor predictors (rotated squads,
# experimental lineups). Comment entries out to include more.
COMPETITIVE_KEYWORDS = [
    "FIFA World Cup",          # includes qualifiers
    "UEFA Euro",
    "Copa América",
    "Africa Cup of Nations",
    "AFC Asian Cup",
    "CONCACAF",
    "UEFA Nations League",
    "African Cup of Nations qualification",
]


def is_competitive(tournament: str) -> bool:
    return any(k.lower() in str(tournament).lower() for k in COMPETITIVE_KEYWORDS)


def run():
    logger.info("Downloading international results dataset...")
    r = requests.get(DATASET_URL, timeout=30)
    r.raise_for_status()

    os.makedirs("./data/raw", exist_ok=True)
    raw_path = "./data/raw/international_raw.csv"
    with open(raw_path, "wb") as f:
        f.write(r.content)

    df = pd.read_csv(raw_path)
    logger.info(f"Downloaded {len(df)} international matches")

    # Drop fixtures not yet played (NA scores) - those are upcoming WC games
    df = df.dropna(subset=["home_score", "away_score"])

    # Recent era + competitive matches only
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df = df[df["date"] >= DATE_CUTOFF]
    df = df[df["tournament"].apply(is_competitive)]

    logger.info(f"After filtering (since {DATE_CUTOFF}, competitive only): {len(df)} matches")

    # Convert to football-data.co.uk format
    out = pd.DataFrame({
        "Div": "INT",
        "Date": df["date"].dt.strftime("%d/%m/%Y"),
        "HomeTeam": df["home_team"],
        "AwayTeam": df["away_team"],
        "FTHG": df["home_score"].astype(int),
        "FTAG": df["away_score"].astype(int),
    })
    out["FTR"] = out.apply(
        lambda r: "H" if r["FTHG"] > r["FTAG"] else ("A" if r["FTAG"] > r["FTHG"] else "D"),
        axis=1,
    )

    os.makedirs("./data/historical", exist_ok=True)
    out.to_csv(OUTPUT_PATH, index=False)
    logger.success(f"💾 Saved {len(out)} matches to {OUTPUT_PATH}")
    print("\nNext steps:")
    print("  1. Retrain the model:   python models/train.py")
    print("  2. Re-run predictions:  python models/predict.py")
    print("\nNational teams (incl. World Cup) are now part of the pipeline.")


if __name__ == "__main__":
    run()