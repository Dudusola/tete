"""
Match Analysis Layer — the REASONING layer, never the predictor.

Takes ML model output + match context -> human-readable analysis.

Providers (Aug 2026): Google Gemini (hosted, free tier) or Ollama (local).
Gemma via Ollama was the original and still works; it is kept as the offline
fallback. The module name stays gemma_analyst for import compatibility.

    AI_PROVIDER=auto     -> Gemini if GEMINI_API_KEY is set, else Ollama
    AI_PROVIDER=gemini   -> force Gemini (fails loudly if no key)
    AI_PROVIDER=ollama   -> force local Ollama

Why hosted: Ollama needs a machine with the model resident, which no free host
provides. Moving the analysis to a hosted free tier is what lets the whole
pipeline run off this laptop.

Usage:
    python analysis/gemma_analyst.py                # smoke-test one match
    python analysis/gemma_analyst.py --list-models  # what this key can reach
"""

import os
import time

import requests
from loguru import logger

# --- Ollama (local fallback) ---------------------------------------------
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")

# --- Gemini (hosted, free tier) ------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

AI_PROVIDER = os.getenv("AI_PROVIDER", "auto").strip().lower()

# Free tiers are rate-limited per minute. The pipeline analyses ~45 matches in a
# burst, which trips that instantly, so space the calls and back off on 429.
GEMINI_MIN_INTERVAL = float(os.getenv("GEMINI_MIN_INTERVAL", "4.5"))  # seconds
_last_gemini_call = 0.0

UNAVAILABLE = "Analysis unavailable at this time."


def active_provider() -> str:
    """Which provider this process will actually use."""
    if AI_PROVIDER == "gemini":
        return "gemini"
    if AI_PROVIDER == "ollama":
        return "ollama"
    return "gemini" if GEMINI_API_KEY else "ollama"


# =========================================================================
#  Availability
# =========================================================================

def check_ollama_running() -> bool:
    """Is the analysis layer usable?

    Name kept for backwards compatibility — predict.py and main.py call it as
    their 'can we write analysis?' gate. It now checks whichever provider is
    active, not just Ollama.
    """
    provider = active_provider()
    if provider == "gemini":
        return _check_gemini()
    return _check_ollama()


def _check_ollama() -> bool:
    try:
        response = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        if response.status_code == 200:
            models = [m["name"] for m in response.json().get("models", [])]
            logger.info(f"Analysis provider: Ollama (local). Models: {models}")
            return True
        return False
    except requests.ConnectionError:
        logger.error("Ollama is not running. Start it with: ollama serve")
        return False


def _check_gemini() -> bool:
    if not GEMINI_API_KEY:
        logger.error(
            "AI_PROVIDER=gemini but GEMINI_API_KEY is empty. "
            "Get a free key at https://aistudio.google.com/apikey"
        )
        return False
    try:
        r = requests.get(
            f"{GEMINI_BASE}/models/{GEMINI_MODEL}",
            headers={"x-goog-api-key": GEMINI_API_KEY},
            timeout=15,
        )
        if r.status_code == 200:
            logger.info(f"Analysis provider: Gemini ({GEMINI_MODEL})")
            return True
        logger.error(
            f"Gemini model '{GEMINI_MODEL}' unreachable (HTTP {r.status_code}). "
            "Run: python analysis/gemma_analyst.py --list-models"
        )
        return False
    except requests.RequestException as e:
        logger.error(f"Gemini check failed: {e}")
        return False


def list_gemini_models() -> list:
    """Ask the API which models this key can actually use, so the model name is
    never a guess. Free-tier availability changes; this is the source of truth."""
    if not GEMINI_API_KEY:
        logger.error("No GEMINI_API_KEY set.")
        return []
    try:
        r = requests.get(
            f"{GEMINI_BASE}/models",
            headers={"x-goog-api-key": GEMINI_API_KEY},
            timeout=20,
        )
        r.raise_for_status()
        out = []
        for m in r.json().get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                out.append(m.get("name", "").replace("models/", ""))
        return sorted(out)
    except requests.RequestException as e:
        logger.error(f"Could not list Gemini models: {e}")
        return []


# =========================================================================
#  Analysis
# =========================================================================

def analyze_match(match_context: dict) -> str:
    """Turn model output + context into 3 sentences of plain-English reasoning."""
    prompt = build_prompt(match_context)
    if active_provider() == "gemini":
        return _analyze_gemini(prompt)
    return _analyze_ollama(prompt)


def _analyze_ollama(prompt: str) -> str:
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 300},
            },
            timeout=120,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("message", {}).get("content", UNAVAILABLE).strip()
    except requests.RequestException as e:
        logger.error(f"Ollama request failed: {e}")
        return UNAVAILABLE


def _analyze_gemini(prompt: str, _attempt: int = 0) -> str:
    """One analysis via Gemini, with free-tier pacing and 429 backoff."""
    global _last_gemini_call

    gap = time.monotonic() - _last_gemini_call
    if gap < GEMINI_MIN_INTERVAL:
        time.sleep(GEMINI_MIN_INTERVAL - gap)

    try:
        r = requests.post(
            f"{GEMINI_BASE}/models/{GEMINI_MODEL}:generateContent",
            headers={
                "x-goog-api-key": GEMINI_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 500,
                    # 2.5-series models "think" before answering, and thinking
                    # tokens count against maxOutputTokens — leave this on and a
                    # short budget gets eaten before any prose is emitted,
                    # returning empty text with finishReason MAX_TOKENS.
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
            timeout=90,
        )
        _last_gemini_call = time.monotonic()

        # Free tier: 429 = per-minute quota. Worth one patient retry.
        if r.status_code == 429 and _attempt < 2:
            wait = 30 * (_attempt + 1)
            logger.warning(f"Gemini rate-limited; retrying in {wait}s")
            time.sleep(wait)
            return _analyze_gemini(prompt, _attempt + 1)

        r.raise_for_status()
        data = r.json()

        candidates = data.get("candidates") or []
        if not candidates:
            # Safety filters can return zero candidates; log why, don't crash.
            logger.warning(f"Gemini returned no candidates: {data.get('promptFeedback')}")
            return UNAVAILABLE

        cand = candidates[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()

        if not text:
            logger.warning(
                f"Gemini returned empty text (finishReason="
                f"{cand.get('finishReason')}) — raise maxOutputTokens if this persists"
            )
            return UNAVAILABLE
        return text

    except requests.RequestException as e:
        logger.error(f"Gemini request failed: {e}")
        return UNAVAILABLE


def build_prompt(ctx: dict) -> str:
    """Build a structured prompt. We give the model the DATA, never ask it to
    recall facts — it explains our numbers, it does not source its own."""
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
    import sys

    if "--list-models" in sys.argv:
        models = list_gemini_models()
        if models:
            print(f"Models available to this key ({len(models)}):")
            for m in models:
                marker = "  <- configured" if m == GEMINI_MODEL else ""
                print(f"  {m}{marker}")
        else:
            print("No models listed — check GEMINI_API_KEY.")
        sys.exit(0)

    print(f"Provider: {active_provider()}")
    if not check_ollama_running():
        if active_provider() == "gemini":
            print("\nSet GEMINI_API_KEY in .env (free key: https://aistudio.google.com/apikey)")
        else:
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
