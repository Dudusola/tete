# PotentialPro ⚽

AI-powered football prediction engine for Nigerian bettors. Scrapes fixtures and
odds, runs a Random Forest over 70k historical matches, has a local/hosted LLM
write the reasoning, and publishes picks to a web app and Telegram.

**The product's credibility rests entirely on an honest track record**, so data
integrity outranks features. Every number shown is one the pipeline actually
computed against verified final scores.

> **New here?** Read [CONTRIBUTING.md](CONTRIBUTING.md) for setup, then
> [CLAUDE.md](CLAUDE.md) for the real project memory — architecture decisions,
> the bugs already fixed, and *why* things are the way they are. Reading
> CLAUDE.md before changing anything will save you a lot of time.

---

## It's live — nothing depends on anyone's laptop

| Piece | Where | URL |
|---|---|---|
| Web app (installable PWA) | Cloudflare Pages | https://potentialpro.pages.dev |
| API (FastAPI) | Render free tier | https://potentialpro-api.onrender.com/docs |
| Telegram bot | Cloudflare Worker | message the bot: `/picks` `/stats` `/help` |
| Daily pipeline | GitHub Actions | 11:00 UTC = 12:00 Lagos, `.github/workflows/daily.yml` |

**Render's free tier sleeps after ~15 minutes idle**, so the first page load
after a quiet spell takes ~50 seconds. Not a bug.

---

## Current status — read this before quoting any number

Figures below are from `db/track_record_health.py` on 7 Aug 2026. Regenerate
them rather than trusting this table — the ledger moves daily.

| Tier | Record | Hit rate | 95% confidence interval |
|---|---|---|---|
| HIGH | 5/13 | **38%** | 18–64% |
| MEDIUM | 15/20 | **75%** | 53–89% |
| LOW | 30/59 | 51% | 38–63% |
| **Overall** | **50/92** | **54%** | 44–64% |

| | |
|---|---|
| Graded picks | **92** |
| Grading coverage | **92 of 212** predicted matches (43%) |
| Ready for testers? | **No** — need ~100 graded and ~30 HIGH |

**The HIGH tier is currently performing WORSE than MEDIUM**, which is the open
question of the project. Two contributing causes are understood — a consensus
"boost" that went 0/3 and has been disabled, and 60–70% picks polluting the HIGH
band — but note the confidence intervals: **HIGH's 18–64% overlaps MEDIUM's
53–89%**, so on this sample the inversion is *not statistically established*. It
is a serious warning sign, not yet a proven fact. Thirteen samples cannot settle
it either way, which is exactly why the answer is "accumulate more", not
"retune the thresholds".

Run `python db/track_record_health.py` before trusting any accuracy figure. It
reports grading **coverage**, not just cadence — a tier percentage is meaningless
if the picks it omits are the ones that lost. That has already happened once:
fixing a results-coverage gap dropped HIGH from 57% to 38%.

The older claim of "HIGH ≈ 75%" came from the training-set evaluation, not from
real graded results. Don't repeat it.

---

## Architecture

Three decoupled layers — the bot and web app are two faces on the same API,
which serves whatever the daily pipeline last wrote. Nothing is generated on
demand.

```
scrapers ──► SQLite ledger ──► Random Forest ──► markets ──► Gemini analysis
                  │
                  ├──► FastAPI (api/server.py) ──► web app (webapp/)
                  └──► Telegram (daily push + webhook bot)
```

**The ledger (`db/sports_predictor.db`) is committed to this repo** and rewritten
daily by GitHub Actions. It is the authoritative track record. Always `git pull`
before touching it — it's binary and cannot be merged.

### Data sources and why each exists

| Source | Provides | Notes |
|---|---|---|
| PredictZ | fixtures + odds (primary) | Playwright **Firefox** — Chromium gets challenged |
| ESPN API | kickoff times, live state, results | keyless, no quota, the most reliable dependency |
| football-data.org | results (big leagues) | free key; free tier has **no odds** |
| API-Football | fixtures + odds (secondary) | free tier, easily rate-limited; optional |
| Forebet | fixtures + Over 2.5 % | **laptop only** — Cloudflare 403s all cloud IPs |
| Gemini 3.5 Flash Lite | the written analysis | free tier; Ollama/Gemma is the offline fallback |

Analysis is **explanation only, never the predictor**. Swapping the LLM cannot
affect calibration.

---

## Quick start

Full instructions in [CONTRIBUTING.md](CONTRIBUTING.md). Short version:

```bash
python -m venv venv && venv\Scripts\activate      # Windows
pip install -r requirements.txt
playwright install chromium firefox                # BOTH — predictz needs firefox
cp .env.example .env                               # then fill in your own keys
python db/database.py                              # schema
python models/train.py                             # builds the model from data/historical
```

Run things:

```bash
python run_daily.py                                # full pipeline (--no-gemma to skip AI text)
uvicorn api.server:app --reload --port 8080        # API at /docs
cd webapp && python -m http.server 3000            # web app — http, never file://
python db/track_record_health.py                   # ledger health + coverage
```

Port 8000 is blocked on the primary dev machine; the project standardises on 8080.

---

## What changed in August 2026

A single day of work moved the project off one laptop and fixed several silent
failures. Listed because most of them are the kind of bug that produces
plausible-looking numbers rather than errors.

### Correctness fixes

- **The ledger had been frozen for 12 days.** Not a code bug: Windows Task
  Scheduler refused the job on battery power (`DisallowStartIfOnBatteries`), so
  nothing ran and nothing logged. `rf_v1e` had produced **zero** predictions
  since being shipped. Recorded as bug #9 in CLAUDE.md.
- **The health check measured the wrong thing** — log-file timestamps instead of
  data. It reported "4 days" while the database was 12 days cold. It now reports
  the age of the newest row in each table, and flags "log fresh but no data".
- **Predictions were being made from an invented prior.** When a fixture had
  neither odds nor source percentages, the feature builder substituted a flat
  33.3/33.3/33.3 into features trained on real bookmaker odds. It never raised —
  it just produced quietly worse picks that were still saved and graded. It now
  refuses and skips, counted separately in the run summary.
- **Matches that had already kicked off were being recommended.** Two causes:
  `kickoff_time` is a bare `HH:MM` with no timezone that PredictZ leaves at
  `00:00` (meaning *unknown*) for ~88% of fixtures, and the frontend treated
  that as midnight. Added `matches.kickoff_utc` populated from ESPN, plus live
  `pre`/`in`/`post` state. Unknown times keep a match **visible** — hiding a
  match we merely failed to time is the worse error.
- **Telegram messages over 4096 characters silently failed.** A 20-pick day
  exceeded the limit and delivered nothing; messages are now split on pick
  boundaries.

### Infrastructure

- Pipeline runs on **GitHub Actions**; local scheduled tasks are disabled.
  Running both would give two diverging ledgers.
- API deployed to **Render** running the real FastAPI — deliberately *not* a
  JavaScript reimplementation, because `build_slip` and the booking-code calls
  existing twice would drift apart.
- Web app on **Cloudflare Pages**; Telegram bot as a **Cloudflare Worker**
  webhook, so `/picks` and `/stats` answer without any process running.
- Daily Telegram push moved into the pipeline — no long-running bot needed.
- Analysis moved from local Gemma to hosted **Gemini** (Ollama kept as fallback
  via `AI_PROVIDER=ollama`). 45 matches in ~2 minutes versus ~7.
- Training CSVs are now committed, so a fresh clone can train with no downloads.
- `requirements.txt` was stale and **omitted fastapi/uvicorn entirely** — a
  clean install produced a broken environment. Rebuilt from the working venv.

### UI

- Fixed bottom nav sitting under the iPhone home indicator, content hidden
  behind that nav, horizontal scroll from long league slugs, sub-44px touch
  targets, and text overflow on ≤380px screens.
- LIVE badge on in-play matches; kickoff times shown in the viewer's timezone.

---

## Known limitations

- **Forebet cannot run in the cloud.** Cloudflare 403s GitHub Actions (plain
  HTTP, Chromium *and* Firefox) and blocks Cloudflare Workers with error 1042.
  Verified exhaustively — don't re-test it without a budget for a residential
  proxy. It still runs locally, where it works.
- **~5 of 18 displayed picks show "time tbc"** — leagues ESPN doesn't carry.
- **Grading coverage is 43%** (92 of 212). Some leagues we predict (Veikkausliiga,
  Superettan, Copa Sudamericana) have no working results source. Aligning
  predicted leagues with scoreable ones is the fix, not re-keying.
- Telegram bot: `/picks`, `/slip` (with a SportyBet booking code attached),
  `/stats`, `/help`. Inline buttons and Telegram's command menu mean a new user
  needs no prior knowledge. `/slip` calls the real API rather than rebuilding
  build_slip in JavaScript, so it cannot drift from the engine.
- Booking-code generation is **verified working** from both the laptop and
  Render (codes UFDQE5 and ZW74P1 loaded in SportyBet). SportyBet does not
  geo-block Render's European IPs.

---

## What's next (priority order)

1. **Let `rf_v1e` accumulate two weeks.** Zero work, highest value. The pipeline
   runs itself now.
2. **Investigate the HIGH-tier inversion** once the sample supports it. Candidate
   fixes: raise the HIGH threshold from ≥65% to ~72%, and/or league-aware
   confidence. Do not hard-change calibration on 13 samples.
3. **Close the grading-coverage gap** — align predicted leagues with scoreable
   ones.
4. Model-vs-source tracker on real results — validates the core premise.
5. Web push notifications (designed, deferred — Telegram already pushes daily).

**Do not invite testers until the track record is real.** Credibility is
one-shot, and the ledger builds itself for free while you wait.
