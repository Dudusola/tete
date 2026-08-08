"""
run_daily.py - The one-command daily pipeline.

Runs in sequence:
    1. Scrape Forebet fixtures (today + tomorrow)
    2. Scrape PredictZ fixtures (second source)
    3. Score yesterday's finished matches (results tracker)
    4. Generate model predictions (with Gemma analysis for top picks)
    5. Cross-source consensus report (Forebet vs PredictZ agreement)

Usage:
    python run_daily.py
    python run_daily.py --no-gemma     (skip AI analysis, faster)

Designed to be run by Windows Task Scheduler every morning.
Exit code 0 = success, 1 = something failed (visible in Task Scheduler history).
"""

import argparse
import sqlite3
import subprocess
import sys
import os
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

# Keep our own emoji/box-drawing output from crashing on a cp1252 console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | {message}", level="INFO")
logger.add("./data/daily_pipeline.log", rotation="1 week", level="INFO")


STEP_TIMEOUT = 15 * 60   # a hung Playwright once ate 19 hours — never again
failed_steps: list[str] = []


def run_step(name: str, args: list, required: bool = True) -> bool:
    """Run a pipeline step. If required=False, a failure won't fail the pipeline."""
    logger.info(f"▶ {name}...")
    try:
        # Force UTF-8 in children: Windows console is cp1252 and any emoji in
        # a child's print/log otherwise raises UnicodeEncodeError -> exit 1
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        result = subprocess.run([sys.executable] + args, cwd=ROOT,
                                timeout=STEP_TIMEOUT, env=env)
        code = result.returncode
    except subprocess.TimeoutExpired:
        logger.error(f"❌ {name} timed out after {STEP_TIMEOUT // 60} min - killed")
        code = -1
    if code == 0:
        logger.success(f"✅ {name} done")
        return True
    failed_steps.append(name)
    if required:
        logger.error(f"❌ {name} failed (exit {code})")
    else:
        logger.warning(f"⚠️  {name} failed (exit {code}) - continuing anyway")
    return False


def send_telegram(text: str):
    """Alert Boss on Telegram. Best-effort - never fails the pipeline."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text}, timeout=15)
    except requests.RequestException as e:
        logger.warning(f"Telegram alert failed: {e}")


def ledger_health() -> list[str]:
    """Check the DB actually grew. Silent stalls are worse than loud failures."""
    problems = []
    try:
        conn = sqlite3.connect(os.getenv("DB_PATH", "./db/sports_predictor.db"))
        today = datetime.now().strftime("%Y-%m-%d")
        upcoming = conn.execute(
            "SELECT COUNT(*) FROM matches WHERE match_date >= ?", [today]
        ).fetchone()[0]
        cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        recent_results = conn.execute(
            "SELECT COUNT(*) FROM results WHERE recorded_at >= ?", [cutoff]
        ).fetchone()[0]
        conn.close()
        if upcoming == 0:
            problems.append("no upcoming fixtures in DB - both scrapers likely blocked")
        if recent_results == 0:
            problems.append("no results recorded in 3 days - the ledger is stalled")
    except sqlite3.Error as e:
        problems.append(f"health check could not read DB: {e}")
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-gemma", action="store_true",
                        help="Skip Gemma AI analysis (faster)")
    args = parser.parse_args()

    logger.info(f"═══ Daily pipeline — {datetime.now().strftime('%A, %d %B %Y %H:%M')} ═══")

    ok = True

    # 1. Fresh fixtures from all sources
    #    Scrapers are flaky by nature, so they're non-required - if one source
    #    fails, the pipeline still runs on whatever data we have.
    #    Forebet is EXPECTED to fail in the cloud: Cloudflare 403s datacenter
    #    IPs (verified from GitHub Actions - plain HTTP, Chromium and Firefox
    #    all blocked). Skipped there via PP_SKIP_FOREBET so a known, unfixable
    #    block doesn't masquerade as a pipeline problem every single day.
    if os.getenv("PP_SKIP_FOREBET", "").strip() == "1":
        logger.info("⏭  Skipping Forebet (PP_SKIP_FOREBET=1 — IP-blocked here)")
    else:
        run_step("Scrape Forebet fixtures", ["scrapers/forebet.py"], required=False)
    # Training CSVs drift: by 8 Aug the year-round leagues were 19-74 days
    # behind, so the model was reasoning about Brazil/MLS/Mexico from months-old
    # form. Cheap to refresh, and only replaces a file when the download has
    # MORE rows than we hold.
    run_step("Refresh training CSVs", ["scrapers/refresh_csvs.py"], required=False)

    run_step("Scrape PredictZ fixtures", ["scrapers/predictz.py"], required=False)
    run_step("Fixtures + odds (API-Football)", ["scrapers/fixtures_api.py"],
             required=False)

    # 2. Score finished matches (two independent sources - Forebet scrape
    #    plus football-data.org API - so a Cloudflare block can't kill scoring)
    if os.getenv("PP_SKIP_FOREBET", "").strip() == "1":
        logger.info("⏭  Skipping Forebet results (same IP block)")
    else:
        run_step("Record results (Forebet)", ["scrapers/results.py"], required=False)
    run_step("Record results (football-data API)", ["scrapers/results_api.py"], required=False)
    run_step("Record results (ESPN)", ["scrapers/results_espn.py"], required=False)

    # 3. Merge cross-source duplicate fixtures BEFORE predicting, so the same
    #    real match never gets two predictions/picks
    run_step("Dedupe fixtures", ["db/dedupe_fixtures.py", "--apply"], required=False)

    # 3b. Real UTC kickoff times (ESPN, keyless). Without these the app
    #     recommends matches that already kicked off: PredictZ leaves
    #     kickoff_time at '00:00' for ~88% of fixtures, which means UNKNOWN.
    #     Runs after dedupe so merged rows get timed once, not twice.
    run_step("Kickoff times (ESPN)", ["scrapers/kickoff_times.py"], required=False)

    # 4. Model predictions (+ Gemma analysis unless skipped)
    predict_args = ["models/predict.py"]
    if args.no_gemma:
        predict_args.append("--no-gemma")
    ok &= run_step("Generate predictions", predict_args)

    # 5. Cross-source consensus report
    # Any pick still missing its written analysis — a --no-gemma run, an LLM
    # outage, or a fixture that can no longer be re-predicted because its odds
    # vanished — gets the text filled in. Never changes a prediction.
    run_step("Backfill missing analysis", ["analysis/backfill_analysis.py"],
             required=False)

    run_step("Cross-source consensus", ["models/consensus.py"], required=False)

    problems = ledger_health()
    for p in problems:
        logger.error(f"HEALTH: {p}")

    if failed_steps or problems:
        lines = ["⚠️ PotentialPro pipeline problems:"]
        lines += [f"• step failed: {s}" for s in failed_steps]
        lines += [f"• {p}" for p in problems]
        send_telegram("\n".join(lines))

    if ok and not problems:
        logger.success("═══ Pipeline complete. Picks are in the DB — bot will serve them. ═══")
        sys.exit(0)
    else:
        logger.error("═══ Pipeline finished with errors — check the log above. ═══")
        sys.exit(1)


if __name__ == "__main__":
    main()