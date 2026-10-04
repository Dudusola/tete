"""nhl_analyst.py - written analysis for upcoming NHL AND NFL picks (the
"Details" panel of each card on the Picks page). Since 4 Oct 2026 it explains
the card's OWN recommendation (models/us_sports.py) instead of deciding a
verdict of its own - the old edge-based verdict could say "no bet" under a
card that recommended a bet. Was the NHL private preview's writer before that.

Writes shadow_predictions.analysis for NHL games in the next ANALYSE_HOURS,
from the facts and market comparison models/elo_shadow.py stored beside them
(facts_json, markets_json). Nothing else is handed to the writer, and the
facts are stored, so every sentence can be checked against its source.

The rules are football's (bug #28), adapted to what hockey analysis has to be
honest about:
  - The single biggest thing a hockey bettor wants is the STARTING GOALIE, and
    we do not have it. The writer must say so rather than guess.
  - Opening week: every "recent" figure is last season's, and the summer's
    trades are not in the data. Flagged first, before any figure it qualifies.
  - The verdict must name a market from the list we priced, and must be "no
    bet" when every gap to the bookmaker is small. Most of the time the market
    will know more than us (it knows the goalies); saying so is the job.

Shown ONLY on the unlisted preview page (webapp/hockey.html). Nothing here
reaches /api/picks, the Record page, the bot or any published accuracy.

Usage:
    python analysis/nhl_analyst.py              # games in the next 36h missing fresh analysis
    python analysis/nhl_analyst.py --force      # rewrite them all
    python analysis/nhl_analyst.py --dry-run    # print the prompts, call nothing
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection, init_db

ANALYSE_HOURS = 72      # NFL slates are posted days ahead; hourly runs keep up
STALE_HOURS = 18        # rewrite if older - prices and form move
# Analyses written before the recommendation-based prompt carried the old
# edge verdict ("no bet ...") and could contradict the card - rewrite them.
PROMPT_SINCE = "2026-10-04 19:00:00"   # just before the new prompt shipped
SPORTS_SQL = "('ice_hockey', 'american_football')"



# Per-sport wording. NHL records are W-L-OTL (an overtime loss earned a point
# and is not a regulation defeat); an NFL overtime loss is just a loss, and a
# tie is possible.
SPORT = {
    "ice_hockey": {"name": "ice hockey", "league": "NHL", "unit": "goals",
                   "venue": "home ice",
                   "unknown": "confirmed starting goalies, injuries, line combinations, travel"},
    "american_football": {"name": "American football", "league": "NFL", "unit": "points",
                          "venue": "home field",
                          "unknown": "quarterback availability, injuries, weather"},
}


def _rec(r: dict, sport: str = "ice_hockey") -> str:
    if not r or not r.get("n"):
        return "no games"
    unit = SPORT[sport]["unit"]
    if sport == "ice_hockey":
        s = f"{r['w']}-{r['l']}-{r['otl']} (W-L-OTL) in {r['n']} games"
    else:
        s = (f"{r['w']}-{r['l']}" + (f"-{r['t']} (W-L-T)" if r.get("t") else " (W-L)")
             + f" in {r['n']} games")
    if r.get("playoff"):
        s += f", {r['playoff']} of them playoff games"
    return s + f", scoring {r['gf_pg']:.2f} and conceding {r['ga_pg']:.2f} {unit} per game"


def _streak(s: str | None, sport: str = "ice_hockey") -> str:
    """'W3' -> 'Won their last 3.' - the code is jargon the writer repeats."""
    if not s:
        return ""
    n = int(s[1:])
    if s[0] == "W":
        return f"Won their last {n}." if n > 1 else "Won their last game."
    if s[0] == "T":
        return "Tied their last game."
    ot = " (overtime losses included)" if sport == "ice_hockey" else ""
    return f"Lost their last {n}{ot}." if n > 1 else "Lost their last game."


def _ordinal(n) -> str:
    if n is None:
        return "unranked"
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def recommendation(p_home: float, markets: dict, home: str, away: str) -> dict:
    """The SAME pick the card shows (models/us_sports.py): the moneyline side
    the model rates likelier, its football-style tier, and the book's view.
    The prose explains this pick - it never decides its own (on 2 Oct 2026 the
    old edge-based verdict could say "no bet" under a card recommending one)."""
    from models.us_sports import tier
    side = "ml_home" if p_home >= 0.5 else "ml_away"
    it = next((i for i in markets.get("items", []) if i["key"] == side), {})
    p = it.get("p", p_home if side == "ml_home" else 1 - p_home)
    mp = it.get("market_p")
    return {"team": home if side == "ml_home" else away, "tier": tier(p),
            "odds": it.get("odds"),
            "gap": None if mp is None else round((p - mp) * 100, 1)}


def facts_block(home: str, away: str, facts: dict, markets: dict,
                sport: str = "ice_hockey") -> str:
    cfg = SPORT[sport]
    h, a = facts["home"], facts["away"]
    out = []
    if h.get("new_season") or a.get("new_season"):
        out.append(f"!! OPENING WEEK: every form figure below is from LAST season "
                   f"(last games played: {home} on {h.get('last_game')}, {away} on {a.get('last_game')}). "
                   f"Summer trades, signings and injuries are NOT in these numbers - say so.")
    out.append(f"- Power rating: {home} {_ordinal(h.get('rank'))} of {h.get('of')}, "
               f"{away} {_ordinal(a.get('rank'))} of {a.get('of')} ({cfg['venue']} is worth a little extra).")
    out.append(f"- {home}, last {h['last10']['n']}: {_rec(h['last10'], sport)}. {_streak(h.get('streak'), sport)}")
    out.append(f"- {away}, last {a['last10']['n']}: {_rec(a['last10'], sport)}. {_streak(a.get('streak'), sport)}")
    out.append(f"- {home} in their last home games: {_rec(h['venue10'], sport)}.")
    out.append(f"- {away} in their last road games: {_rec(a['venue10'], sport)}.")
    if h.get("season", {}).get("n") or a.get("season", {}).get("n"):
        out.append(f"- This season so far: {home} {_rec(h['season'], sport)}; {away} {_rec(a['season'], sport)}.")
    for side, name in ((h, home), (a, away)):
        if side.get("back_to_back"):
            out.append(f"- {name} played YESTERDAY (second night of a back-to-back).")
        elif side.get("rest_days") is not None:
            out.append(f"- {name}: {side['rest_days']} days since their last game.")
    if facts.get("h2h"):
        games = "; ".join(
            f"{g['date']} {g['home']} {g['score']} {g['away']}"
            f"{' (' + g['decided_in'] + ', won by ' + g['winner'] + ')' if g['decided_in'] in ('OT', 'SO') else ''}"
            f"{' [playoffs]' if g['playoff'] else ''}" for g in facts["h2h"])
        out.append(f"- Last {len(facts['h2h'])} meetings, newest first: {games}.")
        # The tally is COMPUTED and stated leader-first: given the list alone,
        # Gemini miscounted ("Florida won two of five" - they won three) and
        # flipped a 2-3 into "Boston's 3-2 edge" (29 Sep 2026, bug #28's lesson).
        n = len(facts["h2h"])
        hw = sum(g["winner"] == home for g in facts["h2h"])
        aw = n - hw
        extra = sum(g["decided_in"] in ("OT", "SO") for g in facts["h2h"])
        lead = (f"{home} lead the last {n} meetings {hw}-{aw}" if hw > aw else
                f"{away} lead the last {n} meetings {aw}-{hw}" if aw > hw else
                f"the last {n} meetings are level {hw}-{aw}")
        out.append(f"- Meetings tally: {lead}"
                   f"{f' ({extra} settled in overtime or a shootout)' if extra else ''}.")
    else:
        out.append("- These teams have not met in our records.")

    mv = facts.get("market_movement") or {}
    if mv.get("moneyline"):
        m = mv["moneyline"]
        out.append(f"- Bookmaker moneyline: {home} {m['now'][0]} / {away} {m['now'][1]} "
                   f"(opened {m['open'][0]} / {m['open'][1]}).")
    line = mv.get("puck_line") or mv.get("spread")
    if line:
        what = "puck line" if sport == "ice_hockey" else "point spread"
        moved = line.get("open_home_line") not in (None, line["home_line"])
        out.append(f"- Bookmaker {what}: {home} {line['home_line']:+} at {line['now'][0]}, "
                   f"{away} {-line['home_line']:+} at {line['now'][1]}"
                   + (f" (the home line has moved from {line['open_home_line']:+})" if moved else "")
                   + ".")
    if mv.get("total"):
        m = mv["total"]
        moved = m.get("open_line") not in (None, m["line"])
        out.append(f"- Bookmaker total: {m['line']} {cfg['unit']}, over {m['now'][0]} / under {m['now'][1]}"
                   + (f" (opened at {m['open_line']})" if moved else "") + ".")
    gk = facts.get("goalies")
    if gk:
        for side, name in (("home", home), ("away", away)):
            gs = gk.get(side) or []
            if not gs:
                continue
            season = gs[0].get("season")
            label = f"{str(season)[:4]}-{str(season)[6:]}" if season else "last season"
            listed = "; ".join(
                f"{g['name']} {g['gp']} games, save % {g['sv_pct']:.3f}, {g['gaa']:.2f} goals against avg"
                for g in gs if g.get("gp") and g.get("sv_pct") is not None and g.get("gaa") is not None)
            if listed:
                out.append(f"- {name} goalies on the roster, with their {label} numbers "
                           f"(possibly earned at another club): {listed}.")
            recent = (gk.get("recent_starts") or {}).get(side) or []
            if recent:
                out.append(f"- {name}'s starters in their last {len(recent)} games in our "
                           f"records, newest first: {', '.join(recent)}.")
        out.append("- TONIGHT'S STARTING GOALIES ARE NOT CONFIRMED (teams announce at "
                   "warm-ups). Never say who will start.")
    out.append(f"- UNKNOWN to us: {cfg['unknown']}. The bookmaker knows all of these.")
    return "\n".join(out)


def build_prompt(home: str, away: str, facts: dict, markets: dict,
                 sport: str = "ice_hockey", p_home: float = 0.5) -> str:
    cfg = SPORT[sport]
    rec = recommendation(p_home, markets, home, away)
    gap = rec["gap"]
    if gap is None:
        market_read = "No bookmaker price is available for this pick - say so in one short clause."
        book = ""
    elif gap <= -5:
        market_read = (f"The bookmaker is LESS convinced: it rates this pick's chance {abs(gap):.0f} "
                       f"PERCENTAGE points lower than we do. Say so plainly - it is the main reason for caution.")
        book = f" Bookmaker price {rec['odds']}."
    elif gap >= 5:
        market_read = (f"We rate this pick's chance {gap:.0f} PERCENTAGE points higher than the "
                       f"bookmaker's price implies. Say so, without calling it value or a sure thing.")
        book = f" Bookmaker price {rec['odds']}."
    else:
        market_read = ("The bookmaker broadly agrees with this pick (its implied chance is within 5 "
                       "PERCENTAGE points of ours). Say so briefly, without mentioning that threshold.")
        book = f" Bookmaker price {rec['odds']}."
    risk_extra = ("Then say that tonight's starting goalies are not confirmed yet and can swing "
                  "an NHL game." if sport == "ice_hockey" else
                  "Then say that quarterback and injury news is not in these figures and can "
                  "swing an NFL game.")
    tier = rec["tier"].lower()
    return f"""You are an {cfg['name']} analyst. Explain this pick in ONE paragraph of
4 or 5 sentences, under 150 words, in plain English.

MATCH: {home} (home) vs {away} ({cfg['league']})

RECOMMENDATION (already decided - explain it, never change or contradict it):
{rec['team']} to win, overtime counts. {rec['tier'].title()} confidence.{book}

EVIDENCE - every fact you are allowed to use:
{facts_block(home, away, facts, markets, sport)}

Write:
1. Why the evidence points to {rec['team']} - quote the specific figures (rating
   ranks, records, {cfg['unit']} per game, the home/road split, the meetings tally).
2. The market read: {market_read}
3. A RISK: a figure in the evidence that argues against {rec['team']}. {risk_extra}
4. Close by restating the recommendation and its {tier} confidence. Never say
   "no bet", never make it sound stronger than {tier} confidence.

RULES - these matter more than style:
- Do not address the reader or mention who they are; just analyse the game.
- Use ONLY the figures above. Invent NOTHING: no injuries, players, trades,
  coaches, standings, streaks or storylines that are not in the evidence.
- Goalies: you may name a team's listed goalies and quote their numbers, but
  NEVER say or imply who will start tonight ("X gets the start", "expected to
  start", "with X in net") - nobody knows yet.
- If the OPENING WEEK warning is present, say plainly that the form is last
  season's and rosters have changed since.
- Never name a model, rating system, provider or data source. Do not quote our
  win percentages; quote the bookmaker's price, and any gap as "percentage
  points" - never plain "points", which in the NFL means the score.
- No hype: never "lock", "banker", "sure", "guaranteed"."""


# \d*\.\d+ so a save percentage written hockey-style (".895") is checked too.
_FIGURE = re.compile(r"\d+(?:-\d+){1,2}|\d*\.\d+")


def _figures(text: str) -> set:
    out = set()
    for tok in _FIGURE.findall(text):
        if "-" in tok:
            out.add(tuple(int(x) for x in tok.split("-")))
        else:
            out.add(round(float(tok), 3))
    return out


# Phrases that assert tonight's starter. The prompt forbids them; this checks.
_STARTER_CLAIM = re.compile(
    r"\b(gets? the (start|nod)|(expected|projected|likely|set|slated|confirmed) to start|"
    r"will start|starts in (goal|net)|in (net|goal) (tonight|for this))\b", re.I)


def unsupported_figures(text: str, prompt: str) -> set:
    """Scores, records and decimals in the prose that the evidence never gave.

    The prompt forbids invention; this CHECKS it, because the prompt alone let
    through "Florida won two of the last five" and "Boston's 3-2 edge" on the
    first day. A score may be quoted either way round ("won 6-2" for a 2-6 road
    win). It cannot catch a real figure pinned on the wrong team - the
    leader-first tally wording is what guards that.
    """
    allowed = _figures(prompt)
    allowed |= {t[::-1] for t in allowed if isinstance(t, tuple) and len(t) == 2}
    bad = {t for t in _figures(text) if t not in allowed}
    claim = _STARTER_CLAIM.search(text)
    if claim:
        bad.add(f"starter claim: {claim.group(0)!r}")
    return bad


def targets(conn, force: bool) -> list:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    horizon = (now + timedelta(hours=ANALYSE_HOURS)).strftime("%Y-%m-%d %H:%M")
    stale = (now - timedelta(hours=STALE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    return conn.execute(f"""
        SELECT s.id, m.sport, m.home_team, m.away_team, m.kickoff_utc, s.p_home,
               s.facts_json, s.markets_json, s.analysis_at
        FROM shadow_predictions s JOIN matches m ON m.match_id = s.match_id
        WHERE m.sport IN {SPORTS_SQL} AND s.facts_json IS NOT NULL
          AND m.kickoff_utc > ? AND m.kickoff_utc <= ?
          AND (m.live_state IS NULL OR m.live_state = 'pre')
          AND (? OR s.analysis IS NULL OR s.analysis_at < ? OR s.analysis_at < ?)
        ORDER BY m.kickoff_utc
    """, [now.strftime("%Y-%m-%d %H:%M"), horizon, 1 if force else 0, stale,
          PROMPT_SINCE]).fetchall()


def run(force: bool = False, dry_run: bool = False) -> int:
    init_db()
    conn = get_connection()
    rows = targets(conn, force)
    if not rows:
        logger.info("NHL/NFL analysis: nothing due.")
        return 0
    from analysis.gemma_analyst import UNAVAILABLE, complete, check_ollama_running
    if not dry_run and not check_ollama_running():
        logger.warning("NHL/NFL analysis: no AI provider reachable - skipped.")
        return 0
    written = skipped_low = 0
    for r in rows:
        markets = json.loads(r["markets_json"] or "{}")
        # Only SERVED picks get prose (the card shows nothing else); a LOW
        # game would cost a provider call for text nobody sees.
        if recommendation(r["p_home"], markets, r["home_team"], r["away_team"])["tier"] == "LOW":
            skipped_low += 1
            continue
        prompt = build_prompt(r["home_team"], r["away_team"], json.loads(r["facts_json"]),
                              markets, r["sport"], r["p_home"])
        if dry_run:
            print("=" * 78, "\n", prompt)
            continue
        text = None
        for _attempt in range(2):
            draft = complete(prompt)
            if not draft or draft == UNAVAILABLE:
                break
            bad = unsupported_figures(draft, prompt)
            if not bad:
                text = draft.strip()
                break
            logger.warning(f"  {r['home_team']} v {r['away_team']}: figures not in the "
                           f"evidence {sorted(bad)} - rewriting")
        # No analysis beats a wrong one: a failed check clears any older text
        # too, since it was written from older facts.
        conn.execute("UPDATE shadow_predictions SET analysis = ?, analysis_at = ? WHERE id = ?",
                     [text, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), r["id"]])
        if not text:
            conn.commit()
            continue
        conn.commit()
        written += 1
        logger.info(f"  analysed {r['home_team']} v {r['away_team']}")
    conn.close()
    if not dry_run:
        served = len(rows) - skipped_low
        logger.success(f"NHL/NFL analysis: {written} of {served} served picks written "
                       f"({skipped_low} LOW-confidence games skipped - not shown on Picks).")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.exit(run(force=args.force, dry_run=args.dry_run))
