"""nba_market_backtest.py - do our NBA prices beat the bookmaker's? (21 Oct 2026)

Same question models/nhl_market_backtest.py answered for hockey, on PAST seasons
because the live ledger needs months. Historical NBA moneylines, spreads and
totals (open + close) from sportsbookreviewsonline.com, 2018-19 to 2022-23 - the
last five it publishes (2023-24 onward redirect to its home page) ->
data/sports_history/nba_odds_sbr.csv. NOT data/historical/: that folder is the
FOOTBALL training set (train.py globs it).

Scored on those five seasons (ESPN labels 2019-2023):
  moneyline  Elo (+ back-to-back and early-season K, models/elo_plus.py) vs the
             book's margin-free closing price.
  spread     P(home covers the book's line), nfl_markets' margin model REFIT on
             2012-2018 only, vs the book (SBR has no spread odds: -110 a side,
             i.e. 50%).
  total      P(over the book's line), same refit, vs 50%.
  Flat-stake ROI at 1.909 (-110) when our edge clears 3/5/8 points, at the open
  and at the close.

Known leak, stated not hidden: Elo's K/HFA/revert and the rest/early-K values
were tuned on 2012-2024, which includes these seasons (5 numbers, smooth).
The margin/total model, which carries the spread/total claims, is clean.

    python models/nba_market_backtest.py            # uses the CSV if present
    python models/nba_market_backtest.py --download
"""

import argparse
import csv
import io
import json
import math
import os
import random
import re
import sys
import time
from datetime import date, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import nfl_markets
from models.elo import HIST, load
from models.elo_plus import run as run_plus

SBR = "https://www.sportsbookreviewsonline.com/scoresoddsarchives/nba-odds-{}/"
SEASONS = ["2018-19", "2019-20", "2020-21", "2021-22", "2022-23"]
ODDS_CSV = os.path.join(HIST, "nba_odds_sbr.csv")
OUT_JSON = os.path.join(HIST, "nba_market_backtest.json")
TRAIN = (2012, 2018)
TEST = (2019, 2023)
EDGES = (0.03, 0.05, 0.08)
PAYOUT = 1.909                       # -110
NBA_PLUS = {"rest": {"b2b": -45}, "early_k": 1.5, "early_n": 10}   # elo_plus.py, 21 Oct 2026
SPECIAL = {"lalakers": "losangeleslakers", "okc": "oklahomacity"}


def _key(s) -> str:
    return re.sub(r"[^a-z]", "", str(s).lower())


def _num(v):
    s = str(v).strip().lower()
    if s in ("pk", "pick"):
        return 0.0
    try:
        f = float(s)
        return None if math.isnan(f) else f
    except ValueError:
        return None


def _dec(a):
    return None if not a else 1 + (a / 100 if a > 0 else 100 / -a)


def _split(v, h):
    """SBR puts the TOTAL on one row and the SPREAD on the favourite's row.
    Returns (home_line, total) or (None, None)."""
    if v is None or h is None:
        return None, None
    if v >= h:                       # visitor row holds the total -> home favoured
        return -h, v
    return v, h                      # home row holds the total -> visitor favoured


def download() -> list:
    import pandas as pd
    import requests
    games = []
    for label in SEASONS:
        html = None
        for attempt in range(3):
            r = requests.get(SBR.format(label), headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            if r.status_code == 200 and "<table" in r.text:
                html = r.text
                break
            time.sleep(3 * (attempt + 1))
        if html is None:
            raise SystemExit(f"{label}: could not download - refusing to write a partial CSV")
        df = max(pd.read_html(io.StringIO(html)), key=len)
        head = [str(x).strip() for x in df.iloc[0]]
        col = {n: head.index(n) for n in ("Date", "Team", "Final", "Open", "Close", "ML")}
        rows = [list(r) for r in df.iloc[1:].itertuples(index=False)]
        start = int(label[:4])
        year = start if int(_num(rows[0][col["Date"]]) or 0) // 100 >= 8 else start + 1
        last_month, n0 = None, len(games)
        for v, h in zip(rows[0::2], rows[1::2]):
            mmdd = int(_num(v[col["Date"]]) or 0)
            month, day = mmdd // 100, mmdd % 100
            if last_month is not None and month < last_month - 3:
                year += 1                      # Dec -> Jan rollover
            last_month = month
            ol, ot = _split(_num(v[col["Open"]]), _num(h[col["Open"]]))
            cl, ct = _split(_num(v[col["Close"]]), _num(h[col["Close"]]))
            games.append({"season": start + 1, "date": f"{year}-{month:02d}-{day:02d}",
                          "away": _key(v[col["Team"]]), "home": _key(h[col["Team"]]),
                          "as": _num(v[col["Final"]]), "hs": _num(h[col["Final"]]),
                          "ml_a": _num(v[col["ML"]]), "ml_h": _num(h[col["ML"]]),
                          "open_line": ol, "open_total": ot, "close_line": cl, "close_total": ct})
        print(f"  {label}: {len(games) - n0} games")
        time.sleep(1)
    with open(ODDS_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(games[0]))
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


def join(odds: list, games: list, ps: list, totals: list) -> tuple[list, dict, set]:
    names, by = {}, {}
    for i, g in enumerate(games):
        names[_key(g["home_name"])] = g["home"]
        names[_key(g["away_name"])] = g["away"]
        by.setdefault((g["home"], g["away"]), []).append(i)

    def fid(k):
        k = SPECIAL.get(k, k)
        hits = {f for n, f in names.items() if n.startswith(k)}
        return hits.pop() if len(hits) == 1 else None
    out, miss, unknown = [], {"team": 0, "game": 0, "score": 0}, set()
    for o in odds:
        h, a = fid(o["home"]), fid(o["away"])
        if not h or not a:
            miss["team"] += 1
            unknown.update(k for k, f in ((o["home"], h), (o["away"], a)) if not f)
            continue
        d = date.fromisoformat(o["date"])
        near = {(d + timedelta(days=k)).isoformat() for k in (-1, 0, 1)}
        idx = [i for i in by.get((h, a), []) if games[i]["date"][:10] in near]
        if not idx:
            miss["game"] += 1
            continue
        g = games[idx[0]]
        if (g["hs"], g["as"]) != (o["hs"], o["as"]):
            miss["score"] += 1
            continue
        out.append((o, g, ps[idx[0]], totals[idx[0]]))
    return out, miss, unknown


def _ll(p, y):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _ci(xs: list) -> list:
    rnd = random.Random(7)
    n = len(xs)
    means = sorted(sum(xs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(1000))
    return [round(means[25], 4), round(means[974], 4)]


def compare(pairs: list) -> dict:
    """pairs: (our p, book p, outcome 0/1). Log loss each, paired diff + CI."""
    ours = [_ll(p, y) for p, _, y in pairs]
    book = [_ll(b, y) for _, b, y in pairs]
    d = [a - b for a, b in zip(ours, book)]
    return {"n": len(pairs), "ours": round(sum(ours) / len(ours), 4),
            "book": round(sum(book) / len(book), 4),
            "diff": round(sum(d) / len(d), 4), "ci": _ci(d)}


def roi(bets: list) -> dict:
    """bets: (our p of the side we back, won 0/1/None push) at 1.909."""
    out = {}
    for e in EDGES:
        pl = [PAYOUT - 1 if y else -1.0 for p, y in bets if p - 0.5 >= e and y is not None]
        out[f"{int(e * 100)}pt"] = {
            "bets": len(pl),
            "roi_pct": round(100 * sum(pl) / len(pl), 1) if pl else None,
            "ci_pct": [round(100 * x, 1) for x in _ci(pl)] if len(pl) > 20 else None}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    a = ap.parse_args()
    if a.download or not os.path.exists(ODDS_CSV):
        print("Downloading historical NBA odds (sportsbookreviewsonline.com)...")
        download()
    odds = load_odds()
    elo = json.load(open(os.path.join(HIST, "elo_params.json"), encoding="utf-8"))["nba"]["params"]
    games = load("nba")
    ps = [p for _, p in run_plus(games, elo, "nba", NBA_PLUS["rest"],
                                 NBA_PLUS["early_k"], NBA_PLUS["early_n"])]
    mk = nfl_markets.fit("nba", train=TRAIN)
    rows, _ = nfl_markets.walk(mk["half_life"], elo, "nba")
    assert len(rows) == len(games) == len(ps)    # all three walk the games in the same order
    totals = [mk["total"]["a"] + mk["total"]["b"] * r["exp_total"] for r in rows]
    joined, miss, unknown = join([o for o in odds if TEST[0] <= o["season"] <= TEST[1]],
                                 games, ps, totals)
    print(f"joined {len(joined)} games; unjoined {miss}"
          + (f"; unknown teams {sorted(unknown)}" if unknown else ""))
    sig, w, ts = mk["sigma_margin"], mk["key_weights"], mk["total"]["sigma"]
    ml, sp, to = [], [], []
    bets = {"spread_open": [], "spread_close": [], "total_open": [], "total_close": []}
    cache = {}
    for o, g, p, mu_t in joined:
        margin, pts = g["hs"] - g["as"], g["hs"] + g["as"]
        dh, da = _dec(o["ml_h"]), _dec(o["ml_a"])
        if dh and da:
            ml.append((p, (1 / dh) / (1 / dh + 1 / da), int(margin > 0)))
        pmf = nfl_markets._cached_pmf(cache, p, sig, w)
        for when in ("open", "close"):
            line, tot = o[f"{when}_line"], o[f"{when}_total"]
            if line is not None:
                ph = nfl_markets.cover(pmf, line)[0]
                v = margin + line
                y = None if v == 0 else int(v > 0)
                if when == "close" and y is not None:
                    sp.append((ph, 0.5, y))
                bets[f"spread_{when}"].append(
                    (ph, y) if ph >= 0.5 else (1 - ph, None if y is None else 1 - y))
            if tot is not None:
                po = nfl_markets.total_probs(mu_t, ts, tot)[0]
                y = None if pts == tot else int(pts > tot)
                if when == "close" and y is not None:
                    to.append((po, 0.5, y))
                bets[f"total_{when}"].append(
                    (po, y) if po >= 0.5 else (1 - po, None if y is None else 1 - y))
    res = {"seasons_test": TEST, "markets_fit_on": TRAIN, "joined": len(joined), "unjoined": miss,
           "moneyline_close": compare(ml), "spread_close": compare(sp), "total_close": compare(to),
           "flat_roi": {k: roi(v) for k, v in bets.items()}}
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    for k in ("moneyline_close", "spread_close", "total_close"):
        r = res[k]
        verdict = "WE are better" if r["ci"][1] < 0 else "BOOK is better" if r["ci"][0] > 0 else "level"
        print(f"  {k:16s} n={r['n']}  ours {r['ours']}  book {r['book']}  "
              f"diff {r['diff']:+} {r['ci']}  -> {verdict}")
    for k, v in res["flat_roi"].items():
        print(f"  ROI {k:13s} " + "  ".join(
            f"{e}: {x['bets']} bets {x['roi_pct']}% {x['ci_pct']}" for e, x in v.items()))
    print(f"Wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
