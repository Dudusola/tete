"""
booking_codes.py - Decode a bookmaker booking code into its legs.

SportyBet Nigeria exposes the slip behind a share code at:
    https://www.sportybet.com/api/ng/orders/share/<CODE>
(unofficial - same endpoint their own site uses to load a shared slip).
Invalid code -> {"bizCode": 19000, "message": "The code is invalid."}
Valid code   -> {"bizCode": 10000, "data": {"outcomes": [...], ...}}

This module is defensive: bookmaker payloads are undocumented and can change,
so unknown shapes are logged raw to data/raw/ for inspection instead of
crashing. MSport support is stubbed until we capture a real payload.

Usage:
    python scrapers/booking_codes.py SPORTYBET_CODE
"""

import json
import os
import sys
import time as _time
from datetime import datetime

import requests
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SPORTYBET_URL = "https://www.sportybet.com/api/ng/orders/share/{code}"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}


class BookingCodeError(Exception):
    """User-presentable decode failure."""


def _dump_raw(provider: str, code: str, payload) -> str:
    """Save an unrecognized payload so we can extend the parser."""
    os.makedirs("data/raw", exist_ok=True)
    path = f"data/raw/booking_{provider}_{code}_{datetime.now():%Y%m%d%H%M%S}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


def _ts_to_date(ms) -> str | None:
    """SportyBet uses epoch milliseconds for kickoff times."""
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return None


def _parse_sportybet_outcome(o: dict) -> dict:
    """One slip leg. Field names follow SportyBet's share payload; every
    access is defensive because the shape is unofficial."""
    market_desc = selection_desc = odds = None
    market_id = specifier = outcome_id = None
    for mkt in o.get("markets") or []:
        market_desc = mkt.get("desc") or mkt.get("title")
        # Keep the raw IDs. SportyBet, MSport and football.com run the SAME
        # platform on Betradar ids, so a slip decoded from one can be rebuilt
        # on another EXACTLY — no fuzzy team/market matching, nothing lost.
        # That is what makes code conversion between them trustworthy.
        market_id = mkt.get("id")
        specifier = mkt.get("specifier") or ""
        for out in mkt.get("outcomes") or []:
            selection_desc = out.get("desc")
            odds = out.get("odds")
            outcome_id = out.get("id")
            break
        break
    return {
        "market_id": market_id,
        "specifier": specifier,
        "outcome_id": outcome_id,
        "home_team": o.get("homeTeamName") or o.get("homeTeam"),
        "away_team": o.get("awayTeamName") or o.get("awayTeam"),
        "league": ((o.get("sport") or {}).get("category") or {}).get("name")
                  or o.get("categoryName"),
        "tournament": ((o.get("sport") or {}).get("category") or {})
                      .get("tournament", {}).get("name") if o.get("sport") else None,
        "match_date": _ts_to_date(o.get("estimateStartTime")),
        "market": market_desc,
        "selection": selection_desc,
        "odds": float(odds) if odds not in (None, "") else None,
        "event_id": o.get("eventId"),
    }


def _decode_sporty_like(code: str, url_template: str, label: str) -> list[dict]:
    """Decode a share code from any site running the SportyBet platform.

    SportyBet and football.com serve byte-identical payloads from the same path
    (verified: football.com resolved a SportyBet code to the same shareCode and
    the same four legs), so they share one parser instead of two that can drift.
    """
    code = code.strip().upper()
    if not code or len(code) > 20:
        raise BookingCodeError("That doesn't look like a booking code.")
    try:
        r = requests.get(url_template.format(code=code),
                         headers=HEADERS, timeout=25)
        payload = r.json()
    except requests.RequestException as e:
        raise BookingCodeError(f"Couldn't reach {label}: {e}") from e
    except ValueError as e:
        raise BookingCodeError(f"{label} returned a non-JSON response "
                               "(possibly blocked).") from e

    biz = payload.get("bizCode")
    if biz != 10000:
        msg = payload.get("message") or f"{label} error (bizCode {biz})"
        raise BookingCodeError(msg)

    data = payload.get("data") or {}
    outcomes = data.get("outcomes") or []
    if not outcomes:
        path = _dump_raw(label, code, payload)
        raise BookingCodeError(
            f"Valid code but unrecognized slip format - raw payload saved "
            f"to {path} so the parser can be extended.")

    legs = []
    for o in outcomes:
        leg = _parse_sportybet_outcome(o)
        if leg["home_team"] and leg["away_team"]:
            legs.append(leg)

    if not legs:
        path = _dump_raw(label, code, payload)
        raise BookingCodeError(
            f"Slip found but no legs could be parsed - raw payload saved "
            f"to {path}.")
    logger.success(f"Decoded {label} code {code}: {len(legs)} legs")
    return legs


def decode_sportybet(code: str) -> list[dict]:
    """Booking code -> list of leg dicts. Raises BookingCodeError on failure."""
    return _decode_sporty_like(code, SPORTYBET_URL, "SportyBet")


# ─── Booking-code GENERATION (SportyBet) ────────────────────────────────────
# Confirmed working 2026-07-16: POST /api/ng/orders/share with
# {"selections":[{"eventId","marketId","specifier","outcomeId"}]} returns a
# shareCode - anonymously, same endpoint their "Book Bet" button uses.

EVENTS_URL = "https://www.sportybet.com/api/ng/factsCenter/pcUpcomingEvents"
SHARE_POST_URL = "https://www.sportybet.com/api/ng/orders/share"
# Hard ceiling so a feed glitch can never make us fetch forever.
MAX_PAGES = int(os.getenv("SPORTY_MAX_PAGES", "40"))

GEN_MARKET_IDS = "1,10,11,16,18,29"   # 1X2, DC, DNB, Asian Handicap, O/U, GG/NG

from difflib import SequenceMatcher

# The shared fixture matcher. Imported under an explicit name so the local
# _sim (used for MARKET and OUTCOME description matching, where plain string
# similarity is the right tool) can't be confused for it again.
from models.team_match import team_sim as _shared_team_sim
from models.team_match import SIM_MEAN as _SHARED_SIM_MEAN
from models.team_match import pair_score as _pair_score


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


_EVENTS_CACHE = {"at": 0.0, "events": []}      # football (the original cache)
_SPORT_CACHES = {}                               # sport_id -> same shape
_EVENTS_TTL = 300   # odds move, but event/market IDs are stable - 5 min is safe


_REFRESHING = set()                  # sport ids with a background refresh running


def fetch_sportybet_events(pages: int = 0, page_size: int = 100,
                           sport_id: str = "sr:sport:1",
                           market_ids: str = GEN_MARKET_IDS,
                           _background: bool = False) -> list[dict]:
    """Flatten SportyBet's upcoming events (with main markets) for one sport.
    Football by default; NHL/NFL pass their sport id and FULL-GAME market ids
    (US_SPORTS below). Each event carries `_tournament` so a mapper can refuse
    an event outside the league it wants (an AHL or college game).

    pages=0 (default) means ASK the feed how many there are.

    This used to be a hardcoded 10 pages, which was right when written and
    silently wrong later: the feed grew past it, so whole competitions fell off
    the end. Legs then came back "unmapped" and users saw games dropped from
    their booking code for no visible reason — Portland Timbers v Club Puebla
    and Austin FC v Club Tijuana were both present in the feed, just beyond
    page 10. The response carries data.totalNum, so the page count is derived
    rather than guessed and cannot rot again.

    Cached for a few minutes so repeated bookings don't refetch the whole list.
    """
    import math
    import time as _time
    cache = (_EVENTS_CACHE if sport_id == "sr:sport:1"
             else _SPORT_CACHES.setdefault(sport_id, {"at": 0.0, "events": []}))
    if cache["events"] and _time.time() - cache["at"] < _EVENTS_TTL:
        return cache["events"]
    # STALE-WHILE-REVALIDATE (7 Oct 2026). A full fetch is ~20 pages and took
    # ~15-20 s; with a 5-minute TTL, the first visitor after every expiry waited
    # for it, on Render's free CPU on top of the matching. Once we HAVE a list,
    # an expired one is served instantly and refreshed in a background thread;
    # only the very first fetch after a boot blocks (and the API prewarms that).
    if cache["events"] and not _background:
        if sport_id not in _REFRESHING:
            _REFRESHING.add(sport_id)
            import threading

            def _refresh():
                try:
                    fetch_sportybet_events(pages, page_size, sport_id, market_ids,
                                           _background=True)
                finally:
                    _REFRESHING.discard(sport_id)
            threading.Thread(target=_refresh, daemon=True).start()
        return cache["events"]

    def fetch_page(page: int) -> list[dict]:
        d = None
        for attempt in (1, 2):   # one retry - a single failed page must not
            try:                 # truncate (and then cache) a partial list
                r = requests.get(EVENTS_URL, headers=HEADERS, timeout=25,
                                 params={"sportId": sport_id,
                                         "marketId": market_ids,
                                         "pageSize": page_size,
                                         "pageNum": page, "option": 1})
                d = r.json()
                break
            except (requests.RequestException, ValueError) as e:
                logger.warning(f"SportyBet events page {page} "
                               f"attempt {attempt} failed: {e}")
        if not d or d.get("bizCode") != 10000:
            return []
        out = []
        for t in (d.get("data") or {}).get("tournaments") or []:
            for e in t.get("events") or []:
                e["_tournament"] = t.get("name")
                out.append(e)
        return out

    # Pages fetched in parallel (sequential took 60s+ and slip requests
    # block on it); pages past the end of the listing just come back empty.
    #
    # HARD WALL-CLOCK BUDGET: 10 pages x 25s timeout x 2 attempts can run past
    # 100s. On a laptop that is merely slow; behind a hosted platform the
    # request is killed and /api/slip returns 502 — which is exactly what
    # happened on Render the first time the cache was cold. Odds are
    # decoration (the docstring says so), so we take whatever pages finished
    # inside the budget and move on rather than hanging the whole endpoint.
    from concurrent.futures import ThreadPoolExecutor, wait

    # Ask the feed how big it is instead of guessing. Page 1 is fetched on its
    # own for this; it is also a real page, so nothing is wasted.
    if not pages:
        first = fetch_page(1)
        total = 0
        try:
            probe = requests.get(EVENTS_URL, headers=HEADERS, timeout=25,
                                 params={"sportId": sport_id,
                                         "marketId": market_ids,
                                         "pageSize": page_size,
                                         "pageNum": 1, "option": 1})
            total = ((probe.json().get("data") or {}).get("totalNum")) or 0
        except (requests.RequestException, ValueError):
            pass
        # +2 pages of slack: totalNum counts events, and events-per-page varies
        # because they arrive grouped by tournament.
        pages = min(math.ceil(total / page_size) + 2, MAX_PAGES) if total else 12
        logger.info(f"SportyBet feed reports {total} events -> fetching {pages} pages")
    else:
        first = None

    budget = float(os.getenv("SPORTY_BUDGET_SECONDS", "25"))
    # NOT a `with` block: ThreadPoolExecutor.__exit__ calls shutdown(wait=True),
    # which blocks until in-flight pages finish — so the budget would leak by up
    # to one request timeout. shutdown(wait=False) lets stragglers die in the
    # background (daemon-ish) while we answer now.
    pool = ThreadPoolExecutor(max_workers=8)   # more pages now, same budget
    try:
        futures = [pool.submit(fetch_page, p) for p in range(1, pages + 1)]
        done, pending = wait(futures, timeout=budget)
        if pending:
            logger.warning(
                f"SportyBet events: {len(done)}/{len(futures)} pages within "
                f"{budget}s budget — continuing with partial odds"
            )
        per_page = [f.result() for f in done if not f.cancelled()]
        if first:
            per_page.append(first)
        complete = not pending
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    # Page 1 can appear twice (probe + parallel sweep); events carry a stable
    # eventId, so de-duplicate on it rather than shipping doubles to the mapper.
    seen, events = set(), []
    for page_events in per_page:
        for e in page_events:
            eid = e.get("eventId") or id(e)
            if eid not in seen:
                seen.add(eid)
                events.append(e)
    logger.info(f"SportyBet events feed ({sport_id}): {len(events)} upcoming matches")
    # Only cache a COMPLETE fetch. Caching a partial one would serve degraded
    # odds for the whole TTL, and slip building treats an unpriced leg as a
    # signal (it swaps legs SportyBet won't price) — so incomplete data doesn't
    # just look worse, it changes which legs get picked.
    if events and complete:
        cache.update(at=_time.time(), events=events)
    return events


def _team_norm(s: str) -> str:
    """Strip club furniture (FC/SC/CF/IF/…) so 'Vaalerenga IF' ~ 'Valerenga'.

    Kept for callers that want the bare normalised string; the MATCHING is done
    by models.team_match.team_sim (see _teams_match below).
    """
    import re
    s = (s or "").lower()
    s = re.sub(r"\b(fc|cf|sc|ec|if|fk|afc|cd|ca|club|de|sk)\b", " ", s)
    return " ".join(s.split())


def _teams_match(a: str, b: str) -> float:
    """Similarity between our team name and a bookmaker's, 0.0-1.0.

    Delegates to the shared matcher. This used to be four lines of its own:

        if na in nb or nb in na:
            return 1.0
        return _sim(na, nb)

    Bare containment, returning a PERFECT score, with no first-word guard and
    no squad-qualifier check. That is the loosest matcher in the codebase, and
    it sat on the one code path where a false positive is not a display bug:

        "Milan"     inside "Inter Milan"      -> 1.0   (different club)
        "Barcelona" inside "Barcelona U20"    -> 1.0   (academy fixture)
        "Chelsea"   inside "Chelsea Women"    -> 1.0   (different competition)

    _find_outcome pairs two of these and accepts a mean above 0.72, so a single
    1.0 only needs 0.44 from the other side to clear the bar. The result is a
    booking code — a real bet, at a real bookmaker, on someone's money, placed
    on a fixture we never predicted. models.team_match.team_sim requires the
    first word to agree, clamps multi-word names whose distinguishing tokens
    disagree, and rejects mismatched squad qualifiers outright.

    Bug #12's rule again: one implementation, read by everyone who needs it.
    """
    return _shared_team_sim(a, b)


def _wanted_outcome(leg: dict) -> tuple | None:
    """Deterministically translate one of OUR legs to (their_market_desc,
    specifier_line, their_outcome_desc). Returns None when the market isn't
    bookable via the share API (Handicap, Result & Goals combos) — better an
    honest skip than a wrong bet slipped into someone's ticket."""
    market = leg.get("market") or ""
    sel = leg.get("selection") or ""
    home, away = leg.get("home_team") or "", leg.get("away_team") or ""
    has_home = home and home in sel
    has_away = away and away in sel
    has_draw = "Draw" in sel

    if market == "Match Result":
        if has_home:
            return ("1x2", None, "home")
        if has_away:
            return ("1x2", None, "away")
        return ("1x2", None, "draw")

    if market == "Double Chance":
        if has_home and has_draw:
            return ("double chance", None, "home or draw")
        if has_home and has_away:
            return ("double chance", None, "home or away")
        if has_draw and has_away:
            return ("double chance", None, "draw or away")
        return None

    if market == "Draw No Bet":
        if has_home:
            return ("draw no bet", None, "home")
        if has_away:
            return ("draw no bet", None, "away")
        return None

    if market.startswith("Over/Under"):
        import re
        m = re.search(r"(\d+\.5)", sel) or re.search(r"(\d+\.5)", market)
        if not m:
            return None
        side = "over" if sel.strip().lower().startswith("over") else "under"
        return ("over/under", m.group(1), side)

    if market == "Both Teams To Score":
        return ("gg/ng", None, "yes" if "Yes" in sel else "no")

    if market == "Handicap":
        # Ours: "<Team> +1.5" / "<Team> -1.5". SportyBet market 16 states the
        # line from the HOME side: hcp=-1.5 means Home(-1.5)/Away(+1.5).
        import re
        m = re.search(r"([+-])(\d+(?:\.\d+)?)\s*$", sel)
        if not m or not (has_home or has_away):
            return None
        sign, num = m.group(1), m.group(2)
        if has_home:
            home_line = f"{'-' if sign == '-' else ''}{num}"
            return ("asian handicap", f"hcp={home_line}", "home")
        # away team's line stated from home side is the opposite sign
        home_line = f"{'-' if sign == '+' else ''}{num}"
        return ("asian handicap", f"hcp={home_line}", "away")

    return None   # Result & Goals combos: not offered for booking


# ── SportyBet's own spellings (7 Oct 2026) ────────────────────────────────
# Boss: "some matches are not being picked with SportyBet". Measured: 4 of 5
# unbookable upcoming picks WERE on SportyBet under decorated names -
# "SE Palmeiras SP v EC Bahia BA", "CSD Independiente del Valle v SD Aucas",
# "PAOK v PAE PS Kalamata", "Betis v Osasuna" (ours: "Real Betis"). These
# helpers clean SPORTYBET's side only, inside this mapper: the shared matcher
# (models/team_match.py) is untouched, so the odds/kickoff paths behave as before.
#
# Club-form prefixes never distinguish two clubs, so they are dropped. A
# Brazilian STATE code can ("Botafogo FC SP" and "Botafogo FR RJ" are different
# clubs in the same feed), so it is dropped ONLY when the remaining name is
# unique among the feed's Brazilian teams - otherwise it stays and the strict
# pair_score decides, exactly as before.
_CLUB_FORM = {"SE", "EC", "CSD", "SD", "PAE", "PS", "CA", "CR", "CS", "CD", "AD",
              "FC", "FR", "FB", "AC", "SC", "AS", "SV", "KV", "AFC", "CF"}
_BR_STATES = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
              "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
              "SP", "SE", "TO"}
# Ours -> SportyBet, for one-offs no rule should cover ("Real" matters in
# Real Madrid; only Betis drops it on SportyBet).
_SPORTY_ALIASES = {"Real Betis": "Betis"}


def _br_base(name: str) -> str:
    toks = name.split()
    if len(toks) > 1 and toks[-1] in _BR_STATES:
        toks = toks[:-1]
    return " ".join(t for t in toks if t not in _CLUB_FORM).lower()


_UNIQUE_CACHE = {"key": None, "val": set()}


def _sporty_unique_bases(events: list[dict]) -> set:
    """Brazilian base names that belong to exactly ONE SportyBet team name.
    Computed once per events list, not once per leg."""
    key = (id(events), len(events))
    if _UNIQUE_CACHE["key"] == key:
        return _UNIQUE_CACHE["val"]
    seen = {}
    for ev in events:
        if "Brasil" not in (ev.get("_tournament") or ""):
            continue
        for n in (ev.get("homeTeamName"), ev.get("awayTeamName")):
            if n:
                seen.setdefault(_br_base(n), set()).add(n)
    val = {b for b, names in seen.items() if len(names) == 1}
    _UNIQUE_CACHE.update(key=key, val=val)
    return val


_INDEX_CACHE = {"key": None, "home": {}, "away": {}}


def _stems(name: str) -> set:
    """3-letter starts of each normalised word ("Man United" -> man, uni)."""
    from models.team_match import normalise
    return {w[:3] for w in normalise(name).split() if len(w) >= 2}


def _candidate_events(leg: dict, events: list[dict]) -> list[dict]:
    """Same-day events that share a word start with the leg on BOTH sides.

    pair_score demands both teams be similar, and every true match measured
    shares a 3-letter word start per side ("Man United"/"Manchester United",
    "Wolves"/"Wolverhampton", "Kalamata"/"PAE PS Kalamata"). Scoring only those
    took /api/picks from ~1,000 comparisons per pick on a weekend to a handful.
    Indexed once per events list; SportyBet's CLEANED names are indexed too.
    If nothing qualifies, the full same-day list is scored - the old behaviour.
    """
    key = (id(events), len(events))
    if _INDEX_CACHE["key"] != key:
        unique = _sporty_unique_bases(events)
        hi, ai = {}, {}
        for i, ev in enumerate(events):
            t = ev.get("_tournament")
            for nm, idx in ((ev.get("homeTeamName") or "", hi), (ev.get("awayTeamName") or "", ai)):
                for st in _stems(nm) | _stems(_sporty_clean(nm, t, unique)):
                    idx.setdefault(st, set()).add(i)
        _INDEX_CACHE.update(key=key, home=hi, away=ai)
    hs = set().union(*[_INDEX_CACHE["home"].get(x, set()) for x in _stems(leg.get("home_team") or "")] or [set()])
    as_ = set().union(*[_INDEX_CACHE["away"].get(x, set()) for x in _stems(leg.get("away_team") or "")] or [set()])
    both = hs & as_
    day = _same_day_events(leg, events)
    if not both:
        return day
    picked = [events[i] for i in sorted(both)]
    day_ids = {id(e) for e in day}
    return [e for e in picked if id(e) in day_ids] or day


def _same_day_events(leg: dict, events: list[dict]) -> list[dict]:
    """Only events within a day of the leg's kickoff (7 Oct 2026, speed).

    A leg can only be a SportyBet fixture on (about) the same date, so scoring
    all ~1,400 events was pure waste: /api/picks ran ~109k pair comparisons per
    request and timed out on Render. +-1 day absorbs timezone/date-filing
    differences (bug #2's rule). A leg with no date, or no event in the window,
    falls back to ALL events - exactly the old behaviour, never fewer matches.
    """
    from datetime import date, datetime, timedelta, timezone
    raw = (leg.get("kickoff_utc") or leg.get("match_date") or "")[:10]
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        return events
    lo, hi = d - timedelta(days=1), d + timedelta(days=1)
    out = []
    for ev in events:
        ms = ev.get("estimateStartTime")
        if not ms:
            out.append(ev)            # unknown start: keep, never drop silently
            continue
        ed = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()
        if lo <= ed <= hi:
            out.append(ev)
    return out or events


def _sporty_clean(name: str, tournament: str | None, unique_br: set) -> str:
    toks = name.split()
    if "Brasil" in (tournament or "") and len(toks) > 1 and toks[-1] in _BR_STATES:
        if _br_base(name) in unique_br:
            toks = toks[:-1]          # unique without the state -> safe to drop
        else:
            return name               # the state code is what tells two clubs apart
    kept = [t for t in toks if t not in _CLUB_FORM]
    return " ".join(kept) if kept else name


ANCHOR_MINUTES = 90       # same kickoff, within this many minutes
ANCHOR_STRONG = 0.92      # one side must match this well...
ANCHOR_WEAK = 0.35        # ...and the other at least this (abbreviations score low)


def _kickoff_anchored(leg: dict, events: list[dict], unique: set) -> dict | None:
    """Fallback when pair_score finds nothing: the same game under an
    abbreviation ("Manchester United v Tottenham Hotspur" / "Man Utd v
    Tottenham", "Paris Saint-Germain" / "PSG").

    The evidence is the KICKOFF: a team cannot play two games at once, so one
    side matching strongly plus a SportyBet game starting at the same time is
    the same fixture, even when the other name is abbreviated past what any
    string similarity accepts. Guards, each against a real failure mode:
      - the leg must carry an exact kickoff (kickoff_utc), else no fallback;
      - team_sim returns 0 when squad qualifiers differ (Women / U21 / II), so
        "Arsenal" can never anchor "Arsenal Women" at the same hour;
      - the other side must still be loosely similar (>= ANCHOR_WEAK) - an
        unrelated opponent is a different game;
      - EXACTLY one candidate, or nothing: two plausible games is a guess.
    Measured 8 Oct 2026: 65 upcoming fixtures were on SportyBet under such
    names and unmatched; every newly matched pair was checked by hand.
    """
    from datetime import datetime, timezone
    from models.team_match import team_sim
    ko = leg.get("kickoff_utc")
    if not ko or len(ko) < 16:
        return None
    try:
        t0 = datetime.strptime(ko[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    home, away = leg.get("home_team") or "", leg.get("away_team") or ""
    found = []
    for ev in events:
        ms = ev.get("estimateStartTime")
        if not ms:
            continue
        mins = abs((datetime.fromtimestamp(ms / 1000, tz=timezone.utc) - t0).total_seconds()) / 60
        if mins > ANCHOR_MINUTES:
            continue
        t = ev.get("_tournament")
        eh = [ev.get("homeTeamName") or ""]
        ea = [ev.get("awayTeamName") or ""]
        eh.append(_sporty_clean(eh[0], t, unique))
        ea.append(_sporty_clean(ea[0], t, unique))
        hs = max(team_sim(home, x) for x in eh)
        as_ = max(team_sim(away, x) for x in ea)
        if max(hs, as_) >= ANCHOR_STRONG and min(hs, as_) >= ANCHOR_WEAK:
            found.append(ev)
    return found[0] if len(found) == 1 else None


def map_leg_to_sportybet(leg: dict, events: list[dict]) -> dict | None:
    """Find the SportyBet event + market + outcome for one of our slip legs.
    Market/outcome translation is deterministic (no cross-market fuzzing) —
    a leg either maps to exactly what the user picked, or is skipped."""
    want = _wanted_outcome(leg)
    if not want:
        return None
    want_market, want_line, want_outcome = want

    # Find the event. pair_score is the shared matcher's own API: it enforces
    # BOTH the per-side floor (0.55) and the pair mean (0.86), which is the
    # distinction models/team_match.py exists to make — an exact opponent can
    # drag a weak team score over a mean-only bar, so a mean alone is not a
    # sufficient test.
    #
    # This was a bare 0.72 mean with no floor. 0.72 is clearable by one side
    # scoring 1.0 and the other 0.44 — two names with nothing in common — and
    # the old _teams_match handed out 1.0 for bare containment. Raising it to
    # the shared bar costs coverage: legs that would previously have been
    # booked at a 0.72-0.85 confidence now come back unmapped, and the caller
    # already reports that honestly ("the code has N of M games"). That is the
    # right trade on this path. A missed leg is a visible shortfall the user
    # can act on; a wrong leg is their money on a fixture we never predicted.
    best_ev, best_s = None, 0.0
    home = _SPORTY_ALIASES.get(leg.get("home_team") or "", leg.get("home_team") or "")
    away = _SPORTY_ALIASES.get(leg.get("away_team") or "", leg.get("away_team") or "")
    unique = _sporty_unique_bases(events)
    lookup = dict(leg, home_team=home, away_team=away)      # aliases applied
    for ev in _candidate_events(lookup, events):
        eh, ea = ev.get("homeTeamName") or "", ev.get("awayTeamName") or ""
        s = _pair_score(home, away, eh, ea)
        # SportyBet's own spellings carry club-form prefixes and (Brazil) state
        # codes our sources never use - try the cleaned names too, keep the best.
        ch = _sporty_clean(eh, ev.get("_tournament"), unique)
        ca = _sporty_clean(ea, ev.get("_tournament"), unique)
        if (ch, ca) != (eh, ea):
            s2 = _pair_score(home, away, ch, ca)
            if s2 is not None and (s is None or s2 > s):
                s = s2
        if s is not None and s > best_s:
            best_ev, best_s = ev, s
    if not best_ev:
        best_ev = _kickoff_anchored(lookup, events, unique)
    if not best_ev:
        return None

    for mkt in best_ev.get("markets") or []:
        desc = (mkt.get("desc") or "").lower()
        if want_market not in desc and desc not in want_market:
            continue
        spec = mkt.get("specifier") or ""
        if want_line:
            want_spec = want_line if "=" in want_line else f"total={want_line}"
            if spec != want_spec:
                continue
        for out in mkt.get("outcomes") or []:
            if not out.get("isActive"):
                continue
            odesc = (out.get("desc") or "").lower()
            if (odesc == want_outcome
                    or odesc.startswith(want_outcome + " (")   # "home (-1.5)"
                    or odesc.startswith(want_outcome + " ")    # "under 2.5"
                    or _sim(odesc, want_outcome) > 0.85):
                return {
                    "selection": {"eventId": best_ev["eventId"],
                                  "marketId": str(mkt.get("id")),
                                  "specifier": spec,
                                  "outcomeId": str(out.get("id"))},
                    "descr": (f"{best_ev['homeTeamName']} vs "
                              f"{best_ev['awayTeamName']} - "
                              f"{mkt.get('desc')}{' ' + spec if spec else ''}: "
                              f"{out.get('desc')}"),
                    "odds": out.get("odds"),
                }
    return None


# ── NHL / NFL ─────────────────────────────────────────────────────────────
# Probed live 2 Oct 2026. Every id below is the FULL-GAME market, because our
# NHL/NFL prices include overtime (and the NHL shootout). The trap, measured:
# SportyBet's plain hockey "1X2" (1) and "Over/Under" (18) are 60-MINUTE
# markets - its 60-minute Over 6.5 paid 2.60 while the full-game one (412)
# paid 2.20 on the same game. Mapping by market NAME would have booked the
# wrong bet, so these legs map by exact market id, exact line and exact
# outcome id, never by description. The 60-minute 1X2 is mapped ONLY from our
# own 60-Minute Result market, which is the same 60-minute bet.
US_SPORTS = {
    "nhl": {"sport_id": "sr:sport:4", "tournament": "NHL", "market_ids": "406,1,410,412",
            "markets": {"Moneyline": "406", "60-Minute Result": "1",
                        "Puck Line": "410", "Over/Under Goals": "412"}},
    "nfl": {"sport_id": "sr:sport:16", "tournament": "NFL", "market_ids": "219,223,225",
            "markets": {"Moneyline": "219", "Spread": "223", "Over/Under Points": "225"}},
}
_US_LEAGUE = {"NHL": "nhl", "NFL": "nfl"}


def leg_sport(leg: dict) -> str:
    """'nhl' / 'nfl' / 'football' for a slip leg (explicit field, else league)."""
    s = (leg.get("sport") or "").lower()
    if s in US_SPORTS:
        return s
    return _US_LEAGUE.get((leg.get("league") or "").strip(), "football")


def _spec_value(spec: str, key: str):
    if not spec or not spec.startswith(key + "="):
        return None
    try:
        return float(spec.split("=", 1)[1])
    except ValueError:
        return None


def _map_us_leg(leg: dict, events: list[dict], code: str) -> dict | None:
    import re
    cfg = US_SPORTS[code]
    market_id = cfg["markets"].get(leg.get("market") or "")
    if not market_id:
        return None
    home, away = leg.get("home_team") or "", leg.get("away_team") or ""
    sel = (leg.get("selection") or "").strip()

    # What outcome, and on which line, did the user pick?
    want_key = want_line = want_outcome = None
    if market_id in ("406", "219"):                       # winner incl. OT
        want_outcome = "4" if sel == home else "5" if sel == away else None
    elif market_id == "1":                                 # 60 minutes
        want_outcome = {home: "1", "Draw": "2", away: "3"}.get(sel)
    elif market_id in ("410", "223"):                      # handicap incl. OT
        m = re.match(r"^(.*) ([+-]\d+(?:\.\d+)?)$", sel)
        if m and m.group(1) in (home, away):
            line = float(m.group(2))
            want_key = "hcp"
            want_line = line if m.group(1) == home else -line   # stated from HOME
            want_outcome = "1714" if m.group(1) == home else "1715"
    elif market_id in ("412", "225"):                      # total incl. OT
        m = re.match(r"^(Over|Under) (\d+(?:\.\d+)?)$", sel)
        if m:
            want_key, want_line = "total", float(m.group(2))
            want_outcome = "12" if m.group(1) == "Over" else "13"
    if not want_outcome:
        return None

    best_ev, best_s = None, 0.0
    for ev in _same_day_events(leg, events):
        if ev.get("_tournament") != cfg["tournament"]:
            continue                                       # never an AHL/college game
        s = _pair_score(home, away, ev.get("homeTeamName") or "", ev.get("awayTeamName") or "")
        if s is not None and s > best_s:
            best_ev, best_s = ev, s
    if not best_ev:
        return None
    for mkt in best_ev.get("markets") or []:
        if str(mkt.get("id")) != market_id:
            continue
        spec = mkt.get("specifier") or ""
        if want_key is not None and _spec_value(spec, want_key) != want_line:
            continue
        for out in mkt.get("outcomes") or []:
            if str(out.get("id")) == want_outcome and out.get("isActive"):
                return {"selection": {"eventId": best_ev["eventId"], "marketId": market_id,
                                      "specifier": spec, "outcomeId": want_outcome},
                        "descr": (f"{best_ev['homeTeamName']} vs {best_ev['awayTeamName']} - "
                                  f"{mkt.get('desc')}{' ' + spec if spec else ''}: {out.get('desc')}"),
                        "odds": out.get("odds")}
    return None


class _EventsBySport:
    """Fetch each sport's SportyBet events at most once per call, on demand."""
    def __init__(self):
        self._got = {}

    def __call__(self, code: str) -> list[dict]:
        if code not in self._got:
            try:
                if code == "football":
                    self._got[code] = fetch_sportybet_events()
                else:
                    cfg = US_SPORTS[code]
                    self._got[code] = fetch_sportybet_events(sport_id=cfg["sport_id"],
                                                             market_ids=cfg["market_ids"])
            except Exception as e:
                logger.warning(f"SportyBet {code} events failed: {e}")
                self._got[code] = []
        return self._got[code]


def map_leg(leg: dict, events_for) -> dict | None:
    """Route a leg to its own sport's events and mapper."""
    code = leg_sport(leg)
    if code == "football":
        return map_leg_to_sportybet(leg, events_for("football"))
    return _map_us_leg(leg, events_for(code), code)


# MSport accepts the identical POST shape and shares Betradar event/market/
# outcome IDs with SportyBet, so one mapping (from SportyBet's events feed)
# can book on either. Confirmed live 2026-07-16 (returned a real shareCode).
MSPORT_SHARE_POST_URL = "https://www.msport.com/api/ng/orders/share"


def generate_code(legs: list[dict], provider: str = "sportybet") -> dict:
    """Our slip legs -> booking code on the chosen bookmaker.
    legs: [{home_team, away_team, market, selection}]
    Returns {code, share_url, mapped: [...], unmapped: [...]}."""
    provider = (provider or "").lower().strip()
    # Bet9ja runs its own event/market ids, so it cannot use the shared
    # Betradar mapping below — it has a dedicated builder.
    if provider == "bet9ja":
        return generate_bet9ja(legs)
    # SHARE_POST_URLS is the single source of truth for "who can we book on"
    # (it is what /api/booking/providers reports). Hardcoding a subset here is
    # how football.com ended up bookable by convert_code but not by the slip
    # builder -- same platform, same Betradar ids, just a missing dict entry.
    post_url = SHARE_POST_URLS.get(provider)
    if not post_url:
        raise BookingCodeError(
            f"Can't generate codes for '{provider}'. Supported: "
            f"{', '.join(sorted(SHARE_POST_URLS))}")
    events_for = _EventsBySport()
    if not events_for(leg_sport(legs[0]) if legs else "football"):
        raise BookingCodeError("Couldn't load SportyBet's fixture list.")

    mapped, unmapped, selections = [], [], []
    for leg in legs:
        m = map_leg(leg, events_for)
        if m:
            selections.append(m["selection"])
            mapped.append({"leg": leg, "as": m["descr"], "odds": m["odds"]})
        else:
            unmapped.append(leg)

    if not selections:
        raise BookingCodeError(
            "None of the slip legs exist on SportyBet (small leagues often "
            "aren't listed, or games already started).")

    try:
        r = requests.post(post_url, headers={
            **HEADERS, "Content-Type": "application/json"},
            json={"selections": selections}, timeout=25)
        d = r.json()
    except (requests.RequestException, ValueError) as e:
        raise BookingCodeError(f"{provider} booking call failed: {e}") from e

    if d.get("bizCode") != 10000 or not (d.get("data") or {}).get("shareCode"):
        _dump_raw(f"{provider}_gen", "post", d)
        raise BookingCodeError(
            d.get("message") or f"{provider} refused the booking.")

    code = d["data"]["shareCode"]
    logger.success(f"Generated {provider} booking code {code} "
                   f"({len(mapped)} legs, {len(unmapped)} unmapped)")
    return {"code": code,
            "share_url": d["data"].get("shareURL"),
            "mapped": mapped, "unmapped": unmapped}


def generate_sportybet(legs: list[dict]) -> dict:
    return generate_code(legs, "sportybet")


def attach_sporty_odds(legs: list[dict]) -> dict:
    """Best-effort SportyBet odds for slip legs (no booking made).
    legs: [{home_team, away_team, market, selection}].
    Returns {"odds": [float|None per leg, same order], "total_odds": float|None}.
    total_odds multiplies only the legs SportyBet prices; None when none map
    or the feed is unreachable. Never raises — odds are decoration, not data."""
    out = {"odds": [None] * len(legs), "total_odds": None}
    events_for = _EventsBySport()
    total, priced = 1.0, 0
    for i, leg in enumerate(legs):
        try:
            m = map_leg(leg, events_for)
            odds = float(m["odds"]) if m and m.get("odds") else None
        except Exception:
            odds = None
        out["odds"][i] = odds
        if odds:
            total *= odds
            priced += 1
    if priced:
        out["total_odds"] = round(total, 2)
    return out


FOOTBALLCOM_URL = "https://www.football.com/api/ng/orders/share/{code}"


def decode_footballcom(code: str) -> list[dict]:
    """football.com runs the SAME backend as SportyBet.

    Verified 8 Aug 2026: GET football.com/api/ng/orders/share/<SPORTYBET_CODE>
    returned bizCode 10000 with the identical shareCode and the same four legs.
    Same endpoint shape, same payload, same Betradar ids -- so it reuses the
    SportyBet parser rather than duplicating it.
    """
    return _decode_sporty_like(code, FOOTBALLCOM_URL, "football.com")


MSPORT_URL = "https://www.msport.com/api/ng/orders/share/{code}"


def decode_msport(code: str) -> list[dict]:
    """MSport uses the same share-URL pattern as SportyBet but a different
    payload: data.bettableBetSlip[] with event/market/outcome objects.
    (Structure captured from a real code, 2026-07-16.)"""
    code = code.strip().upper()
    if not code or len(code) > 20:
        raise BookingCodeError("That doesn't look like a booking code.")
    try:
        r = requests.get(MSPORT_URL.format(code=code), headers=HEADERS,
                         timeout=25)
        payload = r.json()
    except requests.RequestException as e:
        raise BookingCodeError(f"Couldn't reach MSport: {e}") from e
    except ValueError as e:
        raise BookingCodeError("MSport returned a non-JSON response "
                               "(possibly blocked).") from e

    if payload.get("bizCode") != 10000:
        raise BookingCodeError(payload.get("message")
                               or f"MSport error (bizCode {payload.get('bizCode')})")

    slips = (payload.get("data") or {}).get("bettableBetSlip") or []
    if not slips:
        path = _dump_raw("msport", code, payload)
        raise BookingCodeError(
            f"Valid code but unrecognized slip format - raw payload saved "
            f"to {path} so the parser can be extended.")

    legs = []
    for s in slips:
        ev = s.get("event") or {}
        mkt = s.get("market") or {}
        out = s.get("outcome") or {}
        if not (ev.get("homeTeam") and ev.get("awayTeam")):
            continue
        spec = mkt.get("specifiers") or ""
        legs.append({
            "home_team": ev["homeTeam"],
            "away_team": ev["awayTeam"],
            "league": ev.get("tournament") or ev.get("category"),
            "tournament": ev.get("tournament"),
            "match_date": _ts_to_date(ev.get("startTime")),
            "market": (mkt.get("description") or "") +
                      (f" {spec}" if spec else ""),
            "selection": out.get("description"),
            "odds": float(out["odds"]) if out.get("odds") else None,
            "event_id": ev.get("eventId"),
            # Same Betradar ids SportyBet uses (verified on a real MSport
            # payload: sr:match:..., market id 1, outcome id "1"). Keeping them
            # is what lets an MSport code be converted to SportyBet or
            # football.com exactly, instead of re-matched by team name.
            "market_id": mkt.get("id"),
            "specifier": mkt.get("specifier") or "",
            "outcome_id": out.get("id"),
        })

    if not legs:
        path = _dump_raw("msport", code, payload)
        raise BookingCodeError(f"Slip found but no legs parsed - raw payload "
                               f"saved to {path}.")
    logger.success(f"Decoded MSport code {code}: {len(legs)} legs")
    return legs


BET9JA_COUPON_URL = ("https://sports.bet9ja.com/desktop/feapi/CouponAjax/"
                     "GetBookABetCoupon")


def decode_bet9ja(code: str) -> list[dict]:
    """Bet9ja 'Book a Bet' coupon lookup (endpoint found in their JS bundle,
    verified live July 2026: R='OK' with coupon data, R='ERROR' code 333 for
    an unknown coupon). Defensive parse — the exact leg shape needs a REAL
    code; unknown payloads are dumped to data/raw/ so the parser can be
    extended, same playbook as MSport."""
    code = code.strip().upper()
    if not code or len(code) > 20:
        raise BookingCodeError("That doesn't look like a booking code.")
    try:
        r = requests.get(BET9JA_COUPON_URL, params={"couponCode": code},
                         headers=HEADERS, timeout=25)
        payload = r.json()
    except requests.RequestException as e:
        raise BookingCodeError(f"Couldn't reach Bet9ja: {e}") from e
    except ValueError as e:
        raise BookingCodeError("Bet9ja returned a non-JSON response "
                               "(possibly blocked).") from e

    if payload.get("R") != "OK":
        err = (payload.get("D") or {}).get("ERROR_CODE")
        if err == 333:
            raise BookingCodeError("Bet9ja doesn't recognize that code "
                                   "(expired or mistyped?).")
        raise BookingCodeError(f"Bet9ja error {err}.")

    data = payload.get("D") or {}
    # Legs live under D.O, keyed "<EVENT_ID>$<MARKET>_<SIGN>" — the same key
    # shape the booking payload uses. This parser used to probe for an
    # ODDS/EVENTS list because no real code had ever been seen; one was booked
    # on 9 Aug 2026 (5MKRXK8) and the actual shape recorded here.
    #
    #   "9888761$LIVES_1X2_1": {
    #       "E_ID": 9888761, "E_NAME": "Gimnasia y Esgrima La Plata - Barracas Central",
    #       "GN": "Argentina - Liga Profesional", "V": 2.4,
    #       "M_NAME": "1X2", "SGN": "1", "STARTDATE": "2026-08-09 20:45:00"}
    rows = data.get("O")
    if not isinstance(rows, dict) or not rows:
        path = _dump_raw("bet9ja", code, payload)
        raise BookingCodeError(
            f"Valid Bet9ja code but unrecognized slip format - raw payload "
            f"saved to {path} so the parser can be extended.")

    legs = []
    for key, s in rows.items():
        if not isinstance(s, dict):
            continue
        desc = s.get("E_NAME") or ""
        home, away = (desc.split(" - ", 1) + [None])[:2] if " - " in desc             else (desc or None, None)
        legs.append({
            "home_team": home,
            "away_team": away,
            "league": s.get("GN"),
            "match_date": (s.get("STARTDATE") or "")[:10] or None,
            "market": s.get("M_NAME"),
            "selection": s.get("SGN"),
            "odds": float(s["V"]) if s.get("V") is not None else None,
            # Bet9ja's own ids. Deliberately NOT reused for conversion to
            # another bookmaker: they are its private palimpsest, not Betradar,
            # so a converted slip would have to be re-matched by name and could
            # silently land on a different market.
            "event_id": s.get("E_ID"),
            "bet9ja_key": key,
        })

    if not any(l["home_team"] for l in legs):
        path = _dump_raw("bet9ja", code, payload)
        raise BookingCodeError(f"Slip found but no legs parsed - raw payload "
                               f"saved to {path}.")
    logger.success(f"Decoded Bet9ja code {code}: {len(legs)} legs")
    return legs


BETANO_URL = ("https://www.betano.ng/api/sharebookingcode/getbetslip?code={code}")


def decode_betano(code: str) -> list[dict]:
    """Betano booking code -> legs. Endpoint found in their own bundle
    (/api/sharebookingcode/getbetslip) and confirmed anonymous: their config
    sets generateForLoggedOutEnabled=true, and a bad code returns a clean
    422 "InvalidCode" rather than an auth error.

    Structure captured from a real code (L8K65AJT, 8 Aug 2026):
      data.legs[] = {participants:[home,away], market, description (=selection),
                     odds, eventStartTime (epoch ms), eventUrl, marketId, id}

    NOTE: Betano uses its OWN internal ids (marketId 2894867609, event id in the
    URL), NOT the Betradar ids SportyBet/MSport/football.com share. So a Betano
    slip can be READ and evaluated, but converting to or from it would need
    name-based re-matching, which can silently pick a different market. Same
    call as Bet9ja: decode yes, convert no.
    """
    code = code.strip().upper()
    if not code or len(code) > 24:
        raise BookingCodeError("That doesn't look like a booking code.")
    try:
        r = requests.get(BETANO_URL.format(code=code),
                         headers={**HEADERS, "Referer": "https://www.betano.ng/"},
                         timeout=25)
        payload = r.json()
    except requests.RequestException as e:
        raise BookingCodeError(f"Couldn't reach Betano: {e}") from e
    except ValueError as e:
        # Betano returns HTML when it doesn't like the User-Agent.
        raise BookingCodeError("Betano returned a non-JSON response "
                               "(possibly blocked).") from e

    if payload.get("errors"):
        raise BookingCodeError(str(payload["errors"][0])[:200])

    raw_legs = (payload.get("data") or {}).get("legs") or []
    if not raw_legs:
        path = _dump_raw("betano", code, payload)
        raise BookingCodeError(
            f"Valid code but unrecognized slip format - raw payload saved "
            f"to {path} so the parser can be extended.")

    legs = []
    for l in raw_legs:
        parts = [p.get("name") for p in (l.get("participants") or [])]
        home = parts[0] if len(parts) > 0 else None
        away = parts[1] if len(parts) > 1 else None
        if not (home and away):
            continue
        legs.append({
            "home_team": home,
            "away_team": away,
            "league": None,               # Betano gives leagueId, not a name
            "tournament": None,
            "match_date": _ts_to_date(l.get("eventStartTime")),
            "market": l.get("market"),
            "selection": l.get("description"),
            "odds": float(l["odds"]) if l.get("odds") not in (None, "") else None,
            "event_id": str(l.get("eventId") or ""),
            # Betano's own ids — deliberately NOT reused for conversion.
            "market_id": None,
            "specifier": "",
            "outcome_id": None,
        })

    if not legs:
        path = _dump_raw("betano", code, payload)
        raise BookingCodeError(f"Slip found but no legs parsed - saved to {path}.")
    logger.success(f"Decoded Betano code {code}: {len(legs)} legs")
    return legs


PROVIDERS = {
    "sportybet": decode_sportybet,
    "football.com": decode_footballcom,
    "msport": decode_msport,
    "bet9ja": decode_bet9ja,
    "betano": decode_betano,
}

# Providers sharing SportyBet's platform and Betradar ids. A slip can move
# between these EXACTLY -- same event/market/outcome ids, nothing re-matched.
# Bet9ja is deliberately absent: it uses its own internal palimpsest ids, so a
# conversion to or from it would need fuzzy re-mapping, which can silently pick
# a different market. Not something to guess with someone's bet.
SPORTY_PLATFORM = {"sportybet", "football.com", "msport"}

# Bookmakers we can CREATE codes on. Deliberately narrower than PROVIDERS
# (decoding) and than SPORTY_PLATFORM (id compatibility): reading a code and
# writing one are different endpoints with different permissions.
#
# football.com is absent ON PURPOSE. It DECODES SportyBet codes perfectly
# (same backend, same Betradar ids), but POSTing selections to
# football.com/api/<cc>/orders/share returns bizCode 19999 "Sorry, something
# went wrong" for ng/gh/ke, and 503 for other country codes -- with the exact
# same body that SportyBet accepts (verified 9 Aug 2026, code M1X4QK). Their
# share-creation endpoint simply isn't open to us. Don't re-add it without a
# successful POST in hand; a provider listed here is promised to the UI.
SHARE_POST_URLS = {
    "sportybet": SHARE_POST_URL,
    "msport": MSPORT_SHARE_POST_URL,
}


def _post_selections(selections: list[dict], provider: str) -> str:
    """POST rebuilt selections to a bookmaker and return the new share code."""
    url = SHARE_POST_URLS.get(provider)
    if not url:
        raise BookingCodeError(
            f"Can't create codes on {provider}. Supported: "
            f"{', '.join(SHARE_POST_URLS)}")
    try:
        r = requests.post(url, headers={**HEADERS, "Content-Type": "application/json"},
                          json={"selections": selections}, timeout=30)
        payload = r.json()
    except requests.RequestException as e:
        raise BookingCodeError(f"Couldn't reach {provider}: {e}") from e
    except ValueError as e:
        raise BookingCodeError(f"{provider} returned a non-JSON response.") from e

    if payload.get("bizCode") != 10000:
        raise BookingCodeError(payload.get("message")
                               or f"{provider} rejected the slip "
                                  f"(bizCode {payload.get('bizCode')})")
    code = (payload.get("data") or {}).get("shareCode")
    if not code:
        path = _dump_raw(provider, "generated", payload)
        raise BookingCodeError(f"No share code in the response - saved to {path}")
    return code


def _selections_from_legs(legs: list[dict], source: str) -> list[dict]:
    """Rebuild bookmaker selections from decoded legs, exactly.

    Only valid within SPORTY_PLATFORM, where the ids mean the same thing on
    both sides. Legs missing ids are dropped rather than guessed — a slip that
    quietly contains a different market than the user chose is worse than a
    slip with one leg fewer.
    """
    if source not in SPORTY_PLATFORM:
        raise BookingCodeError(
            f"Converting from {source} isn't supported: it uses its own "
            f"internal ids, so the slip would have to be re-matched by name "
            f"and could pick a different market.")
    sels, dropped = [], []
    for l in legs:
        if l.get("event_id") and l.get("market_id") and l.get("outcome_id"):
            sels.append({"eventId": l["event_id"],
                         "marketId": str(l["market_id"]),
                         "specifier": l.get("specifier") or "",
                         "outcomeId": str(l["outcome_id"])})
        else:
            dropped.append(f"{l.get('home_team')} v {l.get('away_team')}")
    return sels, dropped


def convert_code(from_provider: str, code: str, to_provider: str) -> dict:
    """Turn a booking code from one bookmaker into the equivalent on another.

    Exact, not fuzzy: within the SportyBet platform (SportyBet, football.com,
    MSport) the event/market/outcome ids are identical, so the rebuilt slip is
    the same bets — not a best-effort re-match.
    """
    from_provider = (from_provider or "").lower().strip()
    to_provider = (to_provider or "").lower().strip()
    legs = decode(from_provider, code)
    sels, dropped = _selections_from_legs(legs, from_provider)
    if not sels:
        raise BookingCodeError("None of the legs could be carried across.")
    new_code = _post_selections(sels, to_provider)
    logger.success(f"Converted {from_provider} {code} -> {to_provider} {new_code} "
                   f"({len(sels)} legs, {len(dropped)} dropped)")
    return {"code": new_code, "legs": legs, "converted": len(sels),
            "dropped": dropped, "from": from_provider, "to": to_provider}


def merge_codes(sources: list[dict], to_provider: str) -> dict:
    """Combine several booking codes into one slip.

    sources: [{"provider": "sportybet", "code": "ABC123"}, ...]

    Deduplicates by EVENT, not by selection: two slips backing different
    outcomes of the same match can't both win, and most bookmakers reject the
    combination anyway. The first occurrence wins and the clash is reported
    rather than silently resolved.
    """
    to_provider = (to_provider or "").lower().strip()
    all_legs, sels, seen_events, conflicts, dropped = [], [], {}, [], []

    for src in sources:
        provider = (src.get("provider") or "").lower().strip()
        legs = decode(provider, src.get("code") or "")
        part, part_dropped = _selections_from_legs(legs, provider)
        dropped.extend(part_dropped)
        for leg, sel in zip([l for l in legs
                             if l.get("event_id") and l.get("market_id")
                             and l.get("outcome_id")], part):
            ev = sel["eventId"]
            if ev in seen_events:
                prev = seen_events[ev]
                if (prev["marketId"], prev["outcomeId"]) != (sel["marketId"], sel["outcomeId"]):
                    conflicts.append(f"{leg.get('home_team')} v {leg.get('away_team')}")
                continue
            seen_events[ev] = sel
            sels.append(sel)
            all_legs.append(leg)

    if not sels:
        raise BookingCodeError("Nothing to merge — no usable legs found.")
    new_code = _post_selections(sels, to_provider)
    logger.success(f"Merged {len(sources)} codes -> {to_provider} {new_code} "
                   f"({len(sels)} legs, {len(conflicts)} conflicts)")
    return {"code": new_code, "legs": all_legs, "merged_legs": len(sels),
            "conflicts": conflicts, "dropped": dropped, "to": to_provider}


def split_code(provider: str, code: str, parts: int = 2,
               to_provider: str | None = None) -> dict:
    """One booking code -> several smaller ones.

    The point is risk, not tidiness. A 10-leg accumulator needs all ten to land;
    two 5-leg codes can pay out on one half while the other misses. Splitting
    is the only one of these tools that IMPROVES a punter's expected outcome
    without changing any selection.

    Legs are dealt round-robin rather than sliced into blocks, so each code
    gets a mix of the slip's strong and weak legs instead of one code
    inheriting every long shot. Order within the original slip is arbitrary
    anyway — it is whatever the bookmaker returned.

    Only works within SPORTY_PLATFORM, where event/market/outcome ids mean the
    same thing on both sides; see convert_code for why that matters.
    """
    provider = (provider or "").lower().strip()
    to_provider = (to_provider or provider).lower().strip()
    if parts < 2:
        raise BookingCodeError("Splitting needs at least 2 parts.")

    legs = decode(provider, code)
    sels, dropped = _selections_from_legs(legs, provider)
    if len(sels) < parts:
        raise BookingCodeError(
            f"Can't split {len(sels)} usable leg(s) into {parts} codes — "
            f"each code needs at least one leg.")

    usable = [l for l in legs if l.get("event_id") and l.get("market_id")
              and l.get("outcome_id")]
    buckets = [[] for _ in range(parts)]
    leg_buckets = [[] for _ in range(parts)]
    for i, (sel, leg) in enumerate(zip(sels, usable)):
        buckets[i % parts].append(sel)
        leg_buckets[i % parts].append(leg)

    out = []
    for bucket, blegs in zip(buckets, leg_buckets):
        new_code = _post_selections(bucket, to_provider)
        out.append({"code": new_code, "legs": blegs, "leg_count": len(bucket)})
    logger.success(f"Split {provider} {code} -> {parts} codes on {to_provider} "
                   f"({len(sels)} legs, {len(dropped)} dropped)")
    return {"codes": out, "parts": parts, "dropped": dropped,
            "from": provider, "to": to_provider}


def decode(provider: str, code: str) -> list[dict]:
    fn = PROVIDERS.get((provider or "").lower().strip())
    if not fn:
        raise BookingCodeError(
            f"Unknown provider '{provider}'. Supported: "
            f"{', '.join(PROVIDERS)}")
    return fn(code)


# ─── Bet9ja GENERATION ────────────────────────────────────────────────────────
#
# Bet9ja does NOT use Betradar ids, so the SportyBet mapping is useless here —
# it runs its own "palimpsest" of event, market and sign ids. CLAUDE.md recorded
# this as blocked on "a captured real BookABetV2 request". It turned out the
# contract is readable straight out of their own JS bundle, which beats a
# capture: a capture gives one example, the source gives the field names.
#
# From bundle_1.321.0.min.js:
#
#   cw = t => post("/PlacebetAjax/BookABetV2",
#                  form({BETSLIP: JSON.stringify(t.BETSLIP),
#                        IS_PASSBET: t.IS_PASSBET || 0}))
#
#   Df = (BSTYPE, TAB, NUMLINES, COMB, TYPE, STAKE, POTWINMIN, POTWINMAX,
#         BONUSMIN, BONUSMAX, ODDMIN, ODDMAX, ODDS, FIXED) => ({...})
#
#   Tf = betslip => ({BETS: [Df(...)], EVS: {...}, IMPERSONIZE: 0})
#
# So BETSLIP is an OBJECT — their code type-checks it — not the array every
# guess assumed. That is why blind probing returned an opaque ERROR_CODE 100
# whatever was sent: the shape was wrong before the contents mattered.
#
# Verified end to end (9 Aug 2026): booked U. de Chile v Palestino, 1X2 "1" at
# 1.60, received RIS "5MKSHS5", then decoded that code and got the same leg back.
#
# Two details that cost time:
#   * The short shareable code is RIS. COUPONCODE is a UUID; the bundle prefers
#     RIS and only falls back to COUPONCODE, and RIS is what a punter types in.
#   * BSTYPE must be 3. BSTYPE 1 returns ERROR_CODE 103, and a fixture within
#     ~10 minutes of kickoff returns STATUS 1xx with no code at all.

BET9JA_BOOK_URL = ("https://sports.bet9ja.com/desktop/feapi/PlacebetAjax/"
                   "BookABetV2")
BET9JA_PALIMPSEST = "https://sports.bet9ja.com/desktop/feapi/PalimpsestAjax/"
BET9JA_HEADERS = {**HEADERS, "Referer": "https://sports.bet9ja.com/"}

_B9J_CACHE = {"at": 0.0, "events": []}

# Their market ids mapped to the markets this project derives. Only the ones we
# can map with confidence: an unmapped leg is reported as unmapped, never
# guessed onto a neighbouring market. Putting someone's money on the wrong
# market is worse than telling them we could not place it.
# Surveyed live across 24 events (9 Aug 2026): Bet9ja's football palimpsest
# offers 1X2, Double Chance, GG/NG, Over/Under, Odd/Even and half-time
# variants. It has NO Draw No Bet and NO Handicap at all, so legs in those
# markets come back unmapped rather than being bent onto a nearby market —
# which matters, because Handicap and DNB are a large share of what this
# project's slips contain.
BET9JA_MARKETS = {
    "Match Result": "S_1X2",
    "Double Chance": "S_DC",
    "Both Teams To Score": "S_GGNG",
    "Over/Under 0.5": "S_OU@0.5",
    "Over/Under 1.5": "S_OU@1.5",
    "Over/Under 2.5": "S_OU@2.5",
    "Over/Under 3.5": "S_OU@3.5",
    "Over/Under 4.5": "S_OU@4.5",
}


def _bet9ja_events(max_groups: int = 40) -> list:
    """Every football event Bet9ja currently offers, flattened.

    Walks sport -> country -> competition -> events. Cached for the same window
    as the SportyBet feed: a slip build asks repeatedly and the palimpsest does
    not change minute to minute.
    """
    if _B9J_CACHE["events"] and _time.time() - _B9J_CACHE["at"] < _EVENTS_TTL:
        return _B9J_CACHE["events"]

    try:
        pal = requests.get(BET9JA_PALIMPSEST + "GetSports",
                           params={"DISP": "1"}, headers=BET9JA_HEADERS,
                           timeout=25).json()["D"]["PAL"]
    except (requests.RequestException, ValueError, KeyError) as e:
        raise BookingCodeError(f"Could not read the Bet9ja fixture list: {e}") from e

    groups = []
    for sport_id, sport in pal.items():
        # 1 = Soccer. 101/301/303 are Zoom and player virtuals, which are not
        # real matches and must never end up in a slip.
        if str(sport_id) != "1":
            continue
        for country in (sport.get("SG") or {}).values():
            groups.extend((country.get("G") or {}).keys())

    out = []
    for gid in groups[:max_groups]:
        try:
            d = requests.get(BET9JA_PALIMPSEST + "GetEventsInGroup",
                             params={"DISP": "1", "GROUPID": gid},
                             headers=BET9JA_HEADERS, timeout=20).json().get("D") or {}
        except (requests.RequestException, ValueError):
            continue          # one dead competition must not kill the sweep
        events = d.get("E") or []
        events = events if isinstance(events, list) else list(events.values())
        out.extend(ev for ev in events if ev.get("O"))

    _B9J_CACHE.update(at=_time.time(), events=out)
    logger.info(f"Bet9ja palimpsest: {len(out)} football events across "
                f"{len(groups[:max_groups])} competitions")
    return out


def _bet9ja_sign(leg: dict) -> tuple | None:
    """(market_id, sign) for one of our legs, or None when we cannot map it."""
    market_id = BET9JA_MARKETS.get(leg.get("market"))
    if not market_id:
        return None
    sel = (leg.get("selection") or "").lower()
    home = (leg.get("home_team") or "").lower()
    away = (leg.get("away_team") or "").lower()
    has_home = bool(home and home in sel)
    has_away = bool(away and away in sel)
    has_draw = "draw" in sel

    if market_id.startswith("S_OU"):
        return (market_id, "U" if "under" in sel else "O")
    if market_id == "S_GGNG":
        return (market_id, "N" if " no" in sel or sel.endswith("no") else "Y")
    if market_id == "S_1X2":
        if has_home:
            return (market_id, "1")
        if has_away:
            return (market_id, "2")
        return (market_id, "X")
    if market_id == "S_DC":
        if has_home and has_draw:
            return (market_id, "1X")
        if has_home and has_away:
            return (market_id, "12")
        if has_draw and has_away:
            return (market_id, "X2")
    return None


def generate_bet9ja(legs: list) -> dict:
    """Our slip legs -> a Bet9ja booking code.

    Same return shape as generate_code, so the API and the UI can treat every
    bookmaker identically: {code, share_url, mapped, unmapped}.
    """
    events = _bet9ja_events()
    if not events:
        raise BookingCodeError("The Bet9ja fixture list came back empty.")

    odds_map, evs, mapped, unmapped = {}, {}, [], []
    for leg in legs:
        want = _bet9ja_sign(leg)
        if not want:
            unmapped.append(leg)
            continue
        market_id, sign = want

        best, best_score = None, 0.0
        for ev in events:
            teams = (ev.get("DS") or "").split(" - ")
            if len(teams) != 2:
                continue
            # Shared matcher, not raw _sim. This was two raw SequenceMatcher
            # ratios averaged against a 0.62 bar — looser even than the
            # SportyBet path, on the same kind of code: a real bet at a real
            # bookmaker. 0.62 is below the 0.65 that team_sim deliberately
            # clamps "Manchester United"/"Manchester City" down to, so the old
            # threshold accepted exactly the pair the guard exists to reject.
            score = (_shared_team_sim(leg.get("home_team") or "", teams[0])
                     + _shared_team_sim(leg.get("away_team") or "", teams[1])) / 2
            if score > best_score:
                best, best_score = ev, score
        if not best or best_score < _SHARED_SIM_MEAN:
            unmapped.append(leg)
            continue

        market_key = f"{market_id}_{sign}"
        odd = (best.get("O") or {}).get(market_key)
        if odd is None:
            unmapped.append(leg)      # market not offered on this fixture
            continue

        key = f"{best['ID']}${market_key}"
        start = best.get("STARTDATEUTC") or ""
        odds_map[key] = float(odd)
        evs[key] = {
            "id": key, "eventId": best["ID"], "eventCode": best.get("EXTID"),
            "eventName": (best.get("DS") or "").replace(" - ", " v "),
            "market": market_key, "sid": market_id, "sign": sign,
            "GN": best.get("GN"), "leagueName": best.get("GN"),
            "SG": best.get("SG"),
            "startdate": (start[:10].replace("-", "/") + " " + start[11:19]) if start else None,
            "oddValue": float(odd), "hnd": "", "sportName": "Soccer",
        }
        mapped.append({"leg": leg, "as": f"{market_key} @ {odd}",
                       "odds": float(odd)})

    if not odds_map:
        raise BookingCodeError(
            "None of the slip legs could be matched on Bet9ja - they list "
            "different competitions, or the games have already started.")

    total = 1.0
    for value in odds_map.values():
        total *= value

    # Bet type depends on the leg count, and the two values are mutually
    # exclusive — each is rejected with ERROR_CODE 103 in the other's place:
    #     1 leg   -> BSTYPE 3 (single)
    #     2+ legs -> BSTYPE 1 (accumulator)
    # Determined by trying every value against the live endpoint; 4-7 are
    # accepted but return STATUS 100 and no code, which is a silent failure and
    # exactly the kind of "success" this project does not accept.
    bstype = 3 if len(odds_map) == 1 else 1
    bet = {
        "BSTYPE": bstype, "TAB": bstype, "NUMLINES": len(odds_map), "COMB": 1,
        "TYPE": len(odds_map), "STAKE": 0,
        "POTWINMIN": 0, "POTWINMAX": 0, "BONUSMIN": 0, "BONUSMAX": 0,
        "ODDMIN": round(total, 2), "ODDMAX": round(total, 2),
        "ODDS": odds_map, "FIXED": {},
    }
    payload = {"BETS": [bet], "EVS": evs, "IMPERSONIZE": 0}

    try:
        r = requests.post(
            BET9JA_BOOK_URL,
            headers={**BET9JA_HEADERS,
                     "Origin": "https://sports.bet9ja.com",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"BETSLIP": json.dumps(payload), "IS_PASSBET": "0"},
            timeout=30)
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        raise BookingCodeError(f"Bet9ja booking call failed: {e}") from e

    if body.get("R") != "OK" or not body.get("D"):
        path = _dump_raw("bet9ja_gen", "post", body)
        err = (body.get("D") or {})
        raise BookingCodeError(
            f"Bet9ja refused the slip (error "
            f"{err.get('ERROR_CODE') if isinstance(err, dict) else '?'}) "
            f"- saved to {path}")

    d = body["D"][0] if isinstance(body["D"], list) else body["D"]
    code = d.get("RIS") or d.get("COUPONCODE")
    if not code:
        path = _dump_raw("bet9ja_gen", "nocode", body)
        raise BookingCodeError(
            f"Bet9ja accepted the slip but returned no code (STATUS "
            f"{d.get('STATUS')}) - usually a match too close to kickoff. "
            f"Saved to {path}")

    logger.success(f"Generated bet9ja booking code {code} "
                   f"({len(odds_map)} legs, {len(unmapped)} unmapped)")
    return {"code": code, "share_url": None,
            "mapped": mapped, "unmapped": unmapped}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scrapers/booking_codes.py <SPORTYBET_CODE>")
        sys.exit(1)
    try:
        for leg in decode_sportybet(sys.argv[1]):
            print(f"{leg['home_team']} vs {leg['away_team']} "
                  f"[{leg['match_date']}] {leg['market']}: {leg['selection']} "
                  f"@ {leg['odds']}")
    except BookingCodeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
