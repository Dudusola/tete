"""
predictz.py - Second prediction source (PredictZ).
Scrapes today's predictions, stores into scraped_predictions with source='predictz'.

PredictZ gives us: predicted result + score, 1X2 odds (-> implied probabilities),
recent form, and a stable match id. Structure verified 26 June 2026.

Usage:
    python scrapers/predictz.py
"""

import re
import sys
import os
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import insert_match, insert_scraped_prediction

URLS = {
    "today": "https://www.predictz.com/predictions/today/",
    "tomorrow": "https://www.predictz.com/predictions/tomorrow/",
}


def get_rendered_html(url: str) -> str | None:
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/125.0.0.0 Safari/537.36"),
                locale="en-US",
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Dismiss consent banner first (it can block content rendering)
            for sel in ["button:has-text('Accept')", "button:has-text('AGREE')",
                        "button:has-text('Consent')", "button:has-text('I Accept')",
                        ".fc-cta-consent", "[aria-label='Consent']"]:
                try:
                    page.click(sel, timeout=1500)
                    page.wait_for_timeout(1000)
                    break
                except Exception:
                    pass

            # Wait for actual match rows to appear, instead of a fixed sleep
            try:
                page.wait_for_selector("div.pttr", timeout=20000)
            except Exception:
                logger.warning(f"No rows appeared on {url} within 20s")

            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)

            html = page.content()
            browser.close()
            return html
    except Exception as e:
        logger.error(f"Playwright failed on {url}: {e}")
        return None


def odds_to_implied(oh, od, oa):
    """Convert decimal odds to normalized implied probabilities (remove margin)."""
    try:
        ih, idr, ia = 1/oh, 1/od, 1/oa
        total = ih + idr + ia
        return (ih/total*100, idr/total*100, ia/total*100)
    except (ZeroDivisionError, TypeError):
        return (None, None, None)


def parse_row(row) -> dict | None:
    # Team names
    home_el = row.select_one(".ptmobh")
    away_el = row.select_one(".ptmoba")
    if not home_el or not away_el:
        return None
    home_team = home_el.get_text(strip=True)
    away_team = away_el.get_text(strip=True)
    if not home_team or not away_team:
        return None

    # Predicted result + score, e.g. "Draw 1-1" or "Norway 2-1"
    pred_el = row.select_one(".ptpredboxsml")
    predicted_home_goals = predicted_away_goals = None
    if pred_el:
        m = re.search(r"(\d+)\s*-\s*(\d+)", pred_el.get_text(strip=True))
        if m:
            predicted_home_goals = float(m.group(1))
            predicted_away_goals = float(m.group(2))

    # 1X2 odds from the data-odds attributes (MH=home, MD=draw, MA=away)
    oh = od = oa = None
    for a in row.select("a.btnstsm[data-odds]"):
        dtype = a.get("data-type", "")
        try:
            val = float(a["data-odds"])
        except (ValueError, KeyError):
            continue
        if dtype == "MH":
            oh = val
        elif dtype == "MD":
            od = val
        elif dtype == "MA":
            oa = val

    home_win_pct, draw_pct, away_win_pct = odds_to_implied(oh, od, oa)

    # Skip rows with no usable prediction signal
    if home_win_pct is None and predicted_home_goals is None:
        return None

    # Match id + league from the preview link
    link = row.select_one("a.btntsm, .ptgame a")
    forebet_style_id = None
    league = "Unknown"
    if link and link.get("href"):
        href = link["href"].rstrip("/")
        forebet_style_id = href.split("/")[-1]
        parts = href.split("/")
        if len(parts) >= 2:
            # full slug, prettified - [:20] truncation caused "world-cup-2026-last-"
            league = parts[-2].replace("-", " ").title()

    match_date = datetime.now().strftime("%Y-%m-%d")
    match_id = f"predictz_{forebet_style_id}" if forebet_style_id else \
        f"predictz_{match_date}_{home_team}_{away_team}".replace(" ", "_").lower()

    match_record = {
        "match_id": match_id,
        "sport": "football",
        "league": league,
        "home_team": home_team,
        "away_team": away_team,
        "match_date": match_date,
        "kickoff_time": "00:00",
    }
    prediction_record = {
        "match_id": match_id,
        "source": "predictz",
        "home_win_pct": round(home_win_pct, 1) if home_win_pct else None,
        "draw_pct": round(draw_pct, 1) if draw_pct else None,
        "away_win_pct": round(away_win_pct, 1) if away_win_pct else None,
        "predicted_home_goals": predicted_home_goals,
        "predicted_away_goals": predicted_away_goals,
        "over_2_5_pct": None,
        "btts_pct": None,
        "home_form": None,
        "away_form": None,
        "odds_home": oh,
        "odds_draw": od,
        "odds_away": oa,
    }
    return {"match": match_record, "prediction": prediction_record}


def scrape() -> list:
    logger.info("🔍 Scraping PredictZ predictions...")
    all_matches = []
    seen = set()
    for label, url in URLS.items():
        html = get_rendered_html(url)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        rows = soup.select("div.pttr")
        logger.info(f"[{label}] {len(rows)} rows")
        for row in rows:
            try:
                parsed = parse_row(row)
                if parsed and parsed["match"]["match_id"] not in seen:
                    seen.add(parsed["match"]["match_id"])
                    all_matches.append(parsed)
            except Exception as e:
                logger.debug(f"Skip row: {e}")
    logger.success(f"✅ Scraped {len(all_matches)} matches from PredictZ")
    return all_matches


def save_to_db(data: list):
    saved = 0
    for item in data:
        if insert_match(item["match"]) and insert_scraped_prediction(item["prediction"]):
            saved += 1
    logger.success(f"💾 Saved {saved}/{len(data)} PredictZ matches")


def run():
    data = scrape()
    if data:
        save_to_db(data)
        print(f"\n{'='*55}")
        print(f"PredictZ Predictions ({len(data)} matches)")
        print(f"{'='*55}")
        for item in data[:10]:
            m, p = item["match"], item["prediction"]
            print(f"\n{m['home_team']} vs {m['away_team']}  [{m['league']}]")
            if p["home_win_pct"]:
                print(f"  1X2 (from odds): {p['home_win_pct']}% / {p['draw_pct']}% / {p['away_win_pct']}%")
            if p["predicted_home_goals"] is not None:
                print(f"  Predicted: {p['predicted_home_goals']:.0f} - {p['predicted_away_goals']:.0f}")
        if len(data) > 10:
            print(f"\n  ... and {len(data)-10} more")
    else:
        logger.error("Scraped 0 matches — likely blocked. "
                     "Exiting non-zero so the pipeline flags it.")
        sys.exit(1)


if __name__ == "__main__":
    run()
