"""Quick read-only inspection: what scores did we record, and do the
won/lost flags line up with them? Helps diagnose score-matching bugs.
Read-only - changes nothing."""
import sys, os, sqlite3
DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT m.home_team, m.away_team, m.match_date, m.match_id,
           mp.best_bet, mp.confidence,
           r.home_goals, r.away_goals, r.outcome
    FROM model_predictions mp
    JOIN results r ON r.match_id = mp.match_id
    JOIN matches m ON m.match_id = mp.match_id
    ORDER BY m.match_date DESC
""").fetchall()

print(f"{'MATCH':<38} {'PICK':<12} {'SCORE':<7} {'OUT':<4} {'MATCH_ID'}")
print("-"*95)
for r in rows:
    match = f"{r['home_team']} vs {r['away_team']}"[:37]
    score = f"{r['home_goals']}-{r['away_goals']}" if r['home_goals'] is not None else "NULL"
    print(f"{match:<38} {r['best_bet']:<12} {score:<7} {r['outcome'] or '?':<4} {r['match_id']}")
conn.close()
