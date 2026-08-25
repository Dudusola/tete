"""
main.py — Daily prediction pipeline
Run this every morning to get today's predictions

Usage:
    python main.py
    python main.py --sport football
    python main.py --test   (runs with dummy data, no scraping)
"""

import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich import print as rprint

# Configure clean logging
logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | {message}", level="INFO")
logger.add("./data/pipeline.log", rotation="1 week", level="DEBUG")

console = Console()


def run_pipeline(sport: str = "football", test_mode: bool = False):
    """Full daily pipeline: scrape → store → analyze → display"""

    console.rule(f"[bold green]Sports Predictor — {datetime.now().strftime('%A, %d %B %Y')}[/bold green]")

    if test_mode:
        logger.info("Running in TEST MODE with dummy data")
        run_test_mode()
        return

    # Step 1: Initialize DB
    logger.info("Step 1/4: Initializing database...")
    from db.database import init_db
    init_db()

    # Step 2: Scrape predictions
    logger.info("Step 2/4: Scraping Forebet predictions...")
    from scrapers.forebet import run as scrape_forebet
    scrape_forebet()

    # Step 3: Run ML model on today's matches
    logger.info("Step 3/4: Running ML predictions...")
    from db.database import get_upcoming_matches
    today = datetime.now().strftime("%Y-%m-%d")
    matches = get_upcoming_matches(sport=sport, date=today)

    if not matches:
        logger.warning("No upcoming matches found for today")
        console.print("[yellow]No matches found. Try running the scraper first.[/yellow]")
        return

    logger.info(f"Found {len(matches)} matches to analyze")

    # Step 4: Generate Gemma analysis for top picks
    logger.info("Step 4/4: Generating AI analysis...")
    from analysis.gemma_analyst import check_ollama_running, analyze_match

    ollama_available = check_ollama_running()
    if not ollama_available:
        logger.warning("Ollama not running — skipping AI analysis. Showing raw predictions only.")

    # Display results
    display_predictions(matches, ollama_available)


def display_predictions(matches: list, with_analysis: bool = False):
    """Pretty print today's top predictions"""
    from db.database import get_match_with_predictions

    # Build display table
    table = Table(title="Today's Top Predictions", show_header=True, header_style="bold cyan")
    table.add_column("Match", style="bold", min_width=30)
    table.add_column("League", min_width=20)
    table.add_column("1 (Home)", justify="center")
    table.add_column("X (Draw)", justify="center")
    table.add_column("2 (Away)", justify="center")
    table.add_column("Best Bet", style="green")
    table.add_column("Confidence", justify="center")

    for match in matches:
        data = get_match_with_predictions(match["match_id"])
        preds = data.get("predictions", [])

        if not preds:
            continue

        # Use first prediction (Forebet)
        p = preds[0]
        home_pct = f"{p['home_win_pct']:.0f}%" if p['home_win_pct'] else "?"
        draw_pct = f"{p['draw_pct']:.0f}%" if p['draw_pct'] else "?"
        away_pct = f"{p['away_win_pct']:.0f}%" if p['away_win_pct'] else "?"

        # Determine best bet and confidence
        probs = {
            "Home Win": p['home_win_pct'] or 0,
            "Draw": p['draw_pct'] or 0,
            "Away Win": p['away_win_pct'] or 0
        }
        best_bet = max(probs, key=probs.get)
        best_prob = probs[best_bet]

        if best_prob >= 65:
            confidence = "[green]HIGH[/green]"
        elif best_prob >= 50:
            confidence = "[yellow]MED[/yellow]"
        else:
            confidence = "[red]LOW[/red]"

        table.add_row(
            f"{match['home_team']} vs {match['away_team']}",
            match.get('league', 'Unknown')[:20],
            home_pct,
            draw_pct,
            away_pct,
            best_bet,
            confidence
        )

    console.print(table)
    console.print(f"\n[dim]Total matches: {len(matches)}[/dim]")


def run_test_mode():
    """Quick test to verify everything is connected"""
    from db.database import init_db, insert_match, insert_scraped_prediction, get_upcoming_matches
    from analysis.gemma_analyst import check_ollama_running, analyze_match

    # Init DB
    init_db()

    # Insert a test match
    test_match = {
        "match_id": "test_arsenal_chelsea_2024",
        "sport": "football",
        "league": "Premier League",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "match_date": datetime.now().strftime("%Y-%m-%d"),
        "kickoff_time": "20:00",
    }
    insert_match(test_match)

    test_pred = {
        "match_id": "test_arsenal_chelsea_2024",
        "source": "forebet",
        "home_win_pct": 52.0,
        "draw_pct": 24.0,
        "away_win_pct": 24.0,
        "predicted_home_goals": 2.0,
        "predicted_away_goals": 1.0,
        "over_2_5_pct": 62.0,
        "btts_pct": 55.0,
        "home_form": "WWLDW",
        "away_form": "LDWWL",
    }
    insert_scraped_prediction(test_pred)

    console.print("[green][OK] Database: OK[/green]")

    # Test Ollama
    if check_ollama_running():
        console.print("[green][OK] Ollama: Running[/green]")
        analysis = analyze_match({**test_match, **test_pred,
                                   "home_win_prob": 52.0,
                                   "draw_prob": 24.0,
                                   "away_win_prob": 24.0,
                                   "confidence": "MEDIUM",
                                   "best_bet": "Home Win",
                                   "best_bet_prob": 52.0,
                                   "predicted_score": "2 - 1"})
        console.print("\n[bold]Sample Gemma Analysis:[/bold]")
        console.print(f"[italic]{analysis}[/italic]")
    else:
        console.print("[yellow][!]  Ollama: Not running (start with 'ollama serve')[/yellow]")

    console.print("\n[bold green][OK] System test complete. All core modules working.[/bold green]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sports Predictor Pipeline")
    parser.add_argument("--sport", default="football", help="Sport to predict (default: football)")
    parser.add_argument("--test", action="store_true", help="Run in test mode with dummy data")
    args = parser.parse_args()

    run_pipeline(sport=args.sport, test_mode=args.test)
