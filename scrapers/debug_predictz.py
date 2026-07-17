"""Inspect PredictZ's live HTML structure with Playwright"""
import os
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URL = "https://www.predictz.com/predictions/today/"

os.makedirs("data/raw", exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"),
        locale="en-US",
    )
    page = context.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4000)

    # Try clicking away any cookie/consent banner
    for sel in ["button:has-text('Accept')", "button:has-text('AGREE')",
                ".css-47sehv", "button:has-text('Consent')"]:
        try:
            page.click(sel, timeout=2000)
            page.wait_for_timeout(1500)
            break
        except Exception:
            pass

    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(2000)

    html = page.content()
    soup = BeautifulSoup(html, "lxml")

    with open("data/raw/predictz_page.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved full page ({len(html)} chars) to data/raw/predictz_page.html")
    print(f"Page title: {page.title()}")

    # Hunt for the repeating row container - try common patterns
    for sel in ["div.pttr", ".predtr", "table tr", ".pttable tr",
                "div[class*='pred']", "div[class*='row']"]:
        found = soup.select(sel)
        if 5 < len(found) < 200:  # plausible match-row count
            print(f"  Candidate row selector '{sel}': {len(found)} elements")

    browser.close()