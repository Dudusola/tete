# PotentialPro ⚽

Betting predictions for Nigerian bettors: football first, plus NHL, NBA and NFL.
The pipeline pulls fixtures and bookmaker prices, a model rates every game, and
the picks are published to a web app, an Android app and Telegram. Every pick is
graded against the real final score, and the record is public.

**The product stands or falls on an honest track record**, so data integrity
comes before features.

> **New here?** Set up with [CONTRIBUTING.md](CONTRIBUTING.md), then read
> [docs/project-notes.md](docs/project-notes.md), the project notes: why things are built the way they
> are, and the bugs already fixed (so nobody reintroduces them).

---

## It's live

| Piece | Where | Link |
|---|---|---|
| Web app (installable PWA) | Cloudflare Pages | https://potentialpro.pages.dev |
| Android app | a wrapper around the live site, no rebuild needed for site changes | https://potentialpro.pages.dev/app.html |
| API (FastAPI) | Render free tier | https://potentialpro-api.onrender.com/docs |
| Telegram bot | Cloudflare Worker | `/picks` `/slip` `/stats` |
| Daily pipeline | GitHub Actions | started 06:00 UTC by the Worker cron |

Render's free tier sleeps after ~15 minutes idle. The Worker pings it every 10
minutes, but the first load after a long quiet spell can still take ~50 seconds.

Testers have been using the site since 11 Oct 2026.

---

## The record

Don't copy numbers into this file; they go stale. The live figures are on the
Record page, and the same numbers come from:

```bash
python db/calibration_health.py
```

As of 13 Nov 2026 (football, the tiers we publish as bets):

| Tier | Record | Hit rate | Claimed | 95% range |
|---|---|---|---|---|
| HIGH (≥65%) | 379/477 | 79.5% | 74.7% | 75.6–82.8% |
| MEDIUM (≥60%) | 240/388 | 61.9% | 61.4% | 56.9–66.6% |

96.4% of predicted matches have a graded result. NHL, NBA and NFL keep their
**own** records and are never mixed into football's.

What the record does **not** show yet is an edge over the bookmaker. The model
agrees with the market almost exactly (log loss 0.678 vs the market's 0.676), so
a high hit rate mostly means we back the favourites the bookmaker also rates.
The open question is price: does SportyBet pay more than the fair odds? That is
tracked by `db/sporty_value_report.py` and `db/clv_report.py`; nothing has been
proven either way.

---

## What it does

- **Matches** (home page): every fixture by day and league, live and final
  scores, our pick, favourite leagues.
- **Picks**: HIGH and MEDIUM picks with the bookmaker's price beside ours, a
  warning when the price moves against a pick, and short written analysis.
- **Builder**: accumulators by risk level, leagues and sports, with bankers,
  swaps and SportyBet prices per leg.
- **Checker**: paste or build a slip and see our verdict per leg.
- **Booking codes**: create on SportyBet, MSport and Bet9ja; read on those plus
  football.com.
- **Accounts** (Google sign-in): track picks and slips, see your own record and
  profit, get push alerts (morning picks, kick-off, price moves, results).

---

## How it fits together

Three layers. The bot, web app and Android app are faces on one API, and the API
serves whatever the pipeline last wrote; nothing is predicted on demand.

```
ESPN fixtures + odds ──► SQLite ledger ──► model ──► markets ──► written analysis
                              │
                              ├──► FastAPI (api/server.py) ──► web app / Android
                              └──► Telegram (daily push + Worker bot)
```

- **Football model**: a Random Forest on ~72k historical matches (form, goals,
  venue, head-to-head, bookmaker odds). Goals markets come from separate
  attack/defence ratings (`models/goals_model.py`).
- **NHL / NBA / NFL**: Elo ratings plus per-market models, with injury and
  quarterback warnings. See `models/us_sports.py`.
- **The written analysis** (Gemini, falling back to Groq, then Ollama) only
  explains a pick. It can never change a probability.
- **The ledger** (`db/sports_predictor.db`) is committed to this repo and
  rewritten by GitHub Actions. It is binary: always `git pull` before touching
  it, and test anything that writes to it on a copy.
- **User data** lives in Supabase, never in the ledger.

### Data sources

| Source | Gives us | Note |
|---|---|---|
| ESPN | football fixtures, odds, kick-off times, live state, results; all US-sports data | the main source, no key |
| football-data.org | results for the big leagues | free key |
| SportyBet | its own prices and booking | read through our API, since it blocks GitHub's IPs |
| ClubElo | an independent opinion (Elo from results only) | European football only |
| nhle.com, nflverse | NHL goalies, NFL starting quarterbacks | free |
| PredictZ, Forebet | fixtures (historic) | both block GitHub's IPs; switched off in the cloud |
| API-Football | fixtures + odds | account suspended (shared-IP rule); off |

---

## What runs by itself

| What | When |
|---|---|
| Daily pipeline: fixtures → predictions → Telegram → ledger commit | `daily.yml`, 06:00 UTC (GitHub's 08:50 cron is a backup) |
| Closing prices, US-sports lines, hourly analysis refresh | `closing.yml`, every hour at :20 |
| Evening results sweep | `results.yml`, 22:00 UTC |
| API redeploy | Render, on every push to `main` |
| Web app deploy + cache-busting | `deploy-webapp.yml`, on pushes touching `webapp/` |
| Worker (bot, crons, push alerts) | `deploy-worker.yml`, on pushes touching `worker/` |
| Android app build | `android-apk.yml` |

The Telegram morning message also carries a health report
(`db/daily_health.py`): how many priced games were predicted and served, how
many are bookable on SportyBet, grading, and data age.

---

## Running it locally

Full setup in [CONTRIBUTING.md](CONTRIBUTING.md). Short version (Windows):

```bash
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium firefox
cp .env.example .env                               # then fill in your own keys
python db/database.py                              # schema
python models/train.py                             # builds the football model
```

```bash
python run_daily.py --no-gemma                     # full pipeline, no AI text
uvicorn api.server:app --reload --port 8080        # API at /docs (port 8000 is blocked here)
cd webapp && python -m http.server 3000            # web app: over http, never file://
python db/daily_health.py                          # is today's data healthy?
```

**Don't run the local pipeline against the real ledger** while the cloud one is
live: two writers make two diverging track records. Point `DB_PATH` at a copy.

---

## Known limits

- **One football source in the cloud.** ESPN is it. PredictZ and Forebet block
  GitHub's IPs, and API-Football needs a dedicated IP before it can be
  reinstated. Leagues ESPN doesn't price don't get picks.
- **No proven edge over the bookmaker yet**, in any sport (see The record).
- **Bet9ja has no Draw No Bet or Handicap**, so slips that use them only partly
  book there. Unmappable legs are reported, never swapped for a different bet.
- **1xbet** can't be reached (Cloudflare on `.com`, HTTP 406 on `.ng`).
- **Only some goals legs are measured.** "Under 3.5" at ≥70% still runs ~5
  points below its claim (`db/market_calibration.py`, re-run monthly).

---

## More docs

- [docs/project-notes.md](docs/project-notes.md): project notes, fixed bugs, decisions and their reasons
- [CONTRIBUTING.md](CONTRIBUTING.md): setup and how to work without clashing
- [DEPLOY.md](DEPLOY.md): hosting and secrets
- [PRODUCT.md](PRODUCT.md): who the users are and the design direction
- [docs/basketball.md](docs/basketball.md): the US-sports research notes
