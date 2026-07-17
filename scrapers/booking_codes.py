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
    for mkt in o.get("markets") or []:
        market_desc = mkt.get("desc") or mkt.get("title")
        for out in mkt.get("outcomes") or []:
            selection_desc = out.get("desc")
            odds = out.get("odds")
            break
        break
    return {
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


def decode_sportybet(code: str) -> list[dict]:
    """Booking code -> list of leg dicts. Raises BookingCodeError on failure."""
    code = code.strip().upper()
    if not code or len(code) > 20:
        raise BookingCodeError("That doesn't look like a booking code.")
    try:
        r = requests.get(SPORTYBET_URL.format(code=code),
                         headers=HEADERS, timeout=25)
        payload = r.json()
    except requests.RequestException as e:
        raise BookingCodeError(f"Couldn't reach SportyBet: {e}") from e
    except ValueError as e:
        raise BookingCodeError("SportyBet returned a non-JSON response "
                               "(possibly blocked).") from e

    biz = payload.get("bizCode")
    if biz != 10000:
        msg = payload.get("message") or f"SportyBet error (bizCode {biz})"
        raise BookingCodeError(msg)

    data = payload.get("data") or {}
    outcomes = data.get("outcomes") or []
    if not outcomes:
        path = _dump_raw("sportybet", code, payload)
        raise BookingCodeError(
            f"Valid code but unrecognized slip format - raw payload saved "
            f"to {path} so the parser can be extended.")

    legs = []
    for o in outcomes:
        leg = _parse_sportybet_outcome(o)
        if leg["home_team"] and leg["away_team"]:
            legs.append(leg)

    if not legs:
        path = _dump_raw("sportybet", code, payload)
        raise BookingCodeError(
            f"Slip found but no legs could be parsed - raw payload saved "
            f"to {path}.")
    logger.success(f"Decoded SportyBet code {code}: {len(legs)} legs")
    return legs


# ─── Booking-code GENERATION (SportyBet) ────────────────────────────────────
# Confirmed working 2026-07-16: POST /api/ng/orders/share with
# {"selections":[{"eventId","marketId","specifier","outcomeId"}]} returns a
# shareCode - anonymously, same endpoint their "Book Bet" button uses.

EVENTS_URL = "https://www.sportybet.com/api/ng/factsCenter/pcUpcomingEvents"
SHARE_POST_URL = "https://www.sportybet.com/api/ng/orders/share"
GEN_MARKET_IDS = "1,10,11,16,18,29"   # 1X2, DC, DNB, Asian Handicap, O/U, GG/NG

from difflib import SequenceMatcher


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


_EVENTS_CACHE = {"at": 0.0, "events": []}
_EVENTS_TTL = 300   # odds move, but event/market IDs are stable - 5 min is safe


def fetch_sportybet_events(pages: int = 10, page_size: int = 100) -> list[dict]:
    """Flatten SportyBet's upcoming football events (with main markets).
    Cached for a few minutes so repeated bookings don't refetch ~8 pages."""
    import time as _time
    if _EVENTS_CACHE["events"] and _time.time() - _EVENTS_CACHE["at"] < _EVENTS_TTL:
        return _EVENTS_CACHE["events"]
    events = []
    for page in range(1, pages + 1):
        d = None
        for attempt in (1, 2):   # one retry - a single failed page must not
            try:                 # truncate (and then cache) a partial list
                r = requests.get(EVENTS_URL, headers=HEADERS, timeout=25,
                                 params={"sportId": "sr:sport:1",
                                         "marketId": GEN_MARKET_IDS,
                                         "pageSize": page_size,
                                         "pageNum": page, "option": 1})
                d = r.json()
                break
            except (requests.RequestException, ValueError) as e:
                logger.warning(f"SportyBet events page {page} "
                               f"attempt {attempt} failed: {e}")
        if not d or d.get("bizCode") != 10000:
            continue
        tournaments = (d.get("data") or {}).get("tournaments") or []
        if not tournaments:
            break   # genuine end of listing
        for t in tournaments:
            events.extend(t.get("events") or [])
    logger.info(f"SportyBet events feed: {len(events)} upcoming matches")
    if events:
        _EVENTS_CACHE.update(at=_time.time(), events=events)
    return events


def _team_norm(s: str) -> str:
    """Strip club furniture (FC/SC/CF/IF/…) so 'Vaalerenga IF' ~ 'Valerenga'."""
    import re
    s = (s or "").lower()
    s = re.sub(r"\b(fc|cf|sc|ec|if|fk|afc|cd|ca|club|de|sk)\b", " ", s)
    return " ".join(s.split())


def _teams_match(a: str, b: str) -> float:
    na, nb = _team_norm(a), _team_norm(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 1.0
    return _sim(na, nb)


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


def map_leg_to_sportybet(leg: dict, events: list[dict]) -> dict | None:
    """Find the SportyBet event + market + outcome for one of our slip legs.
    Market/outcome translation is deterministic (no cross-market fuzzing) —
    a leg either maps to exactly what the user picked, or is skipped."""
    want = _wanted_outcome(leg)
    if not want:
        return None
    want_market, want_line, want_outcome = want

    # find the event: normalized-team match on both sides
    best_ev, best_s = None, 0.72
    for ev in events:
        s = (_teams_match(leg.get("home_team"), ev.get("homeTeamName")) +
             _teams_match(leg.get("away_team"), ev.get("awayTeamName"))) / 2
        if s > best_s:
            best_ev, best_s = ev, s
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


# MSport accepts the identical POST shape and shares Betradar event/market/
# outcome IDs with SportyBet, so one mapping (from SportyBet's events feed)
# can book on either. Confirmed live 2026-07-16 (returned a real shareCode).
MSPORT_SHARE_POST_URL = "https://www.msport.com/api/ng/orders/share"


def generate_code(legs: list[dict], provider: str = "sportybet") -> dict:
    """Our slip legs -> booking code on the chosen bookmaker.
    legs: [{home_team, away_team, market, selection}]
    Returns {code, share_url, mapped: [...], unmapped: [...]}."""
    post_url = {"sportybet": SHARE_POST_URL,
                "msport": MSPORT_SHARE_POST_URL}.get(provider)
    if not post_url:
        raise BookingCodeError(f"Can't generate codes for '{provider}'.")
    events = fetch_sportybet_events()
    if not events:
        raise BookingCodeError("Couldn't load SportyBet's fixture list.")

    mapped, unmapped, selections = [], [], []
    for leg in legs:
        m = map_leg_to_sportybet(leg, events)
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
        })

    if not legs:
        path = _dump_raw("msport", code, payload)
        raise BookingCodeError(f"Slip found but no legs parsed - raw payload "
                               f"saved to {path}.")
    logger.success(f"Decoded MSport code {code}: {len(legs)} legs")
    return legs


PROVIDERS = {
    "sportybet": decode_sportybet,
    "msport": decode_msport,
}


def decode(provider: str, code: str) -> list[dict]:
    fn = PROVIDERS.get((provider or "").lower().strip())
    if not fn:
        raise BookingCodeError(
            f"Unknown provider '{provider}'. Supported: "
            f"{', '.join(PROVIDERS)}")
    return fn(code)


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
