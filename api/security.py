"""
api/security.py - request limits for a public, unauthenticated API (8 Oct 2026).

The API has no accounts and holds nothing private, so the threats are not data
theft; they are COST and REPUTATION:

  - One oversized POST read into memory can take down a 512 MB free instance.
  - The booking endpoints call SportyBet / MSport / Bet9ja FROM OUR IP. Anyone
    looping them makes us the abuser, and API-Football already suspended an
    account over shared-IP abuse (docs/history.md, cause found 28 Sep 2026).
  - /api/slip with varied parameters is a cache miss every time (~1-1.5 s of
    the free CPU each), so a loop of random queries starves everyone else.

So: a body-size cap, a per-IP token bucket on the expensive routes, and list
caps the endpoints call. Limits sit well above what the web app itself sends
(the Builder fires a slip per click; a slip has at most 20 legs).
"""

import threading
import time

from fastapi import HTTPException
from fastapi.responses import JSONResponse

MAX_BODY_BYTES = 256 * 1024      # the largest real payload (a 20-leg slip) is ~5 KB
MAX_LEGS = 40
MAX_IDS = 300

# (path prefix, method) -> (requests allowed per minute per IP)
LIMITS = (
    ("/api/booking/", "POST", 20),   # each one calls a bookmaker from our IP
    ("/api/slip/", "POST", 60),      # settle / odds / check
    ("/api/slip", "GET", 60),        # uncached for most parameter mixes
    ("/api/results/lookup", "POST", 60),
)

_buckets: dict = {}              # (ip, rule) -> [tokens, last_refill]
_lock = threading.Lock()


def client_ip(request) -> str:
    """The visitor's IP behind Render's proxy. Render sits behind Cloudflare,
    which sets CF-Connecting-IP itself (a client cannot forge it through
    Cloudflare). X-Forwarded-For's LAST entry is the one the proxy appended;
    earlier entries are whatever the client claimed."""
    h = request.headers
    if h.get("cf-connecting-ip"):
        return h["cf-connecting-ip"].strip()
    xff = h.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _allow(key, per_minute: int) -> bool:
    now = time.monotonic()
    with _lock:
        if len(_buckets) > 20000:                 # never grow without bound
            _buckets.clear()
        tokens, last = _buckets.get(key, (float(per_minute), now))
        tokens = min(per_minute, tokens + (now - last) * per_minute / 60.0)
        ok = tokens >= 1.0
        _buckets[key] = [tokens - 1.0 if ok else tokens, now]
        return ok


async def limits_middleware(request, call_next):
    if request.method == "POST":
        # Chunked bodies carry no Content-Length and would bypass the size cap.
        if "content-length" not in request.headers:
            return JSONResponse({"detail": "Content-Length required."}, status_code=411)
        try:
            size = int(request.headers.get("content-length", "0"))
        except ValueError:
            size = MAX_BODY_BYTES + 1
        if size > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Request too large."}, status_code=413)
    path = request.url.path
    for prefix, method, per_minute in LIMITS:
        if request.method == method and (path == prefix or path.startswith(prefix)
                                          and prefix.endswith("/")):
            if not _allow((client_ip(request), prefix, method), per_minute):
                return JSONResponse(
                    {"detail": "Too many requests - please wait a moment and try again."},
                    status_code=429, headers={"Retry-After": "30"})
            break
    return await call_next(request)


def cap(items, limit: int, what: str):
    """Reject an over-long list with a 422 the UI can show, rather than doing
    unbounded work on it."""
    if items is not None and len(items) > limit:
        raise HTTPException(422, f"Too many {what} (max {limit}).")
    return items
