"""
Publish a small JSON feed for the always-on Telegram webhook worker.

WHY: interactive commands (/picks, /stats) need something listening 24/7. A
polling bot means a long-lived process on a machine that is often off — the
exact dependency the cloud migration removed everywhere else.

A Telegram WEBHOOK inverts that: Telegram pushes each message to a URL, which
only has to answer for a few milliseconds. Cloudflare Workers serve that free
and always-on — but a Worker can't read a 800 KB SQLite file, so the pipeline
publishes exactly what the bot needs as JSON instead.

The feed is intentionally tiny and pre-formatted: the Worker does no logic
beyond picking a field, so the numbers it serves are the same ones the pipeline
computed. No second implementation of the stats query to drift out of sync.

Written to feed/bot_feed.json and committed by the daily workflow.

Usage:
    python bot/export_feed.py
"""

import json
import os
import sys
from datetime import datetime, timezone

from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot.telegram_bot import fetch_todays_picks, fetch_stats, format_picks

FEED_PATH = "feed/bot_feed.json"


def build_feed() -> dict:
    picks = fetch_todays_picks()
    stats = fetch_stats()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Pre-rendered so the Worker never re-implements formatting.
        "picks_message": format_picks(picks),
        "picks_count": len(picks),
        "stats": {
            "total": stats["total"],
            "correct": stats["correct"],
            "accuracy": round(stats["accuracy"], 1),
        },
        # Compact structured copy, in case the Worker ever needs to filter.
        "picks": [
            {
                "home": p["home_team"],
                "away": p["away_team"],
                "league": p["league"],
                "date": p["match_date"],
                "kickoff": p["kickoff_time"],
                "bet": p["best_bet"],
                # Stored as a 0-1 fraction (the bot renders it with :.0%),
                # so scale here — the feed exposes a percentage.
                "prob": round((p["best_bet_prob"] or 0) * 100, 1),
                "confidence": p["confidence"],
            }
            for p in picks
        ],
    }


def main() -> int:
    feed = build_feed()
    os.makedirs(os.path.dirname(FEED_PATH), exist_ok=True)
    with open(FEED_PATH, "w", encoding="utf-8") as fh:
        json.dump(feed, fh, ensure_ascii=False, indent=1)

    size = os.path.getsize(FEED_PATH)
    logger.success(
        f"Feed written: {FEED_PATH} ({size} bytes, {feed['picks_count']} picks, "
        f"record {feed['stats']['correct']}/{feed['stats']['total']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
