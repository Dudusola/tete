"""
PotentialPro Telegram Bot
Daily picks delivered to your phone + on-demand commands.

Commands:
    /start  - check the bot is alive
    /picks  - today's model picks (HIGH + MEDIUM)
    /slip   - build a risk-based slip (buttons or: /slip high 5)
    /stats  - running accuracy from scored predictions

Setup - your .env file (project root) must contain:
    TELEGRAM_BOT_TOKEN=your_token_from_botfather
    TELEGRAM_CHAT_ID=your_id_from_userinfobot

Usage:
    python bot/telegram_bot.py
    (keep it running - Ctrl+C to stop)
"""

import os
import sys
# Emoji in our log lines crash a cp1252 Windows console - force safe output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import datetime as dt
from loguru import logger
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from db.database import get_connection
from bot.slip_handlers import register_slip_handlers

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DAILY_HOUR = 8  # 08:00 Lagos time (WAT = UTC+1)


# ─── Data access ─────────────────────────────────────────────────────────────

def fetch_todays_picks() -> list:
    """Latest model prediction per match for today/future, HIGH+MEDIUM only."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT m.home_team, m.away_team, m.league, m.match_date, m.kickoff_time,
               mp.home_win_prob, mp.draw_prob, mp.away_win_prob,
               mp.confidence, mp.best_bet, mp.best_bet_prob, mp.gemma_analysis
        FROM matches m
        JOIN model_predictions mp ON mp.id = (
            SELECT MAX(mp2.id) FROM model_predictions mp2
            WHERE mp2.match_id = m.match_id
        )
        WHERE m.match_date >= date('now')
          -- Don't push picks for matches that already kicked off.
          -- NULL kickoff_utc = unknown, so it stays visible.
          AND (m.kickoff_utc IS NULL OR m.kickoff_utc > strftime('%Y-%m-%d %H:%M','now'))
          AND mp.confidence IN ('HIGH', 'MEDIUM')
        ORDER BY mp.best_bet_prob DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fetch_stats() -> dict:
    """Accuracy of scored predictions (needs results to be recorded)."""
    conn = get_connection()
    row = conn.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE
                WHEN (mp.best_bet = 'Home Win' AND r.outcome = 'H')
                  OR (mp.best_bet = 'Draw'     AND r.outcome = 'D')
                  OR (mp.best_bet = 'Away Win' AND r.outcome = 'A')
                THEN 1 ELSE 0 END) AS correct
        FROM model_predictions mp
        JOIN results r ON r.match_id = mp.match_id
        WHERE mp.confidence IN ('HIGH', 'MEDIUM')
    """).fetchone()
    conn.close()
    total = row["total"] or 0
    correct = row["correct"] or 0
    return {
        "total": total,
        "correct": correct,
        "accuracy": (correct / total * 100) if total else 0.0,
    }


# ─── Message formatting ──────────────────────────────────────────────────────

CONF_EMOJI = {"HIGH": "🟢", "MEDIUM": "🟡"}

def format_picks(picks: list) -> str:
    if not picks:
        return ("📭 No HIGH or MEDIUM confidence picks today.\n\n"
                "The model evaluated today's fixtures and none met the "
                "confidence threshold. No bet is also a decision. 🧠")

    today = dt.date.today().strftime("%A, %d %B %Y")
    lines = [f"⚽ *PotentialPro Picks — {today}*\n"]

    for p in picks:
        emoji = CONF_EMOJI.get(p["confidence"], "⚪")
        lines.append(
            f"{emoji} *{p['home_team']} vs {p['away_team']}*\n"
            f"   {p['league']} | {p['match_date']} {p['kickoff_time']}\n"
            f"   H {p['home_win_prob']:.0%} | D {p['draw_prob']:.0%} | A {p['away_win_prob']:.0%}\n"
            f"   👉 *{p['best_bet']}* ({p['best_bet_prob']:.0%}) — {p['confidence']}"
        )
        if p.get("gemma_analysis"):
            lines.append(f"   💬 _{p['gemma_analysis'][:200]}_")
        lines.append("")

    lines.append("_Bet responsibly. Confidence ≠ certainty._")
    return "\n".join(lines)


# ─── Command handlers ────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *PotentialPro is alive.*\n\n"
        "⚽ /picks — today's model picks\n"
        "🎰 /slip — build a risk-based slip\n"
        "📊 /stats — running accuracy\n"
        "🎫 /code <SportyBet code> — decode a booking code\n\n"
        f"Daily picks arrive automatically at {DAILY_HOUR:02d}:00."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_picks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    picks = fetch_todays_picks()
    await update.message.reply_text(format_picks(picks), parse_mode="Markdown")


async def cmd_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Decode a SportyBet booking code: /code A1B2C3"""
    from scrapers.booking_codes import decode_sportybet, BookingCodeError
    if not context.args:
        await update.message.reply_text(
            "Usage: /code <SportyBet booking code>\ne.g. /code A1B2C3")
        return
    code = context.args[0]
    try:
        legs = decode_sportybet(code)
    except BookingCodeError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return
    lines = [f"🎫 *Booking code {code.upper()}* — {len(legs)} leg(s)\n"]
    for l in legs:
        odds = f" @ {l['odds']}" if l.get("odds") else ""
        lines.append(f"• *{l['home_team']} vs {l['away_team']}*\n"
                     f"   {l.get('market') or ''}: {l.get('selection') or ''}{odds}"
                     f"  ({l.get('match_date') or 'date?'})")
    lines.append("\n_Open the Checker on the web app to see our model's "
                 "verdict on each leg._")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = fetch_stats()
    if s["total"] == 0:
        msg = ("📊 No scored predictions yet.\n\n"
               "Stats appear once match results are recorded against past picks.")
    else:
        msg = (f"📊 *PotentialPro Track Record*\n\n"
               f"Picks scored: {s['total']}\n"
               f"Correct: {s['correct']}\n"
               f"Accuracy: *{s['accuracy']:.1f}%*")
    await update.message.reply_text(msg, parse_mode="Markdown")


# ─── Daily scheduled send ────────────────────────────────────────────────────

async def daily_picks_job(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set - skipping daily send")
        return
    picks = fetch_todays_picks()
    await context.bot.send_message(
        chat_id=CHAT_ID, text=format_picks(picks), parse_mode="Markdown"
    )
    logger.info(f"Daily picks sent ({len(picks)} picks)")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN missing from .env - check the .env file "
                     "exists in the project root and contains TELEGRAM_BOT_TOKEN=...")
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("picks", cmd_picks))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("code", cmd_code))
    register_slip_handlers(app)

    # Daily picks at 08:00 WAT (UTC+1) -> 07:00 UTC
    app.job_queue.run_daily(
        daily_picks_job,
        time=dt.time(hour=DAILY_HOUR - 1, minute=0),
        name="daily_picks",
    )

    logger.success("🤖 PotentialPro bot running. Send /start to it on Telegram.")
    # drop_pending_updates clears any backlog/stuck state from a previous run
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()