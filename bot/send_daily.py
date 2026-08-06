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


# Telegram hard-rejects a sendMessage body over 4096 characters with
# "Bad Request: message is too long". A day with ~20 picks blows straight past
# it, so the message has to be split. Kept under the limit with headroom.
TELEGRAM_LIMIT = 3900


def split_message(text: str) -> list:
    """Split on blank lines so a pick is never cut in half.

    Markdown is per-message in Telegram: an unclosed * would render as a
    literal asterisk in one chunk, so chunks must break between picks, never
    inside one.
    """
    if len(text) <= TELEGRAM_LIMIT:
        return [text]

    chunks, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > TELEGRAM_LIMIT and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send(text: str) -> bool:
    parts = split_message(text)
    for i, part in enumerate(parts, 1):
        if len(parts) > 1:
            part = f"{part}\n\n_({i}/{len(parts)})_"
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": part, "parse_mode": "Markdown"},
            timeout=30,
        )
        if r.status_code != 200:
            # Telegram puts the real reason in the body; the status alone is
            # not actionable (a bad chat_id and a revoked token both give 4xx).
            logger.error(f"Telegram HTTP {r.status_code} on part {i}/"
                         f"{len(parts)}: {r.text[:200]}")
            return False
    if len(parts) > 1:
        logger.info(f"Message split into {len(parts)} parts (4096-char limit)")
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
