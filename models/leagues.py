"""
leagues.py - Turn a raw league label into something a human can read.

ONE mapping, in data/league_codes.json, used by everything server-side. The
Telegram Worker used to print the raw label, so `/picks` showed "Sy1" and
"Fnl" while the web app showed "Syrian Premier League" and "Russia First
League" — the same fixture described two different ways depending on where
you looked.

The obvious fix — paste the table into worker/index.js too — would have made
a THIRD copy of a 164-row lookup in a second language. That is the same trap
as the duplicate team matcher in dedupe_fixtures.py: two implementations of
one rule drift, and the drift is silent. Instead the API now emits a
`league_display` field and the Worker just renders it.

webapp/js/api.js keeps its own literal copy of the same table, deliberately:
the PWA's service worker serves cached pages offline, where there is no API to
ask. That copy is a MIRROR, not a second source — tools/check_league_codes.py
fails if the two disagree, and it runs in the daily pipeline.
"""

import json
import os
import re

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "league_codes.json")

try:
    with open(_PATH, encoding="utf-8") as fh:
        LEAGUE_CODES = json.load(fh)
except (OSError, ValueError):
    # A missing or corrupt file must not take the API down; raw labels are
    # ugly, not broken. Same principle as "unknown kickoff stays visible".
    LEAGUE_CODES = {}


def pretty_league(name: str) -> str:
    """Mirror of prettyLeague() in webapp/js/api.js — keep the two in step."""
    if not name:
        return "Football"
    if name in LEAGUE_CODES:
        return LEAGUE_CODES[name]
    cleaned = re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", name).strip())
    if not cleaned:
        return "Football"
    return re.sub(r"\b[a-z]", lambda m: m.group(0).upper(), cleaned)
