"""elo.py - Elo ratings for the non-soccer leagues: the BASELINE every later
model for NBA / NFL / NHL / MLB has to beat (docs/basketball.md sections 7-8).

WHAT ELO IS
-----------
One number per team. Before a game the rating gap gives a win probability;
after it the winner takes points from the loser - many for an upset, few for
the expected result. Between seasons every rating is pulled part-way back to
the mean, because rosters change. It uses nothing but past results, so it is
point-in-time by construction: a game's probability is computed BEFORE that
game updates the ratings. No leakage is possible (section 7, rule 1).

WHY A BASELINE
--------------
The GitHub search behind section 8 found plain Elo at or above every public ML
model (luke-lite: Elo 65.3% vs best ML 63.5% on 12k NBA games). So a fancier
model is only worth its complexity if it beats THIS on seasons it never saw.
Elo is also the natural first feature for such a model.

HOW IT IS FITTED - honestly
---------------------------
Per league, three parameters are tuned by grid search on TRAINING seasons only
(K = how fast ratings move, HFA = home advantage in Elo points, REVERT = share
pulled back to the mean each off-season), minimising log loss. The last two
COMPLETE seasons are held out and scored once with the chosen values. The first
season of each file is warm-up and never scored (every team starts at 1500).

Margin of victory scales each update, as FiveThirtyEight does, with their
autocorrelation correction (a favourite's big win moves less than an
underdog's): the NBA uses 538's NBA formula, the others the log form 538 used
for the NFL. NHL shootout goals and MLB extra innings are in the margin as
ESPN reports it - small margins, small effect.

Teams are keyed by ESPN FRANCHISE id, not name, so relocations and renames
(Nets, Hornets/Bobcats, Raiders, Rams, Chargers, Thrashers/Jets, Coyotes,
Guardians...) carry their rating through. Neutral-site games get no home
advantage. NFL ties score 0.5.

WHAT IT CANNOT TELL YOU
-----------------------
Whether it beats the bookmaker. The history has no odds (ESPN only prices
upcoming games), so the comparison here is against "always back the home side"
and a coin flip. The market comparison needs live prices and the ledger.

Usage:
    python models/elo.py                 # tune + evaluate all four, write outputs
    python models/elo.py --feed nba
Writes data/sports_history/elo_params.json (chosen values + held-out metrics)
and data/sports_history/elo_ratings_<feed>.csv (current ratings).
"""

import argparse
import csv
import itertools
import json
import math
import os
import sys
from datetime import datetime, timezone

HIST = "data/sports_history"
MEAN = 1505.0          # 538's league mean
TEST_SEASONS = 2       # last N complete seasons are held out

# Search grids. Deliberately coarse: a fine grid on ~15 seasons over-fits the
# tuning period. Ranges bracket the values 538 published (NBA K=20 HFA=100,
# NFL K=20 HFA=65, MLB K=4 HFA=24; NHL public models use K~6-8, HFA~30-50).
GRIDS = {
    "nba": {"k": [10, 14, 18, 20, 24, 28], "hfa": [40, 60, 80, 100, 120],
            "revert": [0.25, 0.33, 0.5], "mov": "nba538"},
    "nfl": {"k": [10, 15, 20, 25, 30], "hfa": [20, 35, 50, 65, 80],
            "revert": [0.25, 0.33, 0.5], "mov": "log"},
    "nhl": {"k": [2, 4, 6, 8, 10, 12], "hfa": [10, 25, 40, 55, 70],
            "revert": [0.25, 0.33, 0.5], "mov": "log"},
    "mlb": {"k": [2, 3, 4, 6, 8], "hfa": [10, 20, 30, 40, 50],
            "revert": [0.2, 0.33, 0.5], "mov": "log"},
}

# A season counts as complete when it has at least this share of the median
# season's games - the in-progress NFL/MLB 2026 seasons fail it.
COMPLETE_SHARE = 0.9


def load(feed: str) -> list:
    path = os.path.join(HIST, f"{feed}.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    games = []
    for r in rows:
        games.append({
            "date": r["kickoff_utc"] or r["date"],
            "season": int(r["season"]),
            "home": r["home_id"], "away": r["away_id"],
            "home_name": r["home_team"], "away_name": r["away_team"],
            "hs": int(r["home_score"]), "as": int(r["away_score"]),
            "neutral": r["neutral_site"] == "1",
        })
    games.sort(key=lambda g: (g["date"], g["home"]))
    return games


def win_prob(diff: float) -> float:
    """P(first side wins) for an Elo difference, home advantage included."""
    return 1.0 / (1.0 + 10 ** (-diff / 400.0))


def mov_mult(kind: str, margin: int, winner_diff: float) -> float:
    """538's margin-of-victory multiplier, with the autocorrelation term that
    shrinks a favourite's blowout. winner_diff = winner Elo - loser Elo."""
    if margin == 0:
        return 1.0
    if kind == "nba538":
        return ((margin + 3) ** 0.8) / (7.5 + 0.006 * winner_diff)
    return math.log(margin + 1) * (2.2 / (winner_diff * 0.001 + 2.2))


def run_elo(games: list, k: float, hfa: float, revert: float, mov: str,
            early_k: float = 1.0, early_n: int = 0):
    """Walk the games in date order. Yields (game, p_home) with p_home computed
    BEFORE the game updates anything, then returns the final ratings.
    early_k/early_n: K x early_k for a team's first early_n games of a season
    (models/elo_plus.py; NBA only, 21 Oct 2026)."""
    ratings, season, played = {}, None, {}
    out = []
    for g in games:
        if g["season"] != season:
            if season is not None:
                for t in ratings:
                    ratings[t] = ratings[t] * (1 - revert) + MEAN * revert
            season, played = g["season"], {}
        rh = ratings.setdefault(g["home"], MEAN)
        ra = ratings.setdefault(g["away"], MEAN)
        adv = 0.0 if g["neutral"] else hfa
        p = win_prob(rh + adv - ra)
        out.append((g, p))

        if g["hs"] == g["as"]:
            s, mult = 0.5, 1.0
        else:
            s = 1.0 if g["hs"] > g["as"] else 0.0
            diff = (rh + adv - ra) if s == 1.0 else (ra - rh - adv)
            mult = mov_mult(mov, abs(g["hs"] - g["as"]), diff)
        kh = k * (early_k if played.get(g["home"], 0) < early_n else 1.0)
        ka = k * (early_k if played.get(g["away"], 0) < early_n else 1.0)
        ratings[g["home"]] = rh + kh * mult * (s - p)
        ratings[g["away"]] = ra - ka * mult * (s - p)
        played[g["home"]] = played.get(g["home"], 0) + 1
        played[g["away"]] = played.get(g["away"], 0) + 1
    return out, ratings


def metrics(pairs: list) -> dict:
    n = len(pairs)
    eps = 1e-12
    ll = br = acc = 0.0
    for g, p in pairs:
        y = 0.5 if g["hs"] == g["as"] else (1.0 if g["hs"] > g["as"] else 0.0)
        p = min(max(p, eps), 1 - eps)
        ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        br += (p - y) ** 2
        if y != 0.5:
            # A 50/50 call has no favourite: half right, not "picked away".
            acc += 0.5 if p == 0.5 else (1.0 if (p > 0.5) == (y == 1.0) else 0.0)
    decided = sum(1 for g, _ in pairs if g["hs"] != g["as"])
    return {"n": n, "log_loss": round(ll / n, 4), "brier": round(br / n, 4),
            "accuracy": round(acc / max(decided, 1), 4)}


def split_seasons(games: list) -> tuple:
    counts = {}
    for g in games:
        counts[g["season"]] = counts.get(g["season"], 0) + 1
    seasons = sorted(counts)
    median = sorted(counts.values())[len(counts) // 2]
    complete = [s for s in seasons if counts[s] >= COMPLETE_SHARE * median]
    test = complete[-TEST_SEASONS:]
    warmup = seasons[0]
    train = [s for s in seasons if warmup < s < test[0]]
    return warmup, train, test


def evaluate(feed: str) -> dict:
    games = load(feed)
    warmup, train, test = split_seasons(games)
    grid = GRIDS[feed]

    best = None
    for k, hfa, rv in itertools.product(grid["k"], grid["hfa"], grid["revert"]):
        pairs, _ = run_elo(games, k, hfa, rv, grid["mov"])
        m = metrics([(g, p) for g, p in pairs if g["season"] in train])
        if best is None or m["log_loss"] < best[0]["log_loss"]:
            best = (m, k, hfa, rv)
    train_m, k, hfa, rv = best

    pairs, ratings = run_elo(games, k, hfa, rv, grid["mov"])
    test_pairs = [(g, p) for g, p in pairs if g["season"] in test]
    elo_m = metrics(test_pairs)

    # Baselines on the SAME held-out games. The home rate is learned from the
    # training seasons only, never from the test seasons it is scored on.
    train_games = [g for g in games if g["season"] in train and g["hs"] != g["as"]]
    home_rate = sum(g["hs"] > g["as"] for g in train_games) / len(train_games)
    home_m = metrics([(g, 0.5 if g["neutral"] else home_rate) for g, _ in test_pairs])
    coin_m = metrics([(g, 0.5) for g, _ in test_pairs])

    # Calibration on the held-out games: does "70%" win about 70%?
    bands = []
    for lo in (0.5, 0.6, 0.7, 0.8):
        hi = lo + 0.1 if lo < 0.8 else 1.01
        sel = []
        for g, p in test_pairs:
            if g["hs"] == g["as"]:
                continue
            fav_p = max(p, 1 - p)
            if lo <= fav_p < hi:
                sel.append((fav_p, (p >= 0.5) == (g["hs"] > g["as"])))
        if sel:
            bands.append({"band": f"{int(lo*100)}-{int(min(hi,1)*100)}%",
                          "n": len(sel),
                          "claimed": round(sum(s[0] for s in sel) / len(sel), 3),
                          "actual": round(sum(s[1] for s in sel) / len(sel), 3)})

    last_name = {}
    games_played = {}
    for g in games:
        last_name[g["home"]] = g["home_name"]
        last_name[g["away"]] = g["away_name"]
        games_played[g["home"]] = games_played.get(g["home"], 0) + 1
        games_played[g["away"]] = games_played.get(g["away"], 0) + 1
    latest_season = max(g["season"] for g in games)
    active = {g["home"] for g in games if g["season"] == latest_season} | \
             {g["away"] for g in games if g["season"] == latest_season}

    return {
        "feed": feed,
        "params": {"k": k, "hfa": hfa, "revert": rv, "mov": grid["mov"]},
        "seasons": {"warmup": warmup, "train": [train[0], train[-1]], "test": test},
        "train": train_m,
        "test": {"elo": elo_m, "home_always": home_m, "coin_flip": coin_m},
        "home_rate_train": round(home_rate, 4),
        "calibration_test": bands,
        "ratings": sorted(((t, round(r, 1), last_name[t], games_played[t])
                           for t, r in ratings.items() if t in active),
                          key=lambda x: -x[1]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Elo baseline for NBA/NFL/NHL/MLB")
    ap.add_argument("--feed", action="append", choices=sorted(GRIDS))
    args = ap.parse_args()
    feeds = args.feed or ["nba", "nfl", "nhl", "mlb"]

    params_path = os.path.join(HIST, "elo_params.json")
    try:
        with open(params_path, encoding="utf-8") as fh:
            saved = json.load(fh)
    except (FileNotFoundError, ValueError):
        saved = {}

    for feed in feeds:
        res = evaluate(feed)
        t = res["test"]
        print(f"\n=== {feed.upper()}  K={res['params']['k']} HFA={res['params']['hfa']} "
              f"revert={res['params']['revert']}  (tuned on {res['seasons']['train'][0]}-"
              f"{res['seasons']['train'][1]}, tested on {res['seasons']['test']})")
        print(f"  held-out {t['elo']['n']} games      log loss   brier   accuracy")
        for name in ("elo", "home_always", "coin_flip"):
            m = t[name]
            print(f"    {name:18} {m['log_loss']:9.4f} {m['brier']:7.4f} {m['accuracy']:9.1%}")
        print("  calibration (favourite's stated chance -> how often it won):")
        for b in res["calibration_test"]:
            print(f"    {b['band']:>8}  n={b['n']:5}  claimed {b['claimed']:.1%}  "
                  f"actual {b['actual']:.1%}")
        print("  top 5 now: " + ", ".join(f"{name} {r:.0f}" for _, r, name, _ in res["ratings"][:5]))

        with open(os.path.join(HIST, f"elo_ratings_{feed}.csv"), "w", newline="",
                  encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["team_id", "team", "rating", "games"])
            for tid, r, name, n in res["ratings"]:
                w.writerow([tid, name, r, n])
        saved[feed] = {k: v for k, v in res.items() if k != "ratings"}
        saved[feed]["fitted_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    with open(params_path, "w", encoding="utf-8") as fh:
        json.dump(saved, fh, indent=2)
    print(f"\nWrote {params_path} and elo_ratings_<feed>.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
