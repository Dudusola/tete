"""
Refresh the training CSVs from football-data.co.uk.

WHY THIS EXISTS: the model's team form comes from data/historical/*.csv. Those
were downloaded by hand, and by 8 Aug 2026 the year-round leagues had drifted
19-74 days behind — Argentina 74, Mexico 70, MLS 69, Brazil 60. Those are
exactly the leagues carrying the ledger through the European off-season, so the
model was reasoning about them from months-old form. A manual routine rots; this
one runs.

TWO KINDS OF FILE, fetched differently:
  - Year-round leagues (Argentina, Brazil, MLS, Mexico, Japan, Scandinavia...)
    live at /new/<CODE>.csv as ONE continuously-updated file.
  - European leagues are per-season at /mmz4281/<SEASON>/<CODE>.csv, e.g.
    2627 for 2026-27. A new season file 404s until the season starts, which is
    normal and not an error.

Files are only replaced when the download has MORE rows than what we hold — a
truncated or error response must never overwrite good training data.

Usage:
    python scrapers/refresh_csvs.py            # refresh everything
    python scrapers/refresh_csvs.py --dry-run
"""

import argparse
import io as _io
import os
import sys
from datetime import date

import pandas as pd
import requests
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HIST = "data/historical"
BASE_NEW = "https://www.football-data.co.uk/new/{code}.csv"
BASE_SEASON = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"

# our filename stem -> football-data.co.uk code (single rolling file)
YEAR_ROUND = {
    "ArgentineLeague": "ARG", "BrazilLeague": "BRA", "USALeague": "USA",
    "MexicanLeague": "MEX", "ChineseLeague": "CHN", "JLeague": "JPN",
    "NorwegianLeague": "NOR", "AustrianLeague": "AUT", "DenmarkLeague": "DNK",
    "PolishLeague": "POL", "RomanianLeague": "ROU", "RussianLeague": "RUS",
    "SwissLeague": "SWZ", "SwedishLeague": "SWE", "FinlandLeague": "FIN",
    "IrelandLeague": "IRL",
}

# our filename stem -> football-data.co.uk division code (per-season files)
PER_SEASON = {
    "EPL": "E0", "Championship": "E1", "EFL League1": "E2", "EFL League2": "E3",
    "English-Conference": "EC",
    "LaLiga": "SP1", "Laliga2": "SP2",
    "Bundesliga1": "D1", "Bundesliga2": "D2",
    "SerieA": "I1", "SerieB": "I2",
    "FrenchLeague1": "F1", "FrenchLeague2": "F2",
    "Eredivisie": "N1", "BelgiumLeague": "B1", "PortugueseLeague": "P1",
    "TurkishLeague": "T1", "GreekLeague": "G1",
}


def season_code(d: date) -> str:
    """football-data's season code: 2627 for 2026-27. Seasons start in July."""
    start = d.year if d.month >= 7 else d.year - 1
    return f"{str(start)[2:]}{str(start + 1)[2:]}"


def _rows(text: str) -> int:
    try:
        return len(pd.read_csv(_io.StringIO(text), on_bad_lines="skip"))
    except Exception:
        return 0


def _save_if_better(path: str, text: str, dry: bool) -> str:
    """Only overwrite when the download genuinely has more matches."""
    new_rows = _rows(text)
    if new_rows == 0:
        return "empty response — kept ours"

    old_rows = 0
    if os.path.exists(path):
        try:
            old_rows = len(pd.read_csv(path, encoding="latin-1", on_bad_lines="skip"))
        except Exception:
            old_rows = 0

    if new_rows < old_rows:
        # Truncated or error page. Overwriting would DELETE training history.
        return f"downloaded {new_rows} < ours {old_rows} — kept ours"
    if new_rows == old_rows:
        return f"unchanged ({old_rows} matches)"

    if not dry:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    return f"updated {old_rows} -> {new_rows} matches (+{new_rows - old_rows})"


def refresh(dry: bool = False) -> int:
    updated = 0
    logger.info("Year-round leagues (one rolling file each)")
    for stem, code in sorted(YEAR_ROUND.items()):
        try:
            r = requests.get(BASE_NEW.format(code=code), timeout=40)
            if r.status_code != 200:
                logger.warning(f"  {stem:20} HTTP {r.status_code}")
                continue
            msg = _save_if_better(os.path.join(HIST, f"{stem}.csv"), r.text, dry)
        except requests.RequestException as e:
            msg = f"fetch failed: {e}"
        if msg.startswith("updated"):
            updated += 1
        logger.info(f"  {stem:20} {msg}")

    season = season_code(date.today())
    logger.info(f"European leagues, season {season} "
                f"(404 just means it hasn't kicked off yet)")
    for stem, code in sorted(PER_SEASON.items()):
        # Our per-season filenames look like EPL-2026-2027.csv
        yr = int(season[:2]) + 2000
        fname = f"{stem}-{yr}-{yr + 1}.csv"
        try:
            r = requests.get(BASE_SEASON.format(season=season, code=code), timeout=40)
            if r.status_code != 200:
                logger.info(f"  {stem:20} not published yet")
                continue
            msg = _save_if_better(os.path.join(HIST, fname), r.text, dry)
        except requests.RequestException as e:
            msg = f"fetch failed: {e}"
        if msg.startswith("updated"):
            updated += 1
        logger.info(f"  {stem:20} {msg}")

    verb = "would update" if dry else "updated"
    logger.success(f"{verb} {updated} file(s)")
    return updated


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    refresh(args.dry_run)
    # Never fatal: stale CSVs are a quality issue, not a reason to stop the
    # pipeline. The freshness is visible in the log either way.
    sys.exit(0)
