"""
Space entrypoint: fetch the ledger, then serve the real FastAPI app.

api/server.py is imported unchanged — that's the whole point of hosting rather
than reimplementing. Every endpoint the web app calls behaves exactly as it
does locally, including build_slip and the booking-code proxying.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sync_ledger import fetch_ledger, start_background_refresh

if __name__ == "__main__":
    # The ledger ships in the image (it's committed to the repo), so a failed
    # refresh is not fatal — we already have real data. Refreshing only keeps a
    # long-running instance from serving yesterday's picks after the pipeline
    # commits a new day. Say which case we're in rather than starting silently.
    if fetch_ledger():
        print("[boot] serving a freshly pulled ledger", flush=True)
    else:
        print("[boot] refresh unavailable — serving the ledger baked into the "
              "image at build time (set GITHUB_TOKEN to enable refresh)",
              flush=True)
    start_background_refresh()

    import uvicorn

    uvicorn.run(
        "api.server:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
        log_level="info",
    )
