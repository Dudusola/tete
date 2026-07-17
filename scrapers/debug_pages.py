"""Debug what Playwright actually renders on Forebet"""
import os
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URLS = {
    "today": "https://www.forebet.com/en/football-tips-and-predictions-for-today",
    "tomorrow": "https://www.forebet.com/en/football-tips-and-predictions-for-tomorrow",
}

os.makedirs("data/raw", exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        locale="en-US",
        viewport={"width": 1366, "height": 768},
    )

    for label, url in URLS.items():
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)

        # Try clicking away any consent/cookie dialog
        for sel in ["button:has-text('Accept')", "button:has-text('AGREE')",
                    "button:has-text('Consent')", ".fc-cta-consent"]:
            try:
                page.click(sel, timeout=2000)
                print(f"[{label}] Clicked consent button: {sel}")
                page.wait_for_timeout(2000)
                break
            except Exception:
                pass

        # Scroll multiple times to trigger lazy loading
        for i in range(5):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)

        html = page.content()
        soup = BeautifulSoup(html, "lxml")
        rows = soup.select("div.rcnt")
        live = sum(1 for r in rows if r.select_one(".l_min"))
        fprc = sum(1 for r in rows if r.select_one(".fprc"))
        first = soup.select_one(".homeTeam span[itemprop='name']")
        dates = [d.get_text(strip=True) for d in soup.select(".date_bah")[:3]]

        print(f"\n[{label}]")
        print(f"  final URL: {page.url}")
        print(f"  page title: {page.title()}")
        print(f"  rows: {len(rows)} | live: {live} | with .fprc: {fprc}")
        print(f"  first match: {first.get_text(strip=True) if first else 'N/A'}")
        print(f"  first 3 dates: {dates}")

        # Save evidence
        page.screenshot(path=f"data/raw/forebet_{label}.png", full_page=False)
        with open(f"data/raw/forebet_{label}.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  saved: data/raw/forebet_{label}.png + .html")
        page.close()

    browser.close()