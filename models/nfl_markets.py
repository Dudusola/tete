"""nfl_markets.py - price the markets an NFL game is actually bet on.

    Moneyline        full game, overtime included (Elo, models/elo.py).
    Point spread     full game, at the bookmaker's line (e.g. home -3.5).
    Total points     full game, at the bookmaker's line (e.g. 44.5).

WHY NOT A PLAIN BELL CURVE (measured 2 Oct 2026, 4,396 games)
-------------------------------------------------------------
NFL scoring comes in 3s and 7s, so the final margin is LUMPY: 14.6% of games
end by exactly 3 points and 8.7% by exactly 7, far more than a normal curve
puts there. Those are also the most common spreads, so a normal model would
misprice exactly the lines people bet. So the margin distribution is:

    normal(mu, sigma) over whole points  x  a weight per |margin|

where the weights ("key numbers") are fitted on TRAINING seasons only, and
mu is chosen so that P(home wins) equals Elo's - the spread and the moneyline
can never contradict each other. Totals are much less lumpy and use a plain
discretised normal around each side's point-in-time scoring rate.

Fitted on 2011-2023 (Elo's training seasons), scored once on 2024-2025 -
`python models/nfl_markets.py` prints the held-out table. Pushes (a whole-
number line landing exactly) are VOID, never a loss: grade() returns None.

WHAT IT CANNOT KNOW: quarterbacks, injuries, weather, rest after a bye. The
market knows all of them.
"""

import csv
import json
import math
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.elo import HIST, load, run_elo

PARAMS_PATH = os.path.join(HIST, "nfl_markets.json")
MAX_MARGIN = 60
HALF_LIVES = (4, 8, 16)        # games; chosen on validation seasons
SEASON_KEEP = 0.6
NEW_SEASON_GAP_DAYS = 60


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def margin_pmf(mu: float, sigma: float, weights: dict) -> dict:
    """P(home margin = k) for k in -60..60, normal x key-number weights."""
    raw = {}
    for k in range(-MAX_MARGIN, MAX_MARGIN + 1):
        p = _phi((k + 0.5 - mu) / sigma) - _phi((k - 0.5 - mu) / sigma)
        raw[k] = p * weights.get(str(abs(k)), 1.0)
    s = sum(raw.values())
    return {k: v / s for k, v in raw.items()}


def _home_win(pmf: dict) -> float:
    return sum(v for k, v in pmf.items() if k > 0) + 0.5 * pmf.get(0, 0.0)


def solve_mu(p_home: float, sigma: float, weights: dict) -> float:
    """The mean margin whose distribution gives Elo's home-win probability."""
    lo, hi = -40.0, 40.0
    for _ in range(50):
        mid = (lo + hi) / 2
        if _home_win(margin_pmf(mid, sigma, weights)) < p_home:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def cover(pmf: dict, home_line: float) -> tuple[float, float, float]:
    """(P home covers, P push, P away covers) for the home side's line."""
    win = push = 0.0
    for k, v in pmf.items():
        x = k + home_line
        if x > 0:
            win += v
        elif x == 0:
            push += v
    return win, push, 1 - win - push


def total_probs(mu_t: float, sigma_t: float, line: float) -> tuple[float, float, float]:
    """(P over, P push, P under) for a total line, discretised normal."""
    if float(line).is_integer():
        push = _phi((line + 0.5 - mu_t) / sigma_t) - _phi((line - 0.5 - mu_t) / sigma_t)
        under = _phi((line - 0.5 - mu_t) / sigma_t)
    else:
        push = 0.0
        under = _phi((line - mu_t) / sigma_t)
    return 1 - under - push, push, under


# -------------------------------------------------------------- the walk

def walk(half_life: float, elo_params: dict):
    """Every game with PRE-game Elo p_home and expected points per side."""
    games = load("nfl")
    with open(os.path.join(HIST, "nfl.csv"), newline="", encoding="utf-8") as fh:
        types = {(r["kickoff_utc"] or r["date"], r["home_id"]): r["season_type"]
                 for r in csv.DictReader(fh)}
    pairs, _ = run_elo(games, elo_params["k"], elo_params["hfa"],
                       elo_params["revert"], elo_params["mov"])
    keep = 0.5 ** (1 / half_life)
    pf, pa, league, season = {}, {}, 22.0, None
    out = []
    for g, p in pairs:
        if g["season"] != season:
            if season is not None:
                for t in pf:
                    pf[t] = pf[t] * SEASON_KEEP + league * (1 - SEASON_KEEP)
                    pa[t] = pa[t] * SEASON_KEEP + league * (1 - SEASON_KEEP)
            season = g["season"]
        h, a = g["home"], g["away"]
        for t in (h, a):
            pf.setdefault(t, league)
            pa.setdefault(t, league)
        exp_total = (pf[h] + pa[a]) / 2 + (pf[a] + pa[h]) / 2
        out.append({"season": g["season"], "type": types.get((g["date"], h), "2"),
                    "p": p, "exp_total": exp_total, "hs": g["hs"], "as": g["as"]})
        pf[h], pa[h] = pf[h] * keep + g["hs"] * (1 - keep), pa[h] * keep + g["as"] * (1 - keep)
        pf[a], pa[a] = pf[a] * keep + g["as"] * (1 - keep), pa[a] * keep + g["hs"] * (1 - keep)
        league = league * 0.995 + (g["hs"] + g["as"]) / 2 * 0.005
    return out, {"pf": pf, "pa": pa, "league": league,
                 "last": max(g["date"] for g in games)[:10]}


# ------------------------------------------------------------- the fit

def _fit_margin(rows: list) -> tuple[float, dict]:
    """sigma from Elo-implied residuals, then key-number weights by |margin|."""
    # 538's rule of thumb: 25 Elo points ~ 1 point of spread. Only used to get
    # a residual scale; the served mu comes from solve_mu() against Elo's p.
    def mu538(p):
        p = min(max(p, 1e-6), 1 - 1e-6)
        return 400 * math.log10(p / (1 - p)) / 25
    res = [(x["hs"] - x["as"]) - mu538(x["p"]) for x in rows]
    sigma = math.sqrt(sum(r * r for r in res) / len(res))
    flat = {}
    expected, observed = {}, {}
    for x in rows:
        mu = solve_mu(x["p"], sigma, flat)
        pmf = margin_pmf(mu, sigma, flat)
        for k, v in pmf.items():
            expected[abs(k)] = expected.get(abs(k), 0.0) + v
        m = abs(x["hs"] - x["as"])
        observed[m] = observed.get(m, 0) + 1
    # Shrink each ratio toward 1 by 20 pseudo-games, so a margin seen a handful
    # of times cannot get an extreme weight.
    weights = {str(k): round((observed.get(k, 0) + 20) / (expected.get(k, 0) + 20), 4)
               for k in range(0, MAX_MARGIN + 1)}
    return round(sigma, 3), weights


def _fit_total(rows: list) -> dict:
    n = len(rows)
    xs = [x["exp_total"] for x in rows]
    ys = [x["hs"] + x["as"] for x in rows]
    mx, my = sum(xs) / n, sum(ys) / n
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    a = my - b * mx
    res = [y - (a + b * x) for x, y in zip(xs, ys)]
    return {"a": round(a, 4), "b": round(b, 4),
            "sigma": round(math.sqrt(sum(r * r for r in res) / n), 3)}


def _ll(p, y):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _score(rows: list, params: dict) -> dict:
    """Held-out checks. No historical lines exist in this file, so spreads and
    totals are scored at the COMMON lines across every game - a calibration
    test of the whole distribution, not of one market price."""
    sig, w, tot = params["sigma_margin"], params["key_weights"], params["total"]
    flat_sig = sig
    out = {"n": len(rows)}
    # 1. Exact-margin likelihood: key-number weights vs a plain normal.
    ll_w = ll_n = 0.0
    for x in rows:
        m = x["hs"] - x["as"]
        for weights, acc in ((w, "w"), ({}, "n")):
            mu = solve_mu(x["p"], sig if acc == "w" else flat_sig, weights)
            p = max(margin_pmf(mu, sig, weights).get(m, 1e-9), 1e-9)
            if acc == "w":
                ll_w -= math.log(p)
            else:
                ll_n -= math.log(p)
    out["exact_margin_log_loss"] = {"key_weights": round(ll_w / len(rows), 4),
                                    "plain_normal": round(ll_n / len(rows), 4)}
    # 2. Covering common lines, against each line's base rate in TRAINING.
    spreads = {}
    for line in (-7.5, -6.5, -3.5, -2.5, -1.5, 1.5, 2.5, 3.5, 6.5, 7.5):
        ys, ps = [], []
        for x in rows:
            pmf = margin_pmf(solve_mu(x["p"], sig, w), sig, w)
            ps.append(cover(pmf, line)[0])
            ys.append(int((x["hs"] - x["as"]) + line > 0))
        base = params["base_cover"][str(line)]
        spreads[str(line)] = {"model": round(sum(map(_ll, ps, ys)) / len(ys), 4),
                              "base": round(sum(_ll(base, y) for y in ys) / len(ys), 4)}
    out["spread_cover_log_loss"] = spreads
    totals = {}
    for line in (40.5, 43.5, 44.5, 46.5, 47.5, 50.5):
        ys, ps = [], []
        for x in rows:
            mu_t = tot["a"] + tot["b"] * x["exp_total"]
            ps.append(total_probs(mu_t, tot["sigma"], line)[0])
            ys.append(int(x["hs"] + x["as"] > line))
        base = params["base_over"][str(line)]
        totals[str(line)] = {"model": round(sum(map(_ll, ps, ys)) / len(ys), 4),
                             "base": round(sum(_ll(base, y) for y in ys) / len(ys), 4)}
    out["total_over_log_loss"] = totals
    return out


def fit() -> dict:
    elo = json.load(open(os.path.join(HIST, "elo_params.json"), encoding="utf-8"))["nfl"]
    lo, hi = elo["seasons"]["train"]
    test = elo["seasons"]["test"]

    def sel(rows, a, b):
        return [x for x in rows if a <= x["season"] <= b]

    # Half-life for the scoring rates, chosen on the last two TRAINING seasons.
    best = None
    for hl in HALF_LIVES:
        rows, _ = walk(hl, elo["params"])
        t = _fit_total(sel(rows, lo, hi - 2))
        val = sel(rows, hi - 1, hi)
        err = sum(((x["hs"] + x["as"]) - (t["a"] + t["b"] * x["exp_total"])) ** 2 for x in val)
        if best is None or err < best[0]:
            best = (err, hl)
    rows, _ = walk(best[1], elo["params"])
    train = sel(rows, lo, hi)
    sigma, weights = _fit_margin(train)
    params = {"half_life": best[1], "sigma_margin": sigma, "key_weights": weights,
              "total": _fit_total(train),
              "base_cover": {str(L): round(sum((x["hs"] - x["as"]) + L > 0 for x in train) / len(train), 4)
                             for L in (-7.5, -6.5, -3.5, -2.5, -1.5, 1.5, 2.5, 3.5, 6.5, 7.5)},
              "base_over": {str(L): round(sum(x["hs"] + x["as"] > L for x in train) / len(train), 4)
                            for L in (40.5, 43.5, 44.5, 46.5, 47.5, 50.5)},
              "seasons": {"train": [lo, hi], "test": test}}
    params["held_out"] = _score(sel(rows, test[0], test[-1]), params)
    params["fitted_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return params


# ------------------------------------------------------------- serving

def load_params() -> dict | None:
    try:
        return json.load(open(PARAMS_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def current_rates(params: dict, elo_params: dict) -> dict:
    _, state = walk(params["half_life"], elo_params)
    return state


def expected_total(state: dict, params: dict, home_id: str, away_id: str, kickoff_date: str) -> float:
    pf, pa, lg = state["pf"], state["pa"], state["league"]
    gap = (datetime.strptime(kickoff_date, "%Y-%m-%d")
           - datetime.strptime(state["last"], "%Y-%m-%d")).days

    def rate(d, t):
        v = d.get(t, lg)
        return v * SEASON_KEEP + lg * (1 - SEASON_KEEP) if gap > NEW_SEASON_GAP_DAYS else v
    raw = (rate(pf, home_id) + rate(pa, away_id)) / 2 + (rate(pf, away_id) + rate(pa, home_id)) / 2
    return params["total"]["a"] + params["total"]["b"] * raw


def _fmt(x: float) -> str:
    return f"{x:+g}"


def price(p_home: float, mu_total: float, params: dict,
          home_line: float | None, total_line: float | None) -> dict:
    """Our probability per grading key. Spread/total keys exist only at the
    bookmaker's own line - we never invent a line nobody is offering."""
    out = {"ml_home": round(p_home, 4), "ml_away": round(1 - p_home, 4)}
    sig, w = params["sigma_margin"], params["key_weights"]
    if home_line is not None:
        pmf = margin_pmf(solve_mu(p_home, sig, w), sig, w)
        h, push, a = cover(pmf, home_line)
        out[f"spread_home_{_fmt(home_line)}"] = round(h, 4)
        out[f"spread_away_{_fmt(-home_line)}"] = round(a, 4)
        if push:
            out["_spread_push"] = round(push, 4)
    if total_line is not None:
        o, push, u = total_probs(mu_total, params["total"]["sigma"], total_line)
        out[f"over_{total_line:g}"] = round(o, 4)
        out[f"under_{total_line:g}"] = round(u, 4)
        if push:
            out["_total_push"] = round(push, 4)
    return out


def grade(key: str, hs: int, as_: int, *_):
    """1 won / 0 lost / None void (push, tie on a moneyline) or unknown key."""
    if key == "ml_home":
        return None if hs == as_ else int(hs > as_)
    if key == "ml_away":
        return None if hs == as_ else int(as_ > hs)
    if key.startswith("spread_"):
        _, side, line = key.split("_")
        v = ((hs - as_) if side == "home" else (as_ - hs)) + float(line)
        return None if v == 0 else int(v > 0)
    if key.startswith(("over_", "under_")):
        side, line = key.split("_")
        d = (hs + as_) - float(line)
        if d == 0:
            return None
        return int(d > 0) if side == "over" else int(d < 0)
    return None


def main() -> int:
    params = fit()
    with open(PARAMS_PATH, "w", encoding="utf-8") as fh:
        json.dump(params, fh, indent=1)
    h = params["held_out"]
    print(f"NFL markets fitted on {params['seasons']['train']}, half-life {params['half_life']} games,"
          f" margin sigma {params['sigma_margin']}; held out {params['seasons']['test']} ({h['n']} games)")
    em = h["exact_margin_log_loss"]
    print(f"  exact margin log loss: key-number model {em['key_weights']} vs plain normal {em['plain_normal']}")
    print("  spread cover (model vs base rate):", ", ".join(
        f"{L}: {v['model']:.4f}/{v['base']:.4f}" for L, v in h["spread_cover_log_loss"].items()))
    print("  total over (model vs base rate):  ", ", ".join(
        f"{L}: {v['model']:.4f}/{v['base']:.4f}" for L, v in h["total_over_log_loss"].items()))
    print(f"  key-number weights: 3 -> {params['key_weights']['3']}, 7 -> {params['key_weights']['7']},"
          f" 1 -> {params['key_weights']['1']}")
    print(f"Wrote {PARAMS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
