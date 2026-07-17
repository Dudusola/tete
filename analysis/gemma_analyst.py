"""
Gemma 3 via Ollama - Match Analysis Layer
Takes ML model output + match context -> human-readable analysis

Usage:
    python analysis/gemma_analyst.py
"""

import os
import requests
from loguru import logger

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")


def check_ollama_running() -> bool:
    """Check if Ollama is running and list available models"""
    try:
        response = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        if response.status_code == 200:
            models = [m["name"] for m in response.json().get("models", [])]
            logger.info(f"Available Ollama models: {models}")
            return True
        return False
    except requests.ConnectionError:
        logger.error("Ollama is not running. Start it with: ollama serve")
        return False


def analyze_match(match_context: dict) -> str:
    """Send match context to Gemma 3 for analysis via /api/chat."""
    prompt = build_prompt(match_context)

    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 300,
                }
            },
            timeout=120
        )
        response.raise_for_status()
        result = response.json()
        return result.get("message", {}).get("content", "Analysis unavailable").strip()

    except requests.RequestException as e:
        logger.error(f"Ollama request failed: {e}")
        return "Analysis unavailable at this time."


def build_prompt(ctx: dict) -> str:
    """Build a structured prompt. We give Gemma the DATA, not ask it to recall facts."""
    home = ctx.get("home_team", "Home Team")
    away = ctx.get("away_team", "Away Team")
    league = ctx.get("league", "Unknown League")

    ml_home = ctx.get("home_win_prob", 0)
    ml_draw = ctx.get("draw_prob", 0)
    ml_away = ctx.get("away_win_prob", 0)

    fb_home = ctx.get("home_win_pct", "N/A")
    fb_draw = ctx.get("draw_pct", "N/A")
    fb_away = ctx.get("away_win_pct", "N/A")

    predicted_score = ctx.get("predicted_score", "N/A")
    over_2_5 = ctx.get("over_2_5_pct", "N/A")
    confidence = ctx.get("confidence", "MEDIUM")
    best_bet = ctx.get("best_bet", "Home Win")
    best_bet_prob = ctx.get("best_bet_prob", 0)

    prompt = f"""You are a sports betting analyst. Analyze this upcoming match and give a brief, clear prediction.

MATCH: {home} vs {away}
LEAGUE: {league}

MODEL PROBABILITIES:
- {home} Win: {ml_home:.1f}%
- Draw: {ml_draw:.1f}%
- {away} Win: {ml_away:.1f}%

FOREBET STATISTICAL PREDICTION:
- {home} Win: {fb_home}%
- Draw: {fb_draw}%
- {away} Win: {fb_away}%
- Predicted Score: {predicted_score}
- Over 2.5 Goals Probability: {over_2_5}%

BEST BET: {best_bet} ({best_bet_prob:.1f}% probability)
CONFIDENCE: {confidence}

Write a 3-sentence analysis that:
1. States the most likely outcome and why
2. Mentions the key factor that drives this prediction
3. Gives one clear betting recommendation

Keep it simple - write for someone who understands football but not statistics.
Do NOT mention model names or percentages directly. Just give clear, confident reasoning."""

    return prompt


def batch_analyze(matches: list) -> list:
    """Analyze multiple matches, add gemma_analysis field to each"""
    results = []
    for i, match in enumerate(matches):
        logger.info(f"Analyzing {i+1}/{len(matches)}: {match.get('home_team')} vs {match.get('away_team')}")
        match["gemma_analysis"] = analyze_match(match)
        results.append(match)
    return results


if __name__ == "__main__":
    print("Checking Ollama connection...")
    if not check_ollama_running():
        print(f"\nMake sure Ollama is running and you have: ollama pull {OLLAMA_MODEL}")
        exit(1)

    test_match = {
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "league": "English Premier League",
        "home_win_prob": 52.3,
        "draw_prob": 24.1,
        "away_win_prob": 23.6,
        "home_win_pct": 55.0,
        "draw_pct": 22.0,
        "away_win_pct": 23.0,
        "predicted_score": "2 - 1",
        "over_2_5_pct": 61.0,
        "confidence": "MEDIUM",
        "best_bet": "Home Win",
        "best_bet_prob": 52.3,
    }

    print(f"\nAnalyzing: {test_match['home_team']} vs {test_match['away_team']}")
    print("-" * 50)
    print(analyze_match(test_match))