"""goals_model.py - per-team attack/defence rates, fitted from the historical files.

WHY THIS EXISTS
---------------
Over/Under and BTTS ask about the TOTAL goals in a fixture. Until this module
existed, nothing in the engine had an opinion about a total, and both markets
were derived one of two wrong ways:

  1. From Forebet's predicted scoreline consumed as a Poisson rate. 49% of those
     scorelines contain a zero ("2-0", "1-0"), and a rate of zero asserts a side
     will not score, probability 1.0. Measured over 651 graded fixtures that put
     BTTS - Yes at 26.8% against a real rate of 49.4% — a 22.6 point error, and
     because risk_from_prob reads the number, the worst legs were the ones
     labelled LOW RISK.

  2. From a flat league-average total of 2.70 for every fixture (what replaced
     it). Correctly calibrated — every goals market inside +5 — but carrying no
     information: Over 2.5 came out at 50.6% on all 46 upcoming fixtures,
     identical, because the probability of a total depends only on the total.

This module supplies the missing quantity: a total that differs per fixture,
built from what the two teams have actually done. It is the standard
independent-Poisson ratings model (the Dixon-Coles skeleton without the
low-score tau correction):

    mu_home = HOME_BASE * attack[home] * defence[away]
    mu_away = AWAY_BASE * attack[away] * defence[home]

attack and defence are multipliers around 1.0, fitted by iterating

    attack[t]  = weighted goals scored by t / expected, holding defences fixed
    defence[t] = weighted goals conceded by t / expected, holding attacks fixed

DELIBERATE CHOICES
------------------
* Recency weighting, not a flat average. A team's rate two seasons ago is
  weaker evidence than last month's. Exponential decay, HALF_LIFE_DAYS.

* Shrinkage toward 1.0 by sample size, n / (n + SHRINK_K). A club with three
  matches on file does not get an extreme rating off three matches; it gets
  something close to average, which is the honest reading of three matches.
  This is the same instinct as the SMALL SAMPLE warning in the analysis prose.

* No per-league base. Leagues score at different rates, and the obvious fix is a
  per-league constant — but that needs the ledger's league name matched to a
  historical CSV filename, which is a second name-matching problem with its own
  failure modes. Instead the league's scoring level is absorbed into its teams'
  attack and defence ratings. That is sound HERE, and only here, because both
  teams in a fixture come from the same league, so the absorbed level cancels
  where it matters. It would be wrong the moment this model was used to compare
  teams across leagues, so don't.

* One rating per team, not split by home and away. HOME_BASE and AWAY_BASE carry
  the venue effect globally. Splitting every team's rating by venue doubles the
  parameters and halves the sample behind each one, which shrinkage would then
  mostly undo.

IMPORT COST
-----------
The API serves markets at request time, so the LOOKUP runs inside the Render
image, where only api/, db/, some of models/ and two data files are copied
(deploy/render/Dockerfile, bug #15 - a missing COPY fails silently and Render
keeps serving the old image). So the lookup path here imports stdlib only, and
pandas/glob are imported inside fit(), which never runs in the container.
BOTH this file and data/goals_ratings.json need a COPY line.
"""
import json
import math
import os

HALF_LIFE_DAYS = 540      # ~18 months; a season and a half back counts half
SHRINK_K = 12             # matches needed before a rating is half-trusted
ITERATIONS = 12           # attack/defence alternation; converges well before 12
MIN_MATCHES = 3           # below this a team gets no rating at all
RATING_CLIP = (0.35, 2.6)  # no team is 4x an average attack; that is noise

RATINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "goals_ratings.json")


# ─────────────────────────── lookup (runs in the API) ───────────────────────
_CACHE: dict | None = None


def load(path: str | None = None) -> dict:
    """Read the fitted ratings. Missing file is not an error - callers fall
    back to the flat league total, which is what shipped before this module."""
    global _CACHE
    if _CACHE is not None and path is None:
        return _CACHE
    p = path or RATINGS_PATH
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    if path is None:
        _CACHE = data
    return data


_INDEX: dict | None = None

# Resolution memo: normalised query name -> normalised rating key, or "" for a
# name we already failed to resolve. The fuzzy pass below is O(teams) — about
# 1,175 comparisons — and the API derives markets at request time on a cold
# Render instance, so repeating that per fixture per request is not affordable.
# Negative results are cached too; a name that cannot be resolved cannot be
# resolved on the next call either.
_RESOLVED: dict = {}

NAME_SIM_MIN = 0.90
NAME_SIM_GAP = 0.05


def _index(data: dict) -> dict:
    """Normalised name -> rating, built once. Uses models.team_match.normalise
    so a lookup here resolves names the same way the rest of the engine does."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    from models.team_match import normalise
    _INDEX = {normalise(k): v for k, v in (data.get("teams") or {}).items()}
    return _INDEX


_STEMS: dict | None = None


def _candidates(name: str, data: dict) -> list:
    global _STEMS
    from models.team_match import normalise
    if _STEMS is None:
        _STEMS = {}
        for orig in (data.get("teams") or {}):
            for w in normalise(orig).split():
                _STEMS.setdefault(w[:3], []).append(orig)
    seen, out = set(), []
    for w in normalise(name).split():
        for orig in _STEMS.get(w[:3], []):
            if orig not in seen:
                seen.add(orig)
                out.append(orig)
    return out


def team_rating(name: str, data: dict | None = None) -> dict | None:
    """Attack/defence for one team, or None if we have no usable history.

    Exact normalised match first, then ONE guarded fuzzy attempt.

    The fuzzy step is guarded the same way predict.py guards its historical
    lookup (NAME_MATCH_MIN / NAME_MATCH_GAP): a candidate must clear a
    similarity floor AND beat the runner-up by a margin. The margin is the part
    that matters — "Al Shabab" scoring 0.93 against three different Al-somethings
    is not a match, it is a coin toss, and resolving it silently would corrupt a
    goal expectation rather than fail loudly.

    Measured need for this: with exact matching only, 55.3% of graded fixtures
    had both teams rated. The misses were not missing data — BrazilLeague.csv,
    USALeague.csv and PortugueseLeague-*.csv are all on file — they were spelling
    ("Braga" here, "Sp Braga" there; "Flamengo" vs "Flamengo RJ"). Coverage is
    the whole value of this module, since an unrated fixture falls back to the
    flat league total and learns nothing.

    Failing to resolve is a perfectly good outcome, which is why the guards can
    afford to be strict: the caller falls back to the league average, which is
    exactly what shipped before this module existed.
    """
    data = data if data is not None else load()
    if not data:
        return None
    from models.team_match import normalise
    idx = _index(data)
    hit = idx.get(normalise(name))
    if hit:
        return hit

    key = normalise(name)
    if key in _RESOLVED:
        got = _RESOLVED[key]
        return idx.get(got) if got else None

    from models.team_match import team_sim
    best = best_score = second = None
    # Only rated teams sharing a 3-letter word start (7 Oct 2026, speed): the
    # full scan made the first Builder request after a deploy take ~37 s
    # (250k comparisons). A name scoring >= NAME_SIM_MIN always shares one;
    # verified identical on every football team name in the ledger.
    for orig in _candidates(name, data):
        s = team_sim(name, orig)
        if best_score is None or s > best_score:
            best, second, best_score = orig, best_score, s
        elif second is None or s > second:
            second = s
    ok = (best_score is not None and best_score >= NAME_SIM_MIN
          and (second is None or best_score - second >= NAME_SIM_GAP))
    _RESOLVED[key] = normalise(best) if ok else ""
    return idx.get(normalise(best)) if ok else None


def expected_goals(home: str, away: str, data: dict | None = None):
    """(mu_home, mu_away) for a fixture, or None when either side is unrated.

    Returning None rather than a half-informed guess is intentional: with one
    team unrated the product below is missing a term, and the result would look
    like a fixture-specific number while carrying less information than the
    league average it displaced.
    """
    data = data if data is not None else load()
    if not data:
        return None
    rh = team_rating(home, data)
    ra = team_rating(away, data)
    if not rh or not ra:
        return None
    hb = data.get("home_base") or 1.5
    ab = data.get("away_base") or 1.2
    mu_h = hb * rh["attack"] * ra["defence"]
    mu_a = ab * ra["attack"] * rh["defence"]
    lo, hi = data.get("total_clip") or [1.4, 4.6]
    tot = mu_h + mu_a
    if tot < lo or tot > hi:
        # Clip the TOTAL, keeping the split. An extreme total is where this
        # model is least trustworthy and where a mispriced Over/Under does the
        # most damage, so the tails come back toward something defensible.
        scale = (lo if tot < lo else hi) / tot
        mu_h, mu_a = mu_h * scale, mu_a * scale
    return round(mu_h, 4), round(mu_a, 4)


# ─────────────────────────── fit (never runs in the API) ────────────────────
def fit(cutoff: str | None = None, verbose: bool = True) -> dict:
    """Fit ratings from data/historical/*.csv.

    cutoff: ISO date. Only matches STRICTLY BEFORE it are used. This exists so
    the model can be graded without look-ahead bias - fitting on matches that
    include the fixtures being graded would report a score no live run can
    reproduce.
    """
    import glob
    import pandas as pd

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = glob.glob(os.path.join(here, "data", "historical", "*.csv"))
    frames = []
    for f in files:
        try:
            df = pd.read_csv(f, encoding="utf-8", on_bad_lines="skip")
        except (UnicodeDecodeError, ValueError):
            try:
                df = pd.read_csv(f, encoding="latin-1", on_bad_lines="skip")
            except Exception:
                continue
        df = df.rename(columns={"Home": "HomeTeam", "Away": "AwayTeam",
                                "HG": "FTHG", "AG": "FTAG"})
        if not {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"} <= set(df.columns):
            continue
        frames.append(df[["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]])

    if not frames:
        raise SystemExit("no historical files parsed - refusing to write ratings")

    d = pd.concat(frames, ignore_index=True)
    d["Date"] = pd.to_datetime(d["Date"], dayfirst=True, format="mixed",
                               errors="coerce")
    d = d.dropna(subset=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])
    d["FTHG"] = pd.to_numeric(d["FTHG"], errors="coerce")
    d["FTAG"] = pd.to_numeric(d["FTAG"], errors="coerce")
    d = d.dropna(subset=["FTHG", "FTAG"])
    if cutoff:
        d = d[d["Date"] < pd.Timestamp(cutoff)]
    d = d.sort_values("Date")
    if d.empty:
        raise SystemExit("no matches survived filtering - refusing to write")

    ref = d["Date"].max()
    age = (ref - d["Date"]).dt.days.clip(lower=0)
    d = d.assign(w=(0.5 ** (age / HALF_LIFE_DAYS)))

    rows = list(zip(d["HomeTeam"].astype(str).str.strip(),
                    d["AwayTeam"].astype(str).str.strip(),
                    d["FTHG"].astype(float), d["FTAG"].astype(float),
                    d["w"].astype(float)))

    wsum = sum(r[4] for r in rows)
    home_base = sum(r[2] * r[4] for r in rows) / wsum
    away_base = sum(r[3] * r[4] for r in rows) / wsum

    teams = sorted({r[0] for r in rows} | {r[1] for r in rows})
    attack = {t: 1.0 for t in teams}
    defence = {t: 1.0 for t in teams}
    # Weighted match count, and weighted goals for/against, per team.
    n = {t: 0.0 for t in teams}
    gf = {t: 0.0 for t in teams}
    ga = {t: 0.0 for t in teams}
    for h, a, hg, ag, w in rows:
        n[h] += w; n[a] += w
        gf[h] += hg * w; ga[h] += ag * w
        gf[a] += ag * w; ga[a] += hg * w

    lo, hi = RATING_CLIP
    for _ in range(ITERATIONS):
        exp_gf = {t: 0.0 for t in teams}
        exp_ga = {t: 0.0 for t in teams}
        for h, a, hg, ag, w in rows:
            exp_gf[h] += home_base * defence[a] * w
            exp_ga[a] += home_base * attack[h] * w
            exp_gf[a] += away_base * defence[h] * w
            exp_ga[h] += away_base * attack[a] * w
        for t in teams:
            if exp_gf[t] > 0:
                attack[t] = min(max(gf[t] / exp_gf[t], lo), hi)
            if exp_ga[t] > 0:
                defence[t] = min(max(ga[t] / exp_ga[t], lo), hi)

    # Shrink toward 1.0 by weighted sample size, and drop the unusable.
    out_teams = {}
    for t in teams:
        raw_n = sum(1 for r in rows if r[0] == t or r[1] == t)
        if raw_n < MIN_MATCHES:
            continue
        k = n[t] / (n[t] + SHRINK_K)
        out_teams[t] = {
            "attack": round(1.0 + (attack[t] - 1.0) * k, 4),
            "defence": round(1.0 + (defence[t] - 1.0) * k, 4),
            "matches": raw_n,
        }

    data = {
        "home_base": round(home_base, 4),
        "away_base": round(away_base, 4),
        "half_life_days": HALF_LIFE_DAYS,
        "shrink_k": SHRINK_K,
        "total_clip": [1.4, 4.6],
        "fitted_from_matches": len(rows),
        "cutoff": cutoff or None,
        "teams": out_teams,
    }
    if verbose:
        tot = home_base + away_base
        print(f"matches used      : {len(rows):,}")
        print(f"teams rated       : {len(out_teams):,} of {len(teams):,}")
        print(f"home/away base    : {home_base:.3f} / {away_base:.3f}"
              f"  (total {tot:.3f})")
    return data


def main():
    import sys
    cutoff = None
    out = RATINGS_PATH
    for a in sys.argv[1:]:
        if a.startswith("--cutoff="):
            cutoff = a.split("=", 1)[1]
        elif a.startswith("--out="):
            out = a.split("=", 1)[1]
    data = fit(cutoff=cutoff)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"), sort_keys=True)
    print(f"wrote {out}  ({os.path.getsize(out):,} bytes)")


if __name__ == "__main__":
    main()
