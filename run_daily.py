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

# ─── Step severity ───────────────────────────────────────────────────────────
#
# Every step used to be required=False except "Generate predictions", and the
# exit code only ever consulted THAT one. So a failing step appended to
# failed_steps, the run exited 0, GitHub went green, and the log still printed
# "Pipeline complete". That is how db/dedupe_fixtures.py crashed on six
# consecutive runs — visible in the log every single time — without anyone
# noticing, while duplicate fixtures accumulated in the ledger.
#
# The missing idea was a middle state. "Abort the run" and "shrug and continue"
# are not the only options; most steps want "keep going, but this run is NOT a
# success".
#
#   CRITICAL  the ledger is wrong or the run is pointless without it.
#             Aborts immediately — later steps would build on bad data.
#   DEGRADED  the run continues, but it ends RED and alerts. The default:
#             a step that fails is a bug until proven otherwise.
#   OPTIONAL  known-flaky or cosmetic, with a real fallback. Logged, never red.
#             Every OPTIONAL needs a one-line justification at the call site.
#
# The bar for OPTIONAL is deliberately high. Today only five steps clear it,
# and the pipeline's last two runs had zero failures of any kind — so making
# DEGRADED the default costs no alert noise now, which is exactly why this is
# the right moment to tighten it.
CRITICAL, DEGRADED, OPTIONAL = "critical", "degraded", "optional"

critical_failures: list[str] = []
degraded_failures: list[str] = []
optional_failures: list[str] = []


class PipelineAbort(Exception):
    """A CRITICAL step failed; nothing downstream can be trusted."""


def run_step(name: str, args: list, severity: str = DEGRADED) -> bool:
    """Run a pipeline step. See the severity block above for the three levels."""
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

    if severity == CRITICAL:
        critical_failures.append(name)
        logger.error(f"❌ {name} failed (exit {code}) - ABORTING, "
                     f"anything after this would build on bad data")
        raise PipelineAbort(name)
    if severity == OPTIONAL:
        optional_failures.append(name)
        logger.warning(f"⚠️  {name} failed (exit {code}) - tolerated, "
                       f"this step has a documented fallback")
        return False
    degraded_failures.append(name)
    logger.error(f"❌ {name} failed (exit {code}) - continuing, but this run "
                 f"is NOT a success")
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
    conn = None
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
        if upcoming == 0:
            problems.append("no upcoming fixtures in DB - both scrapers likely blocked")
        if recent_results == 0:
            problems.append("no results recorded in 3 days - the ledger is stalled")
        problems += source_health(conn)
    except sqlite3.Error as e:
        problems.append(f"health check could not read DB: {e}")
    finally:
        if conn is not None:
            conn.close()
    return problems


def source_health(conn) -> list[str]:
    """A prediction source going silent must be LOUD.

    Step severity now makes a failed scraper turn the run red, but this
    check catches the case severity CANNOT: a step that exits 0 having
    written nothing. That is exactly how
    API-Football's account suspension (6 Aug 2026) went unnoticed until the
    consensus page was found empty days later - the step printed a warning
    and the run went green.

    Two distinct questions, because they fail independently:
      1. Is each source still WRITING rows at all?
      2. Do at least two sources produce USABLE opinions on upcoming matches?
         PredictZ writes plenty of rows but leaves home/draw/away pct NULL
         whenever it has no odds (190 of 228 upcoming rows on 9 Aug 2026), and
         consensus.py's _best_outcome skips a row with NULL pcts. So "rows
         landed" does NOT imply "consensus has anything to compare".
    """
    problems = []
    # apifootball is deliberately absent: the account is suspended, the step is
    # OPTIONAL, and expecting rows from it would fire a "source has gone
    # silent" alert every single day. Add it back the day the account works.
    expected = {"predictz", "espn"}
    if os.getenv("PP_SKIP_FOREBET", "").strip() != "1":
        expected.add("forebet")

    cutoff = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    fresh = {row[0] for row in conn.execute(
        "SELECT DISTINCT source FROM scraped_predictions WHERE scraped_at >= ?",
        [cutoff])}
    for src in sorted(expected - fresh):
        problems.append(f"source '{src}' has written nothing in 2 days - "
                        f"check its step in the log (a failed scraper does not "
                        f"fail the run)")

    today = datetime.now().strftime("%Y-%m-%d")
    usable = conn.execute("""
        SELECT p.source, COUNT(DISTINCT p.match_id)
        FROM scraped_predictions p
        JOIN matches m ON m.match_id = p.match_id
        WHERE m.match_date >= ?
          AND p.home_win_pct IS NOT NULL
          AND p.draw_pct IS NOT NULL
          AND p.away_win_pct IS NOT NULL
        GROUP BY p.source
    """, [today]).fetchall()
    opinionated = [s for s, n in usable if n > 0]

    # Severity by COUNT, not by a flat "fewer than 2 is bad".
    #
    #   0 sources -> the app cannot show a probability at all. Broken. Fail.
    #   1 source  -> consensus is unavailable but every other feature works.
    #                This is the KNOWN state since API-Football's account was
    #                suspended: PredictZ leaves H/D/A NULL whenever it has no
    #                odds, so ESPN is usually the only source with usable
    #                percentages. Failing the run daily for a condition we
    #                already know about is the noise that made a red build
    #                meaningless — and on 10 Aug that red build is what
    #                skipped the Telegram push. Log it; do not fail.
    #   2+        -> healthy.
    #
    # Make this a failure again the day a second opinionated source is back;
    # the Consensus page already tells USERS why it is empty via `diagnosis`.
    if not opinionated:
        problems.append(
            "NO source has usable H/D/A percentages on upcoming matches - "
            "the app cannot price anything")
    elif len(opinionated) < 2:
        logger.warning(
            f"consensus unavailable: only {', '.join(opinionated)} has usable "
            f"H/D/A percentages (need 2+ to cross-check). Known state while "
            f"API-Football is suspended - not failing the run for it.")
    return problems


def run_pipeline() -> list:
    """Execute every step in order. Returns the health problems found.

    Raises PipelineAbort if a CRITICAL step fails — main() catches it so the
    run still reports and alerts properly instead of dying with a traceback.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-gemma", action="store_true",
                        help="Skip Gemma AI analysis (faster)")
    args = parser.parse_args()

    logger.info(f"═══ Daily pipeline — {datetime.now().strftime('%A, %d %B %Y %H:%M')} ═══")

    # 0. Schema migrations. init_db() only ran from main.py and by hand, so a
    #    new column existed on whichever machine happened to run it — and the
    #    cloud, which owns the committed DB, never did. A step that writes a
    #    column the cloud has not added fails there and only there. Idempotent:
    #    every ALTER is wrapped in "already exists" handling.
    run_step("Apply DB migrations", ["db/database.py"], DEGRADED)

    # 1. Fresh fixtures from all sources
    #    Individually these are OPTIONAL/DEGRADED, but the GROUP is critical:
    #    see the gate below. One source dying is survivable; all of them dying
    #    means there is nothing to predict on.
    fixture_sources = {}

    # OPTIONAL: Forebet is EXPECTED to fail in the cloud — Cloudflare 403s
    # datacenter IPs (verified from GitHub Actions: plain HTTP, Chromium AND
    # Firefox, so it is IP reputation). Skipped there via PP_SKIP_FOREBET.
    # Locally the block is time-of-day dependent. A known, unfixable, fully
    # redundant source must not colour the run red every day.
    if os.getenv("PP_SKIP_FOREBET", "").strip() == "1":
        logger.info("⏭  Skipping Forebet (PP_SKIP_FOREBET=1 — IP-blocked here)")
    else:
        fixture_sources["forebet"] = run_step(
            "Scrape Forebet fixtures", ["scrapers/forebet.py"], OPTIONAL)

    # OPTIONAL: training CSVs come from football-data.co.uk, which lags weeks
    # behind at the source anyway. A failed refresh means the model reasons
    # from slightly older form — it does not touch the ledger.
    run_step("Refresh training CSVs", ["scrapers/refresh_csvs.py"], OPTIONAL)

    # DEGRADED: PredictZ is the ONLY fixture source that survives in the cloud
    # now that Forebet is IP-blocked. If it fails, today has no new fixtures.
    fixture_sources["predictz"] = run_step(
        "Scrape PredictZ fixtures", ["scrapers/predictz.py"], DEGRADED)

    # OPTIONAL (demoted 11 Aug 2026, Boss's call, and the evidence supports it).
    #
    # It was DEGRADED as the third independent opinion. Two accounts have now
    # been suspended — the second within hours of being wired in, well under
    # quota and with the rate-limiter in place — so it fails EVERY day. A step
    # that is red every day is not a signal, it is noise, and here it did real
    # damage: the red run made GitHub skip the Telegram push and the bot feed,
    # so a dead optional source took the whole user-facing output down with it.
    #
    # What it actually provided is already covered:
    #   fixtures -> PredictZ (305 rows in the last 3 days)
    #   odds     -> ESPN via kickoff_times.py (132 rows carrying odds)
    # What is genuinely lost is INDEPENDENCE: ESPN and PredictZ both price off
    # the same bookmaker markets, so consensus between them is two views of one
    # market rather than two opinions. That is a real cost and it is recorded
    # in CLAUDE.md — but it is not worth a daily false alarm.
    fixture_sources["apifootball"] = run_step(
        "Fixtures + odds (API-Football)", ["scrapers/fixtures_api.py"], OPTIONAL)

    # THE GROUP GATE. Per-step severity cannot express "any one of these is
    # fine, none of them is fatal" — and that combination is precisely the
    # state where continuing produces a confident, empty pipeline run.
    if fixture_sources and not any(fixture_sources.values()):
        logger.error(f"❌ EVERY fixture source failed ({', '.join(fixture_sources)}) "
                     f"- there is nothing to predict on")
        critical_failures.append("all fixture sources")
        raise PipelineAbort("all fixture sources failed")

    # 2. Score finished matches. Grading COVERAGE is a first-class health
    #    metric (bug #8: half the picks went ungraded for weeks and the tier
    #    hit-rates were computed on a flattering subset), so a results source
    #    dying is a ledger-integrity event, not a nuisance.
    result_sources = {}
    # OPTIONAL for the same reason as the Forebet fixture scrape: IP-blocked
    # in the cloud, redundant with the two APIs below.
    if os.getenv("PP_SKIP_FOREBET", "").strip() == "1":
        logger.info("⏭  Skipping Forebet results (same IP block)")
    else:
        result_sources["forebet"] = run_step(
            "Record results (Forebet)", ["scrapers/results.py"], OPTIONAL)
    result_sources["football-data"] = run_step(
        "Record results (football-data API)", ["scrapers/results_api.py"], DEGRADED)
    result_sources["espn"] = run_step(
        "Record results (ESPN)", ["scrapers/results_espn.py"], DEGRADED)

    # Not CRITICAL even when all fail: unscored matches are graded by the next
    # run, and today's picks are still valid. It must be loud, not fatal —
    # aborting here would throw away a perfectly good prediction run.
    if result_sources and not any(result_sources.values()):
        logger.error("❌ EVERY results source failed - nothing will be graded today")
        degraded_failures.append("all results sources")

    # 3. Merge cross-source duplicate fixtures BEFORE predicting, so the same
    #    real match never gets two predictions/picks
    # DEGRADED, not CRITICAL, and the distinction is the whole lesson of this
    # step: when it silently crashed for six runs, duplicate fixtures got
    # double predictions and one real match was counted twice in the track
    # record. That is ledger corruption — but it is RECOVERABLE, because the
    # next successful run merges the duplicates and collapses the child rows
    # (it did: 115 rows). Aborting the run instead would cost a day of picks
    # to fix something the following run repairs anyway.
    run_step("Dedupe fixtures", ["db/dedupe_fixtures.py", "--apply"], DEGRADED)

    # Same competition, two spellings ('serie-a' from PredictZ vs 'Serie A'):
    # the Builder's league filter showed two chips with identical text. Runs
    # after dedupe so merged rows are relabelled once. Only collapses labels
    # that share teams — see the module docstring for why name matching alone
    # would have fused Chinese and Uzbek "Super League".
    run_step("Normalise league labels",
             ["db/normalize_leagues.py", "--apply"], DEGRADED)

    # ClubElo: the only opinion here that is NOT derived from bookmaker prices.
    #
    # Runs AFTER dedupe so it attaches to the surviving match_id rather than to
    # a row that is about to be merged away. It is an OPINION source and never
    # inserts a fixture — a second source creating matches is how bug #2's
    # cross-source duplicates happened.
    #
    # OPTIONAL, for two independent reasons:
    #   1. The service is genuinely flaky (502, 200, connection reset on three
    #      consecutive probes). It retries internally; a bad afternoon must not
    #      colour the run, or we recreate the daily-red noise of bug #14.
    #   2. Zero matches is often CORRECT — it covers European club football, so
    #      on a day of Copa Libertadores and MLS it has nothing to say. The
    #      script distinguishes "feed empty" (fails) from "no overlap" (fine).
    run_step("ClubElo independent opinion", ["scrapers/clubelo.py"], OPTIONAL)

    # The league-code table exists twice on purpose (JSON for the API, a JS
    # mirror so the offline PWA can still render names). Two copies of one
    # rule drift silently — that is how dedupe_fixtures.py ended up with a
    # weaker team matcher than consensus.py for months. This compares them.
    run_step("Check league-code mirror", ["tools/check_league_codes.py"], DEGRADED)

    # 3b. Real UTC kickoff times (ESPN, keyless). Without these the app
    #     recommends matches that already kicked off: PredictZ leaves
    #     kickoff_time at '00:00' for ~88% of fixtures, which means UNKNOWN.
    #     Runs after dedupe so merged rows get timed once, not twice.
    # DEGRADED: without real kickoff times the app cannot tell a match that
    # has started from one that has not (PredictZ leaves kickoff_time at
    # '00:00' for ~88% of fixtures, meaning UNKNOWN), so it can recommend a
    # game that already kicked off. That is user-facing and money-adjacent.
    run_step("Kickoff times (ESPN)", ["scrapers/kickoff_times.py"], DEGRADED)

    # 4. Model predictions (+ Gemma analysis unless skipped)
    predict_args = ["models/predict.py"]
    if args.no_gemma:
        predict_args.append("--no-gemma")
    # CRITICAL: no predictions, no product. Everything after this decorates
    # or reports on predictions that would not exist.
    run_step("Generate predictions", predict_args, CRITICAL)

    # 5. Cross-source consensus report
    # Any pick still missing its written analysis — a --no-gemma run, an LLM
    # outage, or a fixture that can no longer be re-predicted because its odds
    # vanished — gets the text filled in. Never changes a prediction.
    # DEGRADED: snapshot the bookmaker's price for each new pick, immediately
    # after predicting. This is how the project's central question gets
    # answered — "when we disagree with the market, are we right?" — and it can
    # ONLY be captured now: odds move, so a later lookup measures a different
    # market. Every skipped day is evidence that cannot be reconstructed, which
    # is why a total failure returns non-zero rather than shrugging.
    run_step("Market snapshot (bookmaker price per pick)",
             ["scrapers/market_snapshot.py"], DEGRADED)

    # OPTIONAL: Gemma/Gemini prose is explanation, never the prediction. A
    # pick with no write-up is complete; the next run fills it in.
    run_step("Backfill missing analysis", ["analysis/backfill_analysis.py"],
             OPTIONAL)

    # OPTIONAL: this step only PRINTS a report — the consensus the API serves
    # is computed live by build_consensus(). Nothing downstream reads it.
    run_step("Cross-source consensus", ["models/consensus.py"], OPTIONAL)

    problems = ledger_health()
    for p in problems:
        logger.error(f"HEALTH: {p}")
    return problems


def report_and_exit(problems: list, aborted: str | None = None):
    """Decide the run's verdict. This is the bit that was wrong.

    The old logic sent the Telegram alert on `failed_steps or problems` but
    chose the EXIT CODE from `ok and not problems` — and `ok` only ever
    tracked "Generate predictions". So a failing step alerted (maybe) while the
    process exited 0 and the log printed "Pipeline complete". GitHub Actions
    went green on six consecutive runs with a crashed deduper.

    One rule now, used for both the alert and the exit code: CRITICAL or
    DEGRADED or a health problem means this run was not a success. OPTIONAL
    failures are reported for context but never decide the verdict.
    """
    serious = critical_failures + degraded_failures + problems

    if serious or optional_failures:
        lines = ["⚠️ PotentialPro pipeline problems:"]
        if aborted:
            lines.append(f"• ABORTED at: {aborted}")
        lines += [f"• CRITICAL: {s}" for s in critical_failures]
        lines += [f"• failed: {s}" for s in degraded_failures]
        lines += [f"• {p}" for p in problems]
        # Listed last and labelled, so they add context without ever being
        # the reason an alert fired.
        lines += [f"• (tolerated) {s}" for s in optional_failures]
        if serious:
            send_telegram("\n".join(lines))
        else:
            logger.info("Only tolerated failures — no alert sent: "
                        f"{', '.join(optional_failures)}")

    if serious:
        logger.error("═══ Pipeline finished with errors — check the log above. ═══")
        sys.exit(1)
    logger.success("═══ Pipeline complete. Picks are in the DB — bot will serve them. ═══")
    sys.exit(0)


def main():
    try:
        problems = run_pipeline()
    except PipelineAbort as exc:
        # A CRITICAL step stopped the run. Still report: an abort that dies
        # silently is the same failure mode this whole change exists to end.
        logger.error(f"═══ Pipeline ABORTED: {exc} ═══")
        report_and_exit(ledger_health(), aborted=str(exc))
        return
    report_and_exit(problems)


if __name__ == "__main__":
    main()