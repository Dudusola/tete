"""
Forebet scraper (Playwright version)
Uses a real browser to render JavaScript-loaded fixtures and avoid bot detection.

One-time setup:
    playwright install chromium

Usage:
    python scrapers/forebet.py
"""

from bs4 import BeautifulSoup
from datetime import datetime
from playwright.sync_api import sync_playwright
import sys
import os
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import insert_match, insert_scraped_prediction


URLS = {
    "today": "https://www.forebet.com/en/football-tips-and-predictions-for-today",
    "tomorrow": "https://www.forebet.com/en/football-tips-and-predictions-for-tomorrow",
}


def get_rendered_html(url: str) -> str | None:
    """Load page in real Chromium browser, wait for JS to render fixtures."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                viewport={"width": 1366, "height": 768},
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Wait for match rows to appear, then give JS time to load the rest
            try:
                page.wait_for_selector("div.rcnt", timeout=15000)
            except Exception:
                logger.warning("No match rows appeared within 15s")

            # Scroll to bottom to trigger any lazy loading
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3000)  # 3s grace for lazy content

            html = page.content()
            browser.close()
            return html
    except Exception as e:
        logger.error(f"Playwright failed on {url}: {e}")
        return None


def scrape_today_predictions() -> list:
    """Scrape today's + tomorrow's predictions from Forebet."""
    logger.info("🔍 Scraping Forebet predictions (browser mode)...")
    all_matches = []
    seen_ids = set()

    for label, url in URLS.items():
        html = get_rendered_html(url)
        if not html:
            logger.warning(f"Could not load Forebet {label} page")
            continue

        soup = BeautifulSoup(html, "lxml")
        rows = soup.select("div.rcnt")
        logger.info(f"[{label}] Found {len(rows)} match rows")

        parsed = 0
        for row in rows:
            try:
                match = parse_match_row(row)
                if match and match["match"]["match_id"] not in seen_ids:
                    seen_ids.add(match["match"]["match_id"])
                    all_matches.append(match)
                    parsed += 1
            except Exception as e:
                logger.debug(f"Skipping row: {e}")
                continue

        logger.info(f"[{label}] Parsed {parsed} upcoming matches with predictions")

    logger.success(f"✅ Scraped {len(all_matches)} matches from Forebet")
    return all_matches


def parse_match_row(row):
    """Parse a single match row from Forebet (structure as of June 2026)"""

    # Team names - nested in .homeTeam / .awayTeam spans
    home_el = row.select_one(".homeTeam span[itemprop='name']")
    away_el = row.select_one(".awayTeam span[itemprop='name']")
    if not home_el or not away_el:
        return None

    home_team = home_el.get_text(strip=True)
    away_team = away_el.get_text(strip=True)
    if not home_team or not away_team:
        return None

    # Skip live/in-play matches - only if .l_min contains an actual match minute
    live_el = row.select_one(".l_min")
    if live_el:
        live_text = live_el.get_text(strip=True)
        if live_text and live_text.replace("'", "").isdigit():
            return None  # genuinely live (e.g. "80")

    # Win percentages: .fprc has 3 spans -> home / draw / away
    percentages = row.select(".fprc span")
    home_win_pct = draw_pct = away_win_pct = None
    if len(percentages) >= 3:
        try:
            home_win_pct = float(percentages[0].get_text(strip=True))
            draw_pct = float(percentages[1].get_text(strip=True))
            away_win_pct = float(percentages[2].get_text(strip=True))
        except ValueError:
            pass

    # Predicted score: "2 - 2" in .ex_sc.tabonly
    predicted_home_goals = predicted_away_goals = None
    score_el = row.select_one(".ex_sc.tabonly")
    if score_el:
        score_text = score_el.get_text(strip=True)
        if "-" in score_text:
            parts = score_text.split("-")
            try:
                predicted_home_goals = float(parts[0].strip())
                predicted_away_goals = float(parts[1].strip())
            except ValueError:
                pass

    # Skip matches where Forebet published no prediction at all
    if home_win_pct is None and predicted_home_goals is None:
        return None

    # Average total goals
    avg_goals = None
    avg_el = row.select_one(".avg_sc")
    if avg_el:
        try:
            avg_goals = float(avg_el.get_text(strip=True))
        except ValueError:
            pass

    # Odds: .haodd spans -> home / draw / away (real bookmaker odds - the
    # model's odds features must come from these, not Forebet's percentages)
    odds_home = odds_draw = odds_away = None
    odds_spans = row.select(".haodd span")
    if len(odds_spans) >= 3:
        try:
            odds_home = float(odds_spans[0].get_text(strip=True))
            odds_draw = float(odds_spans[1].get_text(strip=True))
            odds_away = float(odds_spans[2].get_text(strip=True))
        except ValueError:
            odds_home = odds_draw = odds_away = None

    # Date/time: .date_bah -> "12/06/2026 00:00"
    match_date = datetime.now().strftime("%Y-%m-%d")
    kickoff_time = "00:00"
    date_el = row.select_one(".date_bah")
    if date_el:
        raw = date_el.get_text(strip=True)
        parts = raw.split()
        if len(parts) == 2:
            try:
                d = datetime.strptime(parts[0], "%d/%m/%Y")
                match_date = d.strftime("%Y-%m-%d")
                kickoff_time = parts[1]
            except ValueError:
                pass

    # League short tag (e.g. "BrB")
    league_el = row.select_one(".shortTag")
    league = league_el.get_text(strip=True) if league_el else "Unknown"

    # Forebet's own match ID from the URL
    link_el = row.select_one("a.tnmscn")
    forebet_id = None
    if link_el and link_el.get("href"):
        forebet_id = link_el["href"].rstrip("/").split("-")[-1]

    match_id = f"forebet_{forebet_id}" if forebet_id else \
        f"forebet_{match_date}_{home_team}_{away_team}".replace(" ", "_").lower()

    match_record = {
        "match_id": match_id,
        "sport": "football",
        "league": league,
        "home_team": home_team,
        "away_team": away_team,
        "match_date": match_date,
        "kickoff_time": kickoff_time,
    }

    prediction_record = {
        "match_id": match_id,
        "source": "forebet",
        "home_win_pct": home_win_pct,
        "draw_pct": draw_pct,
        "away_win_pct": away_win_pct,
        "predicted_home_goals": predicted_home_goals,
        "predicted_away_goals": predicted_away_goals,
        "over_2_5_pct": avg_goals,
        "btts_pct": None,
        "home_form": None,
        "away_form": None,
        "odds_home": odds_home,
        "odds_draw": odds_draw,
        "odds_away": odds_away,
    }

    return {
        "match": match_record,
        "prediction": prediction_record
    }


def save_to_db(scraped_data: list):
    """Save all scraped matches and predictions to SQLite"""
    saved = 0
    for item in scraped_data:
        m_ok = insert_match(item["match"])
        p_ok = insert_scraped_prediction(item["prediction"])
        if m_ok and p_ok:
            saved += 1
    logger.success(f"💾 Saved {saved}/{len(scraped_data)} matches to database")


def run():
    """Main entry point"""
    data = scrape_today_predictions()
    if data:
        save_to_db(data)
        print(f"\n{'='*50}")
        print(f"Forebet Predictions ({len(data)} matches)")
        print(f"{'='*50}")
        for item in data[:10]:
            m = item["match"]
            p = item["prediction"]
            print(f"\n{m['home_team']} vs {m['away_team']}")
            print(f"  League: {m['league']} | {m['match_date']} {m['kickoff_time']}")
            print(f"  1X2: {p['home_win_pct']}% / {p['draw_pct']}% / {p['away_win_pct']}%")
            if p['predicted_home_goals'] is not None:
                print(f"  Predicted: {p['predicted_home_goals']:.0f} - {p['predicted_away_goals']:.0f}")
        if len(data) > 10:
            print(f"\n  ... and {len(data) - 10} more matches")
    else:
        logger.error("Scraped 0 matches — likely Cloudflare-blocked. "
                     "Exiting non-zero so the pipeline flags it.")
        sys.exit(1)


if __name__ == "__main__":
    run()