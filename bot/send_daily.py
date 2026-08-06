"""
Send today's picks to Telegram once, then exit.

WHY THIS EXISTS: the daily 08:00 push previously required bot/telegram_bot.py
to be RUNNING — a long-lived polling process on Boss's laptop, started by hand.
If the laptop was off, or the process had died, the picks simply never arrived
and nothing said so.

Sending is just one HTTPS call, so it doesn't need a running bot at all. This
runs from GitHub Actions after the pipeline, meaning picks arrive daily whether
or not any machine of ours is awake.

telegram_bot.py is still what serves the INTERACTIVE commands (/picks, /stats,
/slip) — those genuinely need a live process. This only replaces the scheduled
push.

Reuses fetch_todays_picks() and format_picks() from the bot so the message is
byte-identical to the one the bot sends; no second copy of the formatting to
drift out of sync.

Usage:
    python bot/send_daily.py
    python bot/send_daily.py --dry-run   # print, don't send
"""

import argparse
import os
import sys

import requests
from dotenv import load_dotenv
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot.telegram_bot import fetch_todays_picks, format_picks

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


def send(text: str) -> bool:
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=30,
    )
    if r.status_code != 200:
        # Telegram puts the real reason in the body; the status alone is not
        # actionable (a bad chat_id and a revoked token both give 4xx).
        logger.error(f"Telegram HTTP {r.status_code}: {r.text[:200]}")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print the message instead of sending it")
    args = ap.parse_args()

    picks = fetch_todays_picks()
    text = format_picks(picks)

    if args.dry_run:
        print(text)
        return 0

    if not TOKEN or not CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — cannot send.")
        return 1

    if not send(text):
        return 1

    logger.success(f"Daily picks sent ({len(picks)} picks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
