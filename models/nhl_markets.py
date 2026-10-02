"""nhl_markets.py - price the markets an NHL game is actually bet on.

WHICH MARKETS, AND WHY THESE
----------------------------
    Moneyline          full game, overtime and shootout included. The US default.
    60-minute result   3-way, regulation only - a draw means it went to OT.
                       This is what SportyBet and most Nigerian books call
                       "1X2" for hockey (docs/basketball.md section 2's trap).
    Puck line +-1.5    full game. -1.5 needs a win by 2+; an overtime or
                       shootout win is always by exactly one, so it never
                       covers -1.5.
    Total goals 5.5/6.5  full game; the shootout winner is credited one goal,
                       as the books settle it and as ESPN's final already is.

HOW EACH IS PRICED - measured, not assumed (29 Sep 2026)
-------------------------------------------------------
Textbook Poisson does NOT fit the NHL: regulation totals are UNDER-dispersed
(variance 5.5 on a mean of 6.0) and games reach overtime ~22% of the time where
independent Poisson says ~16%. So each market is a small calibrated model fitted
on the history (data/sports_history/nhl.csv), 2012-2024 seasons, and scored ONCE
on the two held-out seasons:

    puck line      logistic on Elo's log-odds + expected total    real skill
    totals         logistic on the expected total                 small skill
    reg. draw      the league rate: no feature beat a constant    no skill
    OT winner      the league rate: a coin flip, near enough      no skill

`python models/nhl_markets.py` refits and prints the held-out table; the numbers
are in data/sports_history/nhl_markets.json. Where the backtest found no skill
we say so and use the base rate - a model that cannot beat a constant is not
allowed to pretend otherwise.

The moneyline is Elo's (models/elo.py), unchanged, so the shadow ledger keeps
grading one number. The 60-minute split is derived FROM it, so the two can
never contradict each other.

Expected total = each side's point-in-time attack rate averaged with the
opponent's defence rate, from exponentially weighted REGULATION goals (the
shootout is not scoring). Ratings are regressed 40% to the league mean each
off-season, the same idea as Elo's revert.

WHAT IT CANNOT KNOW: starting goalies, injuries, lineups, travel. The market
knows all of them. Expect it to beat these numbers; the point of the shadow
ledger is to measure by how much, per market, before anything is served.
"""

import csv
import json
import math
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.elo import HIST, load, run_elo

PARAMS_PATH = os.path.join(HIST, "nhl_markets.json")
SEASON_KEEP = 0.6          # share of a team's rate kept over the off-season
NEW_SEASON_GAP_DAYS = 45   # same rule as models/elo_shadow.py
TOTAL_LINES = (5.5, 6.5)
HALF_LIVES = (10, 20, 40)  # games; chosen on validation seasons, never on test


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def _goals(r: dict) -> tuple:
    """Regulation goals if known, else the final (pre-2010 style rows)."""
    if r.get("reg_home") not in (None, ""):
        return int(r["reg_home"]), int(r["reg_away"])
    return int(r["home_score"]), int(r["away_score"])


def _raw_rows() -> dict:
    with open(os.path.join(HIST, "nhl.csv"), newline="", encoding="utf-8") as fh:
        return {(r["kickoff_utc"] or r["date"], r["home_id"]): r
                for r in csv.DictReader(fh)}


def walk(half_life: float, elo_params: dict):
    """Every historical game with its PRE-game features, in date order.

    Yields dicts carrying Elo's p_home and the expected regulation goals for
    each side, computed before that game updates anything. Returns the rates
    as they stand after the last game, for pricing upcoming games.
    """
    games = load("nhl")
    raw = _raw_rows()
    pairs, _ = run_elo(games, elo_params["k"], elo_params["hfa"],
                       elo_params["revert"], elo_params["mov"])
    keep = 0.5 ** (1 / half_life)
    gf, ga = {}, {}
    league, season = 3.0, None
    out = []
    for g, p in pairs:
        r = raw[(g["date"], g["home"])]
        if g["season"] != season:
            if season is not None:
                for t in gf:
                    gf[t] = gf[t] * SEASON_KEEP + league * (1 - SEASON_KEEP)
                    ga[t] = ga[t] * SEASON_KEEP + league * (1 - SEASON_KEEP)
            season = g["season"]
        h, a = g["home"], g["away"]
        for t in (h, a):
            gf.setdefault(t, league)
            ga.setdefault(t, league)
        exp_h, exp_a = (gf[h] + ga[a]) / 2, (gf[a] + ga[h]) / 2
        out.append({"season": g["season"], "type": r["season_type"],
                    "p": p, "exp_h": exp_h, "exp_a": exp_a,
                    "hs": g["hs"], "as": g["as"],
                    "reg": None if r["reg_home"] == "" else
                    (int(r["reg_home"]), int(r["reg_away"]))})
        rh, ra = _goals(r)
        gf[h], ga[h] = gf[h] * keep + rh * (1 - keep), ga[h] * keep + ra * (1 - keep)
        gf[a], ga[a] = gf[a] * keep + ra * (1 - keep), ga[a] * keep + rh * (1 - keep)
        league = league * 0.999 + (rh + ra) / 2 * 0.001
    return out, {"gf": gf, "ga": ga, "league": league,
                 "last": max(g["date"] for g in games)[:10]}


# ---------------------------------------------------------------- the models

def _features(kind: str, p: float, total: float) -> list:
    return {"total": [total], "margin": [_logit(p), total]}[kind]


TASKS = {
    # name: (feature kind, outcome)
    "over_5.5": ("total", lambda x: x["hs"] + x["as"] > 5.5),
    "over_6.5": ("total", lambda x: x["hs"] + x["as"] > 6.5),
    "home_by_2": ("margin", lambda x: x["hs"] - x["as"] >= 2),
    "away_by_2": ("margin", lambda x: x["as"] - x["hs"] >= 2),
}


def _apply(model: dict, feats: list) -> float:
    return _sigmoid(model["intercept"] + sum(c * f for c, f in zip(model["coef"], feats)))


def price(p_home: float, exp_total: float, params: dict) -> dict:
    """Our probability for every market outcome. Keys are grading keys (grade())."""
    tie, q = params["reg_draw_rate"], params["ot_home_rate"]
    m = params["models"]
    reg_h = p_home - tie * q
    reg_a = (1 - p_home) - tie * (1 - q)
    # A win by 2+ is a regulation win by definition (OT/SO wins are by one),
    # so the puck line can never be likelier than the regulation win.
    h2 = min(_apply(m["home_by_2"], _features("margin", p_home, exp_total)), reg_h)
    a2 = min(_apply(m["away_by_2"], _features("margin", p_home, exp_total)), reg_a)
    out = {"ml_home": p_home, "ml_away": 1 - p_home,
           "reg_home": reg_h, "reg_draw": tie, "reg_away": reg_a,
           "pl_home_-1.5": h2, "pl_away_+1.5": 1 - h2,
           "pl_away_-1.5": a2, "pl_home_+1.5": 1 - a2}
    for line in TOTAL_LINES:
        o = _apply(m[f"over_{line}"], _features("total", p_home, exp_total))
        out[f"over_{line}"], out[f"under_{line}"] = o, 1 - o
    return {k: round(v, 4) for k, v in out.items()}


def grade(key: str, hs: int, as_: int, reg_h, reg_a) -> int | None:
    """1 won / 0 lost / None ungradable, for one of price()'s keys.

    Full-game markets use the FINAL (overtime and shootout included); the
    60-minute market uses the regulation score and is ungradable without it.
    """
    if key == "ml_home":
        return int(hs > as_)
    if key == "ml_away":
        return int(as_ > hs)
    if key.startswith("reg_"):
        if reg_h is None or reg_a is None:
            return None
        return int({"reg_home": reg_h > reg_a, "reg_draw": reg_h == reg_a,
                    "reg_away": reg_a > reg_h}[key])
    if key.startswith("pl_"):
        _, side, line = key.split("_")
        margin = (hs - as_) if side == "home" else (as_ - hs)
        return int(margin + float(line) > 0)
    if key.startswith(("over_", "under_")):
        side, line = key.split("_")
        total = hs + as_
        return int(total > float(line)) if side == "over" else int(total < float(line))
    return None


# ------------------------------------------------------------- describing

# Every gap to the bookmaker below this many points -> we agree with the price.
AGREE_POINTS = 3.0


def market_label(key: str, home: str, away: str) -> tuple[str, str]:
    """(market group, selection) in words a football bettor can follow."""
    if key == "ml_home":
        return "Moneyline (overtime & shootout count)", f"{home} to win"
    if key == "ml_away":
        return "Moneyline (overtime & shootout count)", f"{away} to win"
    if key.startswith("reg_"):
        sel = {"reg_home": f"{home} win", "reg_draw": "Draw (goes to overtime)",
               "reg_away": f"{away} win"}[key]
        return "60-minute result (1X2, overtime not counted)", sel
    if key.startswith("pl_"):
        _, side, line = key.split("_")
        team = home if side == "home" else away
        what = "win by 2+ goals" if line == "-1.5" else "win, or lose by exactly 1"
        return "Puck line (±1.5 goal handicap)", f"{team} {line} ({what})"
    side, line = key.split("_")
    return f"Total goals {line} (overtime & shootout count)", f"{side.title()} {line}"


# A lean needs a MONEYLINE disagreement at least this big. Set by
# models/nhl_market_backtest.py (2 Oct 2026, 5,000 past games, blind seasons):
# every smaller edge, and every puck-line and totals edge, LOST money against
# the bookmaker (-3% to -7%, roughly its margin). Only at 10+ points did the
# market then move our way more often than not (62% / 68% in two separate
# eras, +4 pts on average) - information, but betting it at the open was
# +19% on 154 bets with an interval of -11% to +49%: NOT proven profit.
LEAN_POINTS = 10.0


def verdict(markets: dict, facts: dict, home: str, away: str) -> tuple[str, str | None]:
    """The verdict, decided HERE rather than by the writer (who once called a
    +3.2-point edge "moderate"). The writer may soften it to no bet; never
    strengthen it.

    Rules come from the backtest, not taste: moneyline only, 10+ points only,
    and never on a team's first game of the season (the ratings have not seen
    the new roster; the backtest's signal was measured in-season).
    """
    best = None
    for it in markets.get("items", []):
        if it.get("market_p") is None:
            continue
        gap = (it["p"] - it["market_p"]) * 100
        if best is None or gap > best[0]:
            best = (gap, it)
    if best is None or best[0] < AGREE_POINTS:
        return "no bet - the price looks right", None
    gap, it = best
    ml = max((((i["p"] - i["market_p"]) * 100, i) for i in markets.get("items", [])
              if i["key"].startswith("ml_") and i.get("market_p") is not None),
             default=None, key=lambda t: t[0])
    first_game = facts["home"].get("new_season") or facts["away"].get("new_season")
    if ml and ml[0] >= LEAN_POINTS and not first_game:
        _, sel = market_label(ml[1]["key"], home, away)
        return (f"experimental lean: {sel} at {ml[1]['odds']} (edge {ml[0]:+.1f} points)",
                ml[1]["key"])
    _, sel = market_label(it["key"], home, away)
    why = ("first game of the season - the ratings have not seen the new roster"
           if ml and ml[0] >= LEAN_POINTS else
           "too small to beat the bookmaker's margin in 5,000 past games")
    return f"no bet - our biggest edge ({sel}, {gap:+.1f} points) is {why}", None


# ------------------------------------------------------------------ fitting

def _log_loss(ys, ps) -> float:
    eps = 1e-9
    return sum(-(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps)))
               for y, p in zip(ys, ps)) / len(ys)


def _fit_logistic(X, y) -> dict:
    from sklearn.linear_model import LogisticRegression   # fit-time only
    m = LogisticRegression(C=1e6, max_iter=1000).fit(X, y)
    return {"coef": [round(float(c), 6) for c in m.coef_[0]],
            "intercept": round(float(m.intercept_[0]), 6)}


def _fit_all(rows: list) -> dict:
    models = {}
    for name, (kind, outcome) in TASKS.items():
        X = [_features(kind, x["p"], x["exp_h"] + x["exp_a"]) for x in rows]
        models[name] = _fit_logistic(X, [int(outcome(x)) for x in rows])
    reg = [x for x in rows if x["reg"]]
    ties = [x for x in reg if x["reg"][0] == x["reg"][1]]
    return {"models": models,
            "reg_draw_rate": round(len(ties) / len(reg), 4),
            "ot_home_rate": round(sum(x["hs"] > x["as"] for x in ties) / len(ties), 4)}


def _score(rows: list, params: dict) -> dict:
    """Held-out log loss per market against the training base rate."""
    report = {}
    for name, (kind, outcome) in TASKS.items():
        ys = [int(outcome(x)) for x in rows]
        ps = [_apply(params["models"][name],
                     _features(kind, x["p"], x["exp_h"] + x["exp_a"])) for x in rows]
        base = params["base_rates"][name]
        bands = []
        for lo in [i / 10 for i in range(10)]:
            idx = [i for i, p in enumerate(ps) if lo <= p < lo + 0.1]
            if len(idx) >= 50:
                bands.append({"band": f"{int(lo*100)}-{int(lo*100)+10}%", "n": len(idx),
                              "claimed": round(sum(ps[i] for i in idx) / len(idx), 3),
                              "actual": round(sum(ys[i] for i in idx) / len(idx), 3)})
        report[name] = {"n": len(ys), "log_loss": round(_log_loss(ys, ps), 4),
                        "base_log_loss": round(_log_loss(ys, [base] * len(ys)), 4),
                        "calibration": bands}
    # The 60-minute 3-way, derived exactly as price() derives it.
    reg = [x for x in rows if x["reg"]]
    ll3 = llb = 0.0
    freq = params["base_rates"]["reg3"]
    for x in reg:
        pr = price(x["p"], x["exp_h"] + x["exp_a"], params)
        rh, ra = x["reg"]
        k = "reg_home" if rh > ra else "reg_away" if ra > rh else "reg_draw"
        ll3 -= math.log(max(pr[k], 1e-9))
        llb -= math.log(freq[k])
    report["reg_3way"] = {"n": len(reg), "log_loss": round(ll3 / len(reg), 4),
                          "base_log_loss": round(llb / len(reg), 4)}
    return report


def fit() -> dict:
    elo = json.load(open(os.path.join(HIST, "elo_params.json"), encoding="utf-8"))["nhl"]
    train_seasons, test_seasons = elo["seasons"]["train"], elo["seasons"]["test"]
    first, last = train_seasons[0], train_seasons[-1]

    def regular(rows, lo, hi):
        return [x for x in rows if x["type"] == "2" and lo <= x["season"] <= hi]

    # Half-life chosen on the last two TRAINING seasons, fitted on the rest.
    choice = None
    for hl in HALF_LIVES:
        rows, _ = walk(hl, elo["params"])
        fit_rows, val = regular(rows, first, last - 2), regular(rows, last - 1, last)
        params = _fit_all(fit_rows)
        ll = sum(_log_loss([int(o(x)) for x in val],
                           [_apply(params["models"][n], _features(k, x["p"], x["exp_h"] + x["exp_a"]))
                            for x in val]) for n, (k, o) in TASKS.items())
        if choice is None or ll < choice[1]:
            choice = (hl, ll)
    half_life = choice[0]

    rows, _ = walk(half_life, elo["params"])
    train = regular(rows, first, last)
    test = regular(rows, test_seasons[0], test_seasons[-1])
    params = _fit_all(train)
    params["half_life"] = half_life
    reg = [x for x in train if x["reg"]]
    params["base_rates"] = {n: round(sum(o(x) for x in train) / len(train), 4)
                            for n, (_, o) in TASKS.items()}
    params["base_rates"]["reg3"] = {
        "reg_home": sum(x["reg"][0] > x["reg"][1] for x in reg) / len(reg),
        "reg_draw": sum(x["reg"][0] == x["reg"][1] for x in reg) / len(reg),
        "reg_away": sum(x["reg"][0] < x["reg"][1] for x in reg) / len(reg)}
    params["held_out"] = _score(test, params)
    params["seasons"] = {"train": [first, last], "test": test_seasons}
    params["fitted_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return params


# ------------------------------------------------------------------ serving

def load_params() -> dict | None:
    try:
        return json.load(open(PARAMS_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def current_rates(params: dict, elo_params: dict) -> dict:
    """Team scoring rates after the last completed game in the history."""
    _, state = walk(params["half_life"], elo_params)
    return state


def expected_total(state: dict, home_id: str, away_id: str, kickoff_date: str) -> float:
    gf, ga, lg = state["gf"], state["ga"], state["league"]
    gap = (datetime.strptime(kickoff_date, "%Y-%m-%d")
           - datetime.strptime(state["last"], "%Y-%m-%d")).days

    def rate(d, t):
        v = d.get(t, lg)
        # The history has not reached the new season: apply the off-season
        # regression walk() would apply at the first game of it.
        return v * SEASON_KEEP + lg * (1 - SEASON_KEEP) if gap > NEW_SEASON_GAP_DAYS else v
    return ((rate(gf, home_id) + rate(ga, away_id)) / 2
            + (rate(gf, away_id) + rate(ga, home_id)) / 2)


def main() -> int:
    params = fit()
    with open(PARAMS_PATH, "w", encoding="utf-8") as fh:
        json.dump(params, fh, indent=1)
    print(f"NHL markets fitted on seasons {params['seasons']['train']}, "
          f"half-life {params['half_life']} games; held out {params['seasons']['test']}:")
    print(f"  {'market':10} {'n':>5} {'model LL':>9} {'base LL':>8}  gain")
    for name, r in params["held_out"].items():
        print(f"  {name:10} {r['n']:5} {r['log_loss']:9.4f} {r['base_log_loss']:8.4f}  "
              f"{r['base_log_loss'] - r['log_loss']:+.4f}")
    print(f"  60-min draw rate {params['reg_draw_rate']:.1%} (constant), "
          f"home wins OT/SO {params['ot_home_rate']:.1%} (constant)")
    print(f"Wrote {PARAMS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
