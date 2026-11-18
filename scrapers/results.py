"""
results.py - Scrape REAL final scores from Forebet and score our predictions.

Hardened version:
  - Detects Cloudflare "Just a moment..." challenge pages and retries
  - Stealth launch flags (hides the obvious automation fingerprints)
  - Optional visible-browser fallback (--headed) which passes challenges more often
  - Only genuine score containers (.lscr_td/.l_scr/.lscrsp) - NEVER .ex_sc predictions
  - Only rows explicitly FINISHED (FT marker)

Usage:
    python scrapers/results.py             (headless with stealth)
    python scrapers/results.py --headed    (visible browser - use if blocked)
"""

import re
import sys
import os
import time
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection

# NOTE: the old "/football-tips-and-predictions-for-yesterday" URL now 404s
# (Forebet renamed it, discovered 2026-07-16). Never grade from a 404/empty page.
URLS = {
    "yesterday": "https://www.forebet.com/en/football-predictions-from-yesterday",
    "today": "https://www.forebet.com/en/football-tips-and-predictions-for-today",
}

SCORE_SELECTORS = [".lscr_td", ".l_scr", ".lscrsp"]

STEALTH_INIT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = { runtime: {} };
"""


def is_cloudflare_challenge(html: str) -> bool:
    """Only signatures unique to the ACTUAL challenge page. A generic
    ('challenge' and 'cloudflare') test false-positived on Cloudflare's
    beacon script (/cdn-cgi/challenge-platform/...) that NORMAL pages embed —
    which made us discard perfectly good pages and report 'blocked' for days
    (found 18 July 2026)."""
    if not html:
        return True
    low = html.lower()
    return ("just a moment" in low or "cf-chl" in low
            or "attention required! | cloudflare" in low)


def get_rendered_html(url: str, headed: bool = False, attempts: int = 3) -> str | None:
    for attempt in range(1, attempts + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=not headed,
                    args=["--disable-blink-features=AutomationControlled",
                          "--no-sandbox"],
                )
                context = browser.new_context(
                    user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/125.0.0.0 Safari/537.36"),
                    locale="en-US",
                    timezone_id="Africa/Lagos",
                    viewport={"width": 1366, "height": 768},
                )
                page = context.new_page()
                page.add_init_script(STEALTH_INIT)
                page.goto(url, wait_until="domcontentloaded", timeout=60000)

                # Give Cloudflare's JS challenge time to auto-resolve
                for _ in range(10):
                    html = page.content()
                    if not is_cloudflare_challenge(html):
                        break
                    page.wait_for_timeout(2000)

                try:
                    page.wait_for_selector("div.rcnt", timeout=15000)
                except Exception:
                    pass
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2500)
                html = page.content()
                browser.close()

                if is_cloudflare_challenge(html):
                    logger.warning(f"Cloudflare challenge on {url} "
                                   f"(attempt {attempt}/{attempts})")
                    time.sleep(5 * attempt)
                    continue
                return html
        except Exception as e:
            logger.error(f"Playwright failed on {url}: {e}")
            time.sleep(3)
    logger.error(f"Blocked or failed after {attempts} attempts: {url}")
    return None


def extract_forebet_id(row) -> str | None:
    link_el = row.select_one("a.tnmscn")
    if link_el and link_el.get("href"):
        return link_el["href"].rstrip("/").split("-")[-1]
    return None


def row_status(row) -> str:
    el = row.select_one(".l_min")
    if el:
        t = el.get_text(strip=True).upper().replace("'", "")
        if t == "FT":
            return "finished"
        if t.isdigit() or t == "HT":
            return "live"
    row_text = row.get_text(" ", strip=True).upper()
    if re.search(r"\bFT\b", row_text):
        return "finished"
    return "upcoming"


def extract_final_score(row) -> tuple | None:
    for sel in SCORE_SELECTORS:
        el = row.select_one(sel)
        if not el:
            continue
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", el.get_text(strip=True))
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def scrape_finished_matches(headed: bool = False) -> tuple[list, int]:
    """Returns (results, pages_loaded). pages_loaded=0 means we saw nothing —
    every URL was blocked or 404 — and the caller should exit non-zero."""
    results = []
    first_unparsed_row = None
    pages_loaded = 0

    for label, url in URLS.items():
        html = get_rendered_html(url, headed=headed)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        title = soup.title.get_text(strip=True) if soup.title else ""
        if "404" in title:
            logger.error(f"[{label}] {url} returned a 404 page — URL is stale, fix URLS")
            continue
        pages_loaded += 1
        rows = soup.select("div.rcnt")
        finished_rows = 0

        for row in rows:
            fid = extract_forebet_id(row)
            if not fid:
                continue
            if row_status(row) != "finished":
                continue
            finished_rows += 1
            score = extract_final_score(row)
            if score:
                results.append({"forebet_id": fid,
                                "home_goals": score[0],
                                "away_goals": score[1]})
            elif first_unparsed_row is None:
                first_unparsed_row = row

        logger.info(f"[{label}] {len(rows)} rows, {finished_rows} finished")

    if not results and first_unparsed_row is not None:
        os.makedirs("data/raw", exist_ok=True)
        with open("data/raw/finished_row_debug.html", "w", encoding="utf-8") as f:
            f.write(first_unparsed_row.prettify())
        logger.warning("Finished rows found but scores unparsed - dumped sample to "
                       "data/raw/finished_row_debug.html")

    logger.success(f"✅ {len(results)} finished matches with verified scores (Forebet)")
    return results, pages_loaded


def record_results(finished: list) -> int:
    conn = get_connection()
    inserted = 0
    for r in finished:
        match_id = f"forebet_{r['forebet_id']}"
        if not conn.execute("SELECT 1 FROM matches WHERE match_id = ?",
                            [match_id]).fetchone():
            continue
        hg, ag = r["home_goals"], r["away_goals"]
        outcome = "H" if hg > ag else ("A" if ag > hg else "D")
        cur = conn.execute("""
            INSERT OR IGNORE INTO results
            (match_id, home_goals, away_goals, outcome, over_2_5, btts)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [match_id, hg, ag, outcome,
              1 if (hg + ag) > 2.5 else 0,
              1 if (hg > 0 and ag > 0) else 0])
        if cur.rowcount:
            inserted += 1
        conn.execute("UPDATE matches SET status='finished' WHERE match_id=?",
                     [match_id])
    conn.commit()
    conn.close()
    logger.success(f"💾 Recorded {inserted} new results from Forebet")
    return inserted


def show_scoreboard():
    conn = get_connection()
    rows = conn.execute("""
        SELECT mp.confidence, COUNT(*) AS total,
               SUM(CASE
                   WHEN (mp.best_bet='Home Win' AND r.outcome='H')
                     OR (mp.best_bet='Draw'     AND r.outcome='D')
                     OR (mp.best_bet='Away Win' AND r.outcome='A')
                   THEN 1 ELSE 0 END) AS correct
        FROM model_predictions mp
        JOIN results r ON r.match_id = mp.match_id
        -- Latest prediction row per match, same as /api/stats and the bot.
        -- Without this a re-predicted match is scored once per row and this
        -- console printed a third, different track record from the two the
        -- product publishes. (docs/history.md bug #3.)
        WHERE mp.id = (
            SELECT MAX(mp2.id) FROM model_predictions mp2
            WHERE mp2.match_id = mp.match_id
        )
        GROUP BY mp.confidence
        ORDER BY CASE mp.confidence WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END
    """).fetchall()
    conn.close()
    if not rows:
        print("\nNo scored predictions yet - results will accumulate daily.")
        return
    print(f"\n{'='*45}\nRUNNING TRACK RECORD\n{'='*45}")
    print(f"{'Tier':<10}{'Picks':>8}{'Correct':>10}{'Accuracy':>12}")
    for r in rows:
        acc = (r["correct"] / r["total"] * 100) if r["total"] else 0
        print(f"{r['confidence']:<10}{r['total']:>8}{r['correct']:>10}{acc:>11.1f}%")


def run():
    headed = "--headed" in sys.argv
    finished, pages_loaded = scrape_finished_matches(headed=headed)
    if finished:
        record_results(finished)
    show_scoreboard()
    if pages_loaded == 0:
        logger.error("No Forebet results page could be loaded (blocked or 404) — "
                     "exiting non-zero so the pipeline flags it")
        sys.exit(1)


if __name__ == "__main__":
    run()