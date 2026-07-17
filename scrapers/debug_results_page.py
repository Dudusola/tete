"""Diagnose why results.py finds 0 rows. Saves the rendered page and
counts candidate row containers. Read-only."""
import os
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URL = "https://www.forebet.com/en/football-tips-and-predictions-for-yesterday"

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"))
    page = ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(6000)   # generous wait
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(3000)
    html = page.content()
    b.close()

os.makedirs("data/raw", exist_ok=True)
with open("data/raw/results_page.html", "w", encoding="utf-8") as f:
    f.write(html)

soup = BeautifulSoup(html, "lxml")
print(f"Saved {len(html)} chars to data/raw/results_page.html")
print(f"Page title: {soup.title.get_text() if soup.title else 'NONE'}\n")

# Count candidate row containers
for sel in ["div.rcnt", "div.tr_0", "div.tr_1", "div.schema", ".rcnt",
            "[class*='rcnt']", "div.moduletable", ".tnmscn", ".l_scr", ".lscr_td"]:
    print(f"  {sel:<22}: {len(soup.select(sel))} elements")

# Show a sample of class names present, to spot the real row container
from collections import Counter
classes = Counter()
for el in soup.find_all("div", class_=True):
    for c in el.get("class"):
        classes[c] += 1
print("\nMost common div classes on the page:")
for c, n in classes.most_common(25):
    print(f"  {c}: {n}")
