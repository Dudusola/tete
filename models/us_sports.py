"""us_sports.py - NHL, NFL and NBA as PICKS and BUILDER options, the football way.

Boss (2 Oct 2026): not a separate tab - choose the sport on the Picks page and
in the Builder, and give recommended bets like football does. So this module
turns what models/elo_shadow.py stores (shadow_predictions.markets_json) into:

  picks(conn, sports)        one recommended bet per game, football-style:
                             the moneyline side the model rates likelier, with
                             the SAME tiers football uses (HIGH >= 65%, MEDIUM
                             >= 55%; LOW is not served), the bookmaker's view
                             beside it, and each sport's OWN record.
  slip_options(conn, sports) every market priced for every upcoming game, as
                             models.markets.BetOption, so build_slip() - the
                             Builder's engine - treats a puck line exactly like
                             a football handicap: risk bands, one leg per game,
                             the correlation cap, bankers, swaps.

WHAT THE TIERS MEAN HERE, HONESTLY
---------------------------------
A tier is a claim about how often the pick wins, and for these sports it rests
on Elo's held-out calibration (models/elo.py: NHL 60-70% band said 63.8% and
won 65.0%; NFL accuracy 67% on 2024-25), NOT on a graded live record - there
is none yet. And models/nhl_market_backtest.py found the bookmaker's prices
sharper than ours on 5,000 past NHL games. So every payload carries a
disclosure, every card shows the bookmaker's probability, and the record is
kept PER SPORT, never blended into football's (docs/basketball.md 5.6).
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import nfl_markets, nhl_markets
from models.markets import BetOption, risk_from_prob
from models.sports import AMERICAN_FOOTBALL, BASKETBALL, ICE_HOCKEY, sport_sql

SPORTS = {
    "nhl": {"sport": ICE_HOCKEY, "label": "NHL", "grade": nhl_markets.grade},
    "nfl": {"sport": AMERICAN_FOOTBALL, "label": "NFL", "grade": nfl_markets.grade},
    # Moneyline (Elo) + spread/total at the book's price (elo_shadow.py); graded on the final.
    "nba": {"sport": BASKETBALL, "label": "NBA", "grade": nfl_markets.grade},
}
LABEL_TO_SPORT = {v["label"]: k for k, v in SPORTS.items()}

# Football's serving tiers (models/tiers.py uses the same cut-offs).
HIGH, MEDIUM = 0.65, 0.55

# Market names on the wire. scrapers/booking_codes.py maps THESE strings to
# SportyBet's full-game market ids, so they are a contract: rename one and
# booking silently stops mapping it.
MONEYLINE = "Moneyline"
REG_RESULT = "60-Minute Result"
PUCK_LINE = "Puck Line"
SPREAD = "Spread"
TOTAL_GOALS = "Over/Under Goals"      # "Over/Under" prefix -> build_slip
TOTAL_POINTS = "Over/Under Points"    # splits overs from unders (correlation cap)

DISCLOSURE = {
    "nhl": ("NHL picks are experimental and kept separate from our football record. "
            "On 5,000 past NHL games the bookmaker's prices were sharper than ours, "
            "so check its view beside each pick."),
    "nfl": ("NFL picks are experimental and kept separate from our football record. "
            "Our numbers know results only - not quarterbacks, injuries or weather, "
            "which the bookmaker prices. Check its view beside each pick."),
    "nba": ("NBA picks are experimental and kept separate from our football record. "
            "Our numbers know results and back-to-backs, not injuries or who sits out. "
            "On 5,400 past NBA games the bookmaker was sharper than us, so spread and "
            "total legs in the Builder use its own price. Check its view beside each pick."),
}


# Warning thresholds, from models/line_move_backtest.py (6 Oct 2026): picks
# the price moved against by this much went 6-12 points under their claim in
# BOTH eras of BOTH sports (not significant in any one - hence a WARNING on
# the card, never a changed or pulled pick). NHL: margin-free moneyline
# probability, points. NFL: point spread, points.
MOVE_AGAINST = {"nhl": 5.0, "nfl": 3.0}
QB_WARN = ("Out", "Doubtful", "Questionable", "Injured Reserve", "Suspension")


def _fair(a, b):
    return None if not a or not b else (1 / a) / (1 / a + 1 / b)


def warnings(code: str, facts: dict, pick_key: str) -> list:
    """Visible cautions for a pick: [{"type", "text"}]. Computed from the
    stored facts (the latest pre-kickoff observation), so the card and the
    record's warned-pick tally use the same definition."""
    out = []
    home_pick = pick_key == "ml_home"
    mv = (facts or {}).get("market_movement") or {}
    if code == "nhl" and mv.get("moneyline"):
        o, n = mv["moneyline"]["open"], mv["moneyline"]["now"]
        fo, fn = _fair(o[0], o[1]), _fair(n[0], n[1])
        if fo is not None and fn is not None:
            moved = 100 * ((fo - fn) if home_pick else (fn - fo))
            if moved >= MOVE_AGAINST["nhl"]:
                out.append({"type": "price", "text": f"Price has moved against this pick "
                            f"since it opened ({moved:.0f} points)"})
    if code == "nfl" and mv.get("spread"):
        sp = mv["spread"]
        if sp.get("open_home_line") is not None and sp.get("home_line") is not None:
            d = sp["home_line"] - sp["open_home_line"]
            moved = d if home_pick else -d
            if moved >= MOVE_AGAINST["nfl"]:
                out.append({"type": "price", "text": f"The spread has moved {moved:g} points "
                            f"against this pick since it opened"})
    if code == "nfl":
        for side in ("home", "away"):
            q = ((facts or {}).get("qb") or {}).get(side) or {}
            if q.get("status") in QB_WARN:
                whose = "this pick's" if (side == "home") == home_pick else "the opponent's"
                out.append({"type": "qb", "text": f"QB news: {q['regular']} ({whose} regular "
                            f"starter) is listed {q['status']}"})
    return out


def tier(p: float) -> str:
    return "HIGH" if p >= HIGH else "MEDIUM" if p >= MEDIUM else "LOW"


def selection(key: str, home: str, away: str) -> tuple[str, str] | None:
    """(market, selection) for a grading key, in the wire format above."""
    team = home if "_home" in key else away
    if key in ("ml_home", "ml_away"):
        return MONEYLINE, team
    if key.startswith("reg_"):
        return REG_RESULT, {"reg_home": home, "reg_away": away, "reg_draw": "Draw"}[key]
    if key.startswith("pl_"):
        return PUCK_LINE, f"{team} {key.split('_')[2]}"
    if key.startswith("spread_"):
        return SPREAD, f"{team} {key.split('_')[2]}"
    if key.startswith(("over_", "under_")):
        side, line = key.split("_")
        return None, f"{side.title()} {line}"
    return None


def _offered(it: dict) -> bool:
    """Is this a bet someone is actually offering?

    We price BOTH sides of BOTH puck lines (favourite -1.5 AND favourite +1.5),
    but books only quote the standard pair: favourite -1.5 / underdog +1.5.
    "Favourite +1.5" at ~81% topped every LOW slip and could never be priced
    or booked (found testing, 2 Oct 2026). A puck line is offered only when
    the bookmaker priced that exact side. NFL spreads and totals only exist at
    the book's own line already; NHL totals 5.5/6.5 are both on SportyBet.
    """
    return not it["key"].startswith("pl_") or it.get("market_p") is not None


def _rows(conn, sport: str, upcoming: bool):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    cmp = ">" if upcoming else "<="
    try:
        return conn.execute(f"""
            SELECT m.match_id, m.home_team, m.away_team, m.kickoff_utc, m.match_date,
                   m.live_state, s.p_home, s.market_p_home, s.markets_json, s.facts_json,
                   s.analysis, s.predicted_at,
                   r.home_goals, r.away_goals, r.reg_home_goals, r.reg_away_goals
            FROM shadow_predictions s
            JOIN matches m ON m.match_id = s.match_id
            LEFT JOIN results r ON r.match_id = s.match_id
            WHERE s.markets_json IS NOT NULL {sport_sql(sport)}
              AND m.kickoff_utc {cmp} ?
              {"AND (m.live_state IS NULL OR m.live_state = 'pre')" if upcoming else ""}
            ORDER BY m.kickoff_utc
        """, [now]).fetchall()
    except Exception:
        return []        # ledger not migrated yet (see models/nhl_preview.py)


def _pick(row) -> dict:
    """The recommended bet: the moneyline side the model rates likelier."""
    mk = json.loads(row["markets_json"])
    items = {i["key"]: i for i in mk["items"]}
    side = "ml_home" if row["p_home"] >= 0.5 else "ml_away"
    it = items.get(side, {"p": row["p_home"] if side == "ml_home" else 1 - row["p_home"]})
    return {"key": side, "p": it["p"], "odds": it.get("odds"), "market_p": it.get("market_p"),
            "team": row["home_team"] if side == "ml_home" else row["away_team"]}


def record(conn, code: str) -> dict:
    """This sport's graded record for SERVED picks (HIGH + MEDIUM), on its own."""
    cfg = SPORTS[code]
    won = lost = void = 0
    by_tier = {"HIGH": [0, 0], "MEDIUM": [0, 0]}
    warned = [0, 0]           # picks that carried a warning when they kicked off
    for r in _rows(conn, cfg["sport"], upcoming=False):
        if r["home_goals"] is None:
            continue
        pk = _pick(r)
        t = tier(pk["p"])
        if t == "LOW":
            continue
        g = cfg["grade"](pk["key"], r["home_goals"], r["away_goals"],
                         r["reg_home_goals"], r["reg_away_goals"])
        if g is None:
            void += 1
            continue
        won += g
        lost += 1 - g
        by_tier[t][0] += g
        by_tier[t][1] += 1
        facts = json.loads(r["facts_json"]) if r["facts_json"] else {}
        # Only warnings against THIS pick count (an injured opponent QB helps it).
        if any(w["type"] == "price" or "this pick's" in w["text"]
               for w in warnings(code, facts, pk["key"])):
            warned[0] += g
            warned[1] += 1
    n = won + lost
    return {"sport": code, "graded": n, "won": won, "lost": lost, "void": void,
            "hit_pct": round(won / n * 100, 1) if n else None,
            "tiers": {t: {"won": w, "graded": g} for t, (w, g) in by_tier.items()},
            # Kept apart so the live record can confirm or kill the warnings.
            "warned": {"won": warned[0], "graded": warned[1]}}


def picks(conn, codes: list) -> dict:
    out, records = [], {}
    for code in codes:
        cfg = SPORTS[code]
        records[code] = record(conn, code)
        for r in _rows(conn, cfg["sport"], upcoming=True):
            pk = _pick(r)
            t = tier(pk["p"])
            if t == "LOW":
                continue
            mk = json.loads(r["markets_json"])
            gap = (None if pk["market_p"] is None
                   else round((pk["p"] - pk["market_p"]) * 100, 1))
            markets = []
            for it in mk["items"]:
                if not _offered(it):
                    continue
                sel = selection(it["key"], r["home_team"], r["away_team"])
                if not sel:
                    continue
                name = sel[0] or (TOTAL_GOALS if code == "nhl" else TOTAL_POINTS)
                markets.append({"market": name, "selection": sel[1], "key": it["key"],
                                # model_p = an NBA leg priced at the book's number: we have no own view ("—")
                                "probability": None if "model_p" in it else it["p"],
                                "book_probability": it.get("market_p"),
                                "book_odds": it.get("odds")})
            facts = json.loads(r["facts_json"]) if r["facts_json"] else {}
            out.append({
                "match_id": r["match_id"], "sport": code, "league": cfg["label"],
                "league_display": cfg["label"],
                "home_team": r["home_team"], "away_team": r["away_team"],
                "match_date": r["match_date"], "kickoff_utc": r["kickoff_utc"],
                "best_bet": pk["team"], "best_bet_market": MONEYLINE,
                # What My Record stores and grades (account.html compares it
                # with results.outcome). The moneyline includes overtime, and
                # so does the stored final, so "Home Win" is exactly this bet.
                "track_selection": "Home Win" if pk["key"] == "ml_home" else "Away Win",
                "best_bet_label": f"{pk['team']} to win (overtime counts)",
                "best_bet_prob": pk["p"], "confidence": t,
                "home_win_prob": round(r["p_home"], 4), "away_win_prob": round(1 - r["p_home"], 4),
                "book_odds": pk["odds"], "book_probability": pk["market_p"], "market_gap": gap,
                "expected_total": mk.get("exp_total"),
                "markets": markets,
                "warnings": warnings(code, facts, pk["key"]),
                "analysis": r["analysis"],
                "form": {s: (facts.get(s) or {}).get("last10") for s in ("home", "away")},
                # WHICH side has not played yet - the card must not imply both.
                "first_game_teams": [name for side, name in (("home", r["home_team"]),
                                                             ("away", r["away_team"]))
                                     if (facts.get(side) or {}).get("new_season")],
            })
    out.sort(key=lambda p: p["kickoff_utc"] or "")
    return {"picks": out, "records": records,
            "disclosures": {c: DISCLOSURE[c] for c in codes}}


def slip_options(conn, codes: list) -> list:
    """Every priced market for every upcoming game, as BetOptions."""
    opts = []
    for code in codes:
        cfg = SPORTS[code]
        for r in _rows(conn, cfg["sport"], upcoming=True):
            mk = json.loads(r["markets_json"])
            ko = r["kickoff_utc"] or ""
            for it in mk["items"]:
                if not _offered(it):
                    continue
                # The book beats our NHL totals (shadow_report, 20 Oct 2026: log loss
                # +0.010, CI [+0.002, +0.019], 124 games; same in the backtest), so
                # the Builder does not pick them. Still shown on the card.
                if code == "nhl" and it["key"].startswith(("over_", "under_")):
                    continue
                sel = selection(it["key"], r["home_team"], r["away_team"])
                if not sel:
                    continue
                name = sel[0] or (TOTAL_GOALS if code == "nhl" else TOTAL_POINTS)
                opts.append(BetOption(
                    match_id=r["match_id"], home_team=r["home_team"], away_team=r["away_team"],
                    league=cfg["label"], match_date=ko[:10] or r["match_date"],
                    kickoff_time=ko[11:16], market=name, selection=sel[1],
                    probability=float(it["p"]), risk=risk_from_prob(float(it["p"])),
                    kickoff_utc=ko or None))
    return opts


def sport_of_league(league: str | None) -> str:
    """'NHL' -> 'nhl', 'NFL' -> 'nfl', anything else -> 'football'."""
    return LABEL_TO_SPORT.get((league or "").strip(), "football")


def settle_leg(league: str, market: str, sel: str, home: str, away: str,
               hs: int, as_: int, rh, ra) -> str | None:
    """'won' / 'lost' / 'void' / None for a tracked NHL/NFL slip leg - the
    reverse of selection(), graded by the SAME functions as the sport's record.
    None = cannot grade (unknown market, or a 60-minute leg with no regulation
    score) - never a guess. 'void' = an NFL push or tie."""
    import re
    code = LABEL_TO_SPORT.get((league or "").strip())
    if not code:
        return None
    key = None
    if market == MONEYLINE:
        key = "ml_home" if sel == home else "ml_away" if sel == away else None
    elif market == REG_RESULT:
        key = {home: "reg_home", "Draw": "reg_draw", away: "reg_away"}.get(sel)
    elif market in (PUCK_LINE, SPREAD):
        m = re.match(r"^(.*) ([+-]\d+(?:\.\d+)?)$", sel or "")
        if m and m.group(1) in (home, away):
            side = "home" if m.group(1) == home else "away"
            key = f"{'pl' if market == PUCK_LINE else 'spread'}_{side}_{m.group(2)}"
    elif market in (TOTAL_GOALS, TOTAL_POINTS):
        m = re.match(r"^(Over|Under) (\d+(?:\.\d+)?)$", sel or "")
        if m:
            key = f"{m.group(1).lower()}_{m.group(2)}"
    if not key:
        return None
    if key.startswith("reg_") and (rh is None or ra is None):
        return None
    g = SPORTS[code]["grade"](key, hs, as_, rh, ra)
    if g is None:
        return "void"           # NFL push / tie on a whole-number line
    return "won" if g else "lost"
