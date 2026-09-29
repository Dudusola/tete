"""shadow_report.py - does Elo beat the bookmaker? (NFL / NBA / NHL / MLB)

Reads shadow_predictions (models/elo_shadow.py) for games that now have a
final result, and scores Elo's pre-game home-win probability against the
market's margin-free probability on the SAME games. Lower log loss wins.

Graded on the FINAL result, overtime and shootouts included, because the
recorded moneyline is a full-game market (market_lines, docs/basketball.md
section 2). NFL ties (outcome D) are left out: a 2-way moneyline pushes on a
tie, so neither side was right.

How to read it: until each league has a few hundred graded games, expect the
interval on the difference to straddle zero. A market that has priced these
teams for years is a very hard bar; "Elo roughly matches the market" is a
good result for a model with no injuries, lineups or starting pitchers.

Usage:
    python db/shadow_report.py
"""

import math
import os
import sqlite3
import sys

DB_PATH = os.getenv("DB_PATH", "./db/sports_predictor.db")
LABEL = {"american_football": "NFL", "basketball": "NBA", "ice_hockey": "NHL", "baseball": "MLB"}


def ll(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def main() -> int:
    if not os.path.exists(DB_PATH):
        print(f"No database at {DB_PATH}")
        return 1
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("""
            SELECT m.sport, s.p_home, s.market_p_home, r.outcome
            FROM shadow_predictions s
            JOIN matches m ON m.match_id = s.match_id
            JOIN results r ON r.match_id = s.match_id
            WHERE r.outcome IN ('H', 'A')
        """).fetchall()
        pending = conn.execute("""
            SELECT COUNT(*) FROM shadow_predictions s
            WHERE s.match_id NOT IN (SELECT match_id FROM results)
        """).fetchone()[0]
    except sqlite3.OperationalError:
        print("No shadow_predictions table yet - models/elo_shadow.py has not run.")
        return 0
    conn.close()

    print("=" * 78)
    print("  SHADOW ELO vs THE MARKET — graded full-game moneylines (never served)")
    print("=" * 78)
    print(f"  Awaiting a result: {pending}")
    if not rows:
        print("\n  Nothing graded yet.")
        return 0
    print(f"\n  {'league':6} {'n':>5} {'Elo acc':>8} {'mkt acc':>8} {'Elo LL':>8} "
          f"{'mkt LL':>8}  {'Elo-mkt LL, 95% CI':>24}")
    for sport in ("american_football", "basketball", "ice_hockey", "baseball"):
        g = [r for r in rows if r[0] == sport and r[2] is not None]
        if not g:
            continue
        ys = [1 if r[3] == "H" else 0 for r in g]
        e = [ll(r[1], y) for r, y in zip(g, ys)]
        mk = [ll(r[2], y) for r, y in zip(g, ys)]
        d = [a - b for a, b in zip(e, mk)]
        n = len(d); md = sum(d) / n
        sd = math.sqrt(sum((x - md) ** 2 for x in d) / (n - 1)) if n > 1 else 0
        half = 1.96 * sd / math.sqrt(n) if n > 1 else 0
        acc = lambda probs: sum((p >= 0.5) == (y == 1) for p, y in zip(probs, ys)) / n
        verdict = ("n<50, too few" if n < 50 else
                   "Elo BETTER" if md + half < 0 else
                   "market better" if md - half > 0 else "no difference yet")
        print(f"  {LABEL[sport]:6} {n:5} {acc([r[1] for r in g]):8.1%} {acc([r[2] for r in g]):8.1%} "
              f"{sum(e)/n:8.4f} {sum(mk)/n:8.4f}  {md:+.4f} [{md-half:+.4f},{md+half:+.4f}]  {verdict}")
    no_mkt = sum(1 for r in rows if r[2] is None)
    if no_mkt:
        print(f"\n  {no_mkt} graded games had no stored price and are excluded.")
    nhl_markets_report()
    return 0


def nhl_markets_report() -> None:
    """Every NHL market we priced (models/nhl_markets.py) against the book's.

    One row per market family, scored on the SAME selections: those the book
    was quoting at the line we priced. Each two-way pair is scored once, from
    the side listed first (ml_home, pl_*_-1.5, over_*), since the other side is
    its complement. Then the preview's verdict record at the stored price.
    """
    import json
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from models.nhl_markets import grade, verdict
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("""
            SELECT s.markets_json, s.facts_json, m.home_team, m.away_team,
                   r.home_goals, r.away_goals, r.reg_home_goals, r.reg_away_goals
            FROM shadow_predictions s
            JOIN matches m ON m.match_id = s.match_id
            JOIN results r ON r.match_id = s.match_id
            WHERE m.sport = 'ice_hockey' AND s.markets_json IS NOT NULL
        """).fetchall()
    except sqlite3.OperationalError:
        return
    conn.close()
    print("\n" + "=" * 78)
    print("  NHL MARKETS vs THE BOOK - private preview (webapp/hockey.html)")
    print("=" * 78)
    if not rows:
        print("  No NHL game with priced markets has a result yet.")
        return
    families = {"Moneyline": ("ml_home",), "Puck line": ("pl_home_-1.5", "pl_away_-1.5"),
                "Total": ("over_5.5", "over_6.5")}
    print(f"  {'market':10} {'n':>4} {'our LL':>8} {'book LL':>8}  {'ours-book, 95% CI':>24}")
    for fam, keys in families.items():
        d, ours, book = [], [], []
        for mk, _, _, _, hs, as_, rh, ra in rows:
            for it in json.loads(mk)["items"]:
                if it["key"] not in keys or it.get("market_p") is None:
                    continue
                y = grade(it["key"], hs, as_, rh, ra)
                if y is None:
                    continue
                a, b = ll(it["p"], y), ll(it["market_p"], y)
                ours.append(a)
                book.append(b)
                d.append(a - b)
        n = len(d)
        if not n:
            print(f"  {fam:10} {0:4}  nothing graded")
            continue
        md = sum(d) / n
        sd = math.sqrt(sum((x - md) ** 2 for x in d) / (n - 1)) if n > 1 else 0
        half = 1.96 * sd / math.sqrt(n) if n > 1 else 0
        call = ("n<50, too few" if n < 50 else "OURS BETTER" if md + half < 0 else
                "book better" if md - half > 0 else "no difference yet")
        print(f"  {fam:10} {n:4} {sum(ours)/n:8.4f} {sum(book)/n:8.4f}  "
              f"{md:+.4f} [{md-half:+.4f},{md+half:+.4f}]  {call}")

    bets = won = 0
    units = 0.0
    for mk, facts, home, away, hs, as_, rh, ra in rows:
        if not facts:
            continue
        m = json.loads(mk)
        _, key = verdict(m, json.loads(facts), home, away)
        odds = next((i["odds"] for i in m["items"] if i["key"] == key), None)
        y = grade(key, hs, as_, rh, ra) if key else None
        if y is None or not odds:
            continue
        bets += 1
        won += y
        units += (odds - 1) if y else -1
    print(f"\n  Verdicts: {won}-{bets - won} on {bets} bets, {units:+.2f} units at 1 unit flat"
          f"{'  (too few to mean anything)' if bets < 100 else ''}")


if __name__ == "__main__":
    sys.exit(main())
