"""
check_league_codes.py - Fail if the JS mirror of the league map has drifted
from data/league_codes.json.

There are two copies of the league-code table on purpose:
  data/league_codes.json   the SOURCE, read by models/leagues.py (API, bot)
  webapp/js/api.js         a MIRROR, so the offline PWA can still render names

Two copies of one rule is exactly how the deduper ended up with a second,
weaker team matcher than consensus.py — and nobody noticed for months because
nothing compared them. This script is that comparison. It runs in the daily
pipeline, so drift surfaces the day it is introduced rather than as a confusing
"why does Telegram say Sy1 and the web app say Syrian Premier League".

Exit codes: 0 = in step, 1 = drifted (prints the differences).

Run:
    python tools/check_league_codes.py
"""

import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JSON_PATH = os.path.join(ROOT, "data", "league_codes.json")
JS_PATH = os.path.join(ROOT, "webapp", "js", "api.js")


def parse_js_map(text: str) -> dict:
    """Pull the LEAGUE_CODES object literal out of api.js.

    Deliberately a narrow parse: find the literal, then read `key: "value"`
    pairs, skipping // comments. If the shape ever changes enough to break
    this, that is itself worth failing on rather than silently comparing an
    empty dict and declaring success.
    """
    start = text.find("const LEAGUE_CODES")
    if start == -1:
        raise SystemExit("check_league_codes: LEAGUE_CODES not found in api.js")
    end = text.find("\n};", start)
    if end == -1:
        raise SystemExit("check_league_codes: LEAGUE_CODES literal not closed")
    body = text[start:end]
    body = re.sub(r"//[^\n]*", "", body)          # strip comments
    return dict(re.findall(r'(\w+)\s*:\s*"([^"]*)"', body))


def main() -> int:
    with io.open(JSON_PATH, encoding="utf-8") as fh:
        source = json.load(fh)
    with io.open(JS_PATH, encoding="utf-8") as fh:
        mirror = parse_js_map(fh.read())

    only_json = sorted(set(source) - set(mirror))
    only_js = sorted(set(mirror) - set(source))
    differing = sorted(k for k in set(source) & set(mirror)
                       if source[k] != mirror[k])

    if not (only_json or only_js or differing):
        print(f"league codes in step: {len(source)} entries")
        return 0

    print("LEAGUE CODE DRIFT between data/league_codes.json and webapp/js/api.js")
    for k in only_json:
        print(f"  missing from api.js : {k} = {source[k]!r}")
    for k in only_js:
        print(f"  missing from JSON   : {k} = {mirror[k]!r}")
    for k in differing:
        print(f"  differs             : {k} JSON={source[k]!r} JS={mirror[k]!r}")
    print("\nUpdate whichever is stale so both agree.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
