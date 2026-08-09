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
    GET /api/stats/tiers       -> accuracy broken down by confidence tier
"""

import os
import sys
from datetime import datetime, timezone
from fastapi import FastAPI, Query, Body
from typing import List
from fastapi.middleware.cors import CORSMiddleware

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from models.markets import (derive_markets, build_slip, accumulator_probability,
                            kickoff_passed, teams_key)
from scrapers.booking_codes import attach_sporty_odds
from models.leagues import pretty_league
from models.live_scores import attach as attach_live_scores
from models.consensus import (build_consensus, _same_match, _sim,
                              _dates_close, same_teams)
from scrapers.booking_codes import (decode as decode_booking,
                                    generate_code, BookingCodeError,
                                    convert_code, merge_codes,
                                    PROVIDERS, SHARE_POST_URLS)

app = FastAPI(
    title="PotentialPro API",
    description="Sports prediction engine — picks, slips, consensus, stats",
    version="1.0.0",
)

# Allow the future web app (any origin during dev) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten to your domain before going live
    allow_methods=["*"],
    allow_headers=["*"],
)


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
          -- Already-started matches are not recommendable. NULL
          -- kickoff_utc = unknown time, so it stays visible.
          AND (m.kickoff_utc IS NULL OR m.kickoff_utc > strftime('%Y-%m-%d %H:%M','now'))
          -- ESPN's live state catches what a timestamp cannot:
          -- delayed or rescheduled matches, and anything already
          -- in play. NULL = unknown, so it stays visible.
          AND (m.live_state IS NULL OR m.live_state = 'pre')
    """.format(where=where) if upcoming_only else f"          WHERE 1=1 {where}"
    rows = conn.execute(f"""
        SELECT m.match_id, m.home_team, m.away_team, m.league,
               m.match_date, m.kickoff_time,
               mp.home_win_prob, mp.draw_prob, mp.away_win_prob,
               mp.confidence, mp.best_bet, mp.best_bet_prob, mp.gemma_analysis,
               mp.consensus_adj, m.kickoff_utc, m.live_state,
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
                 league_display=pretty_league(dict(r).get("league")))
            for r in rows]


def _last_generated_at() -> str | None:
    """UTC timestamp of the newest model prediction — when the pipeline last ran."""
    conn = get_connection()
    row = conn.execute("SELECT MAX(predicted_at) AS ts FROM model_predictions").fetchone()
    conn.close()
    return row["ts"] if row else None


def _predictions_with_goals() -> list:
    """Model preds joined with Forebet goal data, for market derivation."""
    conn = get_connection()
    rows = conn.execute("""
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
        -- "Latest row that HAS goals", not "latest row". Every goals-based
        -- market — Over/Under, BTTS, Handicap, Result & Goals — is derived
        -- from the Poisson score matrix, which needs predicted_home/away_goals.
        -- Taking the plain MAX(id) meant the newest row won even when it had
        -- NULL goals, and the newest row is usually the ESPN odds row that
        -- kickoff_times writes (goals NULL by design). Result: 181 of 187
        -- upcoming fixtures HAD goals data, yet the Builder derived Over/Under
        -- for exactly one of them, so those markets had all but disappeared.
        -- Identical mistake to the consensus NULL-pct bug (#11): a newer empty
        -- row silently deleting an older real one.
        LEFT JOIN scraped_predictions p ON p.id = (
            SELECT MAX(p2.id) FROM scraped_predictions p2
            WHERE p2.match_id = m.match_id
              AND p2.predicted_home_goals IS NOT NULL
        )
        WHERE m.match_date >= date('now')
          AND m.status != 'finished'
          AND m.match_id NOT IN (SELECT match_id FROM results)
          -- Same kickoff guard as /api/picks: a slip must never contain a
          -- match that has already started.
          AND (m.kickoff_utc IS NULL OR m.kickoff_utc > strftime('%Y-%m-%d %H:%M','now'))
          -- ESPN's live state catches what a timestamp cannot:
          -- delayed or rescheduled matches, and anything already
          -- in play. NULL = unknown, so it stays visible.
          AND (m.live_state IS NULL OR m.live_state = 'pre')
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
    return {"status": "ok", "service": "PotentialPro API"}


def _started_today() -> list:
    """Today's HIGH/MEDIUM picks that have already kicked off.

    Returned SEPARATELY from `picks`, never mixed in: these are not bettable and
    must never be presented as recommendations. They exist so the card doesn't
    silently empty out through the day — on a busy Saturday nine 14:00 ties
    vanish at once, which looks like the model has nothing rather than like the
    football has started.
    """
    conn = get_connection()
    rows = conn.execute("""
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
def get_picks():
    """Today's HIGH and MEDIUM confidence model picks.

    `picks` are bettable. `started` is today's picks that have already kicked
    off, kept separate so the UI can show them greyed out with results.
    """
    picks = _attach_consensus(_model_predictions(only_strong=True))
    return {"count": len(picks), "generated_at": _last_generated_at(),
            "picks": picks, "started": _started_today()}


@app.get("/api/picks/all")
def get_all_picks():
    """All predictions including LOW confidence."""
    picks = _attach_consensus(_model_predictions(only_strong=False))
    return {"count": len(picks), "generated_at": _last_generated_at(), "picks": picks}


@app.get("/api/slip")
def get_slip(
    risk: str = Query("MEDIUM", description="LOW, MEDIUM, or HIGH"),
    games: int = Query(5, ge=1, le=20, description="Number of games"),
    leagues: str = Query(None, description="Comma-separated league names to restrict to"),
    exclude: str = Query(None, description="Fixtures to leave out, as 'Home||Away' pairs separated by ';;'"),
    pin: str = Query(None, description="Banker legs to lock in, as 'Home||Away||Market||Selection' separated by ';;'"),
):
    """Build a risk-based betting slip (one bet per match). Started games are
    excluded; each leg carries SportyBet odds when the bookmaker lists it.
    build_slip is deterministic, so re-requesting with one fixture excluded
    keeps the other legs and back-fills the next best option ('swap a leg')."""
    preds = _predictions_with_goals()
    all_opts = []
    for p in preds:
        all_opts.extend(derive_markets(p))

    league_set = ({l.strip().lower() for l in leagues.split(",") if l.strip()}
                  if leagues else None)
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
    def build(extra_excludes):
        keys = (exclude_set or set()) | extra_excludes
        return build_slip(all_opts, risk.upper(), games, leagues=league_set,
                          exclude_keys=keys or None, pinned=pinned)

    def sporty_for(slip):
        return attach_sporty_odds([
            {"home_team": l.home_team, "away_team": l.away_team,
             "market": l.market, "selection": l.selection} for l in slip])

    # A leg SportyBet won't price whose kickoff we can't verify as future is
    # very likely in play / just finished — swap it for the next best rather
    # than hand the user an unbookable leg. Legs with a known FUTURE kickoff
    # stay even when unpriced (small leagues simply aren't listed). Two
    # passes max; only runs when the odds feed is actually up.
    slip = build(set())
    sporty = sporty_for(slip)
    suspects = set()   # accumulates across passes or pass 2 re-admits pass 1's
    for _ in range(2):
        if not any(sporty["odds"]):
            break   # feed down/empty — no signal, keep the slip as-is
        today = datetime.now().strftime("%Y-%m-%d")
        new_suspects = {teams_key(l.home_team, l.away_team)
                        for l, o in zip(slip, sporty["odds"])
                        if o is None
                        and l.match_date <= today   # tomorrow's games are safe — just not mapped
                        and (not l.kickoff_time
                             or l.kickoff_time.strip() in ("", "00:00", "0:00"))}
        new_suspects -= suspects
        if not new_suspects:
            break
        suspects |= new_suspects
        slip = build(suspects)
        sporty = sporty_for(slip)
    combined = accumulator_probability(slip)

    # Real kickoffs by match_id, so a slip leg shows the same time the Picks
    # page does rather than PredictZ's placeholder "00:00".
    kickoffs = {p["match_id"]: p.get("kickoff_utc") for p in preds}

    leg_dicts = [
        {
            "home_team": leg.home_team,
            "away_team": leg.away_team,
            "league": leg.league,
            "league_display": pretty_league(leg.league),
            "match_date": leg.match_date,
            "kickoff_time": leg.kickoff_time,
            "kickoff_utc": kickoffs.get(leg.match_id),
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


@app.get("/api/leagues")
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
    if len(live) < 2:
        diagnosis = ("Only one source is currently publishing usable "
                     "percentages" + (f" ({live[0]})" if live else "") +
                     ". Consensus needs at least two to compare.")
    elif not multi:
        diagnosis = (f"{len(live)} sources are live ({', '.join(live)}) but "
                     "they cover different fixtures, so none can be "
                     "cross-checked yet.")
    elif not agreed:
        diagnosis = (f"{len(multi)} fixture(s) have two or more sources, but "
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
def get_stats():
    """Overall running track record (HIGH+MEDIUM scored picks)."""
    conn = get_connection()
    row = conn.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE
                   WHEN (mp.best_bet='Home Win' AND r.outcome='H')
                     OR (mp.best_bet='Draw'     AND r.outcome='D')
                     OR (mp.best_bet='Away Win' AND r.outcome='A')
                   THEN 1 ELSE 0 END) AS correct
        FROM model_predictions mp
        JOIN results r ON r.match_id = mp.match_id
        WHERE mp.confidence IN ('HIGH','MEDIUM')
          AND mp.id = (SELECT MAX(mp2.id) FROM model_predictions mp2 WHERE mp2.match_id = mp.match_id)
    """).fetchone()
    # The same figures across EVERY tier, including LOW. The endpoint has
    # always returned HIGH+MEDIUM while calling itself "overall", and the web
    # app printed it under the words "Overall Hit Rate" directly above a tier
    # table summing to 191 picks. Excluding LOW is right — we never serve
    # those as bets — but the number must not claim to be something it isn't,
    # and the difference is not cosmetic: 60.0% served vs 57.1% all-in.
    all_row = conn.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE
                   WHEN (mp.best_bet='Home Win' AND r.outcome='H')
                     OR (mp.best_bet='Draw'     AND r.outcome='D')
                     OR (mp.best_bet='Away Win' AND r.outcome='A')
                   THEN 1 ELSE 0 END) AS correct,
               SUM(CASE WHEN r.outcome='D' THEN 1 ELSE 0 END) AS draws
        FROM model_predictions mp
        JOIN results r ON r.match_id = mp.match_id
        WHERE mp.id = (SELECT MAX(mp2.id) FROM model_predictions mp2
                       WHERE mp2.match_id = mp.match_id)
    """).fetchone()
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
    }


@app.get("/api/stats/tiers")
def get_stats_by_tier():
    """Accuracy broken down by confidence tier."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT mp.confidence,
               COUNT(*) AS total,
               SUM(CASE
                   WHEN (mp.best_bet='Home Win' AND r.outcome='H')
                     OR (mp.best_bet='Draw'     AND r.outcome='D')
                     OR (mp.best_bet='Away Win' AND r.outcome='A')
                   THEN 1 ELSE 0 END) AS correct
        FROM model_predictions mp
        JOIN results r ON r.match_id = mp.match_id
        WHERE mp.id = (SELECT MAX(mp2.id) FROM model_predictions mp2 WHERE mp2.match_id = mp.match_id)
        GROUP BY mp.confidence
    """).fetchall()
    conn.close()
    tiers = {}
    for r in rows:
        d = dict(r)
        total = d["total"] or 0
        correct = d["correct"] or 0
        tiers[d["confidence"]] = {
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total * 100, 1) if total else None,
        }
    return tiers

@app.get("/api/stats/picks")
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
          {where}
        ORDER BY m.match_date DESC, mp.id DESC
    """, params).fetchall()
    conn.close()
    return {"tier": tier.upper() if tier else "ALL", "count": len(rows),
            "picks": [dict(r) for r in rows]}

@app.get("/api/fixtures")
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


@app.post("/api/slip/check")
def check_slip(legs: List[dict] = Body(..., embed=True)):
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


@app.get("/api/booking/providers")
def booking_providers():
    """Which bookmakers we can read codes from, and create codes on."""
    return {"decode": sorted(PROVIDERS), "generate": sorted(SHARE_POST_URLS)}


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
    try:
        res = generate_code(legs, provider)
    except BookingCodeError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "provider": provider, **res}

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
