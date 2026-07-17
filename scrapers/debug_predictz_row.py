"""Dump the first PredictZ match row's structure"""
from bs4 import BeautifulSoup

with open("data/raw/predictz_page.html", "r", encoding="utf-8") as f:
    soup = BeautifulSoup(f.read(), "lxml")

rows = soup.select("div.pttr")
print(f"Found {len(rows)} rows with div.pttr\n")
print("=" * 70)
print("FIRST ROW PRETTIFIED:")
print("=" * 70)
print(rows[0].prettify()[:3000])

print("\n" + "=" * 70)
print("ALL CLASSES USED INSIDE FIRST ROW:")
print("=" * 70)
classes = set()
for el in rows[0].find_all(True):
    for c in (el.get("class") or []):
        classes.add(c)
print(sorted(classes))