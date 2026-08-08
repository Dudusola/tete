"""
markets.py - Derive multiple bet markets from the model's H/D/A probabilities
plus scraped Forebet data (predicted score, average goals).

No new model needed - every market here is computed from numbers we already have.
"""

import math
from datetime import datetime
from dataclasses import dataclass


@dataclass
class BetOption:
    match_id: str
    home_team: str
    away_team: str
    league: str
    match_date: str
    kickoff_time: str
    market: str        # "Double Chance", "Over/Under", etc.
    selection: str     # "Brazil or Draw", "Over 2.5", etc.
    probability: float # 0..1
    risk: str          # LOW / MEDIUM / HIGH


def risk_from_prob(p: float) -> str:
    """Higher probability = safer = lower risk."""
    if p >= 0.78:
        return "LOW"
    if p >= 0.55:
        return "MEDIUM"
    return "HIGH"


def _poisson_matrix(mu_home: float, mu_away: float, max_goals: int = 8) -> list:
    """Joint scoreline probabilities P(home=i, away=j) assuming independent
    Poisson goals. Standard approach (what Forebet itself does) - much more
    honest than fixed heuristics for goals/handicap markets."""
    def pois(mu, k):
        return math.exp(-mu) * mu ** k / math.factorial(k)
    mu_home = max(mu_home, 0.05)
    mu_away = max(mu_away, 0.05)
    return [[pois(mu_home, i) * pois(mu_away, j)
             for j in range(max_goals + 1)] for i in range(max_goals + 1)]


def _matrix_prob(matrix: list, cond) -> float:
    """Sum P(i,j) over scorelines where cond(i, j) is true."""
    return sum(p for i, row in enumerate(matrix)
               for j, p in enumerate(row) if cond(i, j))


def derive_markets(pred: dict) -> list:
    """Derive all bet markets for one match from model + Forebet data."""
    H = pred["home_win_prob"]
    D = pred["draw_prob"]
    A = pred["away_win_prob"]
    home = pred["home_team"]
    away = pred["away_team"]

    base = dict(
        match_id=pred["match_id"], home_team=home, away_team=away,
        league=pred["league"], match_date=pred["match_date"],
        kickoff_time=pred["kickoff_time"],
    )

    options = []

    def add(market, selection, prob):
        if prob is None or prob <= 0:
            return
        prob = min(prob, 0.99)
        options.append(BetOption(**base, market=market, selection=selection,
                                 probability=prob, risk=risk_from_prob(prob)))

    # 1X2
    add("Match Result", f"{home} Win", H)
    add("Match Result", "Draw", D)
    add("Match Result", f"{away} Win", A)

    # Double Chance
    add("Double Chance", f"{home} or Draw", H + D)
    add("Double Chance", f"{home} or {away}", H + A)
    add("Double Chance", f"Draw or {away}", D + A)

    # Draw No Bet
    if (H + A) > 0:
        add("Draw No Bet", f"{home} (DNB)", H / (H + A))
        add("Draw No Bet", f"{away} (DNB)", A / (H + A))

    # Goals markets from predicted scoreline
    phg = pred.get("predicted_home_goals")
    pag = pred.get("predicted_away_goals")
    avg_goals = pred.get("over_2_5_pct")

    if phg is not None and pag is not None:
        # Poisson means from the predicted scoreline; if Forebet's average-goals
        # signal is present, scale total goals toward it (keep the home/away ratio)
        mu_h, mu_a = float(phg), float(pag)
        if avg_goals and (mu_h + mu_a) > 0:
            scale = ((mu_h + mu_a + float(avg_goals)) / 2) / (mu_h + mu_a)
            mu_h, mu_a = mu_h * scale, mu_a * scale
        mx = _poisson_matrix(mu_h, mu_a)

        # Over/Under lines
        for line in (1.5, 2.5, 3.5):
            over = _matrix_prob(mx, lambda i, j: i + j > line)
            add(f"Over/Under {line}", f"Over {line} Goals", over)
            add(f"Over/Under {line}", f"Under {line} Goals", 1 - over)

        # BTTS from the score matrix (was a hardcoded constant before)
        btts_yes = _matrix_prob(mx, lambda i, j: i >= 1 and j >= 1)
        add("Both Teams To Score", "BTTS - Yes", btts_yes)
        add("Both Teams To Score", "BTTS - No", 1 - btts_yes)

        # Asian Handicap ±1.5 (two-way, no push)
        add("Handicap", f"{home} -1.5", _matrix_prob(mx, lambda i, j: i - j >= 2))
        add("Handicap", f"{away} +1.5", _matrix_prob(mx, lambda i, j: j - i >= -1))
        add("Handicap", f"{away} -1.5", _matrix_prob(mx, lambda i, j: j - i >= 2))
        add("Handicap", f"{home} +1.5", _matrix_prob(mx, lambda i, j: i - j >= -1))

        # Result & Goals combos: model's win prob x P(goals line | that result)
        winner = home if H >= A else away
        winner_prob = max(H, A)
        win_cond = ((lambda i, j: i > j) if winner == home else (lambda i, j: i < j))
        p_win_mx = _matrix_prob(mx, win_cond)
        if p_win_mx > 0:
            for line in (1.5, 2.5):
                p_joint = _matrix_prob(
                    mx, lambda i, j: win_cond(i, j) and i + j > line)
                add("Result & Goals", f"{winner} & Over {line}",
                    winner_prob * (p_joint / p_win_mx))

    return options


def _teams_key(opt) -> str:
    """Sorted-teams fingerprint (date-independent) so home/away flips and
    cross-source rows collapse together."""
    return "_".join(sorted([
        opt.home_team.lower().strip(),
        opt.away_team.lower().strip(),
    ]))


def _dates_close(d1: str, d2: str, days: int = 1) -> bool:
    try:
        a = datetime.strptime(d1, "%Y-%m-%d")
        b = datetime.strptime(d2, "%Y-%m-%d")
        return abs((a - b).days) <= days
    except (ValueError, TypeError):
        return d1 == d2


def kickoff_passed(match_date: str, kickoff_time: str, grace_min: int = 5) -> bool:
    """True when we KNOW the game already kicked off. A '00:00' or unparsable
    kickoff is treated as unknown (kept) — some sources store a placeholder
    time, and wrongly dropping a bookable game is worse than keeping it;
    the bookmaker mapping marks truly-started games unmapped anyway."""
    if not match_date:
        return False
    if not kickoff_time or kickoff_time.strip() in ("", "00:00", "0:00"):
        return match_date < datetime.now().strftime("%Y-%m-%d")
    try:
        h, m = kickoff_time.strip().split(":")
        ko = datetime.strptime(match_date, "%Y-%m-%d").replace(
            hour=int(h), minute=int(m))
    except (ValueError, TypeError):
        return match_date < datetime.now().strftime("%Y-%m-%d")
    return (datetime.now() - ko).total_seconds() > grace_min * 60


def teams_key(home: str, away: str) -> str:
    """Public fingerprint for a fixture — used to exclude fixtures a user
    removed from their slip (matches _teams_key on BetOption)."""
    return "_".join(sorted([home.lower().strip(), away.lower().strip()]))


def build_slip(all_options: list, risk: str, num_games: int,
               leagues: set | None = None,
               exclude_keys: set | None = None,
               pinned: list | None = None) -> list:
    """Build a slip of num_games selections at the requested risk level.
    One bet per real fixture, deduped across sources by teams + a +/-1 day
    window (so the same match filed a day apart by two sources collapses).
    Games that have already kicked off are excluded (nobody can bet them).
    `leagues`: optional set of league names (lowercased) to restrict to.
    `pinned` (Bankers): [{home_team, away_team, market, selection}] legs the
    user locked — force-included first (bypassing risk band and league
    filter, but NOT the started-game check), rest of the slip fills around
    them. A pinned fixture that kicked off silently drops out."""
    risk = risk.upper()

    pinned_legs, pinned_keys = [], set()
    for p in (pinned or []):
        want_key = teams_key(p["home_team"], p["away_team"])
        for opt in all_options:
            if (_teams_key(opt) == want_key
                    and opt.market == p.get("market")
                    and opt.selection == p.get("selection")
                    and not kickoff_passed(opt.match_date, opt.kickoff_time)):
                if want_key not in pinned_keys:
                    pinned_legs.append(opt)
                    pinned_keys.add(want_key)
                break

    # Group candidate options by sorted-teams key.
    # Cap at 92%: above that a bet is a near-certainty bookmakers barely
    # price (odds ~1.0x) and often don't offer at all (e.g. +1.5 on a heavy
    # favourite) - worthless as a slip leg and unbookable via booking codes.
    MAX_SLIP_PROB = 0.92
    by_teams = {}
    for opt in all_options:
        if opt.risk != risk or opt.probability > MAX_SLIP_PROB:
            continue
        if kickoff_passed(opt.match_date, opt.kickoff_time):
            continue
        if leagues and opt.league.lower() not in leagues:
            continue
        key = _teams_key(opt)
        if exclude_keys and key in exclude_keys:
            continue
        if key in pinned_keys:
            continue   # fixture already locked in by a Banker
        by_teams.setdefault(key, []).append(opt)

    chosen = []
    for team_key, opts in by_teams.items():
        # Within a team group, cluster by date window and keep the single
        # highest-probability option per real fixture.
        clusters = []  # each: {"date": str, "best": BetOption}
        for opt in sorted(opts, key=lambda o: o.probability, reverse=True):
            placed = False
            for c in clusters:
                if _dates_close(c["date"], opt.match_date):
                    # same fixture; we already have the higher-prob best
                    placed = True
                    break
            if not placed:
                clusters.append({"date": opt.match_date, "best": opt})
        for c in clusters:
            chosen.append(c["best"])

    chosen.sort(key=lambda o: o.probability, reverse=True)
    return pinned_legs + chosen[:max(0, num_games - len(pinned_legs))]


def accumulator_probability(slip: list) -> float | None:
    """Combined probability of all legs hitting (independent assumption).

    Returns None for an EMPTY slip rather than 1.0. The empty product is
    mathematically 1.0, but as a payload that reads as "100% certain" — the
    worst possible thing to show for a slip with no legs in it. Callers should
    treat None as "no answer", which is the truth.
    """
    if not slip:
        return None
    p = 1.0
    for leg in slip:
        p *= leg.probability
    return p


if __name__ == "__main__":
    # Test: same fixture, two sources, dates a day apart -> must collapse
    from types import SimpleNamespace
    def mk(mid, h, a, date, prob, risk):
        return BetOption(mid, h, a, "WC", date, "00:00", "Double Chance",
                         f"{h} or Draw", prob, risk)
    opts = [
        mk("forebet_1", "Switzerland", "Algeria", "2026-07-03", 0.53, "MEDIUM"),
        mk("predictz_9", "Switzerland", "Algeria", "2026-07-02", 0.52, "MEDIUM"),
        mk("forebet_2", "Portugal", "Croatia", "2026-07-03", 0.51, "MEDIUM"),
        mk("predictz_8", "Portugal", "Croatia", "2026-07-02", 0.50, "MEDIUM"),
        mk("forebet_3", "Spain", "Austria", "2026-07-02", 0.59, "MEDIUM"),
    ]
    slip = build_slip(opts, "MEDIUM", 10)
    print("Slip legs (should be 3 unique fixtures, no Switzerland/Portugal dupes):")
    for leg in slip:
        print(f"  {leg.home_team} vs {leg.away_team} [{leg.match_date}] {leg.probability:.0%}")
    assert len(slip) == 3, f"expected 3, got {len(slip)}"
    print("\nPASS - duplicates collapsed across the date window.")