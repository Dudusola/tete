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
from dotenv import load_dotenv
from loguru import logger

# Callers (predict.py, main.py) load .env themselves, but this module is also
# run directly (--list-models, smoke test) — without this the API key silently
# reads as empty and the provider falls back to Ollama for no visible reason.
load_dotenv()

# --- Ollama (local fallback) ---------------------------------------------
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")

# --- Gemini (hosted, free tier) ------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Thinking models (gemini-3.x-flash, -pro) spend ~760 tokens reasoning BEFORE
# emitting prose, and those count against this cap — at 800 the analysis is
# truncated mid-sentence with finishReason=MAX_TOKENS. The *-lite models don't
# think at all (thoughts=0), so 800 is plenty. Raise to ~2000 if you switch to
# a thinking model. Verified against the live API, Aug 2026.
GEMINI_MAX_TOKENS = int(os.getenv("GEMINI_MAX_TOKENS", "800"))

# ─── Groq: the CLOUD fallback ────────────────────────────────────────────────
# Ollama was the only fallback, and it runs on a laptop — useless to a pipeline
# that executes in GitHub Actions. So a Gemini outage meant no analysis at all,
# with nothing to take over.
#
# Groq serves OPEN-WEIGHT models (Llama and friends) on a free tier with an
# OpenAI-compatible API, which is why it was chosen over another proprietary
# endpoint: if it disappears the same code points at any OpenAI-compatible
# host by changing one base URL.
#
# Safe to swap freely because the analysis is EXPLANATION ONLY and never the
# predictor — no model choice here can move a probability or a pick.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
GROQ_BASE = os.getenv("GROQ_BASE", "https://api.groq.com/openai/v1").strip()

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
    if AI_PROVIDER == "groq":
        return "groq"
    if AI_PROVIDER == "ollama":
        return "ollama"
    # auto: prefer Gemini, then the cloud fallback, then local.
    if GEMINI_API_KEY:
        return "gemini"
    if GROQ_API_KEY:
        return "groq"
    return "ollama"


# =========================================================================
#  Availability
# =========================================================================

def check_ollama_running() -> bool:
    """Is the analysis layer usable — by ANY provider in the chain?

    Name kept for backwards compatibility: predict.py, backfill_analysis.py
    and main.py call it as their 'can we write analysis?' gate.

    THE BUG THIS FIXES (11 Aug 2026). The gate used to check only the PRIMARY
    provider. analyze_match() fails over Gemini -> Groq -> Ollama at call
    time, but nothing ever reached that code when Gemini was unhealthy,
    because the gate had already returned False and the caller skipped
    analysis entirely. So a Gemini outage produced ZERO analysis while a
    perfectly working Groq sat unused — the fallback was real, unreachable,
    and would only have been discovered by noticing blank explanations.

    Verified before the fix: with a broken GEMINI_API_KEY the gate returned
    False, while analyze_match() on the same key produced text via Groq.

    A gate in front of a failover chain must ask the same question the chain
    answers: "can ANYONE serve this?", not "is my first choice healthy?".
    """
    provider = active_provider()

    # Try the active provider first, then everything it would fall back to,
    # in the same order analyze_match() uses.
    if provider == "gemini" and _check_gemini():
        return True
    if provider in ("gemini", "groq") and GROQ_API_KEY:
        # Groq has no cheap health endpoint worth a round trip; a key present
        # is the signal, and a dead call still falls through to Ollama.
        logger.info("Analysis provider: Groq (fallback)"
                    if provider == "gemini" else "Analysis provider: Groq")
        return True
    if _check_ollama():
        return True

    logger.error("No analysis provider is reachable (Gemini, Groq, Ollama all "
                 "unavailable) - predictions will be saved without analysis")
    return False


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
    provider = active_provider()

    if provider == "gemini":
        text = _analyze_gemini(prompt)
        # Actually FAIL OVER rather than just having a fallback configured.
        # A provider that is only consulted when the key is missing is no use
        # during an outage, which is the case that matters.
        if text == UNAVAILABLE and GROQ_API_KEY:
            logger.warning("Gemini returned nothing - falling back to Groq")
            return _analyze_groq(prompt)
        return text

    if provider == "groq":
        text = _analyze_groq(prompt)
        if text == UNAVAILABLE and check_ollama_running():
            logger.warning("Groq returned nothing - falling back to Ollama")
            return _analyze_ollama(prompt)
        return text

    return _analyze_ollama(prompt)


def _analyze_groq(prompt: str) -> str:
    """One analysis via Groq's OpenAI-compatible endpoint."""
    if not GROQ_API_KEY:
        return UNAVAILABLE
    try:
        r = requests.post(
            f"{GROQ_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": GROQ_MODEL,
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.3, "max_tokens": 300},
            timeout=60)
        r.raise_for_status()
        content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content")
        return (content or UNAVAILABLE).strip()
    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        logger.error(f"Groq request failed: {e}")
        return UNAVAILABLE


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
                    "maxOutputTokens": GEMINI_MAX_TOKENS,
                    # Do NOT add thinkingConfig here: thinkingBudget=0 is
                    # rejected outright by the 3.x models (HTTP 400 "invalid
                    # argument"), and thinkingLevel isn't a field in v1beta.
                    # Budget the tokens instead — see GEMINI_MAX_TOKENS.
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

        if r.status_code >= 400:
            # The API puts the ACTUAL reason in the body; the status alone is
            # useless ("400 Bad Request" told us nothing about thinkingBudget).
            try:
                reason = r.json().get("error", {}).get("message", "")[:200]
            except ValueError:
                reason = r.text[:200]
            logger.error(f"Gemini HTTP {r.status_code} for '{GEMINI_MODEL}': {reason}")
            return UNAVAILABLE

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
                f"{cand.get('finishReason')}). If MAX_TOKENS, this model thinks "
                f"before answering — raise GEMINI_MAX_TOKENS (currently "
                f"{GEMINI_MAX_TOKENS}) to ~2000 or use a *-lite model."
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

    # An alternate framing for views that ask a DIFFERENT question of the same
    # numbers. The Draws tab is the case: the default prompt ends "give one
    # clear betting recommendation" and the model duly recommends the
    # favourite, which is the opposite of what that screen is for.
    #
    # Only the instruction changes — the data block below is identical, so the
    # rule that the LLM explains OUR numbers and never sources its own still
    # holds, and every caller keeps the same failover chain.
    if ctx.get("custom_instruction"):
        return f"""You are a sports betting analyst. Analyse this upcoming match.

MATCH: {home} vs {away}
LEAGUE: {league}

MODEL PROBABILITIES:
- {home} Win: {ml_home:.1f}%
- Draw: {ml_draw:.1f}%
- {away} Win: {ml_away:.1f}%

{ctx["custom_instruction"]}

Write for someone who understands football but not statistics. Do NOT mention
model names or quote the percentages directly."""

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
