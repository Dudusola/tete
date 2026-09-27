"""espn_ledger.py - fixtures, tip-off times, live state and FINAL SCORES for the
sports that are not association football. ESPN's public scoreboard, no key,
no Cloudflare.

WHAT THIS IS FOR, AND WHAT IT DELIBERATELY IS NOT
-------------------------------------------------
This is step 2 of `docs/basketball.md` §5, and step 2 only: build a GRADEABLE
LEDGER before a single basketball prediction exists. Coverage first, claims
second — the rule bug #16 paid for.

It therefore does four things and refuses a fifth:

    1. inserts fixtures into `matches` under the right `sport`
    2. writes kickoff_utc + live_state (the half of kickoff_times.py that ports)
    3. records FINAL scores into `results` once ESPN marks a game completed
    4. distinguishes "the feed is empty" from "no games are scheduled"

    NOT: odds. NOT: predictions. NOT: markets.

**No odds on purpose.** ESPN does carry an NBA moneyline (probed 17 Sept 2026,
`docs/basketball.md` §4a) but a 2-way market is a different shape from a 3-way
one all the way up: `_extract_odds` gates on `if h and d and a`, the feature
vector is `odds_home/draw/away`, and `odds_is_market` assumes that triple. The
tempting fix is `d = 1.0`, which is inventing a prior — exactly what bug #7
exists to forbid. Basketball needs its own extractor and its own feature
layout, and that is step 4, not this file.

WHY THERE IS NO TEAM MATCHER HERE
---------------------------------
Every other results path in this codebase fuzzy-matches names because someone
else inserted the fixture: Forebet writes "Man Utd", ESPN says "Manchester
United", and a matcher has to bridge them. That matcher is where bug #22 lived
(`Barcelona` vs `Barcelona U20` scoring 0.92) and it sits on a path where a
false positive grades a pick against the WRONG GAME — bug #1 territory.

Here, we insert the fixture ourselves from the same feed that later reports the
score, so the two sides share ESPN's own stable event id. The result is joined
on `match_id`, exactly. No similarity threshold, no near-miss band, no chance of
crossing two fixtures. If a future source starts inserting NBA fixtures too,
that assumption breaks and this file needs the matcher back — so the id scheme
is namespaced (`espnbb_<event id>`) to make a second writer obvious.

NBA CANNOT DRAW, AND THIS FILE ASSERTS IT
-----------------------------------------
Overtime is played until someone wins, so an equal final score is not a draw —
it is bad data. Recording it as outcome='D' would quietly poison a ledger whose
whole purpose is verification, so an equal-score "completed" game is logged and
SKIPPED rather than stored.

`over_2_5` and `btts` are written as NULL, not 0/1. They are association
football semantics: every NBA and NFL game clears 2.5 "goals" and both sides
essentially always score, so filling them would record two facts that are true
by construction and mean nothing. A NULL says "this question does not apply to
this sport"; a 1 says "we checked".

EMPTY IS NOT FAILURE, BUT A DEAD FEED IS
----------------------------------------
Bug #5's rule is that a scraper which got nothing must fail loudly. The NBA has
a five-month off-season, so "0 events" is the CORRECT answer from June to
October and failing on it would make the pipeline red every day for months —
which is bug #14's noise trap, and noise is not harmless: it hid a real outage.

So this distinguishes the two, the way clubelo.py does:
    every request failed          -> exit 1 (the feed is unreachable)
    requests succeeded, 0 events  -> exit 0 (nothing is scheduled)

Usage:
    python scrapers/espn_ledger.py                     # every feed, -7..+3 days
    python scrapers/espn_ledger.py --feed nba
    python scrapers/espn_ledger.py --days-back 45      # backfill results
    python scrapers/espn_ledger.py --dry-run
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests
from loguru import logger
from typing import NamedTuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection
from models.sports import AMERICAN_FOOTBALL, BASEBALL, BASKETBALL, ICE_HOCKEY

# The sport is a PATH SEGMENT above the league slug, which is why the four
# association-football ESPN scrapers could not simply be pointed at a new slug:
# they all hardcode ".../sports/soccer/{league}/...". Same API shape.
BASE = "https://site.api.espn.com/apis/site/v2/sports/{path}/{league}/scoreboard"


class Feed(NamedTuple):
    """One competition, and the three facts that differ between sports.

    `espn_path` is stated, never derived from `sport`: ESPN calls the American
    game "football" and association football "soccer", which is the opposite of
    this schema's vocabulary (see models/sports.py). Deriving it would file NFL
    games into the soccer ledger.
    """
    sport: str            # our matches.sport value
    espn_path: str        # ESPN's URL segment
    slug: str             # ESPN's league slug
    label: str            # what goes in matches.league
    draws_possible: bool  # see the docstring: a per-sport FACT, not a default
    reg_periods: int      # periods in regulation; any linescore past this is OT/SO
    # Baseball: a home side leading after the top of the 9th does not bat, so
    # its line is ONE inning shorter than the away side's (46% of MLB games,
    # July 2025). Anywhere else, unequal lines mean bad data.
    home_may_skip_last: bool = False


# Deliberately narrow. docs/basketball.md researched the NBA and nothing else,
# and every extra competition here is an unvalidated surface accumulating rows
# nobody has checked. WNBA ("wnba"), college basketball
# ("mens-college-basketball") and college football ("college-football") are the
# slugs when there is a reason.
FEEDS = {
    # The NBA plays overtime until someone wins: a tie is impossible.
    "nba": Feed(BASKETBALL, "basketball", "nba", "NBA",
                draws_possible=False, reg_periods=4),
    # The NFL plays ONE overtime period and lets a level game stand as a tie,
    # so a draw here is a real outcome and must be recorded, not discarded.
    "nfl": Feed(AMERICAN_FOOTBALL, "football", "nfl", "NFL",
                draws_possible=True, reg_periods=4),
    # Regular season: 3 periods, 5-min OT, then a shootout, so the FINAL can
    # never be level. But ESPN credits the shootout winner one goal (probed:
    # NYI @ NSH "Final/SO" 2-1, linescores 0-1-0-0-1 v 0-1-0-0-0), so a game
    # that was 1-1 after regulation - a DRAW on a regulation 1X2 - reads as a
    # home win. Roughly one NHL game in five goes past regulation, which is
    # why decided_in / reg_*_goals exist. Playoff OT is sudden death, no SO.
    "nhl": Feed(ICE_HOCKEY, "hockey", "nhl", "NHL",
                draws_possible=False, reg_periods=3),
    # Extra innings until someone wins, so no draws; decided_in 'OT' means
    # extra innings. A rain-shortened game (fewer than 9 innings, official
    # after 5) has no regulation split and keeps NULL regulation fields.
    # The rare historical "tie" (suspended, never resumed) is skipped.
    "mlb": Feed(BASEBALL, "baseball", "mlb", "MLB",
                draws_possible=False, reg_periods=9, home_may_skip_last=True),
}

# ESPN's season.type: 1 preseason, 2 regular season, 3 postseason. Preseason is
# skipped for every sport: split squads and prospects, results that say nothing
# about the teams that will play in October (14 NHL preseason games were in the
# feed on 26 Sept 2026, and NFL preseason fills August). A ledger that grades
# picks must not be diluted by games nobody should bet on.
PRESEASON = 1

# ESPN's competition.type for games that COUNT. Everything else is an
# exhibition: ALLSTAR (NBA/NHL All-Star, NFL Pro Bowl), QRR (the 2025 4 Nations
# Face-Off, national teams). Surveyed across 2016-2025 on 27 Sept 2026. An
# ALLOW-list, not a block-list, on purpose: a new exhibition format arriving
# under a type we have never seen is skipped (and logged) rather than silently
# graded as a real game between invented teams like "Team LeBron".
#   STD regular season and NBA play-in   CC   NBA Cup championship
#   RD16 / QTR / SEMI / FINAL            playoff rounds
#   QUALIFY  NHL 2020 bubble qualifying round: real, bet-on playoff games,
#            caught by this allow-list on its first run.
COUNTING_TYPES = frozenset({"STD", "CC", "RD16", "QTR", "SEMI", "FINAL",
                            "QUALIFY"})

# ...but the type is not reliable on its own: the 2023 NHL All-Star games
# ("Pacific at Central") are typed SEMI, exactly like a conference final. So
# the event's own notes are checked too. Lower-cased substrings.
EXHIBITION_NOTES = ("all-star", "all star", "pro bowl", "rising stars",
                    "face-off", "skills challenge")

# ESPN returns one scoreboard per league per day, so a window is N requests.
# Ten threads is what results_espn.py settled on (sequential took 18 minutes
# there and blew the 15-minute step timeout).
MAX_WORKERS = 10

# Namespaced per sport so a second writer for the same sport would be obvious,
# and so two sports can never mint the same match_id from ESPN's ids (they are
# unique within a sport, not across them).
ID_PREFIX = {BASKETBALL: "espnbb", AMERICAN_FOOTBALL: "espnnfl",
             ICE_HOCKEY: "espnnhl", BASEBALL: "espnmlb"}


def _fetch_day(job) -> dict | None:
    feed, day, session = job
    try:
        resp = session.get(BASE.format(path=feed.espn_path, league=feed.slug),
                           params={"dates": day}, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"ESPN {feed.espn_path}/{feed.slug} {day}: {e}")
        return None


def fetch_window(feeds: list, days_back: int, days_ahead: int) -> tuple[list, int]:
    """(events, successful_requests) across `feeds` for the date window.

    The second element is what separates "feed down" from "off-season" — see
    the module docstring. Callers must check it before treating [] as failure.
    """
    days = [(datetime.now() + timedelta(days=d)).strftime("%Y%m%d")
            for d in range(-days_back, days_ahead + 1)]
    session = requests.Session()
    jobs = [(feed, day, session) for feed in feeds for day in days]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        responses = list(pool.map(_fetch_day, jobs))

    events, ok = [], 0
    for (feed, day, _), data in zip(jobs, responses):
        if data is None:
            continue
        ok += 1
        for ev in data.get("events", []):
            parsed = _parse_event(ev, feed)
            if parsed:
                events.append(parsed)

    # The same game appears under two adjacent UTC dates when it tips off late
    # US evening. Keyed on ESPN's event id, so this is exact, not heuristic.
    seen, uniq = set(), []
    for e in events:
        if e["match_id"] not in seen:
            seen.add(e["match_id"])
            uniq.append(e)
    return uniq, ok


def _parse_event(ev: dict, feed: Feed) -> dict | None:
    """One ESPN event -> our row shape, or None if it is unusable."""
    event_id = ev.get("id")
    if not event_id:
        return None
    if (ev.get("season") or {}).get("type") == PRESEASON:
        return None
    comp = (ev.get("competitions") or [{}])[0]
    status = (comp.get("status") or {}).get("type", {})
    ctype = (comp.get("type") or {}).get("abbreviation")
    if ctype not in COUNTING_TYPES:
        # Known non-counting types, skipped quietly. EXH is not an exhibition
        # in MLB: it is the PLACEHOLDER for a rained-out playoff game (2014
        # ALCS G3, 2016 ALDS G3); the game that was played has its own id.
        if ctype not in ("ALLSTAR", "QRR", "EXH"):
            logger.warning(f"{feed.slug} event {event_id}: unknown competition "
                           f"type {ctype!r} - skipped; add it to "
                           f"COUNTING_TYPES if these games count")
        return None
    notes = " ".join((n.get("headline") or "") for n in comp.get("notes") or []).lower()
    if any(word in notes for word in EXHIBITION_NOTES):
        return None

    teams, periods = {}, {}
    for c in comp.get("competitors", []):
        side = c.get("homeAway")
        name = ((c.get("team") or {}).get("displayName") or "").strip()
        if not side or not name:
            continue
        score = c.get("score")
        try:
            score = int(score) if score is not None else None
        except (TypeError, ValueError):
            score = None
        teams[side] = (name, score)
        # Per-period scores. Only the first `reg_periods` are regulation;
        # anything after is overtime or (NHL) the shootout "period".
        lines = []
        for ls in c.get("linescores") or []:
            try:
                lines.append(int(ls.get("value")))
            except (TypeError, ValueError):
                lines = None
                break
        periods[side] = lines
    if set(teams) != {"home", "away"}:
        return None

    # How it was decided, and the score at the end of regulation. Left NULL
    # rather than guessed whenever the linescores are missing or do not add up
    # to the final - a wrong regulation score is worse than none (bug #1).
    decided_in = reg_home = reg_away = None
    hl, al = periods.get("home"), periods.get("away")
    fh, fa = teams["home"][1], teams["away"][1]
    # Home skipped its last half-inning: only legitimate in regulation (in
    # extras the away side batted last from level, so home cannot be ahead
    # without batting) and only when home is winning.
    skipped = (feed.home_may_skip_last and hl and al
               and len(hl) == len(al) - 1 == feed.reg_periods - 1
               and fh is not None and fa is not None and fh > fa)
    if (status.get("completed") and hl and al
            and (len(hl) == len(al) or skipped)
            and len(al) >= feed.reg_periods and sum(hl) == fh and sum(al) == fa):
        reg_home = sum(hl[:feed.reg_periods])
        reg_away = sum(al[:feed.reg_periods])
        if len(al) == feed.reg_periods:
            decided_in = "REG"
        elif (status.get("shortDetail") or "").upper().endswith("/SO"):
            decided_in = "SO"
        else:
            decided_in = "OT"
        # A game only goes past regulation if regulation ended LEVEL. ESPN's
        # period split is occasionally wrong (6 of 20,625 NHL games since
        # 2010, e.g. NJD 5-2 OTT "OT" with periods summing to 5-2 in
        # regulation), and a wrong regulation score is worse than none.
        if decided_in != "REG" and reg_home != reg_away:
            decided_in = reg_home = reg_away = None

    kickoff_utc = (ev.get("date") or "").replace("T", " ").replace("Z", "")[:16]
    return {
        "match_id": f"{ID_PREFIX[feed.sport]}_{event_id}",
        "sport": feed.sport,
        "draws_possible": feed.draws_possible,
        "league": feed.label,
        "espn_league": feed.slug,
        "home_team": teams["home"][0],
        "away_team": teams["away"][0],
        "match_date": (ev.get("date") or "")[:10],
        "kickoff_utc": kickoff_utc or None,
        # ESPN's own three-state field, the same one kickoff_times.py stores.
        # It is a SNAPSHOT, not a live feed -- api/server.py ages it past
        # LIVE_MAX_HOURS (bug #13). That expiry is football-shaped (3.5h); a
        # basketball equivalent belongs with the basketball serving path.
        "live_state": (status.get("state") or None),
        "completed": bool(status.get("completed")),
        "home_score": teams["home"][1],
        "away_score": teams["away"][1],
        "decided_in": decided_in,
        "reg_home": reg_home,
        "reg_away": reg_away,
    }


def store(events: list, dry_run: bool = False) -> dict:
    """Upsert fixtures, then record finals. Returns counts for the caller."""
    counts = {"fixtures": 0, "updated": 0, "results": 0, "ties_skipped": 0}
    if not events:
        return counts
    conn = get_connection()
    for e in events:
        if dry_run:
            logger.info(f"  [dry-run] {e['match_date']} {e['home_team']} v "
                        f"{e['away_team']} ({e['live_state']}) "
                        f"{e['home_score']}-{e['away_score']}")
            continue

        cur = conn.execute("""
            INSERT OR IGNORE INTO matches
            (match_id, sport, league, home_team, away_team, match_date,
             kickoff_time, status, kickoff_utc, live_state, live_checked_at,
             espn_league)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [e["match_id"], e["sport"], e["league"], e["home_team"],
              e["away_team"], e["match_date"],
              (e["kickoff_utc"] or " ")[11:16] or None,
              "finished" if e["completed"] else "upcoming",
              e["kickoff_utc"], e["live_state"],
              datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
              e["espn_league"]])
        if cur.rowcount:
            counts["fixtures"] += 1
        else:
            # Already known: refresh the volatile fields. live_state is the
            # whole reason this runs more than once for the same fixture.
            conn.execute("""
                UPDATE matches
                   SET kickoff_utc = ?, live_state = ?, live_checked_at = ?,
                       status = CASE WHEN ? THEN 'finished' ELSE status END
                 WHERE match_id = ?
            """, [e["kickoff_utc"], e["live_state"],
                  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                  1 if e["completed"] else 0, e["match_id"]])
            counts["updated"] += 1

        if not e["completed"]:
            continue
        hs, as_ = e["home_score"], e["away_score"]
        if hs is None or as_ is None:
            logger.warning(f"  completed but no score: {e['match_id']}")
            continue
        if hs == as_ and not e["draws_possible"]:
            # This sport plays overtime until someone wins, so an equal final
            # score is bad data, not a draw. Recording it would put a fake
            # outcome in the ledger (bug #1).
            counts["ties_skipped"] += 1
            logger.warning(f"  equal final score where a draw is impossible, "
                           f"skipped: {e['match_id']} {hs}-{as_}")
            continue
        outcome = "D" if hs == as_ else ("H" if hs > as_ else "A")
        cur = conn.execute("""
            INSERT OR IGNORE INTO results
            (match_id, home_goals, away_goals, outcome, over_2_5, btts,
             decided_in, reg_home_goals, reg_away_goals)
            VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?)
        """, [e["match_id"], hs, as_, outcome,
              e["decided_in"], e["reg_home"], e["reg_away"]])
        if not cur.rowcount and e["decided_in"]:
            # Finals recorded before these columns existed: fill them in once,
            # never overwrite a value already there.
            conn.execute("""
                UPDATE results SET decided_in = ?, reg_home_goals = ?,
                                   reg_away_goals = ?
                 WHERE match_id = ? AND decided_in IS NULL
            """, [e["decided_in"], e["reg_home"], e["reg_away"], e["match_id"]])
        if cur.rowcount:
            counts["results"] += 1
            logger.info(f"  scored: {e['home_team']} {hs}-{as_} "
                        f"{e['away_team']}")
    conn.commit()
    conn.close()
    return counts


def run(feed_names: list | None = None, days_back: int = 7,
        days_ahead: int = 3, dry_run: bool = False) -> int:
    names = feed_names or sorted(FEEDS)
    feeds = [FEEDS[n] for n in names]
    events, ok_requests = fetch_window(feeds, days_back, days_ahead)

    if ok_requests == 0:
        logger.error(f"Every ESPN request failed for {', '.join(names)} "
                     f"- feed unreachable.")
        return 1

    if not events:
        # Correct and expected for most of the year. See the module docstring:
        # failing here would make the pipeline red all summer.
        logger.info(f"ESPN reachable ({ok_requests} responses) for "
                    f"{', '.join(names)}, 0 games scheduled in the window "
                    f"- off-season or rest day.")
        return 0

    counts = store(events, dry_run=dry_run)
    logger.success(
        f"{'+'.join(n.upper() for n in names)}: {len(events)} events -> "
        f"{counts['fixtures']} new fixtures, {counts['updated']} refreshed, "
        f"{counts['results']} finals recorded"
        + (f", {counts['ties_skipped']} impossible ties SKIPPED"
           if counts["ties_skipped"] else "")
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fixtures + results from ESPN for non-soccer sports")
    ap.add_argument("--feed", action="append", choices=sorted(FEEDS),
                    help="which competition (repeatable; default: all)")
    ap.add_argument("--days-back", type=int, default=7,
                    help="how far back to look for finals (default 7)")
    ap.add_argument("--days-ahead", type=int, default=3,
                    help="how far ahead to pull fixtures (default 3)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be written, touch nothing")
    args = ap.parse_args()
    return run(args.feed, args.days_back, args.days_ahead, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
