"""
predict.py - Run the trained Random Forest on today's scraped matches.
v2: Gemma analysis wired in for HIGH/MEDIUM picks (saved to DB -> Telegram).

Usage:
    python models/predict.py
    python models/predict.py --no-gemma   (skip AI analysis, faster)
"""

import os
import sys
import argparse
import joblib
import pandas as pd
from datetime import datetime
from difflib import get_close_matches
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from models.train import load_all_csvs, form_ppg, goals_avg, venue_ppg, h2h_wins, FORM_WINDOW
from models.consensus import build_consensus, _same_match, team_sim

MODEL_PATH = "./models/random_forest_v1.joblib"
# rf_v1b = same trained model, but serving now feeds REAL bookmaker odds to the
# odds features (was Forebet's percentages - train/serve mismatch) and team
# form includes our own recorded results. Bumped so the ledger separates eras.
# rf_v1c = same model, but the confidence TIER is now consensus-adjusted
# (see apply_consensus_modifier). Probabilities are untouched.
MODEL_VERSION = "rf_v2"   # rf_v2 = odds features fixed (closing-odds columns now
                          # read, coverage 39.5% -> 94.1% of training rows) and
                          # new odds_is_market flag distinguishing a real price
                          # from a model's percentages. FEATURE SET CHANGED, so
                          # the ledger must separate this era from rf_v1*: the
                          # tier calibration below was measured on the old model.
                          # rf_v1e = consensus BOOST disabled (was 0/3, harmed
                          # HIGH tier); demote kept. rf_v1d = July data refresh.

# Confidence tiers. Defined in models/tiers.py, not here — the API and the
# Record page's tier tooltip both need these numbers, and neither can import
# this module (joblib + pandas + sklearn at import time, on a 512MB dyno).
# Re-exported so `from models.predict import TIER_HIGH` keeps working for the
# health scripts. The calibration evidence lives with the constants.
from models.tiers import TIER_HIGH, TIER_MEDIUM  # noqa: E402,F401
from models.sports import FOOTBALL, sport_sql  # noqa: E402

# Forebet name -> historical dataset name
# Grows over time as skip logs reveal mismatches
TEAM_ALIASES = {
    # International
    "USA": "United States",
    "South Korea": "South Korea",
    "North Korea": "North Korea",
    "Ireland": "Republic of Ireland",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Trinidad & Tobago": "Trinidad and Tobago",
    "UAE": "United Arab Emirates",
    "DR Congo": "DR Congo",
    "Cape Verde Islands": "Cape Verde",
    "China": "China PR",
    # Clubs. These are names the automatic matcher scores at 0.92 — close, but
    # that band also contains genuinely different clubs ("America Mineiro" vs
    # "Club America" is Brazil vs Mexico; "Athletic Club" vs "Athletico-PR" is
    # Bilbao vs Brazil), so the matcher refuses the whole band and they are
    # confirmed by hand here instead.
    #
    # A run prints its near-misses as ready-to-paste lines. Only add one if you
    # KNOW it's the same club: a wrong entry silently feeds another team's form
    # into the prediction, which is worse than skipping the fixture.
    "AZ": "AZ Alkmaar",
    "Accrington Stanley": "Accrington",
    "Atlanta United": "Atlanta Utd",
    # Copa Libertadores, 11 Aug 2026. Scores 0.92 against "Talleres Cordoba";
    # confirmed the same club (Talleres de Cordoba, Argentina — the only
    # Talleres in the Argentine CSVs).
    "Talleres": "Talleres Cordoba",
    # Saudi Pro League, 25 Aug 2026. PredictZ spells these two differently from
    # SaudiProLeague.csv and both fell below the threshold, so the fixtures were
    # skipped for having no form history — NOT for having no odds, which is what
    # it looks like from the outside. Measured cost on the first Saudi matchday
    # after the CSV landed: 2 of 6 fixtures lost, both carrying a real
    # bookmaker price (1.74 and 1.71) and both in servable territory.
    #
    # Safe to confirm by hand: each name occurs in exactly ONE historical file
    # (SaudiProLeague.csv), so there is no other club either could resolve to.
    # Al Shabab FC is the Riyadh club, which is what "Al Shabab Riyadh" says.
    # Al Diriyah holds only 2 games, so it will correctly fire the SMALL SAMPLE
    # warning rather than pretend to a five-game trend.
    "Al Shabab Riyadh": "Al Shabab",
    "Diriyah Club": "Al Diriyah",
    # DO NOT add "Independiente del Valle" -> "Independiente". It scores 0.92,
    # which is exactly the trap this band exists for: Independiente del Valle
    # is Ecuadorian, Independiente is Argentine. Different clubs, and the
    # alias would silently predict one team's match from the other's form.
    # Same for "Slovan Bratislava" -> "Slovakia" (0.88): a club against a
    # NATIONAL TEAM. The threshold correctly refuses both.
}


def build_current_team_histories() -> dict:
    """Replay all historical matches to get every team's form as of today."""
    logger.info("Rebuilding current team form from historical data...")
    df = load_all_csvs()

    team_history = {}
    for _, row in df.iterrows():
        home, away = row["HomeTeam"], row["AwayTeam"]
        h_result = "W" if row["FTR"] == "H" else ("D" if row["FTR"] == "D" else "L")
        a_result = "W" if row["FTR"] == "A" else ("D" if row["FTR"] == "D" else "L")

        team_history.setdefault(home, []).append({
            "venue": "home", "result": h_result, "opponent": away,
            "goals_for": row["FTHG"], "goals_against": row["FTAG"],
        })
        team_history.setdefault(away, []).append({
            "venue": "away", "result": a_result, "opponent": home,
            "goals_for": row["FTAG"], "goals_against": row["FTHG"],
        })

    # Append OUR recorded real results (matches + results tables) so form
    # reflects games played after the historical CSVs end. Only genuine
    # finished scores live in results, so this can't leak predictions.
    conn = get_connection()
    # Football only. team_history is keyed by team NAME with no sport attached,
    # so a basketball result would land in the same dict the football forest
    # reads its form features from -- and a 120-98 final would enter goals_for
    # as if it were a scoreline. Names collide for real (Phoenix, Arsenal in
    # multiple codes), so this is not hypothetical once a second sport lands.
    rows = conn.execute(f"""
        SELECT m.home_team, m.away_team, r.home_goals, r.away_goals, r.outcome
        FROM results r JOIN matches m ON m.match_id = r.match_id
        WHERE 1=1 {sport_sql(FOOTBALL)}
        ORDER BY m.match_date, r.recorded_at
    """).fetchall()
    conn.close()
    known = list(team_history.keys())
    appended = 0
    for r in rows:
        home = match_team_name(r["home_team"], known) or r["home_team"]
        away = match_team_name(r["away_team"], known) or r["away_team"]
        h_result = "W" if r["outcome"] == "H" else ("D" if r["outcome"] == "D" else "L")
        a_result = "W" if r["outcome"] == "A" else ("D" if r["outcome"] == "D" else "L")
        team_history.setdefault(home, []).append({
            "venue": "home", "result": h_result, "opponent": away,
            "goals_for": r["home_goals"], "goals_against": r["away_goals"],
        })
        team_history.setdefault(away, []).append({
            "venue": "away", "result": a_result, "opponent": home,
            "goals_for": r["away_goals"], "goals_against": r["home_goals"],
        })
        appended += 1

    logger.success(f"✅ Form data ready for {len(team_history)} teams "
                   f"(+{appended} of our own recorded results)")
    return team_history


# Matching a scraped team to the historical dataset is NOT the same problem as
# matching two fixtures. Here a wrong answer doesn't hide a match — it feeds
# ANOTHER CLUB'S form into the prediction, silently. So this is deliberately
# stricter than the fixture matcher in consensus.py.
#
# Measured on real data: at 0.86 the normalised matcher pairs 90 more names but
# includes "Everton Vina del Mar" -> "Everton" (Chile -> England), "Racing
# Louisville W" -> "Racing Club" (USA -> Argentina) and "San Antonio Bulo Bulo"
# -> "San Antonio FC" (Bolivia -> USA). All score 0.92 through the containment
# rule, because the extra words are place names the matcher can't know are
# distinguishing. At 0.95 those disappear and 12 correct pairs remain — clubs
# differing only by a noise word or an accent (FC Porto/Porto, Tijuana/Club
# Tijuana, Atlético PR/Athletico-PR).
NAME_MATCH_MIN = 0.95
# ...and the winner must be clearly ahead of the runner-up, so a name that is
# almost equally close to two clubs is left unmatched rather than guessed.
NAME_MATCH_GAP = 0.05

# Near-misses land here so they can be curated into TEAM_ALIASES by a human
# instead of being auto-accepted.
_near_misses: dict[str, tuple] = {}


def match_team_name(scraped_name: str, known_teams: list) -> str | None:
    """Match a scraped team name to a historical team name."""
    # 1. Manual alias map (exact known mismatches)
    if scraped_name in TEAM_ALIASES:
        aliased = TEAM_ALIASES[scraped_name]
        if aliased in known_teams:
            return aliased
    # 2. Exact match
    if scraped_name in known_teams:
        return scraped_name
    # 3. Normalised match: ignores FC/AFC/CF prefixes, accents and punctuation,
    #    which is how the same club differs between our sources and the CSVs.
    scored = sorted(((team_sim(scraped_name, k), k) for k in known_teams),
                    reverse=True)[:2]
    if scored:
        best, best_name = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if best >= NAME_MATCH_MIN and (best - runner_up) >= NAME_MATCH_GAP:
            return best_name
        # Close but not safe — record it for TEAM_ALIASES rather than guess.
        if best >= 0.85:
            _near_misses[scraped_name] = (best_name, round(best, 2))

    # 4. Legacy fuzzy fallback, kept for names the normaliser can't help with.
    matches = get_close_matches(scraped_name, known_teams, n=1, cutoff=0.85)
    return matches[0] if matches else None


def get_todays_scraped_matches() -> list:
    """Latest scraped prediction per match, today onwards."""
    conn = get_connection()
    rows = conn.execute(f"""
        SELECT m.match_id, m.home_team, m.away_team, m.league,
               m.match_date, m.kickoff_time,
               p.home_win_pct, p.draw_pct, p.away_win_pct,
               p.predicted_home_goals, p.predicted_away_goals,
               p.odds_home, p.odds_draw, p.odds_away
        FROM matches m
        JOIN scraped_predictions p ON p.id = (
            SELECT MAX(p2.id) FROM scraped_predictions p2
            WHERE p2.match_id = m.match_id
        )
        WHERE m.match_date >= date('now')
          -- NEVER predict a match that has started or already has a result
          -- (CLAUDE.md bug #33). match_date alone let a 23:00 UTC kickoff,
          -- dated "tomorrow" by one source, be predicted the next morning -
          -- after the final whistle, with its own result already feeding the
          -- form features. The record counts the LATEST prediction per match,
          -- so such a row silently replaced the real pre-match pick.
          AND (m.kickoff_utc IS NULL
               OR m.kickoff_utc > strftime('%Y-%m-%d %H:%M', 'now'))
          AND NOT EXISTS (SELECT 1 FROM results r WHERE r.match_id = m.match_id)
          {sport_sql(FOOTBALL)}
        ORDER BY m.match_date, m.kickoff_time
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def build_live_features(home_hist: list, away_hist: list,
                        home_team: str, away_team: str,
                        forebet: dict) -> dict:
    """Build the exact feature set the model was trained on.

    Odds features MUST come from real bookmaker odds when available - the
    model was trained on odds-implied probabilities. Source percentages are
    only a fallback (for PredictZ rows they are odds-derived anyway).

    odds_is_market records WHICH of those two happened, because they are not
    the same thing and the model used to be unable to tell:

      1.0  a real bookmaker price
      0.0  another model's percentages, normalised to look like one

    The fallback is not rare. On August's ledger roughly half of the latest
    rows per match had no real price, and PredictZ's own odds coverage fell
    from 62% in July to 24% in August without anything going red — because
    substituting percentages never raises an error, it just silently changes
    what the most important feature in the model means. Flagging it lets the
    forest discount those rows instead of trusting them equally, and makes the
    regression visible in the logs.
    """
    oh, od, oa = (forebet.get("odds_home"), forebet.get("odds_draw"),
                  forebet.get("odds_away"))
    if oh and od and oa:
        h, d, a = 1 / oh, 1 / od, 1 / oa
        is_market = 1.0
    else:
        hp, dp, ap = (forebet.get("home_win_pct"), forebet.get("draw_pct"),
                      forebet.get("away_win_pct"))
        if not (hp and dp and ap):
            # No odds AND no source percentages. The old code substituted
            # 33.3/33.3/33.3 here — a flat prior fed into features the model
            # trained on real bookmaker odds. It never errored: it just made
            # quietly worse predictions that still got graded and still moved
            # the tier stats. Refuse instead; the caller skips the match.
            # (Fixture sources without odds — e.g. football-data.org's free
            # tier — would otherwise poison the ledger silently. See bug #7.)
            return None
        h, d, a = hp / 100, dp / 100, ap / 100
        is_market = 0.0
    total = h + d + a

    return {
        "home_form_ppg": form_ppg(home_hist, FORM_WINDOW),
        "away_form_ppg": form_ppg(away_hist, FORM_WINDOW),
        "home_goals_for_avg": goals_avg(home_hist, FORM_WINDOW, "for"),
        "home_goals_against_avg": goals_avg(home_hist, FORM_WINDOW, "against"),
        "away_goals_for_avg": goals_avg(away_hist, FORM_WINDOW, "for"),
        "away_goals_against_avg": goals_avg(away_hist, FORM_WINDOW, "against"),
        "home_home_ppg": venue_ppg(home_hist, FORM_WINDOW, "home"),
        "away_away_ppg": venue_ppg(away_hist, FORM_WINDOW, "away"),
        "h2h_home_wins": h2h_wins(home_hist, away_team, 5),
        "odds_home_prob": h / total,
        "odds_draw_prob": d / total,
        "odds_away_prob": a / total,
        "odds_is_market": is_market,
    }


def h2h_record(home_hist: list, away_team: str, n: int = 5) -> tuple:
    """(wins, draws, losses) from the home team's view in the last n meetings.

    The `h2h_home_wins` FEATURE is a bare count, and a bare count is ambiguous
    in exactly the way that gets a fabrication published: 0 means "met five
    times, won none" and also "never met". Handed the first reading, an LLM
    filled the gap itself — given "won 0 of the last 5" it wrote "Al Fateh has
    won all of the last 5", which does not follow (they could all be draws) and
    in the test case was simply untrue.

    So the prompt gets the full split and the number of meetings that actually
    happened. Same defect and same fix as odds_is_market: make the absence of
    data distinguishable from a zero.
    """
    meetings = [m for m in home_hist if m["opponent"] == away_team][-n:]
    w = sum(1 for m in meetings if m["result"] == "W")
    d = sum(1 for m in meetings if m["result"] == "D")
    return w, d, len(meetings) - w - d


def recent_results(hist: list, n: int = FORM_WINDOW) -> str:
    """Last n results as 'WWDLW', oldest first. '' when there is no history.

    For the analysis prompt only — never a model feature. The forest sees
    form_ppg (2.00); a reader wants to know that came from W-W-D-L-W rather
    than from D-D-D-D-D-D, and an LLM cannot infer the shape from the average.
    """
    return "".join(m["result"] for m in hist[-n:])


def build_analysis_facts(home_hist: list, away_hist: list, feats: dict,
                         match: dict, away_matched: str) -> dict:
    """The evidence the analyst gets: the model's own features, plus enough
    context to describe them honestly.

    `feats` is passed through UNCHANGED and unrecomputed. That is deliberate —
    the prose is then explaining the exact numbers that produced the pick, so it
    cannot drift from it. Everything added here is either presentational
    (recent_results) or a caveat the features themselves cannot express:

      *_games   how many matches those averages actually rest on. Required,
                because form_ppg divides by len(recent) rather than the window,
                so one win and five wins are both 3.00 (CLAUDE.md bug #27).
                Without this the LLM writes "unbeaten in five" about a club we
                have seen once, which is a fabrication we published.
      odds_*    the raw prices, so the analysis can quote 1.80 instead of a
                normalised probability. Absent when odds_is_market is 0.0.
    """
    return {
        **feats,
        "form_window": FORM_WINDOW,
        "home_games": len(home_hist),
        "away_games": len(away_hist),
        "home_recent": recent_results(home_hist),
        "away_recent": recent_results(away_hist),
        # away_matched, NOT match["away_team"]: history records opponents under
        # the CSV's spelling, so the scraped name would match nothing and every
        # fixture would silently report "no previous meetings". Same reason
        # build_live_features gets the matched names for h2h_wins.
        "h2h_record": h2h_record(home_hist, away_matched),
        "odds_home": match.get("odds_home"),
        "odds_draw": match.get("odds_draw"),
        "odds_away": match.get("odds_away"),
    }


def save_prediction(match_id: str, probs: dict, confidence: str,
                    best_bet: str, best_prob: float, gemma_analysis: str = None,
                    consensus_adj: str = None):
    conn = get_connection()
    conn.execute("""
        INSERT INTO model_predictions
        (match_id, model_version, home_win_prob, draw_prob, away_win_prob,
         confidence, best_bet, best_bet_prob, gemma_analysis, consensus_adj)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [match_id, MODEL_VERSION, probs["H"], probs["D"], probs["A"],
          confidence, best_bet, best_prob, gemma_analysis, consensus_adj])
    conn.commit()
    conn.close()


def apply_consensus_modifier(match: dict, best_outcome: str,
                             confidence: str, consensus: list) -> tuple[str, str | None]:
    """Adjust the confidence tier using cross-source agreement.

    Rules (deliberately conservative — the tier calibration story depends on
    these staying rare and defensible):
      - demote: HIGH -> MEDIUM when 2+ sources agree on a DIFFERENT outcome.
        Protective: it only ever REMOVES a doubtful pick from HIGH.
      - LOW is never promoted: sources agreeing on a coin-flip doesn't make it
        bettable. Probabilities are never changed, only the tier label.

    BOOST DISABLED (rf_v1e, July 2026). The MEDIUM->HIGH boost went 0/3 on the
    honest ledger and every one lost badly (1-5, 0-4, 4-3 against): it was
    dragging genuinely-marginal 56-61% picks into HIGH and tanking the tier's
    calibration (HIGH fell to 5/13). Since the boost is the ONLY mechanism that
    promotes a pick INTO HIGH, disabling it is the single cleanest lever on
    HIGH-tier honesty. Kept the code path but neutered the promotion; re-enable
    only if a matured ledger shows agreement-with-model actually wins. See the
    track record via db/track_record_health.py.

    Returns (confidence, consensus_adj) where consensus_adj is
    'demoted' / None — stored in the ledger so we can later
    measure whether these adjustments actually win.
    """
    for entry in consensus:
        if not _same_match(match, entry):
            continue
        if len(entry["sources"]) < 2 or not entry["agree"]:
            return confidence, None
        # BOOST DISABLED: no longer promote MEDIUM -> HIGH (was 0/3, net-harmful).
        if entry["agreed_outcome"] != best_outcome and confidence == "HIGH":
            return "MEDIUM", "demoted"
        return confidence, None
    return confidence, None


def run(use_gemma: bool = True):
    # Load model
    if not os.path.exists(MODEL_PATH):
        logger.error(f"No trained model at {MODEL_PATH}. Run: python models/train.py")
        sys.exit(1)
    bundle = joblib.load(MODEL_PATH)
    model, columns = bundle["model"], bundle["columns"]
    logger.info(f"Loaded model {MODEL_VERSION}")

    # Gemma availability check (only if requested)
    gemma_ok = False
    if use_gemma:
        from analysis.gemma_analyst import check_ollama_running, analyze_match
        gemma_ok = check_ollama_running()
        if not gemma_ok:
            logger.warning("Ollama not running - predictions will have no AI analysis")

    # Current form for all known teams
    team_history = build_current_team_histories()
    known_teams = list(team_history.keys())

    # Today's scraped fixtures
    scraped = get_todays_scraped_matches()
    if not scraped:
        logger.warning("No scraped matches in DB. Run: python scrapers/forebet.py")
        sys.exit(0)
    logger.info(f"Found {len(scraped)} scraped matches to evaluate")

    # Cross-source agreement, used to adjust confidence tiers
    try:
        consensus = build_consensus()
    except Exception as e:
        logger.warning(f"Consensus unavailable ({e}) — tiers won't be adjusted")
        consensus = []

    predictions = []
    skipped = 0
    no_odds = 0
    pct_fallback = 0
    boosted = demoted = 0

    for match in scraped:
        home_match = match_team_name(match["home_team"], known_teams)
        away_match = match_team_name(match["away_team"], known_teams)

        if not home_match or not away_match:
            skipped += 1
            if match.get("league") in ("WC", "INT"):
                logger.warning(f"Skipped [{match['league']}]: "
                               f"'{match['home_team']}' vs '{match['away_team']}'")
            continue

        feats = build_live_features(
            team_history[home_match], team_history[away_match],
            home_match, away_match, match
        )
        if feats is None:
            # No odds and no source percentages — predicting here would mean
            # inventing a uniform prior. Better no pick than a fake one.
            no_odds += 1
            continue
        if feats["odds_is_market"] == 0.0:
            # Served on another model's percentages rather than a real price.
            # Counted so the run REPORTS it: PredictZ's odds coverage fell from
            # 62% to 24% between July and August and nothing noticed, because
            # this path succeeds silently.
            pct_fallback += 1

        X = pd.DataFrame([feats])[columns]
        probs_arr = model.predict_proba(X)[0]
        probs = dict(zip(model.classes_, probs_arr))

        best_outcome = max(probs, key=probs.get)
        best_prob = probs[best_outcome]

        if best_prob >= TIER_HIGH:
            confidence = "HIGH"
        elif best_prob >= TIER_MEDIUM:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        bet_names = {"H": "Home Win", "D": "Draw", "A": "Away Win"}
        best_bet = bet_names[best_outcome]

        # Consensus tier adjustment (before Gemma, so boosted picks get analysis)
        confidence, consensus_adj = apply_consensus_modifier(
            match, best_outcome, confidence, consensus)
        if consensus_adj == "boosted":
            boosted += 1
            logger.info(f"⬆ Consensus boost MEDIUM→HIGH: "
                        f"{match['home_team']} vs {match['away_team']}")
        elif consensus_adj == "demoted":
            demoted += 1
            logger.info(f"⬇ Consensus demote HIGH→MEDIUM: "
                        f"{match['home_team']} vs {match['away_team']} "
                        f"(sources back a different outcome)")

        # Gemma analysis only for picks worth showing (saves time)
        gemma_text = None
        if gemma_ok and confidence in ("HIGH", "MEDIUM"):
            logger.info(f"🧠 Gemma analyzing: {match['home_team']} vs {match['away_team']}")
            gemma_text = analyze_match({
                "home_team": match["home_team"],
                "away_team": match["away_team"],
                "league": match["league"],
                "home_win_prob": probs["H"] * 100,
                "draw_prob": probs["D"] * 100,
                "away_win_prob": probs["A"] * 100,
                "home_win_pct": match.get("home_win_pct"),
                "draw_pct": match.get("draw_pct"),
                "away_win_pct": match.get("away_win_pct"),
                "predicted_score": (
                    f"{match['predicted_home_goals']:.0f} - {match['predicted_away_goals']:.0f}"
                    if match.get("predicted_home_goals") is not None else "N/A"
                ),
                "over_2_5_pct": match.get("over_2_5_pct", "N/A"),
                "confidence": confidence,
                "best_bet": best_bet,
                "best_bet_prob": best_prob * 100,
                # The features the forest actually used. Before this, the prompt
                # got three probabilities and a ban on quoting them, so the only
                # possible output was generic prose. See _facts_block().
                "facts": build_analysis_facts(
                    team_history[home_match], team_history[away_match],
                    feats, match, away_match),
            })

        save_prediction(match["match_id"], probs, confidence,
                        best_bet, best_prob, gemma_text, consensus_adj)

        predictions.append({
            **match,
            "model_probs": probs,
            "confidence": confidence,
            "best_bet": best_bet,
            "best_prob": best_prob,
            "gemma_analysis": gemma_text,
        })

    if _near_misses:
        logger.info(f"{len(_near_misses)} team name(s) were CLOSE to a known "
                    f"club but not close enough to trust — add to TEAM_ALIASES "
                    f"if correct:")
        for scraped, (guess, score) in sorted(_near_misses.items())[:12]:
            logger.info(f'    "{scraped}": "{guess}",   # similarity {score}')

    logger.success(f"✅ Predicted {len(predictions)} matches "
                   f"({skipped} skipped - teams not in historical data; "
                   f"{no_odds} skipped - no odds/percentages; "
                   f"consensus: {boosted} boosted, {demoted} demoted)")

    # Market-price coverage. This is a health metric, not trivia: the odds
    # features carry two thirds of the model's importance, so the share of picks
    # built on a real price rather than another model's percentages is the single
    # best indicator of whether today's predictions mean what the track record
    # says they mean. Every match that survived build_live_features is predicted
    # (no further `continue`), so these two counts partition len(predictions).
    if predictions:
        total = len(predictions)
        on_market = total - pct_fallback
        logger.info(f"Market-price coverage: {on_market}/{total} "
                    f"({on_market / total:.0%}) picks built on a real bookmaker "
                    f"price; {pct_fallback} on source percentages")
        if pct_fallback > total * 0.4:
            logger.warning(f"{pct_fallback}/{total} picks are running on another "
                           f"model's percentages rather than market odds - check "
                           f"whether PredictZ/ESPN odds scraping has degraded")

    # Display, best picks first
    predictions.sort(key=lambda x: x["best_prob"], reverse=True)

    print(f"\n{'='*70}")
    print(f"MODEL PICKS - {datetime.now().strftime('%A, %d %B %Y')}")
    print(f"{'='*70}")

    for tier in ["HIGH", "MEDIUM"]:
        tier_preds = [p for p in predictions if p["confidence"] == tier]
        if not tier_preds:
            continue
        print(f"\n--- {tier} CONFIDENCE ({len(tier_preds)} picks) ---")
        for p in tier_preds:
            probs = p["model_probs"]
            print(f"\n{p['home_team']} vs {p['away_team']}  [{p['league']}] "
                  f"{p['match_date']} {p['kickoff_time']}")
            print(f"  Model: H {probs['H']:.0%} | D {probs['D']:.0%} | A {probs['A']:.0%}")
            print(f"  >> {p['best_bet']} ({p['best_prob']:.0%})")
            if p.get("gemma_analysis"):
                # ASCII marker, not an emoji. This line used to be "💬 ..." and
                # it CRASHED the whole script on Windows: the console is cp1252,
                # UnicodeEncodeError is raised by print itself, and it fires
                # after every prediction is already saved — so the run died on
                # its own summary, and because predict.py is a CRITICAL step,
                # run_daily.py aborted with it. Never surfaced in the cloud
                # (Actions is UTF-8) and never surfaced locally either, because
                # local runs mostly use --no-gemma and this line only executes
                # when analysis text exists. Same cp1252 class as the webapp
                # rule and db/form_depth_health.py.
                print(f"     {p['gemma_analysis'][:150]}...")

    low_count = sum(1 for p in predictions if p["confidence"] == "LOW")
    if low_count:
        print(f"\n({low_count} LOW confidence matches hidden - not worth betting)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-gemma", action="store_true", help="Skip Gemma analysis")
    args = parser.parse_args()
    run(use_gemma=not args.no_gemma)