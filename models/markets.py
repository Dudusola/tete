"""
markets.py - Derive multiple bet markets from the model's H/D/A probabilities
and fitted team goal rates.

Every market here comes from ONE Poisson score matrix per fixture. The split
between the two sides is implied by the 1X2 probabilities; the total is the
fitted attack/defence expectation from goals_model, or a league average when
either team is unrated.
"""

import math
from datetime import datetime, timedelta
from dataclasses import dataclass

from models import goals_model


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
    # The real kickoff, when we have one. `kickoff_time` above is a PLACEHOLDER
    # for most fixtures — PredictZ writes "00:00", measured at 38/38 of today's
    # slate — so it cannot order a slip, while kickoff_utc was populated for
    # 38/38 of the same slate. Defaulted and declared LAST on purpose: the
    # self-test below builds BetOption positionally, and a non-defaulted field
    # here would break it.
    kickoff_utc: str | None = None


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


_MEANS_CACHE: dict = {}

# Average total goals in a football match. The SCALE for the handicap score
# matrix — the 1X2 probabilities cannot supply a total, only a split.
LEAGUE_AVG_TOTAL_GOALS = 2.70


def _means_from_1x2(H: float, A: float) -> tuple:
    """Poisson means implied by our win probabilities, at a normal goal total.

    The SPLIT between the two means is fitted to the share of non-draw
    probability belonging to the home side, which is what the model actually
    knows. The total is a league-average prior, which it does not — callers
    that have a fitted expected total (see goals_model) rescale to it and keep
    this split.

    The predicted SCORELINE is deliberately not used here. It is an integer
    ("1-0", "0-0"), and feeding it in as a rate is what priced Under 2.5 at
    91-99% while bookmakers had it near even money — a predicted value
    consumed as though it were a measured one, which is bug #1's mistake.
    """
    key = (round(H, 3), round(A, 3))
    hit = _MEANS_CACHE.get(key)
    if hit:
        return hit

    T = LEAGUE_AVG_TOTAL_GOALS
    denom = H + A
    share = 0.5 if denom <= 0 else H / denom

    best, best_err = 0.5, float("inf")
    r = 0.20                      # bounded: never drive a side to zero goals
    while r <= 0.80 + 1e-9:
        mx = _poisson_matrix(T * r, T * (1 - r), max_goals=8)
        h = a = 0.0
        for i, row in enumerate(mx):
            for j, prob in enumerate(row):
                if i > j:
                    h += prob
                elif i < j:
                    a += prob
        got = 0.5 if (h + a) <= 0 else h / (h + a)
        err = (got - share) ** 2
        if err < best_err:
            best, best_err = r, err
        r += 0.01

    out = (round(T * best, 3), round(T * (1 - best), 3))
    _MEANS_CACHE[key] = out
    return out


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
        kickoff_utc=pred.get("kickoff_utc"),
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

    # ── One score matrix, every derived market ──────────────────────────────
    #
    # Handicap, Over/Under, BTTS and Result & Goals all read the SAME Poisson
    # matrix, built from the 1X2 probabilities and a league-average total.
    # Sharing it is deliberate: two matrices meant the same fixture could be
    # quoted a handicap off one set of means and a goals line off another, and
    # nothing forced the two to agree.
    #
    # Handicap used to be derived from the predicted-scoreline block, so it only
    # existed when a scoreline was present — 1 of 18 upcoming fixtures. It was
    # effectively invisible. Moving it here fixed that, and it measured well
    # afterwards: +0 to +5 points, erring safe.
    #
    # Over/Under and BTTS have now followed it, and that REVERSES the call this
    # comment used to record. The old reasoning was that a handicap asks about
    # the MARGIN, which the 1X2 probabilities describe, whereas Over/Under and
    # BTTS ask about the TOTAL, which nothing here models — so those two were
    # left on the predicted scoreline. Measuring the consequence killed that
    # argument. Over 651 graded fixtures:
    #
    #     BTTS - No     predicted 73.2%   actual 50.6%   gap -22.6
    #     BTTS - Yes    predicted 26.8%   actual 49.4%   gap +22.6
    #     Over 1.5      predicted 60.7%   actual 70.6%   gap  +9.9
    #     Over 2.5      predicted 38.7%   actual 48.9%   gap +10.2
    #     Over 3.5      predicted 22.0%   actual 26.1%   gap  +4.1
    #
    # The cause is not a mild bias, it is a degenerate distribution. Forebet
    # publishes an integer MODAL scoreline, and 49% of them contain a zero
    # ("2-0", "1-0", "0-1"). Consumed as a Poisson RATE, a zero asserts that the
    # side will not score, probability 1.0 — floored to 0.05 by _poisson_matrix,
    # which prices BTTS - Yes at 3%. Averaged over a slate where half the
    # fixtures do that, BTTS - Yes reads 26.8% against a real rate of 49.4%.
    # Because risk_from_prob reads these numbers, the worst legs were the ones
    # stamped LOW RISK.
    #
    # Not having a total is better than having a broken one. Scored by Brier on
    # the four goals markets, 180 fixtures with both a scoreline and a stored
    # prediction (720 legs):
    #
    #     scoreline as a rate (old)            0.2819
    #     this matrix, flat league total       0.2242
    #     fitted: affine map of the scoreline  0.2302  (held out)
    #     fitted: blended with avg-goals       0.2365  (held out)
    #
    # Every FITTED alternative lost, and both optimisers walked their parameters
    # to the grid edge — minimum weight on the scoreline, and weight exactly 0.0
    # on the average-goals column. That is the fit reporting that the predicted
    # scoreline carries no usable information about the total once the 1X2
    # probabilities are known. The winner has no fitted parameters at all, so
    # there is nothing in it to overfit and its held-out score IS its score.
    #
    # over_2_5_pct is gone from this function for the same reason. Despite the
    # name it holds an average-goals figure (measured range 0.50-5.13, mean
    # 2.34 against an actual 2.64), it was present on only 29% of rows, and the
    # scaling it fed was a no-op on average — mean total 2.307 before, 2.307
    # after. btts_pct is NULL on every row ever scraped; no scraper writes it.
    #
    # Resulting calibration, same fixtures — every market inside +5, which is
    # the band Match Result and Handicap already sit in:
    #
    #     Over 1.5   -9.9 -> +4.6      Over 3.5   -4.1 -> +2.5
    #     Over 2.5  -10.2 -> +1.7      BTTS      -22.6 -> +0.7
    #
    # Two honest consequences. These markets are now a deterministic function
    # of the 1X2 probabilities, so Over 2.5 and BTTS land near 50% on most
    # fixtures and will rarely clear the LOW-risk bar — correct, because we have
    # no genuine information about the total, and the reason a real goals model
    # (team attack/defence rates from the historical files) is the next job.
    # And they now exist on every priced fixture rather than only those carrying
    # a scoreline, so the builder has more goals legs to choose from, not fewer.
    if (H + A) > 0:
        mu_h, mu_a = _means_from_1x2(H, A)

        # The SPLIT above is what the 1X2 probabilities know. The TOTAL is what
        # they do not, and the flat league constant that first fixed these
        # markets is a placeholder standing in for a measurement. goals_model
        # supplies the measurement: attack/defence rates fitted from 98,422
        # historical matches, time-decayed and shrunk toward average for clubs
        # with little history, giving expected totals from 1.98 to 3.60 instead
        # of 2.70 for every fixture alike.
        #
        # Ratings replace the total and leave the split alone. Fitting the split
        # from the ratings too was measured and is NOT used: it improved the
        # goals Brier by 0.0002 while moving the handicap Brier from 0.1318 to
        # 0.1344, and the handicap markets are already calibrated to within
        # +0/+5. Trading a market that works for a rounding error in one that
        # now also works is not a trade.
        #
        # Graded on 424 fixtures from 2026-06-15 with both teams rated, ratings
        # fitted with a cutoff so none of the graded matches informed them:
        #
        #     goals Brier   flat total 0.2178 -> ratings total 0.2151
        #     hcap  Brier   flat total 0.1318 -> ratings total 0.1324
        #
        # Honest scale: that is a 1.2% goals gain, not a transformation. The
        # large win was refusing to read Forebet's modal scoreline as a rate
        # (0.2819 -> 0.2242); this is a refinement on top of it. Its real value
        # is discrimination — a flat total cannot tell a 1.98-goal fixture from
        # a 3.60-goal one, so before this every Over/Under pick was driven
        # purely by how lopsided the 1X2 was.
        #
        # An unrated side returns None rather than half a guess, and then this
        # falls back to the flat total, which is what shipped before. That is
        # 35% of fixtures today and shrinks as history accumulates.
        xg = goals_model.expected_goals(home, away)
        # An unrated side still gets today's scoring level, not the fixed 2.70.
        total = sum(xg) if xg else goals_model.base_total()
        if total and (mu_h + mu_a) > 0:
            scale = total / (mu_h + mu_a)
            mu_h, mu_a = mu_h * scale, mu_a * scale

        mx = _poisson_matrix(mu_h, mu_a)

        add("Handicap", f"{home} +1.5", _matrix_prob(mx, lambda i, j: i - j >= -1))
        add("Handicap", f"{away} +1.5", _matrix_prob(mx, lambda i, j: j - i >= -1))
        add("Handicap", f"{home} -1.5", _matrix_prob(mx, lambda i, j: i - j >= 2))
        add("Handicap", f"{away} -1.5", _matrix_prob(mx, lambda i, j: j - i >= 2))

        # Over/Under lines
        for line in (1.5, 2.5, 3.5):
            over = _matrix_prob(mx, lambda i, j: i + j > line)
            add(f"Over/Under {line}", f"Over {line} Goals", over)
            add(f"Over/Under {line}", f"Under {line} Goals", 1 - over)

        # BTTS
        btts_yes = _matrix_prob(mx, lambda i, j: i >= 1 and j >= 1)
        add("Both Teams To Score", "BTTS - Yes", btts_yes)
        add("Both Teams To Score", "BTTS - No", 1 - btts_yes)

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


def _market_family(opt) -> str:
    """Group markets that would win or lose TOGETHER across different matches.

    The unit that matters for accumulator correlation is not the market name,
    it is the DIRECTION of the bet against a shared factor:

      * "Over 2.5" here and "Over 1.5" there are both overs — a high-scoring
        round of fixtures carries both, so they belong to one family even
        though the lines differ.
      * "Over 2.5" here and "UNDER 2.5" there are opposite bets on the same
        factor. They partially hedge, so they must NOT share a family —
        counting them together would penalise a slip that is actually more
        balanced than a single-direction one.
      * Match Result / Double Chance / Draw No Bet are team-specific. They
        still share a mild "favourites hold" factor, so they are capped, but
        each market keeps its own family rather than being lumped together.
    """
    market = (opt.market or "").strip()
    selection = (opt.selection or "").lower()
    if market.startswith("Over/Under") or "goals" in selection:
        if "under" in selection:
            return "goals-under"
        if "over" in selection:
            return "goals-over"
    if market == "Both Teams To Score":
        return "btts-no" if "no" in selection else "btts-yes"
    return market


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
               pinned: list | None = None,
               exclude_options: set | None = None) -> list:
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
        # One MARKET of a fixture the bookmaker does not offer (a handicap line
        # it doesn't list, a combo it can't book): drop that option only, so the
        # fixture falls back to its next-best market (api/server.get_slip).
        if exclude_options and (key, opt.market, opt.selection) in exclude_options:
            continue
        if key in pinned_keys:
            continue   # fixture already locked in by a Banker
        by_teams.setdefault(key, []).append(opt)

    # Per fixture, keep the RANKED options rather than only the best one. The
    # correlation cap below needs a fallback: if a fixture's top pick is in a
    # market family that is already full, we want that fixture's next-best pick
    # in another family, not to lose the fixture.
    fixtures = []          # each: {"date": str, "opts": [BetOption ranked]}
    for team_key, opts in by_teams.items():
        clusters = []
        for opt in sorted(opts, key=lambda o: o.probability, reverse=True):
            placed = False
            for c in clusters:
                if _dates_close(c["date"], opt.match_date):
                    c["opts"].append(opt)      # same fixture, lower-ranked
                    placed = True
                    break
            if not placed:
                clusters.append({"date": opt.match_date, "opts": [opt]})
        fixtures.extend(clusters)

    # Soonest matchday first; best fixture within that day.
    #
    # WHY not pure probability, which is what this used to be: the candidate
    # pool spans today, tomorrow AND anything further out in the table, while
    # today's fixtures drop out one by one as their kickoffs pass. By
    # mid-afternoon tomorrow is the bigger, un-depleted pool, so a pure
    # probability sort quietly fills the slip with tomorrow's games — Boss
    # noticed this from the outside ("the builder mostly builds with tomorrow
    # games rather than the same day games"). A slip built at 10am that cannot
    # settle until tomorrow night is a worse product than a slightly
    # lower-probability one that settles tonight.
    #
    # Preferring the soonest day costs very little quality, because every
    # candidate has ALREADY passed the risk-band filter above — we are choosing
    # between legs of comparable strength, not trading a good bet for a soon
    # one. Today fills first and the list simply continues into tomorrow when
    # today runs out, which is also the right behaviour late in the day when
    # little of today is left.
    #
    # It also fixes a quieter version of the same bug: a fixture three days out
    # could previously outrank everything today on probability alone and land
    # in a slip the user thought was for tonight.
    #
    # Nothing random, so the slip stays deterministic and 'swap a leg' still
    # back-fills predictably.
    fixtures.sort(key=lambda c: (c["date"], -c["opts"][0].probability))

    # ── Correlation cap ─────────────────────────────────────────────────────
    # Six legs of "Under 2.5 Goals" is not six bets, it is one bet on a
    # low-scoring afternoon, staked six times. Each leg is individually the
    # best call for its fixture, so the old "highest probability per fixture,
    # take the top N" logic produced exactly that — and then reported an
    # accumulator probability that multiplies the legs as if independent,
    # which materially OVERSTATES the real chance of the slip landing.
    #
    # This is not the "randomise for variety" idea that decision #9 rejects.
    # Nothing here is random, the slip stays deterministic, and the reason is
    # accuracy rather than appearance: a correlated accumulator is a worse bet
    # than its stated odds imply.
    cap = max(2, -(-num_games // 2))          # ceil(n/2), min 2
    family_count = {}
    for leg in pinned_legs:                    # a Banker counts toward the cap
        family_count[_market_family(leg)] = family_count.get(_market_family(leg), 0) + 1

    chosen = []
    slots = max(0, num_games - len(pinned_legs))
    for c in fixtures:
        if len(chosen) >= slots:
            break
        for opt in c["opts"]:                  # ranked best-first
            fam = _market_family(opt)
            if family_count.get(fam, 0) >= cap:
                continue                       # family full — try this
                                               # fixture's next-best market
            chosen.append(opt)
            family_count[fam] = family_count.get(fam, 0) + 1
            break
        # If every option for this fixture is in a full family, the fixture is
        # skipped and a later one takes the slot. Deliberate: dropping a
        # fixture costs one pick, repeating a saturated market costs the slip
        # its independence.

    # Second pass: honour the requested number of games.
    #
    # The cap is a PREFERENCE, not a promise to the user. Asking for 6 and
    # receiving 4 is a worse experience than receiving 6 with a repeat in it —
    # and it happened for real: once the goals markets went back to being
    # rare, a LOW slip had only Double Chance to work with, the cap allowed
    # three, and the slip came back short. Diversify where the options exist;
    # never silently deliver less than was asked for.
    if len(chosen) < slots:
        used_fixtures = {id(o) for o in chosen}
        for c in fixtures:
            if len(chosen) >= slots:
                break
            if any(id(o) in used_fixtures for o in c["opts"]):
                continue                       # this fixture already used
            if c["opts"]:
                best = c["opts"][0]
                chosen.append(best)
                used_fixtures.add(id(best))

    # Bankers stay at the top — the user pinned them, and that placement is
    # deliberate (see the docstring). The rest now read in kickoff order, like
    # a real betting slip, instead of the probability order they happened to be
    # selected in. Selection order and DISPLAY order are different questions;
    # the correlation cap above needs the former, the reader wants the latter.
    # match_date leads so days stay grouped even where kickoff_utc is missing;
    # kickoff_utc then gives the real within-day order (kickoff_time is the
    # "00:00" placeholder on essentially every fixture, so sorting on it did
    # nothing at all).
    chosen.sort(key=lambda o: (o.match_date, o.kickoff_utc or o.kickoff_time or ""))
    return pinned_legs + chosen


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


WHEN_HOURS = {"early": (0, 15), "afternoon": (15, 19), "late": (19, 24)}   # Lagos time


def filter_options(options: list, markets: set | None = None, min_prob: float = 0.0,
                   max_prob: float = 1.0, day: str | None = None, when: str | None = None,
                   keep: set | None = None) -> list:
    """The Builder's optional filters (21 Oct 2026, from Puntrr's slip builder),
    applied BEFORE build_slip so risk bands, dedupe, the correlation cap and
    swaps all work unchanged.
      markets   market families to allow ("Over/Under" matches "Over/Under 2.5")
      min_prob / max_prob   per-leg probability; the page turns an odds range
                into this (odds = 1 / probability)
      day / when  kickoff date and Early (<15:00) / Afternoon / Late (19:00+) in
                Lagos time (UTC+1, no DST). A leg with no real kickoff can't be
                placed in a window, so a time filter leaves it out.
      keep      (teams_key, market, selection) of pinned bankers: always kept."""
    out = []
    for o in options:
        if keep and (_teams_key(o), o.market, o.selection) in keep:
            out.append(o)
            continue
        if markets and not any(o.market == m or o.market.startswith(m + " ") for m in markets):
            continue
        if not (min_prob <= o.probability <= max_prob):
            continue
        if day or when:
            if not o.kickoff_utc:
                continue
            lagos = datetime.strptime(o.kickoff_utc[:16], "%Y-%m-%d %H:%M") + timedelta(hours=1)
            if day and lagos.strftime("%Y-%m-%d") != day:
                continue
            if when and not (WHEN_HOURS[when][0] <= lagos.hour < WHEN_HOURS[when][1]):
                continue
        out.append(o)
    return out


if __name__ == "__main__":
    # NOTE: dates are RELATIVE to today. They used to be hardcoded to July
    # 2026, which meant that from August onwards kickoff_passed() filtered
    # every fixture and the test asserted "expected 3, got 0" on every run —
    # a self-test that always fails teaches people to ignore self-tests.
    from datetime import date, timedelta
    d1 = (date.today() + timedelta(days=2)).strftime("%Y-%m-%d")
    d2 = (date.today() + timedelta(days=3)).strftime("%Y-%m-%d")

    def mk(mid, h, a, dt, prob, risk, market="Double Chance", selection=None):
        return BetOption(mid, h, a, "WC", dt, "00:00", market,
                         selection or f"{h} or Draw", prob, risk)

    # 1. Same fixture from two sources, dates a day apart -> must collapse.
    opts = [
        mk("forebet_1", "Switzerland", "Algeria", d2, 0.53, "MEDIUM"),
        mk("predictz_9", "Switzerland", "Algeria", d1, 0.52, "MEDIUM"),
        mk("forebet_2", "Portugal", "Croatia", d2, 0.51, "MEDIUM"),
        mk("predictz_8", "Portugal", "Croatia", d1, 0.50, "MEDIUM"),
        mk("forebet_3", "Spain", "Austria", d1, 0.59, "MEDIUM"),
    ]
    slip = build_slip(opts, "MEDIUM", 10)
    print("Slip legs (should be 3 unique fixtures, no Switzerland/Portugal dupes):")
    for leg in slip:
        print(f"  {leg.home_team} vs {leg.away_team} [{leg.match_date}] {leg.probability:.0%}")
    assert len(slip) == 3, f"expected 3, got {len(slip)}"
    print("PASS - duplicates collapsed across the date window.")

    # 2. Correlation cap: six fixtures whose best bet is the same market must
    #    NOT produce six identical legs. Each fixture also offers a fallback,
    #    so the slip should still fill to six.
    many = []
    for i in range(6):
        many.append(mk(f"m{i}", f"Home{i}", f"Away{i}", d1, 0.90, "LOW",
                       "Over/Under 2.5", "Under 2.5 Goals"))
        many.append(mk(f"m{i}", f"Home{i}", f"Away{i}", d1, 0.80, "LOW",
                       "Double Chance", f"Home{i} or Draw"))
    slip = build_slip(many, "LOW", 6)
    unders = sum(1 for l in slip if l.selection == "Under 2.5 Goals")
    print(f"\nCap test: {len(slip)} legs, {unders} of them 'Under 2.5 Goals'")
    assert len(slip) == 6, f"expected 6 legs, got {len(slip)}"
    assert unders <= 3, f"correlation cap breached: {unders} same-family legs"
    print("PASS - no more than ceil(n/2) legs share a market family.")
    # 3. Builder filters (filter_options): market family, probability band,
    #    Lagos day/time window, and pinned bankers bypass every filter.
    def ko(mid, utc, market, sel, prob):
        o = mk(mid, f"H{mid}", f"A{mid}", d1, prob, "MEDIUM", market, sel)
        o.kickoff_utc = utc
        return o
    pool = [ko("a", f"{d1} 13:00", "Over/Under 2.5", "Over 2.5 Goals", 0.60),   # 14:00 Lagos
            ko("b", f"{d1} 19:30", "Match Result", "Hb Win", 0.55),             # 20:30 Lagos
            ko("c", f"{d1} 23:30", "Double Chance", "Hc or Draw", 0.80),        # 00:30 next day
            ko("d", None, "Match Result", "Hd Win", 0.70)]                      # no real kickoff
    ids = lambda xs: sorted(x.match_id for x in xs)
    assert ids(filter_options(pool, markets={"Over/Under"})) == ["a"]
    assert ids(filter_options(pool, min_prob=0.58, max_prob=0.75)) == ["a", "d"]
    assert ids(filter_options(pool, when="late")) == ["b"]
    assert ids(filter_options(pool, day=d1)) == ["a", "b"]           # c is tomorrow in Lagos
    # c kicks off 00:30 Lagos, which is "early"; d has no kickoff but is a banker.
    assert ids(filter_options(pool, when="early", keep={(_teams_key(pool[3]), "Match Result", "Hd Win")})) == ["a", "c", "d"]
    print("PASS - builder filters (market, odds band, Lagos day/time, bankers kept).")
