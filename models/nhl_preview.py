"""nhl_preview.py - the payload behind the PRIVATE NHL preview page.

Read-only. Served at /api/preview/nhl to webapp/hockey.html, which is linked
from nowhere and marked noindex. It is the one reader of shadow_predictions
outside the reports, and it exists so Boss can judge the NHL analysis and
markets by eye while the shadow ledger grades them.

It never touches /api/picks, /api/stats, the Record page or the bot. The
preview's own record (`record` below) is reported HERE only, separately from
football, as docs/basketball.md section 5.6 requires of any second sport.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.nhl_markets import PARAMS_PATH, grade, market_label, verdict
from models.sports import ICE_HOCKEY, sport_sql

DISCLOSURE = ("Private preview. These NHL numbers have no graded track record "
              "yet: they are being tested against the bookmaker before anything "
              "is published. Not a tip.")

# The order a hockey bettor reads a market board in.
GROUP_ORDER = ["ml", "reg", "pl", "total"]


def _group(key: str) -> str:
    return "ml" if key.startswith("ml_") else "reg" if key.startswith("reg_") else \
        "pl" if key.startswith("pl_") else "total"


def _markets(items: list, home: str, away: str) -> list:
    groups = {}
    for it in items:
        g = _group(it["key"])
        title, sel = market_label(it["key"], home, away)
        edge = None if it.get("market_p") is None else round((it["p"] - it["market_p"]) * 100, 1)
        groups.setdefault(g, {"id": g, "title": title, "options": []})
        groups[g]["options"].append({"key": it["key"], "selection": sel, "p": it["p"],
                                     "market_p": it.get("market_p"), "odds": it.get("odds"),
                                     "edge": edge})
    return [groups[g] for g in GROUP_ORDER if g in groups]


def _facts_summary(f: dict) -> dict:
    def side(s):
        return {k: s.get(k) for k in ("rank", "of", "last10", "venue10", "streak",
                                      "rest_days", "back_to_back", "new_season", "last_game")}
    return {"home": side(f["home"]), "away": side(f["away"]), "h2h": f.get("h2h", []),
            "movement": f.get("market_movement", {})}


def _backtest() -> dict | None:
    try:
        p = json.load(open(PARAMS_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {"seasons": p.get("seasons"), "fitted_at": p.get("fitted_at"),
            "held_out": {k: {"n": v["n"], "log_loss": v["log_loss"],
                             "base_log_loss": v["base_log_loss"]}
                         for k, v in p.get("held_out", {}).items()}}


def build(conn) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = conn.execute(f"""
        SELECT m.match_id, m.home_team, m.away_team, m.kickoff_utc, m.live_state,
               s.p_home, s.market_p_home, s.predicted_at, s.markets_json, s.facts_json,
               s.analysis, s.analysis_at,
               r.home_goals, r.away_goals, r.decided_in, r.reg_home_goals, r.reg_away_goals
        FROM shadow_predictions s
        JOIN matches m ON m.match_id = s.match_id
        LEFT JOIN results r ON r.match_id = s.match_id
        WHERE s.markets_json IS NOT NULL {sport_sql(ICE_HOCKEY)}
          AND m.kickoff_utc >= ?
        ORDER BY m.kickoff_utc
    """, [(now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M")]).fetchall()

    upcoming, recent = [], []
    bets = won = 0
    units = 0.0
    for r in rows:
        home, away = r["home_team"], r["away_team"]
        mk = json.loads(r["markets_json"])
        facts = json.loads(r["facts_json"]) if r["facts_json"] else None
        call, key = verdict(mk, facts, home, away) if facts else ("no verdict", None)
        odds = next((i["odds"] for i in mk["items"] if i["key"] == key), None)
        game = {"match_id": r["match_id"], "home": home, "away": away,
                "kickoff_utc": r["kickoff_utc"], "exp_total": mk.get("exp_total"),
                "verdict": {"text": call, "key": key, "odds": odds}}
        if r["home_goals"] is not None:
            res = grade(key, r["home_goals"], r["away_goals"],
                        r["reg_home_goals"], r["reg_away_goals"]) if key else None
            if res is not None and odds:
                bets += 1
                won += res
                units += (odds - 1) if res else -1
            game.update(score=f"{r['home_goals']}-{r['away_goals']}",
                        decided_in=r["decided_in"],
                        result=None if res is None else ("won" if res else "lost"))
            recent.append(game)
            continue
        kickoff = datetime.strptime(r["kickoff_utc"], "%Y-%m-%d %H:%M")
        if kickoff < now - timedelta(hours=4):
            continue            # started long ago, no final yet - not "upcoming"
        game.update(started=kickoff <= now,
                    markets=_markets(mk["items"], home, away),
                    facts=_facts_summary(facts) if facts else None,
                    analysis=r["analysis"], analysis_at=r["analysis_at"],
                    priced_at=r["predicted_at"])
        upcoming.append(game)

    return {"disclosure": DISCLOSURE,
            "generated_at": now.strftime("%Y-%m-%d %H:%M"),
            "upcoming": upcoming,
            "recent": list(reversed(recent)),
            # Flat 1 unit on every verdict that named a selection, at the
            # stored price. Tiny for weeks; shown with its n, never as a %.
            "record": {"bets": bets, "won": won, "lost": bets - won,
                       "units": round(units, 2)},
            "backtest": _backtest()}
