"""
Database cleanup utility
- Removes test data
- Dedupes scraped predictions (keeps latest per match)
- Clears old model predictions

Usage:
    python db/cleanup.py
"""

import sqlite3

conn = sqlite3.connect("./db/sports_predictor.db")
cur = conn.cursor()

# Remove test matches
cur.execute("DELETE FROM matches WHERE match_id LIKE 'test_%'")
print(f"Removed {cur.rowcount} test matches")

cur.execute("DELETE FROM scraped_predictions WHERE match_id LIKE 'test_%'")
print(f"Removed {cur.rowcount} test predictions")

# Clear model predictions (will be regenerated on next predict run)
cur.execute("DELETE FROM model_predictions")
print(f"Cleared {cur.rowcount} old model predictions")

# Dedupe scraped predictions - keep only the latest per match
cur.execute("""
    DELETE FROM scraped_predictions
    WHERE id NOT IN (
        SELECT MAX(id) FROM scraped_predictions GROUP BY match_id
    )
""")
print(f"Removed {cur.rowcount} duplicate predictions")

conn.commit()
conn.close()
print("✅ DB cleaned")