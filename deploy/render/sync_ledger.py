"""
Pull the ledger from the GitHub repo into the Space.

The pipeline writes db/sports_predictor.db and commits it. This Space is a
READER — it never writes the ledger back, so there is exactly one writer and no
chance of two copies diverging.

Refreshed periodically rather than only at boot: a Space can stay up for days,
and serving yesterday's picks as if they were today's is the failure mode this
project keeps paying for. If a refresh fails we keep the copy we have and say
so in the logs — stale data is recoverable, no data is not.
"""

import os
import time
import threading

import requests

REPO = os.getenv("LEDGER_REPO", "Dudusola/tete")
LEDGER_PATH = os.getenv("LEDGER_PATH", "db/sports_predictor.db")
DB_PATH = os.getenv("DB_PATH", "/app/db/sports_predictor.db")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
REFRESH_MINUTES = int(os.getenv("LEDGER_REFRESH_MINUTES", "30"))


def fetch_ledger() -> bool:
    """Download the current ledger. Returns True if the file was updated."""
    if not GITHUB_TOKEN:
        print("[sync] GITHUB_TOKEN not set — cannot fetch the ledger.", flush=True)
        return False

    url = f"https://api.github.com/repos/{REPO}/contents/{LEDGER_PATH}"
    try:
        r = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                # raw, so we get the binary rather than base64 JSON
                "Accept": "application/vnd.github.raw",
                "User-Agent": "potentialpro-space",
            },
            timeout=60,
        )
    except requests.RequestException as e:
        print(f"[sync] fetch failed: {e}", flush=True)
        return False

    if r.status_code != 200:
        print(f"[sync] HTTP {r.status_code}: {r.text[:160]}", flush=True)
        return False

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # Write to a temp file then move, so a half-downloaded ledger can never be
    # served: the swap is atomic.
    tmp = f"{DB_PATH}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(r.content)
    os.replace(tmp, DB_PATH)

    print(f"[sync] ledger updated: {len(r.content)} bytes", flush=True)
    return True


def _loop():
    while True:
        time.sleep(REFRESH_MINUTES * 60)
        fetch_ledger()


def start_background_refresh():
    threading.Thread(target=_loop, daemon=True).start()
    print(f"[sync] background refresh every {REFRESH_MINUTES} min", flush=True)


if __name__ == "__main__":
    fetch_ledger()
