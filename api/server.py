"""
api/server.py - REST API exposing the PotentialPro engine.

Turns the prediction engine into JSON endpoints any frontend (web app, mobile,
the Telegram bot) can call. Reads from the same SQLite DB the pipeline writes to.

Run:
    uvicorn api.server:app --reload --port 8000

Then open http://localhost:8000/docs for interactive API docs (try every
endpoint in the browser, no frontend needed yet).

Endpoints:
    GET /                      -> health check
    GET /api/picks             -> today's HIGH/MEDIUM model picks
    GET /api/picks/all         -> all predictions incl. LOW
    GET /api/slip?risk=&games= -> build a risk-based slip
    GET /api/consensus         -> cross-source agreement
    GET /api/stats             -> running track record
    GET /api/stats/tiers       -> accuracy + probability band per confidence tier
"""

import os
import sys
from datetime import datetime, timezone
from fastapi import FastAPI, Query, Body
from typing import List
from fastapi.middleware.cors import CORSMiddleware

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from api.cache import cached, not_modified_middleware, stats as cache_stats, ledger_version
from api.security import limits_middleware, cap, MAX_LEGS
from models.markets import (derive_markets, build_slip, accumulator_probability,
                            kickoff_passed, teams_key)
from models.goals_model import expected_goals
# Thresholds only — models/tiers.py is import-free by design so the API can read
# the tier bands without importing models/predict.py (joblib + pandas + sklearn).
from models.tiers import TIER_BANDS
# This API serves FOOTBALL only. `matches.sport` existed from the first commit
# and nothing filtered on it, so a basketball row would have appeared in the
# picks feed and — worse — inside the published accuracy. See models/sports.py.
from models.sports import FOOTBALL, sport_sql
from models.returns import served_returns
from models.settle import settle as settle_leg
from scrapers.booking_codes import attach_sporty_odds, fetch_sportybet_events
from models.leagues import pretty_league
from models.gradeability import overall_coverage
from db.calibration_health import tier_calibration
from models.draws import rank_draws, DISCLOSURE as DRAW_DISCLOSURE
from models.live_scores import attach as attach_live_scores
from models.consensus import (build_consensus, _same_match, _sim,
                              _dates_close, same_teams)
from scrapers.booking_codes import (decode as decode_booking,
                                    generate_code, BookingCodeError,
                                    convert_code, merge_codes, split_code,
                                    _selections_from_legs, _post_selections,
                                    PROVIDERS, SHARE_POST_URLS)

# Every query in this module that reads `matches` appends this. Built once, from
# the whitelist in models/sports.py, so a typo raises at import rather than
# silently filtering nothing.
SPORT_SQL = sport_sql(FOOTBALL)

app = FastAPI(
    title="PotentialPro API",
    description="Sports prediction engine — picks, slips, consensus, stats",
    version="1.0.0",
)

# Allow the future web app (any origin during dev) to call this API
app.add_middleware(
    CORSMiddleware,
    # Our own site (incl. Pages preview deploys) and local dev only. The API
    # has no cookies or private data, so "*" leaked nothing - but it let any
    # site build on our server's CPU and our IP's standing with the
    # bookmakers. The Render origin serves the webapp same-origin (no CORS).
    allow_origin_regex=r"^https://([a-z0-9-]+\.)?potentialpro\.pages\.dev$"
                       r"|^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "If-None-Match"],
)
# ETag -> 304. Registered AFTER CORS, so it wraps it and every 304 it builds
# still carries the CORS headers (see api/cache.py).
app.middleware("http")(not_modified_middleware)
# Size cap + per-IP rate limits (api/security.py). Outermost, so a rejected
# request costs nothing downstream.
app.middleware("http")(limits_middleware)


# ─── Helpers ─────────────────────────────────────────────────────────────────

# A football match cannot still be in play three and a half hours after kickoff.
# 90 minutes + half time + stoppage is ~2h; extra time and penalties push it to
# ~2h45. 3.5h is generous and still far short of "yesterday evening".
LIVE_MAX_HOURS = 3.5


def effective_live_state(row: dict) -> str | None:
    """`live_state` with its expiry applied.

    The column is a SNAPSHOT written by kickoff_times.py at scrape time, not a
    live feed. A match that happened to be in play when the pipeline ran keeps
    live_state='in' FOREVER — until some later run happens to re-scrape it. The
    pipeline runs a couple of times a day, so matches sat on the Picks page
    with a red pulsing LIVE badge more than twelve hours after full time
    (verified 9 Aug 2026: Boca Juniors v Velez, kicked off 10.7h earlier, still
    'in'; nine more like it).

    Chasing this with more scraping would not fix it — no daily pipeline can
    track a 90-minute state. The state itself has to carry an expiry: past
    LIVE_MAX_HOURS after kickoff, 'in' means "finished, we just never saw the
    final whistle", which is 'post'.
    """
    state = row.get("live_state")
    # 'post' is terminal and NULL means unknown — only the states that claim
    # the match has not finished can go stale. That includes 'pre': the
    # snapshot is just as likely to have been taken BEFORE kickoff and never
    # updated (45 such rows on 9 Aug 2026, one of them already graded with a
    # final score while still claiming 'pre').
    if state not in ("in", "pre"):
        return state
    ko = row.get("kickoff_utc")
    if not ko:
        return state          # no kickoff time = can't age it; leave as-is
    try:
        t = datetime.fromisoformat(str(ko).replace("Z", "+00:00"))
    except ValueError:
        return state
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    hours = (datetime.now(timezone.utc) - t).total_seconds() / 3600
    return "post" if hours > LIVE_MAX_HOURS else state

def _model_predictions(only_strong: bool = True, upcoming_only: bool = True) -> list:
    """Latest prediction per match.

    upcoming_only=True (the default) is right for PICKS: never recommend a
    match that has kicked off. The slip CHECKER needs the opposite — a user
    pasting a code wants "we said X, it finished Y", and filtering those out
    made the checker report 0 covered legs on fixtures we had predicted and
    already graded.
    """
    conn = get_connection()
    where = "AND mp.confidence IN ('HIGH','MEDIUM')" if only_strong else ""
    time_filter = """
          WHERE m.match_date >= date('now') {where}
          {sport}
          -- Already-started matches are not recommendable. NULL
          -- kickoff_utc = unknown time, so it stays visible.
          AND (m.kickoff_utc IS NULL OR m.kickoff_utc > strftime('%Y-%m-%d %H:%M','now'))
          -- ESPN's live state catches what a timestamp cannot:
          -- delayed or rescheduled matches, and anything already
          -- in play. NULL = unknown, so it stays visible.
          AND (m.live_state IS NULL OR m.live_state = 'pre')
    """.format(where=where, sport=SPORT_SQL) if upcoming_only \
        else f"          WHERE 1=1 {where} {SPORT_SQL}"
    rows = conn.execute(f"""
        SELECT m.match_id, m.home_team, m.away_team, m.league,
               m.match_date, m.kickoff_time,
               mp.home_win_prob, mp.draw_prob, mp.away_win_prob,
               mp.confidence, mp.best_bet, mp.best_bet_prob, mp.gemma_analysis,
               mp.consensus_adj, m.kickoff_utc, m.live_state,
               mp.book_prob, mp.close_prob, mp.book_source, mp.close_source,
               sp.predicted_home_goals, sp.predicted_away_goals,
               r.home_goals AS result_home_goals,
               r.away_goals AS result_away_goals,
               r.outcome AS result_outcome
        FROM matches m
        JOIN model_predictions mp ON mp.id = (
            SELECT MAX(mp2.id) FROM model_predictions mp2 WHERE mp2.match_id = m.match_id
        )
        LEFT JOIN scraped_predictions sp ON sp.id = (
            SELECT MAX(sp2.id) FROM scraped_predictions sp2
            WHERE sp2.match_id = m.match_id
              AND sp2.predicted_home_goals IS NOT NULL
        )
        LEFT JOIN results r ON r.match_id = m.match_id
        {time_filter}
        ORDER BY mp.best_bet_prob DESC
    """).fetchall()
    conn.close()
    # live_state is a snapshot; age it before it leaves the API.
    return [dict(r, live_state=effective_live_state(dict(r)),
                 league_display=pretty_league(dict(r).get("league")),
                 warnings=price_warnings(dict(r)))
            for r in rows]


# Football's "price moved against" caution (20 Oct 2026). On 838 graded picks
# with both prices, picks whose price drifted 3+ points AGAINST them before
# kickoff won 44.3% (claim 50.5%); 1+ points TOWARD them won 58.2% (claim 51.0%,
# CI clear of it) - the same direction as NHL/NFL (models/line_move_backtest.py).
# A WARNING only, never a changed or pulled pick; /api/stats keeps warned picks
# apart so the live record can confirm or kill it.
PRICE_MOVE_WARN = 0.03


def _moved_against(r: dict) -> float | None:
    """How far the price for OUR pick fell from pick time to the latest
    pre-kickoff observation (probability points). None unless both prices exist
    and came from the same feed - across two books "movement" is disagreement."""
    b, c = r.get("book_prob"), r.get("close_prob")
    if b is None or c is None or (r.get("book_source") or "espn") != (r.get("close_source") or "espn"):
        return None
    return b - c


def price_warnings(r: dict) -> list:
    moved = _moved_against(r)
    if moved is None or moved < PRICE_MOVE_WARN:
        return []
    return [{"type": "price",
             "text": f"Price has moved against this pick since it was made ({moved * 100:.0f} points)",
             "why": "Past picks whose price moved 3+ points against them before kickoff won "
                    "less often than claimed. A caution, not a change to the pick."}]


def _last_generated_at() -> str | None:
    """UTC timestamp of the newest model prediction — when the pipeline last ran."""
    conn = get_connection()
    # Joined to matches for the sport filter: this timestamp is stamped on the
    # football Picks page as "last updated", and a basketball run would have
    # refreshed it without a single football pick having changed.
    row = conn.execute(f"""
        SELECT MAX(mp.predicted_at) AS ts
        FROM model_predictions mp
        JOIN matches m ON m.match_id = mp.match_id
        WHERE 1=1 {SPORT_SQL}
    """).fetchone()
    conn.close()
    return row["ts"] if row else None


def _predictions_with_goals() -> list:
    """Model preds joined with Forebet goal data, for market derivation."""
    conn = get_connection()
    rows = conn.execute(f"""
        SELECT m.match_id, m.home_team, m.away_team, m.league,
               m.match_date, m.kickoff_time,
               -- Carried through so the Builder and Checker can show the same
               -- real kickoff the Picks page does, instead of falling back to
               -- PredictZ's placeholder '00:00'.
               m.kickoff_utc, m.live_state,
               mp.home_win_prob, mp.draw_prob, mp.away_win_prob,
               p.predicted_home_goals, p.predicted_away_goals, p.over_2_5_pct
        FROM matches m
        JOIN model_predictions mp ON mp.id = (
            SELECT MAX(mp2.id) FROM model_predictions mp2 WHERE mp2.match_id = m.match_id
        )
        -- Plain MAX(id): the latest scraped row, whether or not it carries a
        -- predicted scoreline.
        --
        -- The narrowness of this join no longer matters, and the constraint the
        -- previous comment set here has been satisfied. It read:
        --
        --     "Do not 'fix' this join again without first fixing the goals
        --      derivation it feeds. The correct order is: get a real goals
        --      signal (team attack/defence rates, or the bookmaker's own O/U
        --      line), then widen the join."
        --
        -- That was the right call at the time. Over/Under, BTTS and Handicap
        -- were derived from the predicted SCORELINE as if it were an
        -- expected-goals rate, which priced "Under 2.5" at 91-99% with the
        -- bookmaker near even money, so keeping those markets RARE was the
        -- lesser evil (Boss's call, 9 Aug 2026).
        --
        -- The goals signal now exists: models/goals_model.py fits team
        -- attack/defence rates from 98,422 historical matches, and
        -- derive_markets takes its total from there and the split from the 1X2
        -- probabilities. It does not read predicted_home_goals,
        -- predicted_away_goals or over_2_5_pct at all — a modal integer
        -- scoreline consumed as a Poisson rate was the whole bug, and 49% of
        -- them contain a zero. So goals markets are now priced on every fixture
        -- regardless of what this join returns, and they measure inside +5
        -- rather than -22.6.
        --
        -- The three columns below now have NO consumer at all. They were kept
        -- after PR #7 for one reason: the Picks card displayed them as
        -- "Expected: 2 - 1". That display is gone — the card shows the fitted
        -- rate from goals_model instead (_attach_expected_goals), which is the
        -- number its prices are actually built from. These stay only because
        -- they are cheap and this row shape is shared with other endpoints.
        -- Nothing reads them; do not build anything new on them.
        LEFT JOIN scraped_predictions p ON p.id = (
            SELECT MAX(p2.id) FROM scraped_predictions p2 WHERE p2.match_id = m.match_id
        )
        WHERE m.match_date >= date('now')
          {SPORT_SQL}
          AND m.status != 'finished'
          AND m.match_id NOT IN (SELECT match_id FROM results)
          -- Same kickoff guard as /api/picks: a slip must never contain a
          -- match that has already started.
          AND (m.kickoff_utc IS NULL OR m.kickoff_utc > strftime('%Y-%m-%d %H:%M','now'))
          -- ESPN's live state catches what a timestamp cannot:
          -- delayed or rescheduled matches, and anything already
          -- in play. NULL = unknown, so it stays visible.
          AND (m.live_state IS NULL OR m.live_state = 'pre')
        -- Explicit, not incidental: this used to be whatever order SQLite
        -- scanned `matches` in (rowid), and adding an index changed the plan
        -- and with it the order. build_slip's tie-breaks and the Checker's
        -- dedupe both depend on it, so it is pinned to the original order.
        ORDER BY m.id
    """).fetchall()
    conn.close()
    # live_state is a snapshot; age it before it leaves the API.
    return [dict(r, live_state=effective_live_state(dict(r)),
                 league_display=pretty_league(dict(r).get("league")))
            for r in rows]


def _attach_consensus(picks: list) -> list:
    """Add a `consensus` field to each pick: how many sources cover the
    fixture and whether they agree. Same fuzzy team + ±1 day matching as
    everywhere else (exact matching misses cross-source duplicates)."""
    try:
        cons = build_consensus()
    except Exception:
        cons = []
    for p in picks:
        p["consensus"] = None
        for c in cons:
            if _same_match(p, c):
                if len(c["sources"]) >= 2:
                    p["consensus"] = {
                        "sources": len(c["sources"]),
                        "agree": c["agree"],
                        "agreed_outcome": c["agreed_outcome"],
                    }
                break
    return picks


# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/healthz")
def health():
    # Cache counters are here so "is the cache working in production?" is
    # one curl, not a guess. ledger = (mtime_ns, size) of the served file.
    return {"status": "ok", "service": "PotentialPro API",
            "cache": dict(cache_stats), "ledger": ledger_version()}


def _started_today() -> list:
    """Today's HIGH/MEDIUM picks that have already kicked off.

    Returned SEPARATELY from `picks`, never mixed in: these are not bettable and
    must never be presented as recommendations. They exist so the card doesn't
    silently empty out through the day — on a busy Saturday nine 14:00 ties
    vanish at once, which looks like the model has nothing rather than like the
    football has started.
    """
    conn = get_connection()
    rows = conn.execute(f"""
        SELECT m.home_team, m.away_team, m.league, m.match_date,
               -- kickoff_time joins kickoff_utc so the UI can label each row
               -- with its own day ("Yesterday 22:15"): this list spans two
               -- days and every row used to read as though it were today's.
               m.kickoff_utc, m.kickoff_time, m.live_state, m.espn_league,
               mp.confidence, mp.best_bet, mp.best_bet_prob,
               r.home_goals AS result_home_goals,
               r.away_goals AS result_away_goals,
               r.outcome AS result_outcome
        FROM matches m
        JOIN model_predictions mp ON mp.id = (
            SELECT MAX(mp2.id) FROM model_predictions mp2
            WHERE mp2.match_id = m.match_id
        )
        LEFT JOIN results r ON r.match_id = m.match_id
        WHERE mp.confidence IN ('HIGH','MEDIUM')
          {SPORT_SQL}
          AND m.match_date >= date('now','-1 day')
          AND (
            (m.kickoff_utc IS NOT NULL
             AND m.kickoff_utc <= strftime('%Y-%m-%d %H:%M','now'))
            OR m.live_state IN ('in','post')
          )
        ORDER BY m.kickoff_utc DESC
        LIMIT 20
    """).fetchall()
    conn.close()

    out = []
    for r in rows:
        d = dict(r)
        d["live_state"] = effective_live_state(d)
        d["league_display"] = pretty_league(d.get("league"))
        if d.get("result_outcome"):
            winner = {"H": "Home Win", "D": "Draw",
                      "A": "Away Win"}.get(d["result_outcome"])
            d["model_was_right"] = d["best_bet"] == winner
        else:
            d["model_was_right"] = None      # not graded yet — not "wrong"
        out.append(d)

    # Fill the gap between kickoff and the next pipeline run so a started
    # match shows a real score — live or final — instead of "score pending".
    # Never written to the ledger; grading stays with the results scrapers.
    try:
        attach_live_scores(out)
    except Exception:
        # Decoration must never 500 the page. attach() already logs and
        # degrades per-league; this is the belt to that pair of braces.
        pass
    return out


@app.get("/api/picks")
@cached(ttl=30, max_age=30)
def get_picks():
    """Today's HIGH and MEDIUM confidence model picks.

    `picks` are bettable. `started` is today's picks that have already kicked
    off, kept separate so the UI can show them greyed out with results.
    """
    picks = _attach_consensus(_model_predictions(only_strong=True))
    _attach_market_check(picks)
    try:
        _attach_expected_goals(picks)
    except Exception:
        # Same rule as attach_live_scores: decoration must never 500 the page.
        pass
    return {"count": len(picks), "generated_at": _last_generated_at(),
            "picks": picks, "started": _started_today()}


def _attach_expected_goals(picks: list) -> None:
    """Add the FITTED expected-goals rate for each fixture, in place.

    The Picks card used to display `predicted_home_goals`/`predicted_away_goals`
    under the label "Expected: 2 - 1". Those are Forebet's MODAL scoreline — the
    single most likely result — and PR #7 established they are not a rate: 49% of
    them contain a zero, and consuming one as a Poisson mean is what priced
    "Under 2.5" at 91% while the bookmaker sat near even money.

    Every price on that card now comes from goals_model, so displaying the modal
    scoreline beside them invited exactly the inference that was removed from the
    pricing: a card could read "Expected: 2 - 0" above its own BTTS leg priced
    near 50%. This serves the number the prices are actually built from.

    None when either team is unrated — goals_model returns None rather than a
    half-informed guess, and its name matcher is deliberately strict (see its
    docstring: a fuzzy hit must clear 0.90 AND beat the runner-up, so "Real
    Sociedad" does not silently resolve to the CSVs' "Sociedad"). Measured
    coverage: 64% of all 288 served picks in the ledger have both teams rated,
    but only 42% of today's slate — European qualifier nights lean heavily on
    clubs the historical CSVs never cover. The field is omitted entirely in that
    case, so the UI hides the line rather than rendering a blank or a fake zero.
    An unrated fixture is not degraded by this: derive_markets already falls back
    to the flat league total for it, exactly as it did before this display.

    Free: goals_model imports only json/math/os and is already loaded in this
    process by models.markets, which prices every fixture off the same ratings.
    """
    for p in picks:
        try:
            mu = expected_goals(p.get("home_team"), p.get("away_team"))
        except Exception:
            mu = None
        if mu:
            # Two decimals: this is a rate, and rounding it to an integer is how
            # it gets mistaken for a scoreline again.
            p["expected_goals"] = {"home": round(mu[0], 2),
                                   "away": round(mu[1], 2),
                                   "total": round(mu[0] + mu[1], 2)}


_MARKET_CHECK_CACHE: dict = {}


@app.on_event("startup")
def _prewarm_sportybet() -> None:
    """Fetch SportyBet's fixture list in the background as soon as the server
    boots, so the first Picks visitor after a deploy/restart does not wait the
    ~20 s it takes (models are matched against it on every Picks load)."""
    import threading

    def _go():
        # Then run the two heavy endpoints once, so their per-process caches
        # (team-name resolution in goals_model, the market check) are filled
        # before a visitor arrives - not by the first visitor after a deploy.
        for warm in (fetch_sportybet_events, get_picks,
                     lambda: get_slip(risk="MEDIUM", games=5, leagues=None,
                                      exclude=None, pin=None, sports="football,nhl,nfl")):
            try:
                warm()
            except Exception:
                pass
    threading.Thread(target=_go, daemon=True).start()


def _attach_market_check(picks: list) -> None:
    """Add the bookmaker's price for each pick's own selection, in place.

    This exists because of how a real error got caught. The engine was pricing
    "Under 2.5" at 91% while SportyBet paid odds implying about half that. Boss
    spotted it from the betting side — "not only are we picking unders, we are
    picking unders the bookie added high odds to" — which is a check the app
    could have been making for itself all along.

    A bookmaker's line is the closest thing to a market consensus that exists.
    Showing it beside our own number turns every pick into a standing
    comparison: a large gap is either genuine value or a bug in our maths, and
    both are worth seeing. The margin is removed (the two-way overround is
    normalised out) so the figures are comparable rather than flattering.

    Best-effort and never fatal: odds are decoration, and the picks must render
    whether or not SportyBet answers.
    """
    if not picks:
        return
    try:
        # The mapper identifies a 1X2 outcome by looking for the TEAM NAME in
        # the selection text (scrapers/booking_codes._wanted_outcome), which is
        # how build_slip phrases it: "Arsenal Win", not "Home Win". Passing the
        # generic wording matched neither team, fell through to the draw
        # branch, and returned DRAW odds for every pick — making every one look
        # like a 40-point value edge. The comparison caught my own bug on its
        # first run, which is a fair advertisement for having it.
        def _selection(p):
            bb = p.get("best_bet") or ""
            if bb == "Home Win":
                return f"{p['home_team']} Win"
            if bb == "Away Win":
                return f"{p['away_team']} Win"
            return bb                      # "Draw" maps as-is
        legs = [{"home_team": p["home_team"], "away_team": p["away_team"],
                 "match_date": p.get("match_date"), "kickoff_utc": p.get("kickoff_utc"),
                 "market": "Match Result", "selection": _selection(p)}
                for p in picks]
        # Reused for 5 minutes - the same freshness as the SportyBet events
        # cache it reads. Without it every visitor re-ran the full matching.
        import time as _t
        key = tuple((l["home_team"], l["away_team"], l["selection"]) for l in legs)
        hit = _MARKET_CHECK_CACHE.get(key)
        if hit and _t.time() - hit[0] < 300:
            odds = hit[1]
        else:
            odds = attach_sporty_odds(legs)["odds"]
            _MARKET_CHECK_CACHE.clear()
            _MARKET_CHECK_CACHE[key] = (_t.time(), odds)
    except Exception:
        return

    for p, o in zip(picks, odds):
        p["book_odds"] = o
        p["book_probability"] = None
        p["market_gap"] = None
        if not o or o <= 1.0:
            continue
        implied = 1.0 / o
        p["book_probability"] = round(implied, 4)
        ours = p.get("best_bet_prob")
        if ours:
            # Positive gap = we rate it higher than the market does.
            p["market_gap"] = round(ours - implied, 4)


@app.get("/api/picks/all")
@cached(ttl=60, max_age=60)
def get_all_picks():
    """All predictions including LOW confidence."""
    picks = _attach_consensus(_model_predictions(only_strong=False))
    return {"count": len(picks), "generated_at": _last_generated_at(), "picks": picks}


@app.get("/api/picks/draws")
@cached(ttl=60, max_age=60)
def get_draw_picks():
    """Fixtures where a draw is most plausible — a filter, not a tip.

    Needs the bookmaker's HOME and AWAY prices, which the normal picks payload
    does not carry, so it reads them here. Only rows that actually have odds
    can be judged: a draw list that quietly included unpriced fixtures would
    be reporting a hit rate it never measured.

    See models/draws.py for the 90k-match measurement behind the thresholds,
    and for why this ships with a disclosure rather than a confidence tier.
    """
    conn = get_connection()
    rows = conn.execute(f"""
        SELECT m.match_id, m.home_team, m.away_team, m.league, m.match_date,
               m.kickoff_time, m.kickoff_utc, m.live_state,
               mp.home_win_prob, mp.draw_prob, mp.away_win_prob,
               mp.confidence, mp.best_bet, mp.best_bet_prob,
               mp.draw_analysis,
               od.odds_home, od.odds_draw, od.odds_away
        FROM matches m
        JOIN model_predictions mp ON mp.id = (
            SELECT MAX(mp2.id) FROM model_predictions mp2
            WHERE mp2.match_id = m.match_id)
        JOIN scraped_predictions od ON od.id = (
            SELECT MAX(sp2.id) FROM scraped_predictions sp2
            WHERE sp2.match_id = m.match_id AND sp2.odds_home IS NOT NULL)
        WHERE m.match_date >= date('now')
          {SPORT_SQL}
          -- The same three guards /api/picks uses. This list had only a
          -- `live_state != 'post'` post-filter, which is NOT equivalent:
          -- effective_live_state only ages a snapshot once kickoff is
          -- LIVE_MAX_HOURS past, so a match that kicked off 90 minutes ago is
          -- still 'pre'/'in' and sailed through. Verified leaking: NEC v
          -- Olympiacos, kickoff 17:30 UTC, served as a backable draw at 19:30.
          -- A NULL kickoff still passes on purpose — unknown must never be
          -- read as "already played" (kickoff_times.py's rule).
          AND (m.kickoff_utc IS NULL
               OR m.kickoff_utc > strftime('%Y-%m-%d %H:%M','now'))
          AND (m.live_state IS NULL OR m.live_state = 'pre')
          AND m.match_id NOT IN (SELECT match_id FROM results)
    """).fetchall()
    conn.close()

    # `gemma_analysis` carries the DRAW-framed text here, not the 1X2 text.
    # The stored gemma_analysis argues for the model's best bet — a home or
    # away win — so rendering it under a draw suggestion would contradict the
    # screen in our own voice. Deliberately mapped onto the same key so every
    # card renderer shows the right explanation without special-casing, and
    # left empty rather than filled with the wrong one when absent.
    picks = [dict(r, live_state=effective_live_state(dict(r)),
                  gemma_analysis=dict(r).get("draw_analysis"),
                  league_display=pretty_league(dict(r).get("league")))
             for r in rows]
    # Not yet kicked off — a draw you can no longer back is not a pick.
    picks = [p for p in picks if p.get("live_state") != "post"]
    ranked = rank_draws(picks)

    return {
        "count": len(ranked),
        "generated_at": _last_generated_at(),
        "considered": len(picks),
        # Travels with the payload so no consumer can render the list without
        # the caveat. The honest framing is part of the feature.
        "disclosure": DRAW_DISCLOSURE,
        "picks": ranked,
    }


@app.get("/api/slip")
@cached(ttl=60, max_age=60)
def get_slip(
    risk: str = Query("MEDIUM", description="LOW, MEDIUM, or HIGH"),
    games: int = Query(5, ge=1, le=20, description="Number of games"),
    leagues: str = Query(None, description="Comma-separated league names to restrict to"),
    exclude: str = Query(None, description="Fixtures to leave out, as 'Home||Away' pairs separated by ';;'"),
    pin: str = Query(None, description="Banker legs to lock in, as 'Home||Away||Market||Selection' separated by ';;'"),
    sports: str = Query("football", description="Comma-separated: football, nhl, nfl"),
):
    """Build a risk-based betting slip (one bet per match). Started games are
    excluded; each leg carries SportyBet odds when the bookmaker lists it.
    build_slip is deterministic, so re-requesting with one fixture excluded
    keeps the other legs and back-fills the next best option ('swap a leg')."""
    wanted = _parse_sports(sports)
    preds = _predictions_with_goals() if "football" in wanted else []
    all_opts = []
    for p in preds:
        all_opts.extend(derive_markets(p))
    us_codes = [c for c in wanted if c != "football"]
    if us_codes:
        # NHL/NFL options come from the same nightly pricing as their Picks
        # (models/us_sports.py) and go through the SAME build_slip: risk bands,
        # one leg per game, the correlation cap, bankers and swaps all apply.
        from models.us_sports import slip_options
        conn = get_connection()
        try:
            all_opts.extend(slip_options(conn, us_codes))
        finally:
            conn.close()

    league_set = ({l.strip().lower() for l in leagues.split(",") if l.strip()}
                  if leagues else None)
    if league_set is not None:
        # The league chips list FOOTBALL leagues; a chosen sport is its own
        # "league" and must not be filtered out by them.
        # (BetOption.league is "NHL"/"NFL"; build_slip lowercases it.)
        league_set |= set(us_codes)
    exclude_set = None
    if exclude:
        exclude_set = set()
        for pair in exclude.split(";;"):
            if "||" in pair:
                h, a = pair.split("||", 1)
                exclude_set.add(teams_key(h, a))
    pinned = []
    if pin:
        for item in pin.split(";;"):
            parts = item.split("||")
            if len(parts) == 4:
                pinned.append({"home_team": parts[0], "away_team": parts[1],
                               "market": parts[2], "selection": parts[3]})
    def build(extra_excludes, bad_options=None):
        keys = (exclude_set or set()) | extra_excludes
        return build_slip(all_opts, risk.upper(), games, leagues=league_set,
                          exclude_keys=keys or None, pinned=pinned,
                          exclude_options=bad_options or None)

    def sporty_for(slip):
        # `league` routes NHL/NFL legs to their own SportyBet feed and its
        # FULL-GAME market ids (scrapers/booking_codes.US_SPORTS).
        return attach_sporty_odds([
            {"home_team": l.home_team, "away_team": l.away_team, "league": l.league,
             "match_date": l.match_date, "kickoff_utc": l.kickoff_utc,
             "market": l.market, "selection": l.selection} for l in slip])

    # A leg SportyBet won't price whose kickoff we can't verify as future is
    # very likely in play / just finished — swap it for the next best rather
    # than hand the user an unbookable leg. Legs with a known FUTURE kickoff
    # stay even when unpriced (small leagues simply aren't listed). Two
    # passes max; only runs when the odds feed is actually up.
    slip = build(set())
    sporty = sporty_for(slip)
    # UNPRICED LEGS (8 Oct 2026, Boss: "when you build a slip some of them are
    # not getting priced"). A leg SportyBet won't price is one of three things:
    #   - the GAME is listed but not this market/line (a +1.5 it doesn't offer,
    #     a combo it can't book) -> drop that option; the fixture falls back to
    #     its next-best market;
    #   - the GAME is not listed at all -> swap in another fixture, but only if
    #     the slip keeps its length (a shorter slip is worse than one unpriced
    #     leg, and a game SportyBet lacks can still be bet elsewhere);
    #   - a game that has probably started (old rule, kept).
    # Pinned bankers are never touched. Up to 3 passes; only when the feed is up.
    from scrapers.booking_codes import _EventsBySport, leg_sport, map_leg
    events_for = _EventsBySport()
    pinned_keys = {teams_key(p["home_team"], p["away_team"]) for p in pinned}
    suspects, bad_options, unlisted = set(), set(), set()
    for _ in range(3):
        if not any(sporty["odds"]):
            break   # feed down/empty — no signal, keep the slip as-is
        today = datetime.now().strftime("%Y-%m-%d")
        new = False
        for l, o in zip(slip, sporty["odds"]):
            k = teams_key(l.home_team, l.away_team)
            if o is not None or k in pinned_keys:
                continue
            code = leg_sport({"league": l.league})
            probe = {"home_team": l.home_team, "away_team": l.away_team, "league": l.league,
                     "match_date": l.match_date, "kickoff_utc": l.kickoff_utc,
                     "market": "Match Result" if code == "football" else "Moneyline",
                     "selection": f"{l.home_team} Win" if code == "football" else l.home_team}
            if map_leg(probe, events_for):
                opt = (k, l.market, l.selection)
                if opt not in bad_options:
                    bad_options.add(opt)
                    new = True
            elif (l.match_date <= today and (not l.kickoff_time
                  or l.kickoff_time.strip() in ("", "00:00", "0:00"))):
                if k not in suspects:
                    suspects.add(k)
                    new = True
            elif k not in unlisted:
                unlisted.add(k)
                new = True
        if not new:
            break
        candidate = build(suspects | unlisted, bad_options)
        if len(candidate) < len(slip):
            # Swapping unlisted games would shorten the slip: keep them.
            candidate = build(suspects, bad_options)
        slip = candidate
        sporty = sporty_for(slip)
    combined = accumulator_probability(slip)

    # Real kickoffs by match_id, so a slip leg shows the same time the Picks
    # page does rather than PredictZ's placeholder "00:00".
    kickoffs = {p["match_id"]: p.get("kickoff_utc") for p in preds}

    leg_dicts = [
        {
            # Settlement (/api/slip/settle) looks results up BY match_id, and
            # these legs never carried it - so every slip tracked from the
            # Builder sat at "pending" forever (found 2 Oct 2026; Checker legs
            # always had it via /api/fixtures).
            "match_id": leg.match_id,
            "home_team": leg.home_team,
            "away_team": leg.away_team,
            "league": leg.league,
            "league_display": pretty_league(leg.league),
            "match_date": leg.match_date,
            "kickoff_time": leg.kickoff_time,
            "kickoff_utc": kickoffs.get(leg.match_id) or leg.kickoff_utc,
            "sport": _sport_of_league(leg.league),
            "market": leg.market,
            "selection": leg.selection,
            "probability": round(leg.probability, 4),
            "risk": leg.risk,
            "pinned": any(p["home_team"] == leg.home_team
                          and p["away_team"] == leg.away_team
                          and p["market"] == leg.market
                          and p["selection"] == leg.selection for p in pinned),
        }
        for leg in slip
    ]
    for d, o in zip(leg_dicts, sporty["odds"]):
        d["sporty_odds"] = o

    return {
        "risk": risk.upper(),
        "requested_games": games,
        "actual_games": len(slip),
        # None on an empty slip — see accumulator_probability(); never round
        # it into a 1.0 that reads as "certain".
        "accumulator_probability": round(combined, 4) if combined is not None else None,
        "sporty_total_odds": sporty["total_odds"],
        "legs": leg_dicts,
    }


SPORT_CODES = ("football", "nhl", "nfl", "nba")


def _parse_sports(sports: str | None) -> list:
    """'football,nhl' -> ['football', 'nhl']; unknown names are ignored and an
    empty choice falls back to football, so a bad query never returns nothing
    for a reason the user cannot see."""
    got = [s.strip().lower() for s in (sports or "").split(",") if s.strip()]
    got = [s for s in SPORT_CODES if s in got]
    return got or ["football"]


def _sport_of_league(league: str | None) -> str:
    return {"NHL": "nhl", "NFL": "nfl", "NBA": "nba"}.get((league or "").strip(), "football")


@app.get("/api/picks/sports")
@cached(ttl=60, max_age=60)
def get_sport_picks(sports: str = Query("nhl,nfl,nba", description="Comma-separated: nhl, nfl, nba")):
    """Recommended picks for NHL / NFL, football-style (models/us_sports.py):
    one bet per game with the football tiers, the bookmaker's view beside it,
    and each sport's OWN record - never blended into football's.
    Imported lazily so a failure here costs this endpoint, not the API."""
    from models.us_sports import picks as us_picks
    codes = [c for c in _parse_sports(sports) if c != "football"]
    if not codes:
        return {"picks": [], "records": {}, "disclosures": {}}
    conn = get_connection()
    try:
        return us_picks(conn, codes)
    finally:
        conn.close()


@app.get("/api/leagues")
@cached(ttl=300, max_age=120)
def get_leagues():
    """Distinct leagues among upcoming (not yet kicked off) predicted matches —
    feeds the league filter on the slip builder."""
    preds = _predictions_with_goals()
    seen = {}
    for p in preds:
        if kickoff_passed(p["match_date"], p["kickoff_time"]):
            continue
        seen[p["league"]] = seen.get(p["league"], 0) + 1
    return {"leagues": [{"name": k, "upcoming_matches": v}
                        for k, v in sorted(seen.items())]}


@app.get("/api/consensus")
@cached(ttl=300, max_age=120)
def get_consensus():
    """Cross-source agreement between the live prediction sources.

    Ships a `diagnosis` alongside the picks. An empty consensus page has
    several very different causes -- one source suspended, one source writing
    rows with no percentages, or genuinely no overlap -- and they need
    different responses. Rendering the same "no consensus right now" for all
    of them hid API-Football's account suspension for three days.
    """
    consensus = build_consensus()
    agreed = [c for c in consensus if c["agree"]]
    multi = [c for c in consensus if len(c["sources"]) >= 2]

    live = sorted({s for c in consensus for s in c["sources"]})
    # Diagnosis text is shown to users verbatim: name sources, not slugs.
    label = {"espn": "ESPN", "predictz": "PredictZ", "forebet": "Forebet",
             "apifootball": "API-Football", "clubelo": "ClubElo"}
    named = [label.get(s, s) for s in live]
    if len(live) < 2:
        diagnosis = ("Only one source is currently publishing usable "
                     "percentages" + (f" ({named[0]})" if live else "") +
                     ". Consensus needs at least two to compare.")
    elif not multi:
        diagnosis = (f"{len(live)} sources are live ({', '.join(named)}) but "
                     "they cover different fixtures, so none can be "
                     "cross-checked yet.")
    elif not agreed:
        diagnosis = (f"{len(multi)} {'fixture has' if len(multi) == 1 else 'fixtures have'} two or more sources, but "
                     "the sources disagree on every one.")
    else:
        diagnosis = None

    return {
        "total_fixtures": len(consensus),
        "covered_by_multiple": len(multi),
        "agreed_count": len(agreed),
        "sources": live,
        "diagnosis": diagnosis,
        "agreed_picks": agreed,
    }


@app.get("/api/stats")
@cached(ttl=600, max_age=120)
def get_stats():
    """Overall running track record (HIGH+MEDIUM scored picks)."""
    conn = get_connection()
    # The sport join is not decoration on these four queries: this is the
    # published accuracy, and a second sport averaged into it is the one thing
    # in models/sports.py that cannot be undone by deleting rows later.
    row = conn.execute(f"""
        SELECT COUNT(*) AS total,
               SUM(CASE
                   WHEN (mp.best_bet='Home Win' AND r.outcome='H')
                     OR (mp.best_bet='Draw'     AND r.outcome='D')
                     OR (mp.best_bet='Away Win' AND r.outcome='A')
                   THEN 1 ELSE 0 END) AS correct
        FROM model_predictions mp
        JOIN results r ON r.match_id = mp.match_id
        JOIN matches m ON m.match_id = mp.match_id
        WHERE mp.confidence IN ('HIGH','MEDIUM')
          {SPORT_SQL}
          AND mp.id = (SELECT MAX(mp2.id) FROM model_predictions mp2 WHERE mp2.match_id = mp.match_id)
    """).fetchone()
    # The same figures across EVERY tier, including LOW. The endpoint has
    # always returned HIGH+MEDIUM while calling itself "overall", and the web
    # app printed it under the words "Overall Hit Rate" directly above a tier
    # table summing to 191 picks. Excluding LOW is right — we never serve
    # those as bets — but the number must not claim to be something it isn't,
    # and the difference is not cosmetic: 60.0% served vs 57.1% all-in.
    all_row = conn.execute(f"""
        SELECT COUNT(*) AS total,
               SUM(CASE
                   WHEN (mp.best_bet='Home Win' AND r.outcome='H')
                     OR (mp.best_bet='Draw'     AND r.outcome='D')
                     OR (mp.best_bet='Away Win' AND r.outcome='A')
                   THEN 1 ELSE 0 END) AS correct,
               SUM(CASE WHEN r.outcome='D' THEN 1 ELSE 0 END) AS draws
        FROM model_predictions mp
        JOIN results r ON r.match_id = mp.match_id
        JOIN matches m ON m.match_id = mp.match_id
        WHERE mp.id = (SELECT MAX(mp2.id) FROM model_predictions mp2
                       WHERE mp2.match_id = mp.match_id)
          {SPORT_SQL}
    """).fetchone()
    # Grading coverage — the error bar on every number below it. A hit rate is
    # a claim about the picks we could check, and the ones we couldn't are not
    # a random sample (bug #8: the missing half was mostly losses). Publishing
    # the accuracy without the coverage is how that went unnoticed for weeks.
    coverage = overall_coverage(conn)
    # Does a stated probability mean what it says? Per served tier: the mean
    # probability the model stated, how often those picks won, and the 95%
    # interval. Computed by db/calibration_health.py - the same function the
    # command-line report uses - so the Record page and the script agree.
    calibration = tier_calibration(conn)
    # Would following the served picks have made money, at the price stored
    # when each was made? Published beside the hit rate (models/returns.py).
    returns = served_returns(conn)
    # Served graded picks that carried the price-moved warning, kept apart.
    warned = [0, 0]
    for r in conn.execute(f"""
        SELECT mp.best_bet, mp.book_prob, mp.close_prob, mp.book_source, mp.close_source,
               r.outcome
        FROM model_predictions mp
        JOIN results r ON r.match_id = mp.match_id
        JOIN matches m ON m.match_id = mp.match_id
        WHERE mp.id = (SELECT MAX(mp2.id) FROM model_predictions mp2
                       WHERE mp2.match_id = mp.match_id)
          AND mp.confidence IN ('HIGH','MEDIUM') {SPORT_SQL}
    """).fetchall():
        if price_warnings(dict(r)):
            warned[1] += 1
            warned[0] += {"Home Win": "H", "Away Win": "A", "Draw": "D"}.get(r["best_bet"]) == r["outcome"]
    conn.close()

    total = row["total"] or 0
    correct = row["correct"] or 0
    all_total = all_row["total"] or 0
    all_correct = all_row["correct"] or 0
    draws = all_row["draws"] or 0

    return {
        # Unchanged keys so existing clients keep working. These are the
        # SERVED picks (HIGH+MEDIUM) — the ones actually published as bets.
        "scored_picks": total,
        "correct": correct,
        "accuracy": round(correct / total * 100, 1) if total else None,
        "basis": "HIGH+MEDIUM (picks we actually serve)",
        # Everything we have ever predicted, LOW included.
        "all_tiers": {
            "scored_picks": all_total,
            "correct": all_correct,
            "accuracy": round(all_correct / all_total * 100, 1) if all_total else None,
        },
        # The model has never once chosen 'Draw' as its best bet (0 of 360
        # predictions), so every drawn match is an automatic loss and the
        # ledger's maximum achievable accuracy is the non-draw share. Without
        # this, 60% reads as mediocre when it is 79% of what is reachable.
        "draws": draws,
        "draw_pct": round(draws / all_total * 100, 1) if all_total else None,
        "ceiling_pct": round((all_total - draws) / all_total * 100, 1) if all_total else None,
        # {eligible, graded, pct, ungraded, untracked_leagues, untracked_picks}
        "coverage": coverage,
        # [{tier, n, won, actual, claim, lo, hi, verdict}] for HIGH and MEDIUM
        "calibration": calibration,
        # {graded, priced, unpriced, won, hit_pct, avg_odds, breakeven_pct,
        #  roi_pct, roi_lo, roi_hi, verdict: profit|loss|break_even}
        "returns": returns,
        # Served picks shown with the price-moved warning: {won, graded}
        "price_warned": {"won": warned[0], "graded": warned[1]},
    }


@app.get("/api/stats/trend")
@cached(ttl=600, max_age=120)
def get_stats_trend(weeks: int = Query(8, ge=2, le=26)):
    """Hit rate week by week, so the record shows a DIRECTION not just a total.

    A single lifetime percentage hides everything that matters: whether
    calibration is improving, whether a model change helped, whether a good
    month is carrying a bad one. 57% overall means something different if last
    week was 40% than if last week was 70%.

    Bucketed by the match date rather than when the result was recorded — the
    question is how the model did on that week's football, not how quickly the
    scraper caught up with it.
    """
    conn = get_connection()
    rows = conn.execute(f"""
        SELECT strftime('%Y-%W', m.match_date) AS wk,
               MIN(m.match_date) AS starting,
               COUNT(*) AS total,
               SUM(CASE
                   WHEN (mp.best_bet='Home Win' AND r.outcome='H')
                     OR (mp.best_bet='Draw'     AND r.outcome='D')
                     OR (mp.best_bet='Away Win' AND r.outcome='A')
                   THEN 1 ELSE 0 END) AS correct
        FROM model_predictions mp
        JOIN results r ON r.match_id = mp.match_id
        JOIN matches m ON m.match_id = mp.match_id
        WHERE mp.id = (SELECT MAX(mp2.id) FROM model_predictions mp2
                       WHERE mp2.match_id = mp.match_id)
          {SPORT_SQL}
        GROUP BY wk
        ORDER BY wk
    """).fetchall()
    conn.close()

    # The week containing today is still being played: a 36-pick Monday-to-
    # Saturday slice at 33% is not a collapse, and the page must say so rather
    # than draw it like a finished week. Python's %W numbers weeks exactly as
    # SQLite's strftime('%W') does (Monday-first), so the keys compare.
    current_week = datetime.now(timezone.utc).strftime("%Y-%W")
    out = []
    for r in rows:
        total = r["total"] or 0
        if not total:
            continue
        out.append({
            "in_progress": r["wk"] == current_week,
            "week": r["wk"],
            "starting": r["starting"],
            "total": total,
            "correct": r["correct"] or 0,
            "accuracy": round((r["correct"] or 0) / total * 100, 1),
        })
    # Trailing `weeks` only: the early weeks ran on a different model version
    # and a much thinner ledger, so showing them as part of a trend line
    # invites reading noise as movement.
    return {"weeks": out[-weeks:], "count": len(out[-weeks:])}


@app.get("/api/stats/tiers")
@cached(ttl=600, max_age=120)
def get_stats_by_tier():
    """Accuracy broken down by confidence tier, plus each tier's PROBABILITY BAND.

    The bands come from models/tiers.py — the same two floats predict.py uses to
    assign them — because record.html previously hardcoded them as prose
    ("MEDIUM — 55–65%") and went stale the day TIER_MEDIUM moved to 0.60. Serving
    them means the page can never disagree with the engine again.

    `max_prob` is null for HIGH: the top band IS the cumulative tail. `min_prob`
    is null for LOW. Every tier is present even with no graded picks yet, so the
    UI can always state the bands.
    """
    conn = get_connection()
    # `matches` is joined solely to reach m.sport — this query never needed the
    # table otherwise. An INNER join is correct rather than incidental: a
    # prediction with no matches row has no sport, and an unattributable pick
    # does not belong in a published tier percentage.
    rows = conn.execute(f"""
        SELECT mp.confidence,
               COUNT(*) AS total,
               SUM(CASE
                   WHEN (mp.best_bet='Home Win' AND r.outcome='H')
                     OR (mp.best_bet='Draw'     AND r.outcome='D')
                     OR (mp.best_bet='Away Win' AND r.outcome='A')
                   THEN 1 ELSE 0 END) AS correct
        FROM model_predictions mp
        JOIN results r ON r.match_id = mp.match_id
        JOIN matches m ON m.match_id = mp.match_id
        WHERE mp.id = (SELECT MAX(mp2.id) FROM model_predictions mp2 WHERE mp2.match_id = mp.match_id)
          {SPORT_SQL}
        GROUP BY mp.confidence
    """).fetchall()
    conn.close()
    tiers = {name: {"total": 0, "correct": 0, "accuracy": None,
                    "min_prob": band["min_prob"], "max_prob": band["max_prob"]}
             for name, band in TIER_BANDS.items()}
    for r in rows:
        d = dict(r)
        total = d["total"] or 0
        correct = d["correct"] or 0
        # setdefault, not assignment: an unrecognised confidence value in the
        # ledger (an old model version, a typo) must not lose its counts.
        tiers.setdefault(d["confidence"], {"min_prob": None, "max_prob": None})
        tiers[d["confidence"]].update({
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total * 100, 1) if total else None,
        })
    return tiers


@app.get("/api/stats/picks")
@cached(ttl=600, max_age=120)
def get_scored_picks(tier: str = Query(None, description="HIGH, MEDIUM, or LOW; omit for all")):
    """Individual scored picks (for drill-down). Most recent first."""
    conn = get_connection()
    where = ""
    params = []
    if tier:
        where = "AND mp.confidence = ?"
        params.append(tier.upper())
    rows = conn.execute(f"""
        SELECT m.home_team, m.away_team, m.league, m.match_date,
               mp.confidence, mp.best_bet, mp.best_bet_prob,
               r.home_goals, r.away_goals, r.outcome,
               CASE
                   WHEN (mp.best_bet='Home Win' AND r.outcome='H')
                     OR (mp.best_bet='Draw'     AND r.outcome='D')
                     OR (mp.best_bet='Away Win' AND r.outcome='A')
                   THEN 1 ELSE 0 END AS won
        FROM model_predictions mp
        JOIN results r ON r.match_id = mp.match_id
        JOIN matches m ON m.match_id = mp.match_id
        WHERE mp.id = (SELECT MAX(mp2.id) FROM model_predictions mp2 WHERE mp2.match_id = mp.match_id)
          {SPORT_SQL}
          {where}
        ORDER BY m.match_date DESC, mp.id DESC
    """, params).fetchall()
    conn.close()
    return {"tier": tier.upper() if tier else "ALL", "count": len(rows),
            "picks": [dict(r) for r in rows]}

@app.get("/api/fixtures")
@cached(ttl=60, max_age=60)
def get_fixtures():
    """All upcoming fixtures with their available markets, for the
    slip-checker UI to let the user hand-pick legs."""
    preds = _predictions_with_goals()
    fixtures = []
    seen = set()
    for p in preds:
        # dedupe by teams+date so the picker shows each fixture once
        key = "_".join(sorted([p["home_team"].lower().strip(),
                               p["away_team"].lower().strip()])) + "|" + str(p["match_date"])
        if key in seen:
            continue
        seen.add(key)
        opts = derive_markets(p)
        fixtures.append({
            "match_id": p["match_id"],
            "home_team": p["home_team"],
            "away_team": p["away_team"],
            "league": p["league"],
            "league_display": pretty_league(p["league"]),
            "match_date": p["match_date"],
            "kickoff_time": p["kickoff_time"],
            "kickoff_utc": p.get("kickoff_utc"),
            "live_state": effective_live_state(p),
            "markets": [
                {"market": o.market, "selection": o.selection,
                 "probability": round(o.probability, 4), "risk": o.risk}
                for o in sorted(opts, key=lambda x: x.probability, reverse=True)
            ],
        })
    return {"count": len(fixtures), "fixtures": fixtures}


@app.post("/api/results/lookup")
def results_lookup(match_ids: List[str] = Body(..., embed=True)):
    """Final scores for a list of matches - grades a user's "My Record".

    Tracked picks live in Supabase (per-user, never in the committed ledger),
    but the RESULTS they are graded against must be the same ones the public
    track record uses, so the page asks here instead of scoring anything
    itself. Public data only: final scores, no predictions, no user data.
    """
    ids = [str(m) for m in match_ids][:300]
    if not ids:
        return {"results": {}}
    conn = get_connection()
    rows = conn.execute(f"""
        SELECT match_id, home_goals, away_goals, outcome FROM results
        WHERE match_id IN ({",".join("?" * len(ids))})
    """, ids).fetchall()
    conn.close()
    return {"results": {r["match_id"]: {"home_goals": r["home_goals"], "away_goals": r["away_goals"],
                                        "outcome": r["outcome"]} for r in rows}}


@app.post("/api/slip/settle")
def slip_settle(legs: List[dict] = Body(..., embed=True)):
    cap(legs, MAX_LEGS, "legs")
    """Settle the legs of accumulators a user tracks in My Record.

    Each leg: {match_id, market, selection, home_team, away_team} - exactly
    what the Builder and Checker store. Graded against the SAME `results`
    rows as the public record (see /api/results/lookup); models/settle.py
    owns the rules. status: won | lost | void (Draw No Bet on a draw) |
    pending (no final score yet) | ungradable (a market we can't settle -
    never guessed).
    """
    legs = legs[:300]
    ids = sorted({str(l.get("match_id")) for l in legs if l.get("match_id")})
    scores = {}
    if ids:
        conn = get_connection()
        rows = conn.execute(f"""
            SELECT match_id, home_goals, away_goals, reg_home_goals, reg_away_goals
            FROM results WHERE match_id IN ({",".join("?" * len(ids))})
        """, ids).fetchall()
        conn.close()
        scores = {r["match_id"]: (r["home_goals"], r["away_goals"]) for r in rows}
        regs = {r["match_id"]: (r["reg_home_goals"], r["reg_away_goals"]) for r in rows}
    out = []
    for l in legs:
        sc = scores.get(str(l.get("match_id")))
        if not sc or sc[0] is None or sc[1] is None:
            out.append({"status": "pending"})
            continue
        if _sport_of_league(l.get("league")) != "football":
            # NHL/NFL: the same grading as each sport's public record
            # (models/us_sports.settle_leg), incl. overtime; 60-minute legs
            # use the regulation score.
            from models.us_sports import settle_leg as settle_us
            rh, ra = regs.get(str(l.get("match_id")), (None, None))
            st = settle_us(l.get("league"), l.get("market"), l.get("selection"),
                           l.get("home_team") or "", l.get("away_team") or "",
                           sc[0], sc[1], rh, ra)
        else:
            st = settle_leg(l.get("market"), l.get("selection"),
                            l.get("home_team") or "", l.get("away_team") or "", sc[0], sc[1])
        out.append({"status": st or "ungradable", "home_goals": sc[0], "away_goals": sc[1]})
    return {"legs": out}


@app.post("/api/slip/odds")
def slip_odds(legs: List[dict] = Body(..., embed=True)):
    cap(legs, MAX_LEGS, "legs")
    """SportyBet prices for a hand-built slip (the Checker's legs carry none),
    so a tracked accumulator records the price at the moment it was tracked.
    Best effort: a leg SportyBet doesn't list comes back null."""
    return attach_sporty_odds([{"home_team": l.get("home_team"), "away_team": l.get("away_team"),
                                "market": l.get("market"), "selection": l.get("selection")}
                               for l in legs[:30]])


@app.post("/api/slip/check")
def check_slip(legs: List[dict] = Body(..., embed=True)):
    cap(legs, MAX_LEGS, "legs")
    """Score a user-assembled slip.
    Each leg: {match_id, selection}. Returns each leg's probability +
    the combined accumulator probability. Legs we can't find are flagged."""
    preds = {p["match_id"]: p for p in _predictions_with_goals()}
    scored = []
    combined = 1.0
    scorable = 0

    for leg in legs:
        mid = leg.get("match_id")
        selection = leg.get("selection")
        p = preds.get(mid)
        if not p:
            scored.append({**leg, "found": False,
                           "note": "No prediction available for this match."})
            continue
        opts = {o.selection: o for o in derive_markets(p)}
        opt = opts.get(selection)
        if not opt:
            scored.append({**leg, "found": False,
                           "note": "That market isn't available for this match."})
            continue
        scored.append({
            "match_id": mid,
            "home_team": p["home_team"], "away_team": p["away_team"],
            "league": p["league"],
            "league_display": pretty_league(p["league"]),
            "match_date": p["match_date"],
            "market": opt.market, "selection": opt.selection,
            "probability": round(opt.probability, 4), "risk": opt.risk,
            "found": True,
        })
        combined *= opt.probability
        scorable += 1

    return {
        "legs": scored,
        "scorable_legs": scorable,
        "total_legs": len(legs),
        "accumulator_probability": round(combined, 4) if scorable else 0.0,
    }


@app.post("/api/booking/decode")
def booking_decode(provider: str = Body("sportybet"), code: str = Body(...)):
    """Decode a bookmaker booking code (SportyBet for now) into its legs,
    and attach our model's verdict for any fixture we cover (fuzzy team
    match + ±1 day window, like everywhere else)."""
    try:
        legs = decode_booking(provider, code)
    except BookingCodeError as e:
        return {"ok": False, "error": str(e)}

    # upcoming_only=False: a checker's whole value is judging slips whose
    # games may already have kicked off or finished.
    ours = _model_predictions(only_strong=False, upcoming_only=False)
    goals_by_id = {p["match_id"]: p for p in _predictions_with_goals()}
    covered = scorable = 0
    combined = 1.0
    for leg in legs:
        leg["our_take"] = None
        leg["our_probability"] = None
        for p in ours:
            if not _dates_close(leg.get("match_date") or "", p["match_date"]):
                continue
            # same_teams handles how bookmakers name clubs: a flat 0.75 on raw
            # similarity rejected "Club Puebla" vs "Puebla" (0.71) and
            # "Club Tijuana de Caliente" vs "Club Tijuana" (0.67), which is
            # why the checker said it covered none of a user's games.
            if same_teams(leg.get("home_team") or "", leg.get("away_team") or "",
                          p["home_team"], p["away_team"]):
                leg["our_take"] = {
                    "best_bet": p["best_bet"],
                    "confidence": p["confidence"],
                    "home_win_prob": round(p["home_win_prob"], 3),
                    "draw_prob": round(p["draw_prob"], 3),
                    "away_win_prob": round(p["away_win_prob"], 3),
                }
                # Finished matches: hand back the score so the checker can say
                # "we said X, it finished Y" rather than staying silent.
                if p.get("result_outcome"):
                    winning_bet = {"H": "Home Win", "D": "Draw",
                                   "A": "Away Win"}.get(p["result_outcome"])
                    leg["result"] = {
                        "home_goals": p.get("result_home_goals"),
                        "away_goals": p.get("result_away_goals"),
                        "outcome": p.get("result_outcome"),
                        "model_was_right": p["best_bet"] == winning_bet,
                    }
                covered += 1
                # Score the bookmaker's actual selection against our markets:
                # fuzzy-match their market+selection text to our derived options
                gp = goals_by_id.get(p["match_id"])
                if gp:
                    target = f"{leg.get('market') or ''} {leg.get('selection') or ''}"
                    best_opt, best_score = None, 0.55   # floor: below this, no match
                    for o in derive_markets(gp):
                        s = max(_sim(target, f"{o.market} {o.selection}"),
                                _sim(leg.get("selection") or "", o.selection))
                        if s > best_score:
                            best_opt, best_score = o, s
                    if best_opt:
                        leg["our_probability"] = round(best_opt.probability, 4)
                        leg["matched_selection"] = best_opt.selection
                        combined *= best_opt.probability
                        scorable += 1
                break

    return {"ok": True, "provider": provider, "code": code.strip().upper(),
            "legs": legs, "total_legs": len(legs), "covered_legs": covered,
            "scorable_legs": scorable,
            "accumulator_probability": round(combined, 4) if scorable else None}


@app.post("/api/booking/split")
def booking_split(provider: str = Body(...), code: str = Body(...),
                  parts: int = Body(2), to_provider: str = Body(None)):
    """One code -> several smaller codes.

    A 10-leg acca needs all ten; two 5-leg codes can pay on one half. This is
    the only slip tool that improves the expected outcome without touching a
    single selection.
    """
    try:
        return {"ok": True, **split_code(provider, code, parts, to_provider)}
    except BookingCodeError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/booking/optimize")
def booking_optimize(provider: str = Body(...), code: str = Body(...),
                     min_probability: float = Body(0.5),
                     to_provider: str = Body(None)):
    """Drop the legs OUR model rates below `min_probability`, rebuild the code.

    This is the one slip tool that is not just plumbing. Merging, splitting and
    converting are mechanical — any site can do them. Deciding WHICH leg is
    dragging a slip down needs a model and a track record to justify trusting
    it, which is the thing this project actually has.

    Deliberate design choices:
      * Legs we have NO view on are KEPT, never dropped. "We don't cover this
        fixture" is not evidence against the bet, and silently deleting a
        user's selection because we are ignorant of it would be the worst
        possible behaviour.
      * Every drop is returned with the probability that caused it, so the user
        can see the reasoning rather than trusting a black box.
      * The original code is never modified — a new one is created.
    """
    decoded = booking_decode(provider=provider, code=code)
    if not decoded.get("ok"):
        return decoded

    legs = decoded["legs"]
    keep, dropped = [], []
    for leg in legs:
        p = leg.get("our_probability")
        if p is not None and p < min_probability:
            dropped.append({"leg": leg, "our_probability": p,
                            "reason": f"model rates this {round(p * 100)}%, "
                                      f"below the {round(min_probability * 100)}% floor"})
        else:
            keep.append(leg)

    if not keep:
        return {"ok": False,
                "error": "Every leg fell below the floor — nothing left to book. "
                         "Lower the threshold or rebuild the slip."}
    if not dropped:
        return {"ok": True, "unchanged": True, "code": decoded["code"],
                "kept": keep, "dropped": [],
                "message": "Nothing to trim — no leg is below the floor."}

    target = (to_provider or provider or "").lower().strip()
    try:
        sels, unmappable = _selections_from_legs(keep, provider)
        if not sels:
            return {"ok": False,
                    "error": "The kept legs can't be rebuilt on this bookmaker."}
        new_code = _post_selections(sels, target)
    except BookingCodeError as e:
        return {"ok": False, "error": str(e)}

    return {"ok": True, "unchanged": False, "code": new_code,
            "from_code": decoded["code"], "kept": keep, "dropped": dropped,
            "kept_legs": len(sels), "dropped_legs": len(dropped),
            "provider": target}


@app.get("/api/booking/providers")
def booking_providers():
    """Which bookmakers we can read codes from, and create codes on."""
    # Bet9ja generates through its own builder rather than SHARE_POST_URLS
    # (different id system entirely), so it is added explicitly.
    return {"decode": sorted(PROVIDERS),
            "generate": sorted(set(SHARE_POST_URLS) | {"bet9ja"})}


@app.post("/api/booking/convert")
def booking_convert(from_provider: str = Body(...), code: str = Body(...),
                    to_provider: str = Body(...)):
    """Rebuild a booking code on another bookmaker.

    Exact within the SportyBet platform (SportyBet, football.com, MSport):
    those share Betradar event/market/outcome ids, so the converted slip is the
    same bets rather than a name-matched approximation.
    """
    try:
        return {"ok": True, **convert_code(from_provider, code, to_provider)}
    except BookingCodeError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/booking/merge")
def booking_merge(sources: List[dict] = Body(...),
                  to_provider: str = Body("sportybet")):
    """Combine several booking codes into one slip.

    sources: [{"provider": "sportybet", "code": "ABC123"}, ...]
    Duplicate fixtures are dropped and reported — two slips backing different
    outcomes of the same match cannot both win.
    """
    cap(sources, 10, "codes")
    try:
        return {"ok": True, **merge_codes(sources, to_provider)}
    except BookingCodeError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/booking/generate")
def booking_generate(legs: List[dict] = Body(..., embed=True),
                     provider: str = Body("sportybet")):
    """Turn slip legs into a real booking code on any SHARE_POST_URLS
    bookmaker (SportyBet, MSport, football.com — one shared Betradar mapping).
    Each leg: {home_team, away_team, market, selection}. Legs the bookmaker
    doesn't list (small leagues, started games) come back as `unmapped`."""
    cap(legs, MAX_LEGS, "legs")
    try:
        res = generate_code(legs, provider)
    except BookingCodeError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "provider": provider, **res}


@app.get("/api/preview/nhl")
@cached(ttl=120, max_age=60)
def preview_nhl():
    """The NHL page (webapp/hockey.html; public since 2 Oct 2026): upcoming games with
    every standard hockey market priced beside the bookmaker's, the facts-only
    analysis, and the preview's own separate record. Never part of /api/picks
    or /api/stats - see models/nhl_preview.py.

    Imported here, not at the top: an import error in the preview must cost
    this one endpoint, never the football API (bug #15's failure mode).
    """
    from models.nhl_preview import build
    conn = get_connection()
    try:
        return build(conn)
    finally:
        conn.close()

# ─── Serve the PWA from the API (single-origin deploy) ────────────────────────
# Mounting the web app here means ONE tunnel/host exposes both the site and the
# API at the same origin: config.js can stay blank (api.js falls back to the
# page's own origin), so a changing quick-tunnel URL needs no redeploy. Mounted
# last so every /api/... route above still takes precedence. html=True serves
# index.html at / and handles the other .html pages.
from fastapi.staticfiles import StaticFiles  # noqa: E402

_WEBAPP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webapp")
if os.path.isdir(_WEBAPP_DIR):
    app.mount("/", StaticFiles(directory=_WEBAPP_DIR, html=True), name="webapp")
