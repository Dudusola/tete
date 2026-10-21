"""
Arbitrage finder: SportyBet vs MSport, the two books our users can bet AND we
can book codes on (21 Oct 2026, from the Puntrr audit's tool list).

Both run on Betradar, so a match has the SAME event id ("sr:match:...") and the
same market/outcome ids on both sides: the join is exact, no team-name
matching. For each market both books list, take the best price per outcome;
if 1/best summed over the outcomes is under 1, backing every outcome at its
best price returns more than the total staked whatever happens.

Only markets whose outcomes are exclusive AND exhaustive with no refund:
1X2, Over/Under on half-goal lines, GG/NG. Double Chance overlaps, Draw No Bet
and whole-goal totals can void, and a void leg breaks the arithmetic.

MSport's feed (probed 21 Oct 2026, from their own JS bundle): POST
/facts-center/query/frontend/sports-matches-list with header operId: 2 and
body [], 50 events a page, paged by ?lastEventId=<last id of the previous page>.

    python scrapers/arbs.py          # print what it finds now
"""
import sys
import time

import requests
from loguru import logger

sys.path.insert(0, ".")
from scrapers.booking_codes import HEADERS, fetch_sportybet_events  # noqa: E402

MSPORT_LIST = "https://www.msport.com/api/ng/facts-center/query/frontend/sports-matches-list"
MARKETS = "1,18,29"
LABEL = {"1": "1X2", "18": "Over/Under", "29": "Both teams to score"}
MAX_PAGES = 60          # ~1,500 football events today = 31 pages; a cap, not a guess
_CACHE = {"at": 0.0, "events": []}
_TTL = 300


def fetch_msport_events() -> list[dict]:
    """Every not-yet-started MSport football event with the markets above."""
    if _CACHE["events"] and time.time() - _CACHE["at"] < _TTL:
        return _CACHE["events"]
    headers = {**HEADERS, "operId": "2", "Content-Type": "application/json"}
    out, last = [], None
    for _ in range(MAX_PAGES):
        params = {"sportId": "sr:sport:1", "marketIds": MARKETS}
        if last:
            params["lastEventId"] = last
        r = requests.post(MSPORT_LIST, params=params, json=[], headers=headers, timeout=25)
        data = (r.json() or {}).get("data") or {}
        page = [e for t in data.get("tournaments") or [] for e in t.get("events") or []]
        if not page or data.get("lastEventId") in (None, last):
            break
        out += page
        last = data["lastEventId"]
    else:
        logger.warning(f"MSport feed still had pages after {MAX_PAGES} - raise MAX_PAGES")
    out = [e for e in out if e.get("status") == 0]
    _CACHE.update(at=time.time(), events=out)
    return out


def _prices(event: dict) -> dict:
    """{(market id, specifier): {outcome id: (odds, outcome name)}} for usable markets."""
    out = {}
    for m in event.get("markets") or []:
        mid, spec = str(m.get("id")), m.get("specifier") or m.get("specifiers") or ""
        if mid not in LABEL or (mid == "18" and not spec.endswith(".5")):
            continue
        outs = {str(o["id"]): (float(o["odds"]), o.get("desc") or o.get("description") or str(o["id"]))
                for o in m.get("outcomes") or []
                if o.get("isActive", 1) and float(o.get("odds") or 0) > 1}
        if outs:
            out[(mid, spec)] = outs
    return out


def best_prices(books: dict) -> tuple[dict, float]:
    """books: {name: {outcome id: (odds, label)}} for ONE market. Returns the best
    {outcome id: (odds, label, book)} over the outcomes every book lists, and
    sum(1/odds). Under 1.0 = an arbitrage."""
    common = set.intersection(*(set(p) for p in books.values()))
    best = {}
    for oid in common:
        book, (odds, label) = max(((b, p[oid]) for b, p in books.items()), key=lambda x: x[1][0])
        best[oid] = (odds, label, book)
    return best, (sum(1 / v[0] for v in best.values()) if best else float("inf"))


def find(stake: float = 1000.0, near: int = 5) -> dict:
    """Arbitrages now, plus the `near` closest misses so an empty list still
    shows how close the two books are."""
    # The default market list (booking's) already holds 1, 18 and 29: reuse its cache.
    sporty = {e["eventId"]: e for e in fetch_sportybet_events()}
    msport = {e["eventId"]: e for e in fetch_msport_events()}
    rows = []
    for eid in sporty.keys() & msport.keys():
        s, m = sporty[eid], msport[eid]
        ps, pm = _prices(s), _prices(m)
        for key in ps.keys() & pm.keys():
            best, book = best_prices({"SportyBet": ps[key], "MSport": pm[key]})
            # 1X2 needs all three outcomes, the 2-way markets both.
            if len(best) != (3 if key[0] == "1" else 2):
                continue
            payout = stake / book
            rows.append({
                "event_id": eid,
                "home_team": s.get("homeTeamName"), "away_team": s.get("awayTeamName"),
                "kickoff_ms": s.get("estimateStartTime"),
                "market": LABEL[key[0]] + (f" {key[1].split('=')[-1]}" if key[1] else ""),
                "book_pct": round(book * 100, 2),
                "profit_pct": round((1 / book - 1) * 100, 2),
                "legs": [{"outcome": lab, "odds": o, "book": bk, "stake": round(stake / o / book, 2)}
                         for o, lab, bk in sorted(best.values(), key=lambda v: v[1])],
                "payout": round(payout, 2),
            })
    rows.sort(key=lambda r: r["book_pct"])
    arbs = [r for r in rows if r["book_pct"] < 100]
    return {"arbs": arbs, "near_misses": [r for r in rows if r["book_pct"] >= 100][:near],
            "compared_markets": len(rows), "matched_events": len(sporty.keys() & msport.keys()),
            "stake": stake, "checked_at": int(time.time())}


if __name__ == "__main__":
    # Self-check of the arithmetic: 2.10 / 2.10 on a 2-way market is a 4.8% arb.
    best, book = best_prices({"A": {"1": (2.10, "Yes"), "2": (1.80, "No")},
                              "B": {"1": (1.70, "Yes"), "2": (2.10, "No")}})
    assert {k: v[2] for k, v in best.items()} == {"1": "A", "2": "B"} and abs(book - 2 / 2.10) < 1e-9
    r = find()
    print(f"{r['matched_events']} matches on both books, {r['compared_markets']} markets compared, "
          f"{len(r['arbs'])} arbitrages")
    for a in (r["arbs"] or r["near_misses"])[:10]:
        print(f"  {a['book_pct']:6.2f}%  {a['home_team']} v {a['away_team']}  {a['market']}  "
              + "  ".join(f"{l['outcome']} {l['odds']} @{l['book']}" for l in a["legs"]))
