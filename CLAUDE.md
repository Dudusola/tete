# PotentialPro — Project Memory (CLAUDE.md)

> This file orients any Claude Code session. Read it first. It captures not just
> *what* the code does but *why* decisions were made and *what bugs were already
> fixed* — so they aren't reintroduced.

## What this is
A zero-budget AI sports-betting **prediction** web app for Nigerian football
bettors. Football-first, multi-sport eventually. Solo developer ("Boss").
Personal-use phase now; commercial later. Inspired by Puntrr.ai and Forebet.
The product's credibility rests entirely on an **honest track record**, so data
integrity is the highest priority — higher than features.

## Architecture (3 decoupled layers)
1. **Engine** — scrapers → Random Forest model → market derivation → Gemma analysis
2. **API** — FastAPI exposing the engine as JSON (port 8080)
3. **Frontends** — Telegram bot (live) + web app / PWA (webapp/, being built)

The layers are decoupled on purpose: the bot and web app are just two faces on
the same API. The API serves whatever the daily pipeline last wrote; it does not
generate predictions on demand.

## Environment
- Windows + PowerShell, Python 3.13, venv
- Project root: `C:\Users\OWNER\Desktop\PECULIAR DESKTOP\projects\files`
- Ollama local LLMs: `gemma3:4b` (analysis/reasoning), `qwen3-coder:30b` (coding)
- `.env` holds: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, OLLAMA_MODEL, OLLAMA_HOST,
  DB_PATH, FOOTBALL_DATA_TOKEN
- DB: SQLite at ./db/sports_predictor.db

## Run commands
- Full daily pipeline:  `python run_daily.py`  (add `--no-gemma` to skip AI text, faster)
- API:                  `uvicorn api.server:app --reload --port 8080`  (docs at /docs)
- Web app:              `cd webapp; python -m http.server 3000`  (NOT file:// — breaks fetch)
- Bot:                  `python bot/telegram_bot.py`
- Dedup DB:             `python db/dedupe_fixtures.py` (dry run) then `--apply`
- Score results:        `python scrapers/results_api.py` (API, reliable) and
                        `python scrapers/results.py` (Forebet scrape; `--headed` if Cloudflare-blocked)

## File map (under project root)
- run_daily.py            one-command pipeline: forebet → predictz → results(Forebet) → results(API) → predict → consensus
- scrapers/forebet.py     Playwright scrape of Forebet predictions (today+tomorrow)
- scrapers/predictz.py    Playwright scrape of PredictZ; odds→implied probs. Flaky; pipeline tolerates failure.
- scrapers/results.py     REAL final scores from Forebet. Hardened vs Cloudflare (retries, stealth, --headed).
- scrapers/results_api.py Final scores from football-data.org API (free tier). Cloudflare-immune. Covers big leagues only.
- scrapers/results_espn.py Final scores from ESPN public scoreboard API (no key). Third results source,
                          Cloudflare-immune; ~30 leagues football-data misses: MLS/USL, Liga MX, South
                          America (Arg 1+2, Bra 2, Ecu, Col, Uru, Par, Chi, Per), UEFA comps (qualifiers!),
                          Scandinavia, smaller Europe, China/India/A-League. Only records events ESPN marks
                          completed. Fetches league×day scoreboards in a 10-thread pool (~30s; sequential
                          took 18 min and would hit the 15-min step timeout).
- models/train.py         Random Forest trainer. 66.8k historical matches (89 CSVs since 2018 + internationals).
                          July 2026: refreshed all football-data.co.uk extra-league CSVs and added
                          SwedishLeague/FinlandLeague/IrelandLeague (in-season leagues) — doubled July
                          pick volume (6→12/day). Refresh source: football-data.co.uk/new/<CODE>.csv
                          (ARG BRA USA MEX CHN JPN NOR AUT DNK POL ROU RUS SWZ SWE FIN IRL → the
                          *League.csv names). Note ARG/BRA/USA/MEX lag ~6 weeks at the source itself.
- models/predict.py       Live predictions + Gemma. TEAM_ALIASES dict for name mismatches.
- models/markets.py       Derive ~22 markets from H/D/A + Poisson score matrix (1X2, DC, DNB,
                          O/U 1.5/2.5/3.5, BTTS, Asian Handicap ±1.5, Result&Goals). build_slip dedupes by teams + ±1 day.
- models/consensus.py     Cross-source agreement (Forebet vs PredictZ), fuzzy teams + ±1 day window.
- analysis/gemma_analyst.py  Gemma via Ollama /api/chat (reasoning layer, NOT the predictor)
- api/server.py           FastAPI. Endpoints below.
- bot/telegram_bot.py     /start /picks /slip /stats + daily 08:00 send. drop_pending_updates=True.
- bot/slip_handlers.py    /slip interactive flow
- db/database.py          schema/helpers
- db/dedupe_fixtures.py   merge cross-source duplicate fixtures (fuzzy teams + ±1 day). Handles results UNIQUE(match_id).
- db/purge_results.py     wipe contaminated results table (used once after the fake-scores fix)
- db/inspect_scores.py    read-only: show scored picks vs recorded scores
- models/model_vs_source.py  read-only: how often model disagrees with Forebet
- webapp/                 the PWA. index.html (Picks), slip.html (Builder), record.html (Track Record),
                          consensus.html, checker.html (Slip Checker). js/api.js = fetch glue. logo.png in header.
                          Picks page has a summary stat strip, tier filter chips (All/High/Medium),
                          skeleton loaders, and per-card "Expected: H–A" scoreline. Dark mode lives in
                          js/api.js: it injects !important overrides remapping the Material tokens to a
                          dark palette under html.dark (covers accent/error/border tokens too — keep new
                          token classes in that list) + a .theme-toggle button injected in-flow into the
                          desktop top nav and the mobile bottom nav (never floating over content),
                          persisted in localStorage 'pp-theme'. api.js also makes the logo+name a
                          link to index.html on every page, and on mobile trims the bottom nav to
                          Picks/Builder/Record/More — Consensus, Checker and the theme toggle live in
                          the #pp-sheet bottom sheet behind the More (hamburger) button.
                          PWA (July 2026): manifest.json + icons/ (from logo.png) + sw.js registered by
                          api.js. sw.js is NETWORK-FIRST on purpose (cache only serves offline) — never
                          switch it to cache-first for HTML/JS or the stale-api.js bug returns.
                          prettyLeague() in api.js is the shared league-slug prettifier (all 5 pages).
                          Project is a git repo now (initial commit July 2026; .gitignore excludes
                          .env, venv, db/*.db, data/, models/*.joblib). NEVER edit these html files with
                          PowerShell Get-Content/Set-Content — PS5.1 reads UTF-8 as ANSI and mojibakes
                          •/≥/– (happened once; repaired via cp1252→utf8 reversal). HTML loads js/api.js?v=N — BUMP N on any api.js change
                          or browsers serve stale JS (the "Couldn't book" bug). Note nav bars use the
                          bg-…/90 opacity variant, themed separately in the dark CSS.

## API endpoints (port 8080)
GET / ; GET /api/picks ; GET /api/picks/all ; GET /api/slip?risk=&games= ;
GET /api/consensus ; GET /api/stats ; GET /api/stats/tiers ;
GET /api/stats/picks?tier= (drill-down) ; GET /api/fixtures (for slip-checker) ;
POST /api/slip/check (score a user-assembled slip). CORS allow_origins=["*"].

## The model
Random Forest (scikit-learn), 65,174 matches. Features: form ppg, goals for/against
avg, venue ppg, h2h, odds-implied probs. Time-aware 80/20 split, no leakage.
Calibration is the core value — accuracy by confidence tier:
  HIGH (≥65% prob) historically ~75%, MEDIUM (≥55%) ~66%, LOW hidden as bets but tracked.
Gemma is explanation only, never the predictor.
Confirmed the model has an independent voice (disagrees with Forebet often, via
models/model_vs_source.py) — but whether its disagreements are *profitable* is
unknown until the honest ledger matures.

## CRITICAL bugs already fixed — DO NOT reintroduce
1. **Fake scores (the big one).** results.py once included `.ex_sc` in its score
   selectors — that's Forebet's PREDICTED exact-score column. So predicted scores
   were recorded as final results, grading picks against Forebet's own predictions
   → fake 100% hit rate. FIX: only genuine score containers (.lscr_td/.l_scr/.lscrsp),
   and a row must be explicitly FINISHED (FT marker). Contaminated results were purged.
   **Never read a prediction column as a result. Never grade before full-time.**
2. **Cross-source duplicate fixtures.** Forebet + PredictZ create separate match
   rows (different ids, league labels, dates a day apart) for the same fixture.
   Inflated counts everywhere. FIX: fuzzy team + ±1 day matching in dedupe_fixtures.py,
   consensus.py, and build_slip. Exact-date matching MISSES day-apart dupes — always
   use the ±1 day window.
3. **Double-counted predictions.** Repeated pipeline runs created multiple
   model_predictions per match. FIX: stats queries use latest prediction per match
   (MAX(id) per match_id) — one verdict per match.
4. **Cloudflare on Forebet results.** "Just a moment..." challenge pages return 0
   rows. FIX: results.py has retries + stealth + --headed; results_api.py is the
   Cloudflare-immune backup. Keep both — API for big leagues, Forebet for small ones.
5. **Silent pipeline stall (July 2026).** Scrapers logged ✅ SUCCESS and exited 0
   even when Cloudflare gave them 0 rows, and results.py's "yesterday" URL had
   become a 404 (Forebet renamed it to /football-predictions-from-yesterday) —
   so the ledger silently recorded nothing for 2 weeks while the pipeline said
   "complete". FIX: all scrapers now exit non-zero on 0 rows; results.py detects
   404 pages; run_daily.py has 15-min step timeouts (a Playwright hang once ate
   19h), a post-run ledger health check, and Telegram alerts on failure.
   **A scraper that got nothing must fail loudly — never exit 0 with 0 rows.**
6. **False-positive Cloudflare detection (July 2026).** is_cloudflare_challenge
   used ("challenge" in html and "cloudflare" in html) — but NORMAL pages embed
   Cloudflare's beacon script (/cdn-cgi/challenge-platform/...), so perfectly good
   results/PredictZ pages were discarded as "blocked" for days. The results ledger
   looked Cloudflare-starved when scraping actually worked. FIX: detector now only
   matches signatures unique to the real challenge page ("just a moment", "cf-chl",
   "attention required! | cloudflare"). **Never detect a block with substrings that
   can appear on a healthy page — and when a scraper reports blocked, verify by
   checking whether the page had rows.** Also: PredictZ challenges Chromium even
   with stealth, but plain Playwright FIREFOX passes — predictz.py uses firefox
   (needs `playwright install firefox`); do NOT add timezone/viewport context
   overrides there, the fingerprint mismatch triggers the challenge.
7. **Train/serve odds mismatch.** The model trains on real bookmaker odds-implied
   probs, but predict.py used to feed Forebet's percentages into those features —
   making live predictions echo Forebet. FIX: scrapers store real odds
   (odds_home/draw/away in scraped_predictions), predict.py prefers them (source
   pct only as fallback), MODEL_VERSION bumped to rf_v1b so ledger eras are
   separable. Team form at predict time also now includes our own recorded
   results, not just the historical CSVs.

## Decisions & their reasoning
- **Web app / PWA, not React Native** — free deploy, one codebase, installable. RN deferred to paying-users phase.
- **Do NOT invite testers until 2–3 weeks of REAL track record accumulates.** One-shot
  credibility. The ledger builds itself for free while we wait. This is a hard rule.
- **Booking-code GENERATION: DONE for SportyBet (July 2026)** — their "Book Bet" flow is
  anonymous: POST www.sportybet.com/api/ng/orders/share with
  {"selections":[{"eventId","marketId","specifier","outcomeId"}]} returns a shareCode.
  Event/market/outcome IDs come from GET .../factsCenter/pcUpcomingEvents
  (sportId=sr:sport:1, marketId=1,10,11,18,29 → 1X2, DC, DNB, O/U, GG/NG(BTTS); ~8 pages,
  cached 5 min). scrapers/booking_codes.py maps our legs by fuzzy teams + desc synonyms
  (Match Result→1X2, BTTS→GG/NG); POST /api/booking/generate; button on slip.html.
  Legs not on SportyBet come back "unmapped" (small leagues, started games).
  **MSport works too (same day):** identical URL pattern (www.msport.com/api/ng/orders/
  share/<CODE> to decode, same POST body to generate) and SAME Betradar event/market/
  outcome IDs as SportyBet — so one mapping (from SportyBet's events feed) books on
  both. decode_msport parses data.bettableBetSlip[]. generate_code(legs, provider)
  handles both; slip.html has SportyBet + MSport buttons. Unofficial APIs — can break
  anytime. Emoji in console output crashes cp1252 Windows terminals — .bat files set
  PYTHONUTF8; keep it. webapp html loads js/api.js?v=N — bump N when api.js changes
  or browsers serve stale JS ("Couldn't book" bug).
- **Slip-checker = user picks from existing fixtures** (reliable, no parsing). Free-text
  parsing is a later enhancement. Bookie-code GENERATION is still Phase 4, but code
  READING is built (July 2026): scrapers/booking_codes.py decodes SportyBet share
  codes via their unofficial endpoint (www.sportybet.com/api/ng/orders/share/<CODE>,
  bizCode 10000=ok / 19000=invalid); POST /api/booking/decode attaches our model's
  verdict per leg; paste box on checker.html. Valid-payload parsing is defensive and
  dumps unknown shapes to data/raw/ — needs a REAL code to validate the parse.
  MSport is stubbed pending a captured payload. Unofficial API = ToS grey area;
  fine for personal phase, reassess before commercial.
- **Build engine/API before frontend** so screens map to real endpoints.
- **Consensus** should eventually be a badge on Picks + a confidence booster in the
  engine, not just a standalone screen. Standalone page kept for the transparency story.

## What's next (priority order)
1. **Let the honest ledger accumulate 2–3 weeks.** Highest value, zero work. Scheduler
   runs daily. Watch: do the tiers hold calibration on REAL scores? Do the model's
   disagreements with Forebet win or lose?
2. ~~Consensus badge on the Picks page~~ DONE (July 2026): /api/picks and
   /api/picks/all attach a `consensus` field per pick ({sources, agree,
   agreed_outcome} or null); index.html shows "N sources agree" / "sources split"
   badges. Reuses consensus.py's fuzzy ±1 day matching via _attach_consensus.
3. ~~Consensus as a confidence modifier in predict.py~~ DONE (July 2026):
   apply_consensus_modifier — MEDIUM→HIGH when 2+ sources agree WITH the model,
   HIGH→MEDIUM when they agree AGAINST it, LOW never promoted, probabilities
   untouched. Adjustment stored in model_predictions.consensus_adj
   ('boosted'/'demoted'/NULL) so the ledger can answer "do boosts win?";
   MODEL_VERSION bumped to rf_v1c to separate eras. /api/picks serves
   consensus_adj; index.html shows "consensus boost"/"tier lowered" chips.
4. Model-vs-Forebet-on-real-results tracker (needs mature ledger) — validates the premise.
5. Polish: truncated league names ("world-cup-2026-last-"), add "Checker" to all navs.
6. DEFERRED until football is validated: second sport, cloud 24/7 deploy, real booking codes.

## Gotchas
- PowerShell `Out-File` writes null bytes → "source code string cannot contain null bytes". Use New-Item for empty files.
- Ollama uses /api/chat (response in message.content), not /api/generate. Clear __pycache__ if stale endpoints 404.
- Forebet/PredictZ need Playwright (JS-rendered, bot-blocked otherwise). Use wait_for_selector, not fixed sleeps.
- Port 8000 blocked on this Windows box (WinError 10013) → use 8080.
- Serve webapp over http (python -m http.server), never file:// — fetch/CORS breaks.
- Telegram token was exposed in chat and should be revoked/regenerated via BotFather.
- Cloudflare blocking of Forebet/PredictZ is time-of-day dependent: 07:30 scheduled
  runs got blocked repeatedly; midday/evening runs (~12:00, ~20:00) passed. Prefer
  scheduling around midday; all inserts are INSERT OR IGNORE so retry runs are safe.
  July 2026: new Task Scheduler task "PotentialPro Daily Pipeline 1200" runs the
  pipeline at 12:00 daily. The OLD "PotentialPro Daily Pipeline" (07:30) is owned by
  the "Peculiar" account and couldn't be disabled from Claude's session — disable it
  manually in Task Scheduler (harmless if it keeps running; duplicate runs are safe).
- July 2026 off-season: Europe on break, so the ledger depends on Brazil/MLS/Mexico/
  Scandinavia — that's why results_espn.py exists (football-data free tier lacks MLS).
