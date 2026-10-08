"""
api/cache.py - response cache for the read endpoints, keyed by LEDGER VERSION.

WHY (8 Oct 2026). Every GET recomputed its answer from SQLite on every request,
on a Render free instance with a fraction of one CPU. /api/stats took 2.1 s of
server time live (0.3 s on a laptop), and a page firing three calls at once
queued them behind each other - long enough to trip the "Waking the prediction
server" notice on a server that was awake.

But nearly all of it is a pure function of one file. The ledger only changes
when the sync installs a new copy (deploy/render/sync_ledger.py, an atomic
os.replace), so the file's (mtime, size) is a free version number for the
data: when it changes, every cached answer is stale; until it does, none is.

    key   = endpoint + its arguments + ledger version
    entry = the ENCODED JSON bytes + an ETag of them

Storing bytes, not dicts, means a hit skips the encoding too (/api/fixtures is
734 KB of JSON) and nobody can mutate a cached object in place.

Some answers also depend on the CLOCK - picks drop out once they kick off, live
scores move - so each endpoint declares a TTL as well. The version check makes
a new ledger visible at once; the TTL bounds how stale a clock-dependent
answer can get between ledgers.

SINGLE-FLIGHT. Ten visitors arriving on a cold key do not compute it ten
times: the first computes, the rest wait on its lock and take its result. On a
0.1-CPU box a stampede is exactly what turns 2 s into 20 s.

ETag / 304 and Cache-Control are set on every cached response, so a browser
can reuse an answer without asking, or ask and get an empty "not modified".
"""

import functools
import hashlib
import json
import os
import threading
import time

from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response

from db.database import DB_PATH

MAX_ENTRIES = 512          # slip queries are the only open-ended keys


def ledger_version():
    """(mtime_ns, size) of the ledger file - changes exactly when the sync
    installs a new copy. None if it is missing (then nothing is cached)."""
    try:
        st = os.stat(DB_PATH)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


_store: dict = {}           # key -> (version, stored_at, body, etag)
_locks: dict = {}
_guard = threading.Lock()
stats = {"hits": 0, "misses": 0}


def _lock_for(key):
    with _guard:
        return _locks.setdefault(key, threading.Lock())


def _encode(value) -> bytes:
    # Same settings as FastAPI's own JSONResponse, so a cached response is
    # byte-for-byte what the endpoint would have returned uncached.
    return json.dumps(jsonable_encoder(value), ensure_ascii=False,
                      allow_nan=False, indent=None,
                      separators=(",", ":")).encode("utf-8")


def _response(body: bytes, etag: str, max_age: int) -> Response:
    return Response(
        content=body, media_type="application/json",
        headers={
            "ETag": etag,
            # stale-while-revalidate: a browser may show the last answer
            # instantly while it fetches the next one in the background.
            "Cache-Control": f"public, max-age={max_age}, "
                             f"stale-while-revalidate={max_age * 10}",
        })


def cached(ttl: float, max_age: int | None = None):
    """Cache a GET handler's JSON for `ttl` seconds or until the ledger changes.
    `max_age` is what browsers are told (defaults to the TTL)."""
    browser_age = int(ttl if max_age is None else max_age)

    def deco(fn):
        @functools.wraps(fn)          # FastAPI reads the signature through this
        def wrapper(*args, **kwargs):
            version = ledger_version()
            if version is None:
                return fn(*args, **kwargs)
            key = (fn.__name__, json.dumps([args, kwargs], sort_keys=True, default=str))
            now = time.monotonic()
            hit = _store.get(key)
            if hit and hit[0] == version and now - hit[1] < ttl:
                stats["hits"] += 1
                return _response(hit[2], hit[3], browser_age)
            with _lock_for(key):
                hit = _store.get(key)                     # someone may have just filled it
                if hit and hit[0] == version and time.monotonic() - hit[1] < ttl:
                    stats["hits"] += 1
                    return _response(hit[2], hit[3], browser_age)
                stats["misses"] += 1
                value = fn(*args, **kwargs)
                if isinstance(value, Response):           # errors etc. pass through
                    return value
                body = _encode(value)
                etag = '"' + hashlib.sha1(body).hexdigest()[:20] + '"'
                if len(_store) >= MAX_ENTRIES:
                    # Oldest first. Crude, and fine: the keys are a handful of
                    # endpoints plus slip queries.
                    for k in sorted(_store, key=lambda k: _store[k][1])[:MAX_ENTRIES // 4]:
                        _store.pop(k, None)
                _store[key] = (version, time.monotonic(), body, etag)
                return _response(body, etag, browser_age)
        return wrapper
    return deco


async def not_modified_middleware(request, call_next):
    """Turn a matching If-None-Match into an empty 304. The full answer was
    already computed or cached; this only saves sending it again."""
    response = await call_next(request)
    if request.method == "GET" and response.status_code == 200:
        etag = response.headers.get("etag")
        inm = request.headers.get("if-none-match")
        # WEAK comparison, as RFC 9110 specifies for If-None-Match. Render's
        # proxy gzips the body and rewrites our ETag to W/"...", so the
        # browser sends back the weak form; a strict match never fired live.
        weak = lambda t: t.strip().removeprefix("W/")
        if etag and inm and weak(etag) in [weak(t) for t in inm.split(",")]:
            # Keep every header but the body's own - in particular the CORS
            # ones: the site is cross-origin, and a 304 without
            # Access-Control-Allow-Origin can fail the browser's check.
            keep = {k: v for k, v in response.headers.items()
                    if k.lower() not in ("content-length", "content-type",
                                         "content-encoding")}
            return Response(status_code=304, headers=keep)
    return response
