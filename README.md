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
| Daily pipeline | GitHub Actions | 08:50 UTC ≈ 10:00 Lagos, `.github/workflows/daily.yml` |

**Render's free tier sleeps after ~15 minutes idle**, so the first page load
after a quiet spell takes ~50 seconds. Not a bug.

---

## Current status — read this before quoting any number

Figures below were regenerated on **11 Aug 2026**. Regenerate them rather than
trusting this table — the ledger moves daily, and this file goes stale between
commits by design.

| Tier | Record | Hit rate | 95% confidence interval |
|---|---|---|---|
| HIGH | 28/37 | **76%** | 60–87% |
| MEDIUM | 39/63 | **62%** | 50–73% |
| LOW | 83/158 | **53%** | 45–60% |
| **All tiers** | **150/258** | **58%** | 52–64% |

| | |
|---|---|
| Graded picks | **258** |
| Served picks (HIGH+MEDIUM, the ones published as bets) | 67/100 = **67%** |
| Ready for testers? | **Not yet** — see "what would change that" below |

**Read the ceiling before judging any of these numbers.** The model has never
once rated a draw as the most likely result — 0 of ~360 predictions — while
**58 of 258 graded matches (22.5%) ended level**. Every draw is a loss it
cannot avoid, so the maximum achievable hit rate on this ledger is **77.5%**,
not 100%. HIGH at 76% is therefore running at ~98% of what is reachable, which
is a very different sentence from "76 out of 100".

**How the headline has moved, because the history is the point:**

| When | HIGH | What changed |
|---|---|---|
| July (training eval) | ~75% | never real — measured on training data |
| Late July | 57% | first honest grading |
| 6 Aug | 38% | fixed a results-coverage gap (bug #8) |
| 8 Aug | 67% | fixed a second coverage gap |
| 11 Aug | 76% | fixed a grading MATCHER, not the model (see below) |

**Four times the headline moved sharply and the model was unchanged every
time.** What moved was which matches could be graded. The most recent: the
grading matcher compared raw strings, so "Cardiff" never matched "Cardiff
City" and 11 of 14 EFL Cup ties sat ungraded while ESPN had every score. That
omission was not random — it was "clubs whose short name differs from the
feed's", overwhelmingly English — so the tier percentages were computed on a
biased sample.

**The lesson, stated once and enforced in code:** a tier percentage is
meaningless if the picks it omits are not random. Run
`python db/track_record_health.py` — it reports **coverage**, not just cadence
— before quoting any accuracy figure anywhere.

**The published number blends model versions, and that is unresolved.** The
same lesson, one step further: a tier percentage is also meaningless if the
picks in it came from different models. Graded HIGH picks in the ledger as of
23 Aug 2026:

| version | graded HIGH | correct | |
|---|---|---|---|
| `rf_v1` | 5 | 2 | 40.0% |
| `rf_v1b` | 3 | 3 | 100.0% |
| `rf_v1c` | 1 | 0 | 0.0% |
| `rf_v1d` | 11 | 7 | 63.6% |
| `rf_v1e` | 61 | 45 | 73.8% |

The headline is computed across **all** of them. `model_predictions.model_version`
records which model made each pick, but only `db/track_record_health.py` can
filter on it (optional argument) — the bot, the API and `feed/bot_feed.json`
do not. So the published figure is dominated by `rf_v1e` and slightly dragged
by 20 picks from four earlier models, one of which went 2-for-5.

This needs a decision rather than a quiet fix, because both options cost
something real:

- **Filter to the current model** — honest, but resets the graded count to zero
  the day a new model ships, losing the 30-sample gate the tiers rest on.
- **Keep blending** — preserves the sample, but the number describes a mixture
  no user is actually being served.

Not decided yet. It matters now because `rf_v2` (see below) is a materially
different model, not a tweak.

**What would make this ready for testers:** HIGH has passed the 30-sample gate
(37 graded). The remaining blocker is not the number, it is that consensus is
currently blind — see Known limitations. Two independent opinions are what the
"cross-checked" story rests on.

The old claim of "HIGH ≈ 75%" came from training-set evaluation, not graded
results. Don't repeat it — and note the same trap applies to the `rf_v2`
hold-out figures below. A hold-out test set is a genuinely better measurement
than a training set, but it is still not a graded live result.

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
| API-Football | fixtures + odds (secondary) | **account suspended — twice.** Step is OPTIONAL; see limitations |
| Forebet | fixtures + Over 2.5 % | **laptop only** — Cloudflare 403s all cloud IPs |
| Gemini 3.5 Flash Lite | the written analysis | free tier; Ollama/Gemma is the offline fallback |
| SportyBet / MSport / Bet9ja | booking codes (read AND write) | unofficial APIs; Bet9ja uses its own ids, not Betradar |
| **ClubElo** | **independent H/D/A probabilities** | free, no key; an Elo model built from RESULTS — the only source here that has never seen a bookmaker line |
| Groq (Llama, open-weights) | analysis fallback | free tier; fires only if Gemini fails |

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

### 23 August: the model was trained on odds it mostly didn't have (`rf_v2`)

`models/train.py` read bookmaker odds from three columns — `B365H/D/A` — and
substituted a flat `0.33/0.33/0.33` whenever they were missing. Missing was
the common case: **60.5% of the 70,916 training rows** got the constant.

The odds columns are the model's three most important features. So on most of
its training data, the strongest signal it had was a constant — and a constant
that says "all three outcomes are equally likely", on rows where the actual
outcome was known and usually wasn't a draw. That is not a gap in coverage, it
is a systematic lie about six matches in ten.

Most of those rows *did* have odds, just under different names. football-data.co.uk
publishes closing prices as `B365CH/CD/CA` and carries Pinnacle, market-average
and best-available families too; 85 of 102 files have Bet365 closing prices and
**101 of 102 have some closing family**. The loader looked for exactly one
family and gave up.

Fixed by trying seven column families in order (closing before opening, so the
feature means one thing across the whole set) and adding an explicit
`odds_is_market` flag — 1.0 for a real price, 0.0 for the fallback — so the
model can tell a market price from a placeholder instead of being told a
placeholder *is* a market price. Coverage went **39.5% → 94.1%**.

Measured on the same time-aware hold-out split, before and after:

| | `rf_v1e` | `rf_v2` |
|---|---|---|
| odds coverage in training | 39.5% | **94.1%** |
| overall accuracy | 49.7% | 51.3% |
| ≥65% tier | 74.2% on 8.4% of matches | **74.6% on 12.5%** |
| ≥55% tier | 65.1% on 24.3% | 65.0% on 33.3% |
| draws predicted | ~0 (recall 0.00) | **129**, precision 0.32 |
| odds share of feature importance | 41.3% | 66.6% |

The result that matters is the second-to-last row: **the HIGH tier holds its
accuracy while covering 49% more matches.** Same quality, half again as many
picks — that is more served fixtures per day, not a better-looking number.

The draw row is the diagnosis confirmed. "Never predicts a draw" was a known
pathology with several plausible causes; a constant 0.33 draw prior on 60% of
training rows was one of them, and removing it produced draws immediately —
129 of them at 32% precision against a 25.7% base rate. Not yet a good draw
model. No longer a blind one.

**Overall accuracy is still 51.3%, below the ~53–55% a bookmaker's favourite
gets.** Stated plainly because it is the number that matters for the product's
premise: the calibrated HIGH tier is where the value is, and the flat top-line
is unchanged by this work. `python db/edge_report.py` remains the honest test.

`MODEL_VERSION` is bumped to `rf_v2` in `models/predict.py`, so live picks are
attributable — see the version-blending problem in Current status. Editing
`train.py` also invalidates the GitHub Actions model cache key, so the cloud
retrains itself on the next daily run; no manual step.

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

### 9–11 August: silent failures, and one loud one

Almost everything here was a system reporting success while doing nothing. The
pattern is the point — none of it threw an error.

- **Consensus returned 0 covered fixtures.** Not a data drought: the query took
  `MAX(id)` per source, and PredictZ writes rows with NULL percentages when it
  has no odds, so a newer empty row silently deleted an older real one.
  0/19 → 19/19 after filtering inside the subquery.
- **The deduper had crashed on every run for six consecutive runs.** It read a
  column the query never selected. The step was non-fatal, so every run
  reported success while duplicate fixtures accumulated — one real match was
  counted twice in the track record.
- **The grading matcher never matched short club names.** "Cardiff" vs "Cardiff
  City" scored 0.65 on raw string similarity. Eleven of fourteen EFL Cup ties
  sat ungraded with ESPN holding every score. Fixing it recorded 57 results in
  one run. This was the **third** private copy of a team matcher found in the
  codebase; all now delegate to the one in `models/consensus.py`.
- **A pulsing "LIVE" badge on matches that finished 12 hours earlier.**
  `live_state` is a snapshot, not a feed, and never expired. It does now.
- **The Builder's goals markets were fiction** — the predicted *scoreline*
  (an integer) was being used as an expected-goals *rate*, pricing "Under 2.5"
  at 91% while bookmakers had it near even money. Caught by Boss from the
  betting side, not by the code.
- **The whole product went dark for a day** — and the pipeline was working. See
  bug #14 in CLAUDE.md; two of the three faults were introduced by the previous
  day's reliability "improvement".

Added in the same window: booking-code generation for **Bet9ja** (contract
recovered from their JS bundle, not a devtools capture), slip tools (split /
trim weak legs / convert / merge), live and final scores on started matches, a
**market check** showing the bookmaker's price beside our own probability on
every pick, a league filter, a weekly hit-rate chart, and a five-item mobile
menu that replaced a hamburger sheet.

The market check earns its place immediately: it caught a bug on its first run
(ours, in the glue code) and now shows our numbers tracking SportyBet within
±8 points — quiet, continuous validation that the 1X2 model is calibrated.

---

## Operating it — what a new team member needs to know

**Nothing depends on a laptop, and nothing needs running by hand.**

| What happens | When |
|---|---|
| Pipeline: fixtures → predictions → Telegram → bot feed → ledger commit | `daily.yml`, cron **08:50 UTC** (≈10:00 Lagos) |
| Evening results sweep | `results.yml`, cron **22:00 UTC** |
| API redeploy | Render, on every push to `main` |
| Web app deploy **and** cache-bust | `deploy-webapp.yml`, on any push touching `webapp/` |

The `api.js?v=N` cache-bust used to be a manual edit across five files, and
forgetting it served stale JavaScript against new HTML — the cause of a real
"Couldn't book" bug. CI now stamps a **content hash** instead: it cannot be
forgotten and cannot be wrong. Identical file → identical query string, so
caching still works; changed file → new string, so nobody sees stale JS.

**One-time setup, the only human step left.** The deploy workflow needs
Cloudflare credentials; until they exist it skips with a notice instead of
failing, so nothing breaks in the meantime:

```bash
# Cloudflare dashboard -> My Profile -> API Tokens -> Create Token
# (template "Edit Cloudflare Workers", or Account | Cloudflare Pages | Edit)
gh secret set CLOUDFLARE_API_TOKEN  --body "<token>"
gh secret set CLOUDFLARE_ACCOUNT_ID --body "<account id>"
```

Use `--body`, never a pipe: PowerShell 5.1 prepends a BOM that breaks the value
server-side. Manual deploy still works if ever needed:
`npx wrangler pages deploy webapp --project-name potentialpro`.

### How it fails, and how to tell

The pipeline grades its own steps. Read `run_daily.py`'s severity block first —
it is the single most useful thing to understand about operations.

| Level | Meaning | Examples |
|---|---|---|
| `CRITICAL` | aborts the run; downstream would build on bad data | Generate predictions; "every fixture source failed" |
| `DEGRADED` | run continues but ends **red** and alerts | PredictZ, results scrapers, dedupe, kickoff times |
| `OPTIONAL` | logged, never red — needs a written justification | Forebet (IP-blocked by design), API-Football, analysis backfill |

**Do not mark a step OPTIONAL to silence an alert.** The bar is "there is a
real fallback", not "it fails a lot". That distinction is the difference
between an alert that means something and one everybody ignores.

**Two traps that have already bitten, both recorded as bug #14:**

1. **A red run SKIPS steps.** GitHub's default is "run only if everything
   before succeeded". When failures started turning runs red, the Telegram
   push and bot feed were silently skipped — the picks existed, nobody was
   told. Anything that must happen regardless needs `if: always()`.
2. **The ledger push can fail while everything else works.** `data/historical/*.csv`
   are tracked and get rewritten by the CSV refresh step, which once blocked
   `git pull --rebase` and threw away a whole day of predictions — under an
   error message that wrongly blamed a second writer.

### First things to check when something looks wrong

```bash
gh run list --workflow=daily.yml -L 5        # did it run, and was it green?
python db/track_record_health.py             # coverage + freshness, not cadence
curl -s .../api/picks | head                 # is the API serving today's data?
```

The health check answers "did fresh rows land?", which is the right question.
"Did the last run succeed?" is not — a run can succeed and write nothing.

---

## Known limitations

- **Consensus now has a genuinely independent second opinion — but thin
  coverage.** Until 11 Aug every source traced back to bookmaker prices
  (PredictZ publishes odds-derived percentages; the ESPN rows are odds
  converted), so "2 sources agree" meant two views of one market. **ClubElo**
  fixes the independence: an Elo model built from results, which has never seen
  a line. First run produced exactly what independence should — three
  agreements and one genuine disagreement (Slovan Bratislava: ESPN home,
  ClubElo away). Two market-derived feeds almost never disagree, which is why
  the old consensus looked reassuring and said nothing.
  **The remaining limit is coverage**: ClubElo covers European club football
  and the UEFA competitions, so it matched 6 of 28 fixtures during a Scottish
  Challenge Cup week. That rises with the European season. API-Football is
  still suspended and Forebet still cannot run in the cloud (re-probed 11 Aug:
  HTTP 403 from Actions on plain requests, Chromium *and* Firefox — IP
  reputation, so no stealth work will fix it).
- **Forebet cannot run in the cloud.** Cloudflare 403s GitHub Actions (plain
  HTTP, Chromium *and* Firefox) and blocks Workers with error 1042. Verified
  exhaustively — don't re-test without a budget for a residential proxy. It
  still runs locally, where it works.
- **The goals markets are approximate.** Over/Under, BTTS and Handicap come
  from a Poisson matrix whose TOTAL is a league-average prior, not a modelled
  number. They are accurate in aggregate (within a point of real football at
  every line) but carry no per-match edge. Handicap is derived from the model's
  own win probabilities and does move per fixture; Over/Under barely does.
  Fixing it properly needs team attack/defence rates or the bookmaker's own
  line — not the predicted scoreline, which is an integer and was once fed in
  as a rate, producing "Under 2.5 at 91%, LOW RISK" against a bookmaker price
  near even money.
- **Bet9ja has no Draw No Bet and no Handicap** (surveyed across 24 live
  events). Since slips lean on exactly those, a typical slip only partially
  maps; unmappable legs are reported, never bent onto a neighbouring market.
- **1xbet is unreachable**: Cloudflare on `.com`, HTTP 406 on `.ng`.
- Booking codes: read **and** write on SportyBet, MSport and Bet9ja; read-only
  on football.com and Betano. Slip tools (split, trim weak legs, convert,
  merge) live on the Checker page.

---

## What's next (priority order)

1. **Get a second independent source back.** This is now the top item — it
   blocks the consensus story and the tester invite. Options: resolve the
   API-Football account, pay for a residential proxy so Forebet works in the
   cloud, or find a new source that publishes its own probabilities.
2. **Let the ledger keep accumulating.** Zero work, high value.
3. **Close the grading-coverage gap** — align predicted leagues with scoreable
   ones. `scrapers/espn_leagues.py` is the single list both the results scraper
   and the kickoff enrichment read, so they cannot drift apart again.
4. **A real goals signal** (team attack/defence rates), which would make the
   Over/Under markets carry actual information.
5. **Answer "do our disagreements win?"** — now measurable rather than
   aspirational. `scrapers/market_snapshot.py` records the bookmaker's price
   for every pick at pick time; `python db/edge_report.py` groups graded picks
   by how far we sat from the market and reports who was right.
   **This is the question the product rests on.** Today's baseline: mean gap
   **-1.0 points**, zero disagreements of 15+ points — we currently agree with
   the market almost exactly, which means no edge has been demonstrated in
   either direction. Note it cannot be backfilled: odds move, so only picks
   made from 11 Aug onward carry a snapshot.
6. Web push notifications (designed, deferred — Telegram already pushes daily).

**Settled, so nobody reopens it:** the "raise the HIGH threshold to ~72%" plan
is withdrawn. It came from the 65–70% band looking weak on 7 samples; with 37
graded HIGH picks that band sits at 73% and the tier ranks correctly. Leave the
threshold at ≥65%.

**Do not invite testers until the track record is real.** Credibility is
one-shot, and the ledger builds itself for free while you wait.
