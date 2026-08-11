# PotentialPro API on Render (free web service).
#
# Build context is the REPO ROOT (see render.yaml), so this copies the real
# source directly — the API that runs in the cloud is byte-for-byte the one
# that runs locally. No reimplementation of build_slip or the booking calls,
# which is the whole reason for hosting rather than porting to JavaScript.
#
# Deliberately slim: the API reads the ledger and derives markets with plain
# maths. No pandas / scikit-learn / playwright — the pipeline does the
# predicting elsewhere and commits the result.

FROM python:3.13-slim

WORKDIR /app

COPY deploy/render/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only what api/server.py imports transitively — named file by file, not whole
# directories: models/ also holds predict.py and train.py (pandas, scikit-learn)
# and scrapers/ holds the Playwright scrapers. None are used by the API, and
# copying them would drag heavy, irrelevant code into the image.
#
# ⚠️ ADDING A NEW MODULE THAT api/server.py IMPORTS? ADD IT HERE TOO.
# This allowlist is a trap worth knowing about: a missing file does NOT fail
# loudly. The image builds fine, the container dies on ModuleNotFoundError at
# import, /healthz never answers, and Render keeps the PREVIOUS version
# running — so the API stays up serving stale code and the deploy looks like
# it simply "didn't happen". That is exactly how league_display appeared to
# deploy for 10 minutes without ever reaching production (Aug 2026).
COPY api/__init__.py api/server.py            ./api/
COPY db/__init__.py db/database.py            ./db/
COPY models/__init__.py models/markets.py models/consensus.py models/leagues.py models/live_scores.py ./models/
COPY scrapers/__init__.py scrapers/booking_codes.py ./scrapers/
COPY deploy/render/sync_ledger.py deploy/render/app_entry.py ./

# Data files models/leagues.py reads at import time.
COPY data/league_codes.json ./data/league_codes.json

# The web app. api/server.py already mounts this at "/" when the directory
# exists — but the directory never existed in the image, so the mount silently
# did nothing and the Render root returned 404 for months. Same allowlist trap
# as models/leagues.py: a missing COPY fails quietly, it does not fail loudly.
#
# With this, EVERY push redeploys a frontend that is guaranteed to match the
# API it ships with. Cloudflare Pages remains the primary host (always-on and
# fast, where Render's free tier sleeps for ~50s), but this means the site can
# never be silently stale relative to the API, and there is a working URL even
# if a Pages deploy is skipped.
COPY webapp/ ./webapp/

# The ledger is committed to the repo, so it ships INSIDE the image — the API
# has real data the moment it boots, with no network round-trip. sync_ledger
# then refreshes it in the background so a long-running instance doesn't serve
# yesterday's picks after the pipeline runs.
COPY db/sports_predictor.db ./db/sports_predictor.db

ENV DB_PATH=/app/db/sports_predictor.db \
    PYTHONUNBUFFERED=1

# Render injects PORT; default matches their convention.
ENV PORT=10000
EXPOSE 10000

CMD ["python", "app_entry.py"]
