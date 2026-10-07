"""line_move_backtest.py - when the price moves AGAINST our pick before the
game, does the pick lose more often than its probability says?

Boss (6 Oct 2026) chose this over "only recommend when we agree with the
bookmaker", whose flaws he spotted: that rule raises the hit rate by keeping
easy favourites, deletes the only disagreements that carry signal, and turns
us into a copy of the book. A late move is different: it is the market
reacting to NEWS we cannot see (injuries, goalies, quarterbacks) AFTER our
pick, so it can warn about a pick without replacing it.

Data: sportsbookreviewsonline.com archives, opening AND closing prices.
  NHL  opening/closing moneyline   (data/sports_history/nhl_odds_sbr.csv,
                                     from models/nhl_market_backtest.py)
  NFL  opening/closing point SPREAD (this script downloads 2010-11..2021-22;
                                     the archive has no opening moneyline)
A pick = the side Elo rates >= 55% (the served threshold). "Moved against" =
  NHL: the pick's margin-free moneyline probability fell by >= T points
  NFL: the spread moved >= T points toward the opponent

HONESTY: Elo re-tuned on early seasons only (the production parameters were
tuned on seasons that include the test years); the threshold T is chosen on
those same early seasons; the effect is scored ONCE on the later seasons.

Usage:
    python models/line_move_backtest.py --sport nhl
    python models/line_move_backtest.py --sport nfl [--download]
"""

import argparse
import csv
import io
import math
import os
import sys
import time
from datetime import date, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import nhl_market_backtest as nb
from models.elo import GRIDS, HIST, load, metrics, run_elo

NFL_ODDS = os.path.join(HIST, "nfl_odds_sbr.csv")
NFL_SEASONS = [f"{y}-{str(y + 1)[2:]}" for y in range(2010, 2022)]
SPLIT = {"nhl": ((2012, 2018), (2019, 2023)), "nfl": ((2011, 2017), (2018, 2022))}
THRESHOLDS = {"nhl": (2, 3, 4, 5, 7), "nfl": (0.5, 1, 1.5, 2, 3)}
SERVE = 0.55


# ------------------------------------------------------------- NFL odds

def download_nfl() -> list:
    import pandas as pd
    import requests
    games = []
    for label in NFL_SEASONS:
        r = requests.get(f"https://www.sportsbookreviewsonline.com/scoresoddsarchives/nfl-odds-{label}",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        if r.status_code != 200:
            raise SystemExit(f"{label}: HTTP {r.status_code} - refusing to write a partial CSV")
        df = max(pd.read_html(io.StringIO(r.text)), key=len)
        rows = [list(x) for x in df.itertuples(index=False)][1:]
        start = int(label[:4])
        year, last_month = start, None
        for v, h in zip(rows[0::2], rows[1::2]):
            mmdd = int(nb._num(v[0]) or 0)
            month, day = mmdd // 100, mmdd % 100
            if last_month is not None and month < last_month - 3:
                year += 1
            last_month = month
            # Open/Close hold the SPREAD on the favourite's row and the TOTAL on
            # the other; the smaller absolute number is the spread ("pk" = 0).
            def num(x):
                s = str(x).strip().lower()
                return 0.0 if s in ("pk", "pick") else nb._num(x)
            vo, ho, vc, hc = num(v[9]), num(h[9]), num(v[10]), num(h[10])
            if None in (vo, ho, vc, hc):
                continue
            # Home's spread (negative = home favoured), open and close.
            home_fav_open = ho < vo
            home_fav_close = hc < vc
            sp_open = -ho if home_fav_open else vo
            sp_close = -hc if home_fav_close else vc
            games.append({"season": start + 1, "date": f"{year}-{month:02d}-{day:02d}",
                          "away": nb._key(v[3]), "home": nb._key(h[3]),
                          "as": nb._num(v[8]), "hs": nb._num(h[8]),
                          "home_spread_open": sp_open, "home_spread_close": sp_close})
        print(f"  {label}: {len(rows) // 2} games")
        time.sleep(1)
    with open(NFL_ODDS, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(games[0]))
        w.writeheader()
        w.writerows(games)
    return games


NFL_NAMES = {   # SBR's labels -> the start of our ESPN names
    "kansas": "kansascity", "kccity": "kansascity", "lvraiders": "lasvegas",
    "oakland": "oakland", "lachargers": "losangeleschargers", "larams": "losangelesrams",
    "sandiego": "sandiego", "stlouis": "stlouis", "nyjets": "newyorkjets",
    "nygiants": "newyorkgiants", "tampa": "tampabay", "newengland": "newengland",
    "washington": "washington", "washingtom": "washington", "neworleans": "neworleans",
    "greenbay": "greenbay", "sanfrancisco": "sanfrancisco",
}


def load_nfl_odds() -> list:
    with open(NFL_ODDS, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k in ("as", "hs", "home_spread_open", "home_spread_close"):
            r[k] = float(r[k]) if r[k] not in ("", None) else None
        r["season"] = int(r["season"])
    return rows


def join_nfl(odds, pairs):
    names = nb.team_map([g for g, _ in pairs])
    by = {}
    for g, p in pairs:
        by.setdefault((g["home"], g["away"]), []).append((g, p))
    out, miss = [], 0
    for o in odds:
        h = nb.franchise(NFL_NAMES.get(o["home"], o["home"]), names)
        a = nb.franchise(NFL_NAMES.get(o["away"], o["away"]), names)
        d = date.fromisoformat(o["date"])
        near = {(d + timedelta(days=k)).isoformat() for k in (-1, 0, 1)}
        hit = [(g, p) for g, p in by.get((h, a), []) if g["date"][:10] in near
               and o["hs"] is not None and g["hs"] - g["as"] == o["hs"] - o["as"]]
        if hit:
            out.append((o, *hit[0]))
        else:
            miss += 1
    return out, miss


# ---------------------------------------------------------------- Elo

def tune(games, seasons):
    grid, best = GRIDS[FEED], None
    for k in grid["k"]:
        for hfa in grid["hfa"]:
            for rv in grid["revert"]:
                pairs, _ = run_elo(games, k, hfa, rv, grid["mov"])
                ll = metrics([(g, p) for g, p in pairs if seasons[0] <= g["season"] <= seasons[1]])["log_loss"]
                if best is None or ll < best[0]:
                    best = (ll, (k, hfa, rv, grid["mov"]))
    return best[1]


# -------------------------------------------------------------- scoring

def wilson(k, n):
    if not n:
        return (0.0, 0.0)
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


def picks(joined, sport):
    """(season, claim p, won, move-against amount) for every served pick."""
    out = []
    for o, g, p in joined:
        if g["hs"] == g["as"]:
            continue
        home_pick = p >= 0.5
        pp = p if home_pick else 1 - p
        if pp < SERVE:
            continue
        won = (g["hs"] > g["as"]) == home_pick
        if sport == "nhl":
            fo = nb.fair(nb.american_to_decimal(o["ml_open_h"]), nb.american_to_decimal(o["ml_open_a"]))
            fc = nb.fair(nb.american_to_decimal(o["ml_close_h"]), nb.american_to_decimal(o["ml_close_a"]))
            if fo is None or fc is None:
                continue
            move = 100 * ((fo - fc) if home_pick else (fc - fo))      # + = against us
        else:
            so, sc = o["home_spread_open"], o["home_spread_close"]
            # home spread rising (e.g. -7 -> -3) = market cooling on home.
            move = (sc - so) if home_pick else (so - sc)               # + = against us
        out.append((g["season"], pp, int(won), move))
    return out


def summary(rows, T, label):
    against = [r for r in rows if r[3] >= T]
    rest = [r for r in rows if r[3] < T]
    def line(rs, name):
        n, k = len(rs), sum(r[2] for r in rs)
        if not n:
            return f"    {name:22} n=0"
        claim = 100 * sum(r[1] for r in rs) / n
        lo, hi = wilson(k, n)
        return (f"    {name:22} {k:4}/{n:<5} {100*k/n:5.1f}%  claim {claim:5.1f}%  "
                f"gap {100*k/n - claim:+5.1f}  CI [{lo:.1f}, {hi:.1f}]")
    print(f"  {label}")
    print(line(against, f"moved against >= {T}"))
    print(line(rest, "no big move against"))
    return against, rest


def gap_diff_ci(a, b):
    """Difference in (hit - claim) between two groups, with a 95% CI."""
    def g(rs):
        return [r[2] - r[1] for r in rs]
    ga, gb = g(a), g(b)
    if len(ga) < 2 or len(gb) < 2:
        return None
    ma, mb = sum(ga) / len(ga), sum(gb) / len(gb)
    va = sum((x - ma) ** 2 for x in ga) / (len(ga) - 1)
    vb = sum((x - mb) ** 2 for x in gb) / (len(gb) - 1)
    se = math.sqrt(va / len(ga) + vb / len(gb))
    d = ma - mb
    return 100 * d, 100 * (d - 1.96 * se), 100 * (d + 1.96 * se)


def main() -> int:
    global FEED
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", choices=["nhl", "nfl"], required=True)
    ap.add_argument("--download", action="store_true")
    a = ap.parse_args()
    FEED = a.sport
    train, test = SPLIT[FEED]
    games = load(FEED)
    k, hfa, rv, mov = tune(games, train)
    pairs, _ = run_elo(games, k, hfa, rv, mov)
    if FEED == "nhl":
        joined, miss = nb.join(nb.load_odds(), pairs)
    else:
        if a.download or not os.path.exists(NFL_ODDS):
            print("Downloading NFL odds archive...")
            download_nfl()
        joined, miss = join_nfl(load_nfl_odds(), pairs)
    rows = picks(joined, FEED)
    tr = [r for r in rows if train[0] <= r[0] <= train[1]]
    te = [r for r in rows if test[0] <= r[0] <= test[1]]
    print(f"{FEED.upper()}: Elo tuned on {train} (K {k}, HFA {hfa}, revert {rv}); "
          f"{len(joined)} games joined ({miss} unjoined); served picks: train {len(tr)}, test {len(te)}")

    # Threshold: the T on TRAINING seasons with the most negative hit-minus-claim
    # gap for moved-against picks, among those with >= 40 such picks.
    best = None
    for T in THRESHOLDS[FEED]:
        ag = [r for r in tr if r[3] >= T]
        if len(ag) < 40:
            continue
        gap = sum(r[2] - r[1] for r in ag) / len(ag)
        if best is None or gap < best[0]:
            best = (gap, T)
    T = best[1]
    print(f"  threshold chosen on training seasons: T = {T} "
          f"({'probability points' if FEED == 'nhl' else 'spread points'})")
    summary(tr, T, f"TRAINING seasons {train} (where T was chosen)")
    ag, rs = summary(te, T, f"HELD-OUT seasons {test} (scored once)")
    d = gap_diff_ci(ag, rs)
    if d:
        verdict = ("moved-against picks do WORSE" if d[2] < 0 else
                   "moved-against picks do better" if d[1] > 0 else "no measurable difference")
        print(f"  held-out: (hit - claim) moved-against minus the rest = {d[0]:+.1f} pts "
              f"[{d[1]:+.1f}, {d[2]:+.1f}] -> {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
