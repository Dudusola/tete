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

## Where it runs (LIVE since 6 Aug 2026 — nothing depends on Boss's laptop)
| Piece | Host | URL / entry point |
|---|---|---|
| Daily pipeline | GitHub Actions | `.github/workflows/daily.yml`, 11:00 UTC = 12:00 Lagos |
| API (the real FastAPI) | Render free tier | https://potentialpro-api.onrender.com (`/docs`, `/healthz`) |
| Web app (PWA) | Cloudflare Pages | https://potentialpro.pages.dev |
| Telegram bot (interactive) | Cloudflare Worker | https://potentialpro-bot.potentialpro.workers.dev |
| Telegram daily push | pipeline step | `bot/send_daily.py` — no running bot needed |
| Repo | GitHub (private) | https://github.com/Dudusola/tete |

- **The ledger lives in the repo.** `db/sports_predictor.db` is COMMITTED and the
  workflow writes it daily. One writer. Never run the local pipeline and the
  cloud one at once, and `git pull` before touching the DB.
- Render free tier SLEEPS after ~15 min idle (~50s cold start). Deploys are
  automatic on push to main (`render.yaml`).
- Frontend deploy is manual: `wrangler pages deploy webapp --project-name potentialpro`.
  **Bump `api.js?v=N` in all 5 HTML files first** or browsers serve stale JS.
- Worker secrets (BOT_TOKEN, GITHUB_TOKEN, WEBHOOK_SECRET) are write-only —
  `wrangler secret list` shows names, never values. GITHUB_TOKEN is a
  fine-grained read-only PAT; when it expires `/picks` says "couldn't reach the
  picks feed".
- Local Task Scheduler jobs are DISABLED. Re-enabling one without disabling the
  cron gives two pipelines writing two diverging ledgers.

## Environment
- Windows + PowerShell, Python 3.13, venv
- Project root: `C:\Users\OWNER\Desktop\PECULIAR DESKTOP\projects\files`
- Ollama local LLMs: `gemma3:4b` (analysis/reasoning), `qwen3-coder:30b` (coding)
- `.env` holds: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, OLLAMA_MODEL, OLLAMA_HOST,
  DB_PATH, FOOTBALL_DATA_TOKEN
- DB: SQLite at ./db/sports_predictor.db

## Run commands
- **Pipeline now runs in the CLOUD** (GitHub Actions, .github/workflows/daily.yml,
  11:00 UTC = 12:00 Lagos). The local Task Scheduler jobs are DISABLED — do not
  re-enable them without disabling the cron, or two pipelines write two diverging
  ledgers. db/sports_predictor.db is COMMITTED and the workflow updates it daily.
  Forebet is excluded in the cloud (IP-blocked); PredictZ + API-Football cover it.
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
- webapp/                 the PWA. slip.html Builder (July 2026): league multi-select chips ("All leagues"
                          default), per-leg SportyBet odds + total, remove/swap legs (✕ excludes the
                          fixture and back-fills next best — build_slip is deterministic so other legs
                          stay put), and Bankers (📌 pin locks a leg's exact market+selection through
                          risk/count/league changes and re-rolls; served via /api/slip pin= param).
                          index.html (Picks), slip.html (Builder), record.html (Track Record),
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
8. **Silent results-COVERAGE gap (July 2026).** The pipeline ran daily and
   scored promptly, yet ~half of all predicted matches were never graded — so
   the tier hit-rates were computed on a flattering subset (the picks we COULD
   score skewed toward the ones we got right). Root cause: results_espn.py
   queried only the main UEFA slugs (uefa.europa/champions/conf), which are
   EMPTY in summer because ESPN keeps qualifying rounds under SEPARATE slugs
   (uefa.europa_qual, uefa.champions_qual, uefa.europa.conf_qual). The World
   Cup had no slug at all. FIX: added fifa.world + the three _qual slugs +
   swe.2; widened default lookback 4→7 days (a single failed run no longer
   permanently loses matches >4 days old). Backfill (results_espn.py --days 45)
   took graded 1X2 picks 55→88 and — tellingly — dropped the HIGH tier from
   57% to 38%, i.e. the missing data was mostly losses. Residual ungraded =
   Veikkausliiga/Superettan/Copa Sudamericana, which ESPN lists but publishes
   NO scores for (need another source or don't serve them as gradeable).
   **Grading COVERAGE is a first-class health metric, not just cadence —
   check `python db/track_record_health.py`. A tier % means nothing if the
   picks it omits are the ones that lost. Run it before trusting calibration.**
9. **The 12-day silent stall — Task Scheduler, not the code (Aug 2026).** Between
   26 Jul and 6 Aug NOTHING entered the DB: no fixtures, no predictions, no
   results. rf_v1e (shipped late July to disable the harmful consensus boost)
   had produced **zero predictions ever** — the whole point of the change was
   generating no evidence, silently. Every fix from bug #5 worked as designed
   (scrapers exit non-zero, health check fired, Telegram alert attempted) and
   it STILL went unnoticed for 12 days, because:
   - The task "PotentialPro Daily Pipeline 1200" had `DisallowStartIfOnBatteries
     = True` (Task Scheduler's DEFAULT). On battery the task is refused before
     python ever starts — last-run result 2147946720 (0x800710E0, "operator or
     administrator has refused the request"). No log line, because nothing ran.
   - `StartWhenAvailable = False` (also default) meant a missed 12:00 run — PC
     asleep or off — was skipped permanently, never caught up.
   - The one run that did fire (2 Aug) hit a genuine network outage, so even
     the failure alert couldn't send: DNS wouldn't resolve api.telegram.org.
   FIX: task now has `StartWhenAvailable=True`, `DisallowStartIfOnBatteries=False`,
   `StopIfGoingOnBatteries=False`, ExecutionTimeLimit 2h (was 72h).
   **A pipeline that never STARTS produces no logs and no alerts — the bug #5
   hardening only covers runs that begin. Health is "did fresh rows land
   today?", checked against the DB, not "did the last run succeed?". Task
   Scheduler defaults are hostile on a laptop.** Verify with:
   `Get-ScheduledTaskInfo -TaskName "PotentialPro Daily Pipeline 1200"` — a
   LastTaskResult of 0x800710E0 means it was refused, not that it failed.
   Note the alert channel shares fate with the pipeline (same box, same
   network); a truly independent watchdog would have to live off-machine.
10. **Truncated SportyBet feed → games silently dropped from booking codes
    (Aug 2026).** Users reported that "some games aren't picked" when
    generating a code. fetch_sportybet_events() fetched a HARDCODED 10 pages —
    accurate when written ("~8 pages" per its own comment), silently wrong
    later because the feed grew to 1537 events. Ten pages returned 843, so
    whole competitions fell off the end (MLS, Leagues Cup). Those legs came
    back "unmapped", which READS as "SportyBet doesn't offer this match" — so
    the label actively hid the cause. Verified: Portland Timbers v Club Puebla
    and Austin FC v Club Tijuana were in the feed the whole time, just past
    page 10. It failed identically on the laptop and in the cloud, ruling out
    geo-blocking. FIX: page count is DERIVED from `data.totalNum` in the
    response (+2 pages slack, since events arrive grouped by tournament so
    per-page counts vary), capped by SPORTY_MAX_PAGES. Same 6-leg slip went
    2/6 bookable → 6/6; codes UFDQE5 and ZW74P1 confirmed loading in SportyBet.
    **A hardcoded limit against a third-party feed is a time bomb — derive it
    from the response, or it will be right today and wrong in six weeks. And
    never let a "not found" label stand in for "we didn't look far enough".**

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
  **Bet9ja code READING added (July 2026):** GET sports.bet9ja.com/desktop/feapi/
  CouponAjax/GetBookABetCoupon?couponCode=<CODE> (found in their JS bundle;
  R="OK"/coupon data, ERROR_CODE 333 = unknown code, -123 = missing param).
  decode_bet9ja parses defensively and dumps unknown shapes to data/raw/ —
  NEEDS A REAL CODE to finalize the leg parse. Bet9ja GENERATION found but not
  built: POST /desktop/feapi/PlacebetAjax/BookABetV2 with {BETSLIP: json, IS_PASSBET:0}
  — BETSLIP uses Bet9ja's internal event/market/sign IDs (their own palimpsest,
  NOT Betradar like Sporty/MSport), so it needs a captured real BookABetV2 request
  (book one bet manually with devtools open, or run a --headed Playwright capture).
  Their events feed: /desktop/feapi/PalimpsestAjax/GetSports works; GetEventsInDailyBundleV3
  returned ERROR 319 from curl — params/headers not yet cracked.
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
3. ~~Consensus as a confidence modifier in predict.py~~ DONE then PARTLY
   REVERTED (July 2026): apply_consensus_modifier stored consensus_adj
   ('boosted'/'demoted'/NULL) so the ledger could answer "do boosts win?".
   Answer: NO — the MEDIUM→HIGH **boost went 0/3 and every one lost badly**
   (1-5, 0-4, 4-3), dragging marginal 56-61% picks into HIGH and tanking the
   tier (HIGH → 5/13). **Boost DISABLED in rf_v1e**; the protective HIGH→MEDIUM
   demote is kept. Re-enable only if a matured ledger shows agreement-with-model
   wins. index.html still renders the chips; boosted picks simply won't occur.
4. **Investigate the HIGH-tier inversion (NEW top priority).** With honest
   coverage, HIGH 5/13 (38%) < MEDIUM 15/20 (75%). Beyond the boost, the tier is
   polluted by 60-70% picks: the ≥73% picks are ~4/1, everything ≤70% is ~1/7,
   and WC/MLS favorites drew or got upset (3 WC "Home Win" HIGH picks ended 0-0).
   Candidate fixes once the sample matures: raise the HIGH threshold from ≥65% to
   ~72%, and/or league-aware confidence (MLS parity, WC draw-proneness). Do NOT
   hard-change calibration on 13 samples — let rf_v1e accumulate first.
5. Model-vs-Forebet-on-real-results tracker (needs mature ledger) — validates the premise.
6. Polish: truncated league names ("world-cup-2026-last-"), add "Checker" to all navs.
7. **Web push notifications for the PWA — DESIGNED, DEFERRED ON PURPOSE (Aug 2026).**
   Not built because Telegram ALREADY delivers a real push notification to the
   phone every day (bot/send_daily.py, from the cloud pipeline). Web push would
   be a second channel carrying the same payload. It earns its keep only when
   there are testers who won't join a Telegram bot — i.e. at the invite stage,
   not before. Prerequisite already satisfied: iOS only permits web push for
   INSTALLED PWAs, and the app is installable (manifest.json + icons/).
   Design when it's time:
     - VAPID keypair; public key ships in the client, private key is a Worker
       secret (`wrangler secret put VAPID_PRIVATE_KEY`). Never commit either.
     - Permission prompt in the UI — request it on a user gesture, never on
       page load, or browsers hard-block it for the origin.
     - Each subscribing device returns a PushSubscription; store it in
       Cloudflare Workers KV (free tier), keyed by endpoint hash.
     - Sending: the existing Worker (worker/index.js) signs a JWT per push
       (Web Crypto can do ES256) and POSTs to the endpoint in each
       subscription. Apple/Google/Mozilla each have quirks; expect to test on
       real devices, not just Chrome desktop.
     - sw.js needs a `push` listener + `notificationclick` to focus the app.
       Keep it NETWORK-FIRST — don't let a push change bug #6's caching rule.
     - **The trap to watch:** subscriptions expire silently. A 404/410 from the
       push endpoint means "delete this subscription" — if that isn't handled,
       sends fail quietly and nobody notices, which is exactly the failure
       pattern behind bugs #5, #8 and #9. Log delivery counts, and treat a drop
       to zero as an alert, not as "nobody used it today".
8. **User accounts + per-user records — DESIGNED, DEFERRED (Aug 2026).**
   Asked for as "every user has login details and their own record". Deferred
   for the same reason as web push: it is a LAUNCH feature, and the tier
   question isn't settled. Building auth now means maintaining it while the
   thing it serves is unproven. Sequence: ledger matures → HIGH-tier question
   resolved → accounts + web push together, at tester-invite time.

   **The distinction that must not be blurred — two different records:**
   - **The MODEL's track record stays GLOBAL.** "HIGH hit 38% on 92 graded
     picks" is a claim about the engine, verified against real scores. It is
     the product's entire credibility. Per-user it would be meaningless and
     actively misleading: a new user sees 0/0, a lucky one sees 3/3 = 100%.
     That is the fake-scores trap wearing a different hat — a number that
     looks personal but proves nothing.
   - **The USER's own record is PER-USER.** Which picks they actually took,
     stake, P&L. A betting diary. Show both on one screen: "the model is 54%
     overall; you are 7/12 on the slips you took."

   **Hard constraint: user data CANNOT live in db/sports_predictor.db.** That
   file is committed to the repo and rewritten by GitHub Actions daily — user
   rows there would be readable by anyone with repo access and overwritten
   every run. Use a separate store: Supabase free tier (hosted Postgres +
   auth built in) is the fit, so nobody hand-rolls password handling.

9. **Slip determinism — KEEP IT. Do not randomise for the sake of variety.**
   Verified Aug 2026: the same /api/slip query returns byte-identical legs;
   changing risk changes them. Every user asking for "5 games, MEDIUM" gets
   the same slip, and that is CORRECT: if the five best MEDIUM picks are A-E,
   serving user B picks F-J means knowingly giving them worse selections.
   That trades the one real asset (accuracy) for the appearance of
   personalisation. build_slip is also relied upon to be deterministic by the
   Builder's remove/swap back-fill.

   **Variety should come from PREFERENCES, which already exist**: risk tier,
   game count, league filter, pinned bankers, excluded fixtures. Two users
   with different filters already get different slips honestly.

   The ONE legitimate place for randomness is tie-breaking among picks whose
   difference is inside the noise (66.2% vs 66.0%) — vary which appears, cost
   nothing. Anything beyond that is degrading picks to look bespoke.

10. DEFERRED until football is validated: second sport, real booking codes.

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
- **Piping a secret to a CLI in PowerShell 5.1 prepends a BOM.** `$v | gh secret set X`
  stores "﻿<value>" — the token then dies server-side with
  `UnicodeEncodeError: 'latin-1' codec can't encode character '﻿'` when it
  goes into an HTTP header. The local .env was clean; PS added the BOM at the
  pipe. Use `gh secret set X --body $v` instead (PS 5.1 has no `<` redirection
  for native commands). Applies to any value piped into any native exe.
- **Forebet IP block: every free route is now ruled out (6 Aug 2026).**
  GitHub Actions = HTTP 403 challenge (plain requests, Chromium AND Firefox —
  so it is IP reputation, not browser fingerprint). Cloudflare Workers =
  error 1042, Cloudflare's loop protection refusing a Worker subrequest to a
  Cloudflare-proxied site. Forebet is itself behind Cloudflare, so no free
  cloud egress we can reach will scrape it. Remaining options are all paid
  (residential proxy ~$50/mo) or self-hosted (a runner on Boss's own IP).
  The pipeline therefore skips Forebet in the cloud via PP_SKIP_FOREBET=1.
  **Cost of losing it is small and measured**: PredictZ supplies MORE rows and
  MORE odds; Forebet's only unique field is over_2_5_pct, which feeds the
  analysis text, not the model. Do not spend more time on this without a
  budget — it has been tested to exhaustion.
- **GitHub Actions IP reality (probed 6 Aug 2026, twice, same result):**
  Forebet = BLOCKED (0 rows, both today and tomorrow pages);
  PredictZ = WORKS (166 matches — Firefox stealth survives datacenter IPs);
  football-data.org API = WORKS; ESPN API = WORKS. So the cloud can run
  everything EXCEPT Forebet fixture scraping. Re-probe with the
  "Probe scrapers" workflow before assuming this still holds.
