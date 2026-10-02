"""nhl_market_backtest.py - do our NHL numbers beat the BOOKMAKER? Answered on
past seasons, so it does not take weeks of live games.

WHY THIS EXISTS (2 Oct 2026)
---------------------------
The shadow ledger answers "Elo vs the market" one live game at a time; at ~15
games a day a usable sample is weeks away. Boss: "I can't be waiting for 3
weeks." The history has no odds - but sportsbookreviewsonline.com publishes
NHL opening AND closing moneylines, puck-line prices and totals for every game
2007-08 to Nov 2022 (puck line from 2014-15). That is ~5,000 games of the real
market on seasons our models can be kept blind to.

HONESTY RULES - the whole value is that the test is clean
---------------------------------------------------------
  - EVERYTHING is refitted on 2012-2018 seasons only (Elo K/HFA/revert by the
    same grid as models/elo.py, the goals half-life on 2017-18 validation, the
    market logistics), then scored ONCE on 2019-2023. The production params in
    elo_params.json / nhl_markets.json were tuned on seasons up to 2024 and
    would leak into this test, so they are NOT used here.
  - Games are joined to our results on both franchise ids + date (+-1 day,
    ESPN dates are UTC) and the FINAL SCORE must agree - a join that only
    matches teams is how the wrong game gets graded (bug #1/#22).
  - The comparison is with the CLOSING price, the sharpest the market gets.
    The opening price is reported too: our live numbers are made in the
    morning, nearer the open.

Usage:
    python models/nhl_market_backtest.py             # uses the cached odds CSV
    python models/nhl_market_backtest.py --download  # refetch the 16 seasons
Writes data/sports_history/nhl_odds_sbr.csv (cache) and
data/sports_history/nhl_market_backtest.json (results).
"""

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import time
from datetime import date, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import nhl_markets
from models.elo import GRIDS, HIST, load, metrics, run_elo

SBR = "https://www.sportsbookreviewsonline.com/scoresoddsarchives/nhl-odds-{}"
SBR_SEASONS = ["2010-11", "2011-12", "2012-13", "2013-14", "2014-15", "2015-16",
               "2016-17", "2017-18", "2018-19", "2019-20", "2021", "2021-22", "2022-23"]
ODDS_CSV = os.path.join(HIST, "nhl_odds_sbr.csv")
OUT_JSON = os.path.join(HIST, "nhl_market_backtest.json")
TRAIN = (2012, 2018)          # ESPN season labels (2012 = 2011-12)
VALID = (2017, 2018)          # half-life choice, inside TRAIN
TEST = (2019, 2023)
EDGES = (0, 2, 4, 6, 8, 10)   # bet thresholds, percentage points

# SBR team label (spaces/punctuation removed, lower) -> start of our ESPN name.
SPECIAL = {"nyrangers": "newyorkrangers", "nyislanders": "newyorkislanders",
           "tampa": "tampabay", "la": "losangeles", "losangeleskings": "losangeles",
           "arizonacoyotes": "arizona", "phoenix": "phoenix", "vegasgoldenknights": "vegas",
           "stlouis": "stlouis", "seattlekraken": "seattle"}


def _key(s: str) -> str:
    return re.sub(r"[^a-z]", "", str(s).lower())


def _num(v):
    try:
        f = float(str(v).strip())
        return None if math.isnan(f) else f
    except (ValueError, TypeError):
        return None


def american_to_decimal(a):
    if a is None or a == 0:
        return None
    return 1 + (a / 100 if a > 0 else 100 / -a)


# --------------------------------------------------------------- the odds

def download() -> list:
    import pandas as pd
    import requests
    games = []
    for label in SBR_SEASONS:
        html = None
        for attempt in range(3):
            r = requests.get(SBR.format(label), headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            if r.status_code == 200:
                html = r.text
                break
            time.sleep(3 * (attempt + 1))
        if html is None:
            raise SystemExit(f"{label}: could not download - refusing to write a partial CSV")
        df = max(pd.read_html(io.StringIO(html)), key=len)
        rows = [list(r) for r in df.itertuples(index=False)][1:]
        start = int(label[:4]) - (1 if label == "2021" else 0)
        season = start + 1
        # A season normally opens in October of `start`. Two did not: the
        # 2012-13 lockout and 2020-21 (COVID) opened in JANUARY of start+1.
        # Dating those from `start` put ~1,700 games a year early, where they
        # silently failed to join (found 2 Oct 2026, before any conclusion).
        first_month = int(_num(rows[0][0]) or 0) // 100
        year = start if first_month >= 8 else start + 1
        last_month, n0 = None, len(games)
        has_pl = df.shape[1] >= 16
        for v, h in zip(rows[0::2], rows[1::2]):
            mmdd = int(_num(v[0]) or 0)
            month, day = mmdd // 100, mmdd % 100
            if last_month is not None and month < last_month - 3:
                year += 1                      # Dec -> Jan rollover
            last_month = month
            g = {"season": season, "date": f"{year}-{month:02d}-{day:02d}",
                 "away": _key(v[3]), "home": _key(h[3]),
                 "as": _num(v[7]), "hs": _num(h[7]),
                 "ml_open_a": _num(v[8]), "ml_open_h": _num(h[8]),
                 "ml_close_a": _num(v[9]), "ml_close_h": _num(h[9])}
            if has_pl:
                g.update(pl_line_h=_num(h[10]), pl_odds_h=_num(h[11]), pl_odds_a=_num(v[11]),
                         ou_open=_num(v[12]) or _num(h[12]), ou_open_v=_num(v[13]), ou_open_h=_num(h[13]),
                         ou_close=_num(v[14]) or _num(h[14]), ou_close_v=_num(v[15]), ou_close_h=_num(h[15]))
            else:
                g.update(ou_open=_num(v[10]) or _num(h[10]), ou_close=_num(v[11]) or _num(h[11]))
            games.append(g)
        print(f"  {label}: {len(games) - n0} games")
        time.sleep(1)
    cols = sorted({k for g in games for k in g})
    with open(ODDS_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(games)
    return games


def load_odds() -> list:
    with open(ODDS_CSV, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k, v in r.items():
            if k not in ("date", "home", "away"):
                r[k] = None if v in ("", None) else float(v)
        r["season"] = int(r["season"])
    return rows


# -------------------------------------------------------------- the join

def team_map(games: list) -> dict:
    """SBR key -> ESPN franchise id, from the names in our history."""
    names = {}
    for g in games:
        names[_key(g["home_name"])] = g["home"]
        names[_key(g["away_name"])] = g["away"]
    return names


def franchise(key: str, names: dict):
    k = SPECIAL.get(key, key)
    hits = {fid for n, fid in names.items() if n.startswith(k)}
    return hits.pop() if len(hits) == 1 else None


def join(odds: list, pairs: list) -> tuple[list, dict]:
    by = {}
    for g, p in pairs:
        by.setdefault((g["home"], g["away"]), []).append((g, p))
    names = team_map([g for g, _ in pairs])
    out, miss = [], {"team": 0, "game": 0, "score": 0}
    for o in odds:
        h, a = franchise(o["home"], names), franchise(o["away"], names)
        if not h or not a:
            miss["team"] += 1
            continue
        d = date.fromisoformat(o["date"])
        near = {(d + timedelta(days=k)).isoformat() for k in (-1, 0, 1)}
        cands = [(g, p) for g, p in by.get((h, a), []) if g["date"][:10] in near]
        if not cands:
            miss["game"] += 1
            continue
        g, p = cands[0]
        # The final must agree. ESPN credits the shootout winner +1; some
        # sources do not, so a one-goal difference on the winner is allowed.
        if o["hs"] is None or abs((g["hs"] - g["as"]) - (o["hs"] - o["as"])) > 1 \
                or (g["hs"] > g["as"]) != (o["hs"] > o["as"]):
            miss["score"] += 1
            continue
        out.append((o, g, p))
    return out, miss


# ------------------------------------------------------------- the fits

def tune_elo(games: list) -> dict:
    grid = GRIDS["nhl"]
    best = None
    for k in grid["k"]:
        for hfa in grid["hfa"]:
            for rv in grid["revert"]:
                pairs, _ = run_elo(games, k, hfa, rv, grid["mov"])
                ll = metrics([(g, p) for g, p in pairs
                              if TRAIN[0] <= g["season"] <= TRAIN[1]])["log_loss"]
                if best is None or ll < best[0]:
                    best = (ll, {"k": k, "hfa": hfa, "revert": rv, "mov": grid["mov"]})
    return best[1]


def fit_markets(elo_params: dict) -> dict:
    def regular(rows, lo, hi):
        return [x for x in rows if x["type"] == "2" and lo <= x["season"] <= hi]
    best = None
    for hl in nhl_markets.HALF_LIVES:
        rows, _ = nhl_markets.walk(hl, elo_params)
        params = nhl_markets._fit_all(regular(rows, TRAIN[0], VALID[0] - 1))
        val = regular(rows, *VALID)
        ll = sum(nhl_markets._log_loss(
            [int(o(x)) for x in val],
            [nhl_markets._apply(params["models"][n], nhl_markets._features(k, x["p"], x["exp_h"] + x["exp_a"]))
             for x in val]) for n, (k, o) in nhl_markets.TASKS.items())
        if best is None or ll < best[0]:
            best = (ll, hl)
    rows, _ = nhl_markets.walk(best[1], elo_params)
    params = nhl_markets._fit_all(regular(rows, *TRAIN))
    params["half_life"] = best[1]
    return params, rows


# ---------------------------------------------------------- the scoring

def ll(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def fair(a_dec, b_dec):
    if not a_dec or not b_dec:
        return None
    return (1 / a_dec) / (1 / a_dec + 1 / b_dec)


def paired(ours, book):
    d = [a - b for a, b in zip(ours, book)]
    n = len(d)
    md = sum(d) / n
    sd = math.sqrt(sum((x - md) ** 2 for x in d) / (n - 1))
    return {"n": n, "ours": round(sum(ours) / n, 4), "book": round(sum(book) / n, 4),
            "diff": round(md, 4), "lo": round(md - 1.96 * sd / math.sqrt(n), 4),
            "hi": round(md + 1.96 * sd / math.sqrt(n), 4)}


def roi(bets):
    """bets: list of (decimal odds, won). Flat 1 unit; 95% CI on the mean."""
    if len(bets) < 2:
        return {"n": len(bets)}
    pl = [(o - 1) if w else -1.0 for o, w in bets]
    n = len(pl)
    m = sum(pl) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in pl) / (n - 1))
    h = 1.96 * sd / math.sqrt(n)
    return {"n": n, "won": sum(w for _, w in bets), "roi_pct": round(m * 100, 1),
            "lo": round((m - h) * 100, 1), "hi": round((m + h) * 100, 1)}


def two_way(sel_p, odds_a, odds_b, y, results, bets_close, edge_floor):
    """One two-way market: log loss vs the book, and bets where we see an edge."""
    fa = fair(odds_a, odds_b)
    if fa is None:
        return
    results["ours"].append(ll(sel_p, y))
    results["book"].append(ll(fa, y))
    for t in EDGES:
        if sel_p - fa >= t / 100:
            bets_close[t].append((odds_a, y))
        elif (1 - sel_p) - (1 - fa) >= t / 100:
            bets_close[t].append((odds_b, 1 - y))


def evaluate(joined: list, mk_rows: list, params: dict) -> dict:
    exp = {}
    for x in mk_rows:      # walk() order == history order; key by season+score+p
        exp[(x["season"], x["hs"], x["as"], round(x["p"], 6))] = x["exp_h"] + x["exp_a"]
    test = [(o, g, p) for o, g, p in joined if TEST[0] <= g["season"] <= TEST[1]]
    out = {}

    def bucket():
        return {"ours": [], "book": []}, {t: [] for t in EDGES}

    # Moneyline (overtime and shootout included) - against close AND open.
    for when in ("close", "open"):
        res, bets = bucket()
        for o, g, p in test:
            h = american_to_decimal(o[f"ml_{when}_h"])
            a = american_to_decimal(o[f"ml_{when}_a"])
            two_way(p, h, a, int(g["hs"] > g["as"]), res, bets, None)
        out[f"moneyline_{when}"] = {"log_loss": paired(res["ours"], res["book"]),
                                    "bets": {t: roi(b) for t, b in bets.items()}}

    # Puck line and totals, closing prices.
    pl, plb = bucket()
    tot, totb = bucket()
    over_rows = []
    for o, g, p in test:
        e = exp.get((g["season"], g["hs"], g["as"], round(p, 6)))
        if e is None:
            continue
        ours = nhl_markets.price(p, e, params)
        line = o.get("pl_line_h")
        if line in (-1.5, 1.5) and o.get("pl_odds_h") and o.get("pl_odds_a"):
            key = "pl_home_-1.5" if line == -1.5 else "pl_home_+1.5"
            y = nhl_markets.grade(key, g["hs"], g["as"], None, None)
            two_way(ours[key], american_to_decimal(o["pl_odds_h"]),
                    american_to_decimal(o["pl_odds_a"]), y, pl, plb, None)
        cl = o.get("ou_close")
        if cl in nhl_markets.TOTAL_LINES and o.get("ou_close_v") and o.get("ou_close_h"):
            over_rows.append((o, g, ours, cl))
    # Which SBR row carries the OVER price is not labelled. Decide it from
    # the data rather than assume: the right reading is the one whose implied
    # over-probability is calibrated against what actually happened.
    def over_ll(over_on_v):
        s = 0.0
        for o, g, _, cl in over_rows:
            ov, un = (o["ou_close_v"], o["ou_close_h"]) if over_on_v else (o["ou_close_h"], o["ou_close_v"])
            s += ll(fair(american_to_decimal(ov), american_to_decimal(un)), int(g["hs"] + g["as"] > cl))
        return s
    over_on_v = over_ll(True) < over_ll(False)
    for o, g, ours, cl in over_rows:
        ov, un = (o["ou_close_v"], o["ou_close_h"]) if over_on_v else (o["ou_close_h"], o["ou_close_v"])
        y = int(g["hs"] + g["as"] > cl)
        two_way(ours[f"over_{cl}"], american_to_decimal(ov), american_to_decimal(un), y, tot, totb, None)
    out["puck_line_close"] = {"log_loss": paired(pl["ours"], pl["book"]),
                              "bets": {t: roi(b) for t, b in plb.items()}}
    out["totals_close"] = {"log_loss": paired(tot["ours"], tot["book"]),
                           "bets": {t: roi(b) for t, b in totb.items()},
                           "over_price_row": "visitor" if over_on_v else "home"}

    # Does the market MOVE toward us? (the closing-line-value question, which
    # the live ledger can answer in days if the answer here is yes)
    toward = against = 0
    for o, g, p in test:
        fo = fair(american_to_decimal(o["ml_open_h"]), american_to_decimal(o["ml_open_a"]))
        fc = fair(american_to_decimal(o["ml_close_h"]), american_to_decimal(o["ml_close_a"]))
        if fo is None or fc is None or abs(p - fo) < 0.03 or abs(fc - fo) < 0.005:
            continue
        if (fc - fo) * (p - fo) > 0:
            toward += 1
        else:
            against += 1
    out["line_moves_toward_us"] = {"toward": toward, "against": against,
                                   "share": round(toward / max(toward + against, 1), 3)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    a = ap.parse_args()
    if a.download or not os.path.exists(ODDS_CSV):
        print("Downloading historical NHL odds (sportsbookreviewsonline.com)...")
        download()
    odds = load_odds()
    games = load("nhl")
    print(f"{len(odds)} odds rows; tuning Elo on {TRAIN[0]}-{TRAIN[1]} only...")
    elo_params = tune_elo(games)
    pairs, _ = run_elo(games, elo_params["k"], elo_params["hfa"], elo_params["revert"], elo_params["mov"])
    joined, miss = join(odds, pairs)
    print(f"Elo {elo_params}; joined {len(joined)} games, unjoined {miss}")
    params, mk_rows = fit_markets(elo_params)
    res = evaluate(joined, mk_rows, params)
    res["setup"] = {"train": TRAIN, "test": TEST, "elo": elo_params,
                    "half_life": params["half_life"], "joined": len(joined), "unjoined": miss}
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)

    print(f"\nTEST seasons {TEST[0]}-{TEST[1]} (never used for fitting). Log loss, lower is better:")
    print(f"  {'market':18} {'n':>5} {'ours':>7} {'book':>7}   ours-book [95% CI]")
    for k in ("moneyline_close", "moneyline_open", "puck_line_close", "totals_close"):
        r = res[k]["log_loss"]
        print(f"  {k:18} {r['n']:5} {r['ours']:7.4f} {r['book']:7.4f}   "
              f"{r['diff']:+.4f} [{r['lo']:+.4f}, {r['hi']:+.4f}]")
    print("\nFlat 1-unit bets wherever our edge over the fair price >= threshold:")
    for k in ("moneyline_close", "moneyline_open", "puck_line_close", "totals_close"):
        cells = []
        for t, b in res[k]["bets"].items():
            cells.append(f"{t:>2}pt: n={b['n']}" + (f" {b['roi_pct']:+.1f}% [{b['lo']:+.1f},{b['hi']:+.1f}]"
                                                   if b.get("roi_pct") is not None else ""))
        print(f"  {k}:\n    " + "\n    ".join(cells))
    m = res["line_moves_toward_us"]
    print(f"\nWhen we disagree with the open by 3+ pts, the line then moved TOWARD us "
          f"{m['toward']} times, AWAY {m['against']} ({m['share']:.1%}).")
    print(f"Wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
