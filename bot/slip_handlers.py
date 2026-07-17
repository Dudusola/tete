"""
slip_handlers.py - Interactive risk-based slip building for the Telegram bot.

Two entry points:
  /slip                    -> button flow (tap risk, tap game count)
  /slip <risk> <count>     -> direct, e.g. /slip high 5

Pulls today's model predictions, derives all markets, builds a slip at the
chosen risk level, one bet per match.

Wire into telegram_bot.py main():
    from bot.slip_handlers import register_slip_handlers
    register_slip_handlers(app)
"""

import os
import sys
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (CommandHandler, CallbackQueryHandler, ContextTypes)

from db.database import get_connection
from models.markets import derive_markets, build_slip, accumulator_probability

RISK_EMOJI = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}
VALID_RISK = {"LOW", "MEDIUM", "HIGH"}
VALID_COUNTS = [3, 5, 7, 10]


# ─── Data ────────────────────────────────────────────────────────────────────

def load_today_predictions() -> list:
    """Latest model prediction per upcoming match, with Forebet goal data."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT m.match_id, m.home_team, m.away_team, m.league,
               m.match_date, m.kickoff_time,
               mp.home_win_prob, mp.draw_prob, mp.away_win_prob,
               p.predicted_home_goals, p.predicted_away_goals, p.over_2_5_pct
        FROM matches m
        JOIN model_predictions mp ON mp.id = (
            SELECT MAX(mp2.id) FROM model_predictions mp2 WHERE mp2.match_id = m.match_id
        )
        LEFT JOIN scraped_predictions p ON p.id = (
            SELECT MAX(p2.id) FROM scraped_predictions p2 WHERE p2.match_id = m.match_id
        )
        WHERE m.match_date >= date('now')
        ORDER BY m.match_date, m.kickoff_time
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def gather_all_markets() -> list:
    """Derive every market for every upcoming predicted match."""
    preds = load_today_predictions()
    all_opts = []
    for p in preds:
        all_opts.extend(derive_markets(p))
    return all_opts


# ─── Slip formatting ─────────────────────────────────────────────────────────

def format_slip(slip: list, risk: str) -> str:
    if not slip:
        return (f"{RISK_EMOJI.get(risk,'')} No *{risk}* risk picks available right now.\n\n"
                "Either no matches are scheduled, or none fit this risk band today. "
                "Try a different risk level.")

    emoji = RISK_EMOJI.get(risk, "")
    lines = [f"{emoji} *{risk} RISK SLIP* — {len(slip)} games\n"]

    for i, leg in enumerate(slip, 1):
        lines.append(
            f"*{i}. {leg.home_team} vs {leg.away_team}*\n"
            f"   {leg.league} | {leg.match_date} {leg.kickoff_time}\n"
            f"   🎯 {leg.market}: *{leg.selection}*\n"
            f"   📊 {leg.probability:.0%} likely"
        )
        lines.append("")

    combined = accumulator_probability(slip)
    lines.append(f"🎲 *Accumulator hit chance: {combined:.1%}*")
    lines.append("_(all legs must win — higher legs = lower combined odds)_")
    lines.append("\n_Bet responsibly. This is a model estimate, not a guarantee._")
    return "\n".join(lines)


def make_slip(risk: str, count: int) -> str:
    all_opts = gather_all_markets()
    slip = build_slip(all_opts, risk, count)
    return format_slip(slip, risk)


# ─── Button flow ─────────────────────────────────────────────────────────────

async def cmd_slip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /slip            -> show risk buttons
    /slip high 5     -> direct slip
    """
    args = context.args
    if len(args) >= 2:
        risk = args[0].upper()
        if risk not in VALID_RISK:
            await update.message.reply_text("Risk must be: low, medium, or high.")
            return
        try:
            count = int(args[1])
        except ValueError:
            await update.message.reply_text("Game count must be a number, e.g. /slip high 5")
            return
        count = max(1, min(count, 20))
        await update.message.reply_text(make_slip(risk, count), parse_mode="Markdown")
        return

    # No args -> button flow
    keyboard = [[
        InlineKeyboardButton("🟢 Low", callback_data="risk:LOW"),
        InlineKeyboardButton("🟡 Medium", callback_data="risk:MEDIUM"),
        InlineKeyboardButton("🔴 High", callback_data="risk:HIGH"),
    ]]
    await update.message.reply_text(
        "🎰 *Build a slip*\n\nHow much risk do you want?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def on_risk_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    risk = query.data.split(":")[1]

    buttons = [InlineKeyboardButton(f"{n} games", callback_data=f"count:{risk}:{n}")
               for n in VALID_COUNTS]
    # arrange 2 per row
    keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]

    await query.edit_message_text(
        f"{RISK_EMOJI[risk]} *{risk} risk selected.*\n\nHow many games?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def on_count_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Building your slip...")
    _, risk, count = query.data.split(":")
    text = make_slip(risk, int(count))
    await query.edit_message_text(text, parse_mode="Markdown")


def register_slip_handlers(app):
    """Call this from telegram_bot.py main()."""
    app.add_handler(CommandHandler("slip", cmd_slip))
    app.add_handler(CallbackQueryHandler(on_risk_chosen, pattern=r"^risk:"))
    app.add_handler(CallbackQueryHandler(on_count_chosen, pattern=r"^count:"))
    logger.info("Slip handlers registered (/slip)")
