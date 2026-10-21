"""elo_plus.py - can Elo be improved with information we already have?

Boss (6 Oct 2026): "we should start thinking of making it better". Elo knows
results and home advantage only. Two candidates every serious public model
uses, both computable from data/sports_history alone (point-in-time: only the
dates of games ALREADY played):

  1. REST - a team on the second night of a back-to-back (NHL), or on a short
     week / coming off a bye (NFL), plays at a different level. Modelled as
     Elo points added/removed by rest-day bucket, per side.
  2. EARLY-SEASON LEARNING - ratings carry over the summer with a partial
     revert, so for the first games of a season they still describe last
     year's roster. A higher K for each team's first N games of a season lets
     them catch up faster.

HONESTY: same split as models/elo.py (warm-up season, tune on training
seasons, score the last two complete seasons ONCE), the base K/HFA/revert are
the production values, and an extension only counts if its held-out log-loss
gain has a paired bootstrap 95% CI entirely below zero.

Usage:
    python models/elo_plus.py --feed nhl
    python models/elo_plus.py --feed nfl
    python models/elo_plus.py --feed nba
"""

import argparse
import itertools
import json
import math
import os
import random
import sys
from datetime import date

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.elo import HIST, MEAN, load, mov_mult, split_seasons, win_prob

# Rest buckets (days since the team's previous game), per league.
BUCKETS = {
    "nhl": [("b2b", 0, 1), ("normal", 2, 3), ("long", 4, 99)],
    "nfl": [("short", 0, 5), ("normal", 6, 8), ("bye", 9, 99)],
    "nba": [("b2b", 0, 1), ("normal", 2, 2), ("long", 3, 99)],
}
GRID = {
    "nhl": {"rest": [0, 10, 20, 30, 40], "early_k": [1.0, 1.5, 2.0, 3.0], "early_n": [5, 10, 20]},
    "nfl": {"rest": [0, 10, 20, 30, 45], "early_k": [1.0, 1.5, 2.0, 3.0], "early_n": [2, 4, 6]},
    "nba": {"rest": [0, 15, 30, 45, 60, 80], "early_k": [1.0, 1.5, 2.0, 3.0], "early_n": [5, 10, 20]},
}


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


def run(games: list, base: dict, feed: str, rest_pts: dict, early_k: float, early_n: int):
    """Elo with rest adjustment and early-season K. Yields (game, p_home).

    rest_pts: bucket -> Elo points ADDED to a side in that bucket (fitted).
    """
    ratings, season = {}, None
    last_game, played = {}, {}
    out = []
    for g in games:
        if g["season"] != season:
            if season is not None:
                for t in ratings:
                    ratings[t] = ratings[t] * (1 - base["revert"]) + MEAN * base["revert"]
            season = g["season"]
            played = {}
        h, a = g["home"], g["away"]
        rh = ratings.setdefault(h, MEAN)
        ra = ratings.setdefault(a, MEAN)
        adj = 0.0
        for team, sign in ((h, 1), (a, -1)):
            prev = last_game.get(team)
            if prev and _days(prev, g["date"]) < 40:          # same season only
                d = _days(prev, g["date"])
                for name, lo, hi in BUCKETS[feed]:
                    if lo <= d <= hi:
                        adj += sign * rest_pts.get(name, 0.0)
        hfa = 0.0 if g["neutral"] else base["hfa"]
        diff = rh + hfa - ra + adj
        p = win_prob(diff)
        out.append((g, p))
        if g["hs"] == g["as"]:
            s, mult = 0.5, 1.0
        else:
            s = 1.0 if g["hs"] > g["as"] else 0.0
            wd = diff if s == 1.0 else -diff
            mult = mov_mult(base["mov"], abs(g["hs"] - g["as"]), wd)
        kh = base["k"] * (early_k if played.get(h, 0) < early_n else 1.0)
        ka = base["k"] * (early_k if played.get(a, 0) < early_n else 1.0)
        ratings[h] = rh + kh * mult * (s - p)
        ratings[a] = ra - ka * mult * (s - p)
        played[h] = played.get(h, 0) + 1
        played[a] = played.get(a, 0) + 1
        last_game[h] = last_game[a] = g["date"]
    return out


def _ll(g, p):
    y = 0.5 if g["hs"] == g["as"] else (1.0 if g["hs"] > g["as"] else 0.0)
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def mean_ll(pairs, seasons):
    xs = [_ll(g, p) for g, p in pairs if g["season"] in seasons]
    return sum(xs) / len(xs), len(xs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", choices=["nhl", "nfl", "nba"], required=True)
    feed = ap.parse_args().feed
    base = json.load(open(os.path.join(HIST, "elo_params.json"), encoding="utf-8"))[feed]["params"]
    games = load(feed)
    _, train, test = split_seasons(games)
    train, test = set(train), set(test)
    names = [b[0] for b in BUCKETS[feed]]
    grid = GRID[feed]

    baseline = run(games, base, feed, {}, 1.0, 0)
    base_train, _ = mean_ll(baseline, train)

    # Rest: one magnitude per non-normal bucket, signed by fitting (a b2b side
    # LOSES points, a rested side may GAIN), searched on TRAINING seasons only.
    best = (base_train, {}, 1.0, 0)
    odd = [n for n in names if n != "normal"]
    for mags in itertools.product(grid["rest"], repeat=len(odd)):
        for signs in itertools.product((-1, 1), repeat=len(odd)):
            rest = {n: s * m for n, s, m in zip(odd, signs, mags)}
            ll, _ = mean_ll(run(games, base, feed, rest, 1.0, 0), train)
            if ll < best[0] - 1e-9:
                best = (ll, rest, 1.0, 0)
    rest = best[1]
    for ek, en in itertools.product(grid["early_k"], grid["early_n"]):
        ll, _ = mean_ll(run(games, base, feed, rest, ek, en), train)
        if ll < best[0] - 1e-9:
            best = (ll, rest, ek, en)
    _, rest, ek, en = best

    plus = run(games, base, feed, rest, ek, en)
    rest_only = run(games, base, feed, rest, 1.0, 0)
    early_only = run(games, base, feed, {}, ek, en)
    print(f"{feed.upper()}  base {base}  train {min(train)}-{max(train)}  test {sorted(test)}")
    print(f"  chosen on TRAINING seasons: rest {rest or 'none'}, early K x{ek} for first {en} games")
    b_ll, n = mean_ll(baseline, test)
    print(f"  held-out log loss ({n} games): baseline Elo {b_ll:.4f}")

    def compare(label, pairs):
        d = [_ll(g, p2) - _ll(g, p1) for (g, p1), (_, p2) in zip(baseline, pairs)
             if g["season"] in test]
        m = sum(d) / len(d)
        rng = random.Random(7)
        boots = sorted(sum(d[rng.randrange(len(d))] for _ in d) / len(d) for _ in range(2000))
        lo, hi = boots[50], boots[1949]
        acc = lambda ps: sum((p >= 0.5) == (g["hs"] > g["as"]) for g, p in ps
                             if g["season"] in test and g["hs"] != g["as"]) / \
            sum(1 for g, _ in ps if g["season"] in test and g["hs"] != g["as"])
        verdict = "BETTER" if hi < 0 else ("worse" if lo > 0 else "no measurable difference")
        print(f"  {label:22} {b_ll + m:.4f}  diff {m:+.4f} [{lo:+.4f}, {hi:+.4f}]  "
              f"acc {acc(pairs):.1%} (baseline {acc(baseline):.1%})  -> {verdict}")
    compare("+ rest", rest_only)
    compare("+ early-season K", early_only)
    compare("+ both", plus)
    return 0


if __name__ == "__main__":
    sys.exit(main())
