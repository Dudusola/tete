"""nhl_analyst.py - written analysis for upcoming NHL games. PRIVATE PREVIEW.

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
from models.nhl_markets import AGREE_POINTS, market_label, verdict

ANALYSE_HOURS = 36
STALE_HOURS = 18        # rewrite if older - prices and form move



def _rec(r: dict) -> str:
    if not r or not r.get("n"):
        return "no games"
    s = f"{r['w']}-{r['l']}-{r['otl']} (W-L-OTL) in {r['n']} games"
    if r.get("playoff"):
        s += f", {r['playoff']} of them playoff games"
    return s + f", scoring {r['gf_pg']:.2f} and conceding {r['ga_pg']:.2f} per game"


def _streak(s: str | None) -> str:
    """'W3' -> 'Won their last 3.' - the code is jargon the writer repeats."""
    if not s:
        return ""
    n = int(s[1:])
    if s[0] == "W":
        return f"Won their last {n}." if n > 1 else "Won their last game."
    return (f"Lost their last {n} (overtime losses included)." if n > 1
            else "Lost their last game.")



def _ordinal(n) -> str:
    if n is None:
        return "unranked"
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def facts_block(home: str, away: str, facts: dict, markets: dict) -> str:
    h, a = facts["home"], facts["away"]
    out = []
    if h.get("new_season") or a.get("new_season"):
        out.append(f"!! OPENING WEEK: every form figure below is from LAST season "
                   f"(last games played: {home} on {h.get('last_game')}, {away} on {a.get('last_game')}). "
                   f"Summer trades, signings and injuries are NOT in these numbers - say so.")
    out.append(f"- Power rating: {home} {_ordinal(h.get('rank'))} of {h.get('of')}, "
               f"{away} {_ordinal(a.get('rank'))} of {a.get('of')} (home ice is worth a little extra).")
    out.append(f"- {home}, last {h['last10']['n']}: {_rec(h['last10'])}. {_streak(h.get('streak'))}")
    out.append(f"- {away}, last {a['last10']['n']}: {_rec(a['last10'])}. {_streak(a.get('streak'))}")
    out.append(f"- {home} in their last home games: {_rec(h['venue10'])}.")
    out.append(f"- {away} in their last road games: {_rec(a['venue10'])}.")
    if h.get("season", {}).get("n") or a.get("season", {}).get("n"):
        out.append(f"- This season so far: {home} {_rec(h['season'])}; {away} {_rec(a['season'])}.")
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
        # The tally is COMPUTED, not left to the writer: given the list alone,
        # Gemini wrote "Florida won two of the last five" when they won three
        # (29 Sep 2026) - bug #28's lesson that counts must be handed over.
        # Leader FIRST: "Boston won 2, Rangers won 3" came back as "Boston's
        # 3-2 edge". A sentence that names the leader cannot be flipped that way.
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
    if mv.get("puck_line"):
        m = mv["puck_line"]
        out.append(f"- Bookmaker puck line: {home} {m['home_line']:+} at {m['now'][0]}, "
                   f"{away} {-m['home_line']:+} at {m['now'][1]}"
                   f"{' (the home line has moved from ' + format(m['open_home_line'], '+') + ')' if m.get('open_home_line') not in (None, m['home_line']) else ''}.")
    if mv.get("total"):
        m = mv["total"]
        out.append(f"- Bookmaker total: {m['line']} goals, over {m['now'][0]} / under {m['now'][1]}"
                   f"{' (opened at ' + str(m['open_line']) + ')' if m.get('open_line') not in (None, m['line']) else ''}.")

    rows = []
    for it in markets.get("items", []):
        if it.get("market_p") is None:
            continue
        group, sel = market_label(it["key"], home, away)
        gap = (it["p"] - it["market_p"]) * 100
        rows.append((gap, f"  {sel} [{group}] at {it['odds']}: OUR EDGE {gap:+.1f} points"))
    if rows:
        out.append("- Where our numbers differ from the bookmaker's (positive = we rate it "
                   "likelier than the price implies):")
        out.extend(r for _, r in sorted(rows, key=lambda r: -r[0]))
    out.append("- UNKNOWN to us: starting goalies, injuries, line combinations, travel. "
               "The bookmaker knows all of these.")
    return "\n".join(out)


def build_prompt(home: str, away: str, facts: dict, markets: dict) -> str:
    call, _ = verdict(markets, facts, home, away)
    return f"""You are an ice hockey analyst. Analyse this game in ONE paragraph of
4 or 5 sentences, under 150 words, in plain English.

MATCH: {home} (home) vs {away}

EVIDENCE - every fact you are allowed to use:
{facts_block(home, away, facts, markets)}

VERDICT (already decided - explain it, do not change it): {call}

Write:
1. Which side the evidence favours and why - quote the specific figures (rating
   ranks, W-L-OTL records, goals per game, the home/road split, the meetings
   tally).
2. The market read: where our edge is largest, in points, and in which market -
   or, if every edge is under {AGREE_POINTS:.0f} points, that we broadly agree with the price.
3. A RISK: a figure in the evidence that argues against the verdict. Then say
   that starting goalies are not known yet and can swing an NHL game.
4. The VERDICT above, in substance. You may soften it to "no bet" if the risk
   you found is serious; never make it stronger.

RULES - these matter more than style:
- Do not address the reader or mention who they are; just analyse the game.
- Use ONLY the figures above. Invent NOTHING: no injuries, goalies, trades,
  coaches, standings, streaks or storylines that are not in the evidence.
- If the OPENING WEEK warning is present, say plainly that the form is last
  season's and rosters have changed since.
- Never name a model, rating system, provider or data source. Do not quote our
  win percentages; quote bookmaker prices and edges in points instead.
- When you mention the puck line or a 60-minute result, explain it in a few
  words the first time (e.g. "the -1.5 puck line, a win by two or more").
- No hype: never "lock", "banker", "sure", "guaranteed"."""


_FIGURE = re.compile(r"\d+(?:-\d+){1,2}|\d+\.\d+")


def _figures(text: str) -> set:
    out = set()
    for tok in _FIGURE.findall(text):
        if "-" in tok:
            out.add(tuple(int(x) for x in tok.split("-")))
        else:
            out.add(round(float(tok), 2))
    return out


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
    return {t for t in _figures(text) if t not in allowed}


def targets(conn, force: bool) -> list:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    horizon = (now + timedelta(hours=ANALYSE_HOURS)).strftime("%Y-%m-%d %H:%M")
    stale = (now - timedelta(hours=STALE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    return conn.execute("""
        SELECT s.id, m.home_team, m.away_team, m.kickoff_utc,
               s.facts_json, s.markets_json, s.analysis_at
        FROM shadow_predictions s JOIN matches m ON m.match_id = s.match_id
        WHERE m.sport = 'ice_hockey' AND s.facts_json IS NOT NULL
          AND m.kickoff_utc > ? AND m.kickoff_utc <= ?
          AND (m.live_state IS NULL OR m.live_state = 'pre')
          AND (? OR s.analysis IS NULL OR s.analysis_at < ?)
        ORDER BY m.kickoff_utc
    """, [now.strftime("%Y-%m-%d %H:%M"), horizon, 1 if force else 0, stale]).fetchall()


def run(force: bool = False, dry_run: bool = False) -> int:
    init_db()
    conn = get_connection()
    rows = targets(conn, force)
    if not rows:
        logger.info("NHL analysis: nothing due.")
        return 0
    from analysis.gemma_analyst import UNAVAILABLE, complete, check_ollama_running
    if not dry_run and not check_ollama_running():
        logger.warning("NHL analysis: no AI provider reachable - skipped.")
        return 0
    written = 0
    for r in rows:
        prompt = build_prompt(r["home_team"], r["away_team"],
                              json.loads(r["facts_json"]), json.loads(r["markets_json"] or "{}"))
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
        logger.success(f"NHL analysis: {written} of {len(rows)} games written (private preview).")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.exit(run(force=args.force, dry_run=args.dry_run))
