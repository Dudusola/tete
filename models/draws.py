"""
draws.py - Draw-leaning fixtures, and an honest label for what that's worth.

The question this answers
------------------------
"Can we offer a draws-only option? There has to be a pattern."

There is a pattern, and it is real. Measured on 90,381 historical matches that
carry a genuine bookmaker draw price:

    all matches                              26.2% draws
    sides the market rates evenly (<5 pts)   30.1%
    a low-scoring league                     28.0%
    both together                            32.6%
    the very tightest (<2 pts) + low-scoring 33.8%

So "evenly matched" lifts the draw rate by about a third, using nothing but
information available before kickoff. That part of the intuition is correct.

Why this module does NOT claim an edge
--------------------------------------
The same measurement shows the bookmaker already knows. Actual draw rate
tracks the implied rate within about a point in every price band:

    draw price 2.9-3.1   actual 31.8%   implied 31.0%
    draw price 3.3-3.5   actual 27.4%   implied 27.6%
    draw price 3.8-4.2   actual 23.0%   implied 23.7%

Backing every draw returns -7.2% (the margin). The best segment returns -1.2%
(95% CI -6.5% to +4.2%) — indistinguishable from break-even, on 2,742 matches.
A tighter slice showed +2.1%, but its interval spans -6.3% to +10.6%: that is
noise, and treating it as a finding would be the thin-sample mistake this
project has already made once with the consensus boost.

**So this is a FILTER, not a tip.** It surfaces the fixtures where a draw is
most plausible and states the honest rate. Anything stronger would be selling
a pattern we have just proved is priced in.

Why not the league term
-----------------------
The low-scoring-league signal is real but needs a league's historical scoring
rate, and our historical files are keyed per season ("SerieB-2024-2025") while
live fixtures carry labels like "Serie B" or "Copa Libertadores". That mapping
is guesswork, and a wrong mapping would silently apply Argentina's draw rate to
a Danish match. Market balance carries most of the lift (30.1 of the 32.6) and
needs no mapping at all, so it is what this uses. Revisit if the league labels
are ever reconciled properly.
"""

# The market's own view of how evenly matched the sides are: |1/odds_home -
# 1/odds_away|. Below this, historical draw rate is 30.1% against a 26.2%
# baseline. Not tuned to maximise anything on our own thin ledger — taken
# straight from the 90k-match measurement, where it is a stable step rather
# than a fitted optimum.
BALANCE_TIGHT = 0.05
BALANCE_VERY_TIGHT = 0.02

# What the segment actually hits, for labelling. Stated as measured, not as a
# promise: these are historical frequencies at a price the book sets.
RATE_TIGHT = 30.1
RATE_VERY_TIGHT = 33.8
RATE_BASELINE = 26.2


def market_balance(odds_home, odds_away):
    """How evenly the bookmaker rates the two sides. Lower = more even.

    Uses the raw reciprocals rather than de-margined probabilities: the
    overround affects both sides almost equally, so it cancels in the
    difference, and using the raw observable keeps this independent of any
    margin assumption (same reasoning as market_snapshot.py).
    """
    try:
        oh, oa = float(odds_home), float(odds_away)
    except (TypeError, ValueError):
        return None
    if oh <= 1 or oa <= 1:
        return None
    return abs(1.0 / oh - 1.0 / oa)


def draw_lean(pick: dict) -> dict | None:
    """Score one fixture's draw plausibility, or None if it can't be judged.

    Returns {balance, tier, expected_pct, why} where tier is
    'tight' / 'very_tight'. Fixtures without usable odds return None rather
    than a guess — a draw filter that silently includes unpriced matches would
    be reporting a rate it never measured.
    """
    bal = market_balance(pick.get("odds_home"), pick.get("odds_away"))
    if bal is None or bal > BALANCE_TIGHT:
        return None

    very = bal <= BALANCE_VERY_TIGHT
    return {
        "balance": round(bal, 4),
        "tier": "very_tight" if very else "tight",
        "expected_pct": RATE_VERY_TIGHT if very else RATE_TIGHT,
        "why": ("The market cannot separate these sides"
                + (" at all" if very else "")
                + f" — historically {RATE_VERY_TIGHT if very else RATE_TIGHT}% "
                  f"of such matches end level, against {RATE_BASELINE}% overall."),
    }


def rank_draws(picks: list) -> list:
    """The draw-leaning fixtures, most evenly matched first.

    Deliberately no cut on our model's draw probability. The model has never
    once rated a draw as the most likely outcome (0 of 360 predictions; its
    highest draw probability ever is 35.4%), so ranking by it would order the
    list by a number that barely varies. Where the model IS useful is the
    other direction — when it puts a draw under 20%, the observed rate is
    7.4% — so that is used to EXCLUDE, not to rank.
    """
    out = []
    for p in picks:
        lean = draw_lean(p)
        if not lean:
            continue
        # The one thing the model is demonstrably good at here: ruling draws
        # out. Below 20% stated draw probability the real rate collapses to
        # 7.4%, so those fixtures do not belong in a draws list however even
        # the market rates them.
        dp = p.get("draw_prob")
        if dp is not None and dp < 0.20:
            continue
        out.append({**p, "draw_lean": lean})
    out.sort(key=lambda x: x["draw_lean"]["balance"])
    return out


# The sentence the UI must show. Kept here so the honest framing travels with
# the feature rather than being re-invented (or quietly dropped) per surface.
DISCLOSURE = (
    f"Draws are the hardest market. These are the fixtures the bookmaker "
    f"rates most evenly, where draws historically land about "
    f"{RATE_TIGHT:.0f}-{RATE_VERY_TIGHT:.0f}% of the time versus "
    f"{RATE_BASELINE:.0f}% overall — but the odds already reflect that, so "
    f"this is a filter for finding them, not a claim that they are underpriced."
)
