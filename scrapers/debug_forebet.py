"""
Debug: inspect Forebet's live HTML structure
Run: python scrapers/debug_forebet.py
"""
import os
import requests
from bs4 import BeautifulSoup

URL = "https://www.forebet.com/en/football-tips-and-predictions-for-today"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
}

response = requests.get(URL, headers=HEADERS, timeout=15)
soup = BeautifulSoup(response.text, "lxml")

rows = soup.select("div.rcnt")
print(f"Found {len(rows)} rows with div.rcnt")

# Save full page for inspection
os.makedirs("data/raw", exist_ok=True)
with open("data/raw/forebet_page.html", "w", encoding="utf-8") as f:
    f.write(response.text)
print("Full page saved to data/raw/forebet_page.html")

# Inspect .fprc content for each non-live row
print("\n" + "=" * 70)
print("PERCENTAGE DEBUG FOR UPCOMING (non-live) ROWS:")
print("=" * 70)

upcoming_count = 0
for row in rows:
    if row.select_one(".l_min"):
        continue  # skip live/finished with minute indicator
    home = row.select_one(".homeTeam span[itemprop='name']")
    away = row.select_one(".awayTeam span[itemprop='name']")
    if not home or not away:
        continue

    upcoming_count += 1
    spans = row.select(".fprc span")
    print(f"\n{home.get_text(strip=True)} vs {away.get_text(strip=True)}")
    print(f"  .fprc spans found: {len(spans)}")
    for i, s in enumerate(spans):
        print(f"    span[{i}]: '{s.get_text(strip=True)}' (classes: {s.get('class')})")

    fprc_div = row.select_one(".fprc")
    if not fprc_div:
        print("  No .fprc div at all in this row")

print(f"\nTotal upcoming rows inspected: {upcoming_count}")