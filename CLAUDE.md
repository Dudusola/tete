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
| Daily pipeline | GitHub Actions | `.github/workflows/daily.yml`, started 06:00 UTC by the Worker cron (GitHub's own 08:50 cron is a backup) |
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
- **Frontend deploy is AUTOMATIC (11 Aug 2026)** —
  `.github/workflows/deploy-webapp.yml` fires on any push touching `webapp/`.
  It stamps `js/api.js?v=<sha256 prefix>` into all five HTML files itself, so
  the manual version bump that used to cause the "Couldn't book" stale-JS bug
  is gone: a content hash cannot be forgotten and cannot be wrong.
  It needs `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` secrets; without
  them it SKIPS with a notice instead of failing. `wrangler pages deploy webapp
  --project-name potentialpro` still works by hand if ever needed.
- The API image now also ships `webapp/`, so Render serves the site at its own
  origin as a fallback. `api/server.py` had always mounted it; the Dockerfile
  simply never copied the directory, so the mount silently did nothing and the
  Render root 404'd for months — the same allowlist trap as models/leagues.py.
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
  08:50 UTC ≈ 10:00 Lagos). The local Task Scheduler jobs are DISABLED — do not
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
- Closing-line value:   `python db/clv_report.py` (does the price move toward our picks?)
- Calibration check:    `python db/calibration_health.py` (tiers, bands, sign test, eras — run it,
                        don't re-derive item 4 by hand)
- Daily health:         `python db/daily_health.py` - also sent to Telegram after the picks every
                        morning (bot/send_daily.py; HEALTH_CHAT_ID overrides the chat). Football
                        funnel priced -> predicted -> served (warns < 80% predicted: the 7 Oct name
                        collapse ran at 69%), SportyBet-bookable share, last-week grading, data age,
                        NHL/NFL counts. Thresholds = measured normal minus a margin, so it stays quiet.
- "Anything to refine?": `python db/refine_report.py` (segments with an earlier/later-half
                        stability check, plus the model+bookmaker blend tested out of sample)
- Non-soccer ledger:    `python scrapers/espn_ledger.py [--feed nfl|nba|nhl|mlb] [--dry-run]`
- Non-soccer history:   `python scrapers/sports_history.py` (update) / `--feed mlb --full` (rebuild)
- **Testing anything that writes the DB: use a COPY** — `cp db/sports_predictor.db <scratch>/t.db`
  then `DB_PATH=<scratch>/t.db`. The real file is the committed ledger.

## File map (under project root)
- run_daily.py            one-command pipeline: forebet → predictz → results(Forebet) → results(API) → predict → consensus
- scrapers/forebet.py     Playwright scrape of Forebet predictions (today+tomorrow)
- scrapers/predictz.py    Playwright scrape of PredictZ; odds→implied probs. Flaky; pipeline tolerates failure.
- scrapers/results.py     REAL final scores from Forebet. Hardened vs Cloudflare (retries, stealth, --headed).
- scrapers/results_api.py Final scores from football-data.org API (free tier). Cloudflare-immune. Covers big leagues only.
- scrapers/market_snapshot.py  Stores the bookmaker's price for OUR pick, AT PICK TIME
                          (model_predictions.book_odds / book_prob). This is the only way to
                          answer the project's central question — "when we disagree with the
                          market, are we right?" — which is NOT the same as "is the model
                          accurate". A model that agrees with the odds adds nothing over reading
                          the odds; any edge lives in the disagreements.
                          MUST run at prediction time: odds move, so a later lookup measures a
                          different market and no backfill can recover it. Every skipped day is
                          evidence lost forever, which is why pricing ZERO picks returns non-zero
                          instead of shrugging. Read it with db/edge_report.py.
                          Baseline on 11 Aug: mean gap -1.0 pts, 0 disagreements of 15+ pts — i.e.
                          we currently agree with the market almost exactly, so no edge is
                          demonstrated yet either way.
                          Since 27 Sep also records book_source + book_observed_at (WHICH feed,
                          WHEN observed): a stored ESPN row can be days older than the pick.
- scrapers/closing_snapshot.py  The SAME market's price just before kickoff (close_odds/close_prob/
                          close_at/close_mins_before), hourly via `.github/workflows/closing.yml`,
                          overwritten each run so the last pre-kickoff observation stays.
                          Closing-line value = close_prob - book_prob: if the price keeps moving
                          TOWARD our picks, the market is agreeing with us after the fact — the
                          standard edge test, and it needs no match result, so it answers weeks
                          before edge_report.py can. MUST run before kickoff: ESPN removes odds from
                          finished games (probed), so a missed hour is a close gone for good.
                          Reuses kickoff_times' fetcher + team matcher (bug #17) with a 3h kickoff
                          guard. Read with db/clv_report.py, which scores only picks whose open and
                          close came from the SAME feed (across two books "movement" is just their
                          disagreement) and lists pre-27-Sep picks (no book_source) separately.
- scrapers/clubelo.py     THE independent opinion. Elo ratings from api.clubelo.com (free, no key,
                          not behind Cloudflare). GET /Fixtures returns a goal-difference distribution
                          per fixture; summing it gives real H/D/A. Every OTHER source here is derived
                          from bookmaker prices, so this is the only one that can disagree for a
                          different reason — which is the whole point of consensus.
                          OPINION SOURCE ONLY: attaches to matches we already have, never inserts a
                          fixture (that is how bug #2's duplicates happened). Odds columns left NULL
                          so predict.py cannot mistake a rating model for a market line.
                          Covers European club football + UEFA comps; 6 of 28 fixtures on 11 Aug,
                          rising with the European season. Retries 3s/6s/9s — the service is a hobby
                          API and returned 502/200/reset on three consecutive probes. OPTIONAL in the
                          pipeline, and it distinguishes "feed empty" (fails) from "no overlap" (fine).
- scrapers/results_espn.py Final scores from ESPN public scoreboard API (no key). Third results source,
                          Cloudflare-immune; ~30 leagues football-data misses: MLS/USL, Liga MX, South
                          America (Arg 1+2, Bra 2, Ecu, Col, Uru, Par, Chi, Per), UEFA comps (qualifiers!),
                          Scandinavia, smaller Europe, China/India/A-League. Only records events ESPN marks
                          completed. Fetches league×day scoreboards in a 10-thread pool (~30s; sequential
                          took 18 min and would hit the 15-min step timeout).
- scrapers/espn_history.py Downloads HISTORICAL results for any ESPN league slug into a
                          football-data.co.uk-shaped CSV, so a league the model has never seen can
                          be given form. Exists because every other route for a new league was
                          blocked: football-data.co.uk publishes no CSV outside its ~36 leagues,
                          Forebet is IP-blocked in the cloud, and the API-Football account is
                          suspended. ESPN's scoreboard takes a date RANGE, so a month is one
                          request and four seasons ~40. Gives NO odds (ESPN prices only upcoming
                          games) — which is fine and not a silent compromise: extract_odds already
                          handles odds-less rows with odds_is_market=0.0, and bug #7 stops
                          predict.py serving a pick that has no odds. Coverage varies per league;
                          ask for 2018 and Saudi gives you 2022, so read the per-season counts it
                          prints. First use: data/historical/SaudiProLeague.csv, 1,179 matches.
- scrapers/espn_fixtures.py FOOTBALL fixtures + odds from ESPN for days +2..+5 (PR #18, 27 Sep 2026).
                          PredictZ only has today/tomorrow and was the only fixture INSERTER, so the
                          site could never see past tomorrow (empty during the Sept international
                          break with 87 priced club fixtures four days out). Inserts from +2 ONLY so it
                          never writes PredictZ's days; the day-boundary duplicates it does create are
                          the deduper's job, so it must run BEFORE dedupe. Odds come from the same
                          payload as the fixture — no fuzzy matcher on a path that feeds features.
- scrapers/espn_ledger.py THE non-soccer ledger: NFL, NBA, NHL, MLB fixtures + FINAL scores from
                          ESPN (DEGRADED pipeline step). NOT odds, predictions or markets — coverage
                          first, claims second (bug #16). Fixture and result share ESPN's event id
                          (`espnnfl_/espnbb_/espnnhl_/espnmlb_<id>`), so results join EXACTLY — no
                          team matcher, no bug #22. One `Feed` per league declares the facts that
                          differ: ESPN path (ESPN calls American football "football" and soccer
                          "soccer" — never derive the path from our sport name), draws_possible
                          (NFL yes; NBA/NHL/MLB no, so a level final is bad data and SKIPPED),
                          reg_periods (4/4/3/9), home_may_skip_last (MLB). Since 27 Sep 2026 it also
                          appends every pre-game MONEYLINE change to `market_lines` (DraftKings via
                          ESPN; open + current; full game incl. OT - grade on the FINAL score), daily
                          in the pipeline AND hourly in closing.yml, so each game keeps its opening
                          and closing price. Its own table on purpose: scraped_predictions is
                          football's 3-way feature path (bug #7). This is what lets Elo be tested
                          against the market later — ESPN deletes odds once a game is final.
                          Since 29 Sep 2026 also the SPREAD (puck/run line; `line` = the HOME
                          side's line) and the TOTAL (`odds_over`/`odds_under` at `line`), each
                          with its opening line — the line itself moves (NYI @ TOR o6.5 -> o5.5).
                          Exits 0 on an empty
                          schedule (off-season), 1 only if every request failed. `_parse_event` is
                          also the parser for sports_history.py — see bug #31 for what it filters.
- scrapers/sports_history.py  Historical results for the four non-soccer leagues into
                          data/sports_history/{nba,nfl,nhl,mlb}.csv — 20,497 / 4,396 / 20,579 /
                          38,799 games since 2010. Stockpiled for when a model for those sports is
                          built; nothing trains on it yet. NEVER put these in data/historical/:
                          train.py globs that folder into the FOOTBALL model and the Actions cache
                          key hashes it. Daily OPTIONAL step, incremental (refetches from the newest
                          game in the file, so a missed day self-repairs); `--full` rebuilds. Stores
                          ESPN FRANCHISE ids (home_id/away_id) — join on those, not names: "Hornets"
                          was two franchises, Nets/Raiders/Rams/Chargers/Coyotes/Marlins/Guardians/
                          Commanders renamed or moved. Exactly 30/32/33/30 ids (NHL 33 = Arizona,
                          gone 2024). A month that fails leaves the file UNCHANGED — never a gap.
- models/elo.py           Elo ratings for NBA/NFL/NHL/MLB — THE BASELINE any model for those sports
                          must beat on held-out seasons (docs/basketball.md §8). Point-in-time by
                          construction; keyed by ESPN franchise id; 538's margin-of-victory
                          multiplier. K / home advantage / off-season revert tuned by grid search on
                          training seasons ONLY, the last two complete seasons scored once. Writes
                          data/sports_history/elo_params.json + elo_ratings_<feed>.csv. Not in the
                          pipeline: nothing consumes it yet. `python models/elo.py` (~25 s).
- models/elo_shadow.py    SHADOW Elo predictions for NFL/NBA/NHL/MLB into `shadow_predictions`, with
                          the margin-free market probability beside each. Nothing PUBLIC reads it:
                          no /api/picks, /api/stats, Record page or bot. Its ONE reader outside the
                          reports is the private NHL preview (29 Sep 2026, below). For NHL games it
                          also stores `markets_json` + `facts_json`. Daily step (DEGRADED) +
                          hourly in closing.yml, updated until kickoff. Reverts ratings for a new
                          season the history file has not reached yet. Read with
                          `python db/shadow_report.py` (Elo vs market log loss, paired 95% CI,
                          graded on the FINAL result; NFL ties excluded). The evidence clock for
                          serving any non-soccer sport (docs/basketball.md sections 5 and 7).
- models/nhl_markets.py   Prices the four markets hockey is bet on: moneyline (Elo, incl. OT/SO),
                          60-minute 1X2 (what SportyBet calls hockey "1X2"), puck line ±1.5, totals
                          5.5/6.5. NOT Poisson: NHL regulation totals are UNDER-dispersed (var 5.5 on
                          mean 6.0) and 22% of games reach OT vs Poisson's ~16%. Each market is a small
                          logistic fitted on 2012-2024, half-life chosen on validation seasons, scored
                          ONCE on 2025-26 (2,624 games): puck line gains ~0.009 log loss over the base
                          rate, totals ~0.006 (and ~3 pts over-confident in the 60-70% band), 60-min
                          3-way ~0.013. The reg. draw and the OT winner had NO skill, so they use the
                          league rates (23.0%, home 51.3%) - never dress a constant up as a model.
                          `grade()` settles every key (full game on the FINAL, 60-min on reg score);
                          `verdict()` picks the largest edge >= 3 pts, strength by size, capped at
                          "slight" in opening week. Refit: `python models/nhl_markets.py` ->
                          data/sports_history/nhl_markets.json (NOT refit daily; do it each summer).
- models/nhl_market_backtest.py  DOES IT BEAT THE BOOKMAKER? answered on PAST seasons (2 Oct 2026,
                          because live grading needs weeks). Historical NHL open/close moneylines,
                          puck lines and totals from sportsbookreviewsonline.com (2010-11 to Nov
                          2022) -> data/sports_history/nhl_odds_sbr.csv. EVERYTHING refitted on
                          2012-2018 only, scored once on 2019-2023 (5,001 games), joined on both
                          franchise ids + date +-1 + the final score. Result: the book is BETTER
                          than us on moneyline (close AND open) and puck line, LEVEL on totals; every
                          flat-bet rule at the close loses -3..-10% (~the margin). The one signal: on
                          10+ pt moneyline disagreements with the OPEN, the line then moves our way
                          62% (2012-18) / 68% (2019-23), ~+4 pts - information, but betting it at the
                          open is +19% on 154 bets, CI -11..+49: NOT proven. Hence LEAN_POINTS=10,
                          moneyline only, in nhl_markets.verdict(). A parser bug dated the two
                          January-start seasons (2012-13 lockout, 2020-21) a year early and silently
                          dropped ~1,700 games; the first, wrong run showed a "+20% at 8 pts" lead
                          that vanished once fixed. **Count the unjoined rows before reading a result.**
- models/nhl_form.py      The FACTS an NHL analysis may use: last 10 (W-L-OTL, with how many were
                          playoff games), home/road last 10, streak, rest/back-to-back, last 5
                          meetings, rating rank. Point-in-time; shootout "goal" removed from GF/GA.
- analysis/nhl_analyst.py Prose for NHL AND NFL picks (the card's "Details"), from facts_json/
                          markets_json only. Since 4 Oct 2026 it EXPLAINS THE CARD'S OWN PICK
                          (us_sports' moneyline side + tier) - the old edge verdict ("no bet unless
                          10+ pts") was live under cards recommending a bet, a contradiction in our
                          own voice. The bookmaker's view is stated per pick (less convinced by 5+
                          PERCENTAGE points -> said plainly; "points" alone was misread in the NFL as
                          the spread). Guards, each added after Gemini broke a rule: the h2h TALLY is
                          precomputed leader-first ("Florida won two of five" when they won three;
                          "Boston's 3-2 edge" when Boston trailed 2-3); `unsupported_figures()`
                          rejects any score/record/decimal not in the evidence (caught an invented
                          "3-6-0") and any starting-goalie claim, retries once, then stores NOTHING.
                          Served (HIGH/MEDIUM) picks only, 72h ahead, rewritten when 18h old or older
                          than PROMPT_SINCE. Daily OPTIONAL step AND hourly in closing.yml.
- scrapers/nhl_goalies.py NHL goalies per game from api-web.nhle.com (no key) into `nhl_goalies`.
                          Probed 29 Sep 2026: pre-game the API lists each team's goalies with LAST
                          season's numbers but NEVER the starter (confirmed at warm-ups, after the
                          analysis is written) -> `started` NULL = UNKNOWN. Post-game the boxscore
                          flags `starter` + saves/shots/decision; boxscores persist, so
                          `--days-back 30` backfills. Joined to ESPN fixtures EXACTLY on both
                          normalised names + UTC date (all 32 names verified; no fuzzy matcher).
                          The analyst may quote goalie numbers but a regex rejects any starter
                          claim ("gets the start", "expected to start"...). Daily OPTIONAL step,
                          before the shadow step. Cloud reachability of api-web.nhle.com UNPROBED.
- models/nhl_preview.py   Payload for GET /api/preview/nhl (the old standalone NHL page). SUPERSEDED
                          2 Oct 2026 by models/us_sports.py: Boss did not want a separate tab, so
                          webapp/hockey.html is now only a redirect to index.html?sport=nhl. The
                          endpoint stays for old clients. db/shadow_report.py still grades each NHL
                          market family against the book + the verdicts' flat-stake P/L.
- models/us_sports.py     NHL + NFL INSIDE Picks and the Builder (2 Oct 2026, Boss: "choose the
                          sports you want ... recommended bets like football"). picks(): one bet per
                          game - the moneyline side the model rates likelier - with FOOTBALL's tiers
                          (HIGH >=65%, MEDIUM >=55%, LOW not served), the bookmaker's view beside it,
                          and each sport's OWN record (never blended into football's). slip_options():
                          every OFFERED market as BetOption so build_slip() treats them like football
                          (risk bands, correlation cap, bankers, swaps). "Offered" matters: we price
                          favourite +1.5 too, but books only quote favourite -1.5 / dog +1.5, and that
                          unbookable 81% leg topped every LOW slip until filtered. Market names
                          (Moneyline, 60-Minute Result, Puck Line, Spread, Over/Under Goals/Points) are
                          a CONTRACT with scrapers/booking_codes.US_SPORTS. settle_leg() grades tracked
                          NHL/NFL slip legs with the same grade() as the record (NFL push = void).
- NHL/NFL IMPROVEMENT TESTS (6-7 Oct 2026, Boss: "make it better"). All on held-out seasons:
  models/elo_plus.py      rest days / back-to-backs and faster early-season K: NO measurable gain
                          in either sport (NHL -0.0013 [-0.0033,+0.0007]; NFL +0.0003). Not shipped.
  models/line_move_backtest.py  Picks the price moved AGAINST before the game (NHL fair prob -5+ pts,
                          NFL spread 3+ pts): 6-12 pts under claim in BOTH eras of BOTH sports
                          (NHL -10.8/-6.1, NFL -12.1/-9.9); significant in none (rare: ~2-8% of picks).
  models/nfl_qb.py        Starting QB per game from nflverse (joins on ESPN id, 4426/4427). A backup
                          costs ~70 Elo (~2.8 pts of spread); held out -0.0053 [-0.0139,+0.0036]
                          overall, 0.6199 -> 0.6032 on the 182 affected games. NOT significant.
                          `live_status()` = regular starter (last 6 starts) + ESPN injury status.
  SHIPPED as WARNINGS, never changed probabilities (models/us_sports.warnings): "price moved
  against this pick" and "QB news: X (this pick's / the opponent's regular starter) is listed Out".
  record() keeps warned picks APART ("warned") so the live ledger can confirm or kill them.
  Rejected on reasoning first: "only recommend when we agree with the book" (Boss) - raises hit rate
  by keeping easy favourites, deletes the only disagreements with signal, copies the book.
- models/nfl_markets.py   NFL moneyline (Elo) / spread / total at the BOOK's lines. Margin = normal
                          x KEY-NUMBER weights by |margin| (fitted 2011-23: 3 -> x2.54, 7 -> x1.75;
                          14.6% of games end by exactly 3), mu solved so P(home win) == Elo's.
                          Held out 2024-25 (570 g): beats the base rate at every spread -7.5..+7.5
                          and every total 40.5..50.5; key-number model beats a plain normal on exact
                          margins. Pushes are VOID. No QB/injury/weather data - expect big gaps to
                          the book early in a season (Washington 57% vs book 37%, 2 Oct).
- Booking FOOTBALL name cleaning (7 Oct 2026, Boss: "some matches are not being picked with
                          SportyBet"): SportyBet decorates names ("SE Palmeiras SP v EC Bahia BA",
                          "CSD Independiente del Valle", "PAE PS Kalamata", "Betis"). The mapper now
                          ALSO tries SportyBet names with club-form prefixes dropped, and a Brazilian
                          state code dropped ONLY if the base name is unique in the feed ("Botafogo
                          FC SP" vs "Botafogo FR RJ" are different clubs - their codes stay), plus
                          _SPORTY_ALIASES for one-offs ("Real Betis"->"Betis"). SportyBet side only;
                          models/team_match.py untouched. Measured: served picks bookable 46->50/51,
                          all fixtures 306->317/420, every one of the 11 changed mappings checked by
                          hand, zero previously-mapped fixtures moved; code QEC1CT decoded back right.
                          KICKOFF ANCHOR (8 Oct): if pair_score finds nothing, accept the ONE SportyBet
                          game within 90 min of our kickoff where one team matches >= 0.92 and the other
                          >= 0.35 ("Man Utd v Tottenham", "PSG v Le Mans FC", "Liverpool v Man City") -
                          a team cannot play twice at once. Squad qualifiers still zero the score; two
                          candidates = no match. Upcoming fixtures mapped 321 -> 366, all 45 checked.
                          BUILDER (8 Oct, Boss: "some legs are not getting priced"): get_slip drops a
                          leg's MARKET when the game is listed but that market/line isn't (falls back to
                          the fixture's next-best market via build_slip(exclude_options)), and swaps an
                          UNLISTED game only if the slip keeps its length; bankers never touched.
                          18 test slips: 0 of 120 legs unpriced; 10-leg mixed slip booked UE6WL9 10/10.
- Booking NHL/NFL (scrapers/booking_codes.US_SPORTS): SportyBet FULL-GAME ids only - NHL 406 winner,
                          410 handicap, 412 total (all "incl. overtime and penalties"), 1 = 60-min 1X2
                          (from our 60-Minute Result only); NFL 219/223/225 "incl. overtime". Mapped by
                          exact market id + line + outcome id, never by name: SportyBet's plain hockey
                          18 "Over/Under" is 60-MINUTE (Over 6.5 paid 2.60 vs 2.20 for 412). Verified by
                          booking code QBV5GK and decoding it back. Bet9ja: NHL/NFL legs unmapped.
- models/sports.py        `matches.sport` values (football, basketball, american_football,
                          ice_hockey, baseball) and `sport_sql()`, the WHERE fragment that keeps
                          them apart. Rejects unknown sports so a typo can't silently filter
                          nothing. **Every reader of `matches` that fuzzy-matches against a
                          football feed must carry `sport_sql(FOOTBALL)`** — see bug #30.
- models/train.py         Random Forest trainer. 72.1k historical matches (103 CSVs since 2018 + internationals).
                          July 2026: refreshed all football-data.co.uk extra-league CSVs and added
                          SwedishLeague/FinlandLeague/IrelandLeague (in-season leagues) — doubled July
                          pick volume (6→12/day). Refresh source: football-data.co.uk/new/<CODE>.csv
                          (ARG BRA USA MEX CHN JPN NOR AUT DNK POL ROU RUS SWZ SWE FIN IRL → the
                          *League.csv names). Note ARG/BRA/USA/MEX lag ~6 weeks at the source itself.
- models/predict.py       Live predictions + Gemma. TEAM_ALIASES dict for name mismatches.
- models/markets.py       Derive ~22 markets from H/D/A + Poisson score matrix (1X2, DC, DNB,
                          O/U 1.5/2.5/3.5, BTTS, Asian Handicap ±1.5, Result&Goals). build_slip dedupes by teams + ±1 day.
- models/consensus.py     Cross-source agreement (Forebet vs PredictZ), fuzzy teams + ±1 day window.
- models/gradeability.py  Grading COVERAGE, derived from the ledger — the error bar on every
                          accuracy figure we publish (see bug #16). `untracked_leagues()` is
                          evidence-based and self-updating: 4+ gradeable picks and never one
                          scored. Run `python models/gradeability.py` for the per-league table.
- models/draws.py         Draw-leaning fixtures. A FILTER, NOT A TIP, and the module says why:
                          measured on 90,381 matches, evenly-matched sides draw 30.1% (33.8%
                          if also low-scoring) against a 26.2% baseline — a real pattern, and
                          one the bookmaker prices to within ~1 point in every band. Backing
                          every draw returns -7.2%; the best segment -1.2% (CI -6.5 to +4.2).
                          Ranks on MARKET BALANCE alone: the league-scoring signal is real but
                          needs a league→history mapping we cannot verify (our CSVs are keyed
                          per season, "SerieB-2024-2025", fixtures say "Serie B"), and a wrong
                          mapping would apply Argentina's draw rate to a Danish match.
                          The model's draw_prob EXCLUDES (<20% → real rate 7.4%) but never
                          ranks — it has never once rated a draw most likely (0 of 360).
                          DISCLOSURE travels in the API payload so no UI can drop the caveat.
- models/team_match.py    THE abbreviation-aware team matcher (Wolves/Wolverhampton,
                          Union Saint Gilloise/Union St.-Gilloise), shared by kickoff_times.py
                          and live_scores.py. Two matchers against one feed is how bug #17
                          hid live scores. consensus/dedupe/results_api still use the looser
                          models.consensus.team_sim ON PURPOSE — they grade, see bug #17.
- models/live_scores.py   On-demand ESPN score for started, ungraded matches (decoration only,
                          never written to results). Memoised 60s; the Picks page polls it.
- analysis/gemma_analyst.py  Gemma via Ollama /api/chat (reasoning layer, NOT the predictor)
- api/server.py           FastAPI. Endpoints below.
- tools/check_docker_copies.py  Asserts every module api/server.py imports is COPYed into the
                          Render image. Guards bug #15, which shipped three times because a
                          missing COPY fails invisibly (Render keeps serving the old image).
- bot/telegram_bot.py     /start /picks /slip /stats + daily 08:00 send. drop_pending_updates=True.
- bot/slip_handlers.py    /slip interactive flow
- db/database.py          schema/helpers. `results` gained decided_in ('REG'|'OT'|'SO'; NULL =
                          unknown or soccer) + reg_home_goals/reg_away_goals (27 Sep 2026, bug #31).
- db/calibration_health.py  Tier/band calibration, sign test and model-era table from the ledger
                          (PR #13). Read-only. Replaces the hand analysis in item 4.
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
                          skeleton loaders, and per-card "Expected: H–A" scoreline. ACCESSIBILITY lives in
                          api.js too (27 Sep 2026): a :focus-visible outline on every control, and
                          labelIcons() + a MutationObserver that aria-hide decorative Material icons
                          (their ligature text was read aloud: "check_circle WON") and name icon-only
                          controls. A page's own aria-label always wins. Dark mode lives in
                          js/api.js: it injects !important overrides remapping the Material tokens to a
                          dark palette under html.dark (covers accent/error/border tokens too — keep new
                          token classes in that list) + a .theme-toggle button injected in-flow into the
                          desktop top nav and the mobile bottom nav (never floating over content),
                          persisted in localStorage 'pp-theme'. api.js also makes the logo+name a
                          link to index.html on every page, and on mobile trims the bottom nav to
                          Picks/Builder/Record/More — Consensus, Checker and the theme toggle live in
                          the #pp-sheet bottom sheet behind the More (hamburger) button.
                          STYLES (28 Sep 2026): pages load a PREBUILT css/app.css (30 KB, 6.5 KB
                          gzipped), not the Tailwind Play CDN (419 KB script that compiled CSS in
                          the browser, 7.9 s to fetch on a slow link). Tokens live in
                          tools/tailwind/tailwind.config.js (theme.js is gone). deploy-webapp.yml
                          REBUILDS it every deploy and hash-stamps css/app.css?v= like api.js, so
                          production can't ship stale CSS; the committed copy is for local dev —
                          `cd tools/tailwind && npm ci && npm run build` after adding classes.
                          RULE: never build a class name from pieces ("text-"+x) — the build only
                          sees whole class names written in webapp/*.html and webapp/js/*.js.
                          Swap verified by computed-style diff: 0 differences, 8 pages x
                          phone/desktop x light/dark.
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
GET / ; GET /api/picks ; GET /api/picks/all ; GET /api/picks/draws ;
GET /api/picks/sports?sports=nhl,nfl (NHL/NFL picks + each sport's own record) ;
GET /api/slip?risk=&games=&sports=football,nhl,nfl (slip legs now carry match_id + sport;
before 2 Oct 2026 they had NO match_id, so Builder-tracked slips never settled) ;
GET /api/consensus ; GET /api/stats ; GET /api/stats/tiers ;
GET /api/stats/picks?tier= (drill-down) ; GET /api/stats/trend (weekly hit
rate, for the Record chart) ; GET /api/fixtures (for slip-checker) ;
GET /api/leagues ; POST /api/slip/check (score a user-assembled slip) ;
POST /api/results/lookup {match_ids} (final scores - grades a user's My Record).
POST /api/slip/settle {legs} (won/lost/void/pending/ungradable per leg, via
models/settle.py - grades tracked accumulators) ; POST /api/slip/odds {legs}
(SportyBet prices for a hand-built slip, recorded when it is tracked).
Booking: POST /api/booking/decode | generate | convert | merge | split |
optimize ; GET /api/preview/nhl (PRIVATE NHL preview, see models/nhl_preview.py) ;
GET /api/booking/providers (authoritative list of who we can read
from vs create on — they are NOT the same set). CORS allow_origins=["*"].

Payload notes worth knowing before adding a consumer:
- /api/picks carries `league_display` (readable league), `book_odds` /
  `book_probability` / `market_gap` (the bookmaker check), `consensus`, and a
  `started` list whose legs get `live_score` / `live_detail` / `live_final`.
- /api/stats returns the SERVED (HIGH+MEDIUM) figures in its top-level keys
  and everything else under `all_tiers`, plus `draws` / `ceiling_pct`, and
  `calibration` ([{tier, n, won, actual, claim, lo, hi, verdict}] from
  db/calibration_health.tier_calibration — the SAME function the CLI report
  uses, so record.html and the script cannot disagree). /api/stats/trend
  flags the current, unfinished week with `in_progress`.

## The model
Random Forest (scikit-learn), **70,916 matches / 102 CSV files**, feature matrix
67,481 rows. Features: form ppg, goals for/against avg, venue ppg, h2h,
odds-implied probs, **`odds_is_market`** (1.0 = a real bookmaker price, 0.0 = the
flat fallback prior — added with `rf_v2`, see bug #18). Time-aware 80/20 split
(53,984 train / 13,497 test), no leakage.

Calibration is the core value. Hold-out accuracy by confidence tier, `rf_v2`,
measured 23 Aug 2026:
  **HIGH (≥65% prob) 74.6% on 12.5% of matches; MEDIUM (≥55%) 65.0% on 33.3%;
  LOW never served.** Overall 51.3%.

Two things to keep straight about those numbers:
- They are **hold-out test-set** figures, not graded live results. Better than
  the old training-set "~75%", still not the same thing as the ledger.
- **Overall 51.3% is below the ~53–55% a bookmaker's favourite gets.** The value
  claim rests entirely on the calibrated top tier, not the top line. Do not
  quote 51.3% as if it beat the market, and do not "fix" it by re-tuning the
  tier thresholds off one run.

Gemma/Gemini is explanation only, never the predictor.
Confirmed the model has an independent voice (disagrees with Forebet often, via
models/model_vs_source.py) — but whether its disagreements are *profitable* is
unknown until the honest ledger matures (`db/edge_report.py`).

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

11. **Consensus silently empty — "latest row" beat "latest OPINION" (Aug 2026).**
    The consensus page showed nothing and `/api/consensus` reported
    `covered_by_multiple: 0` — which READ as a data drought (Forebet skipped in
    the cloud, API-Football down) but was a query bug. 19 upcoming fixtures each
    had TWO sources with usable H/D/A percentages. consensus.py picked
    `MAX(id) per (match_id, source)`; PredictZ writes a row per fixture whether
    or not it found odds, leaving the pct columns NULL when it didn't (190 of
    228 upcoming rows). So the newest PredictZ row was often an EMPTY one,
    `_best_outcome` returned None, and PredictZ vanished from every entry.
    FIX: filter to rows with non-NULL pcts *inside* the MAX(id) subquery —
    a NULL-pct row is the absence of a verdict, not a newer verdict. After the
    fix: 19/19 covered, 19 agreeing.
    **When "latest per group" meets nullable payload columns, filter before
    taking the max, or a newer empty row silently deletes an older real one.**
    Two related things found in the same pass:
    - **API-Football's account is SUSPENDED** (`{'access': 'Your account is
      suspended, check on https://dashboard.api-football.com.'}`), so that
      source has written nothing since 6 Aug. Its pipeline step is
      `required=False`, so the run stayed GREEN while a whole source died —
      the bug #5/#8/#9 pattern again. FIX: `source_health()` in run_daily.py
      now alerts when an expected source writes nothing in 2 days, AND when
      fewer than 2 sources have usable percentages on upcoming matches
      ("consensus is blind"). Fixing the account needs Boss (billing/ToS).
    - **The two live sources are not independent.** ESPN's pcts and PredictZ's
      pcts are both DERIVED FROM BOOKMAKER ODDS, so 19/19 "agreement" is
      largely two views of the same market, not two opinions. Forebet was the
      genuinely independent model. Do not sell current consensus as strong
      corroboration until an independent source is back.
    - `/api/consensus` now returns `sources` + `diagnosis`, and consensus.html
      renders the reason it is empty. An empty page must say WHICH failure it is.

12. **football.com can READ codes but not CREATE them (Aug 2026).** It decodes
    SportyBet codes perfectly (same backend, same Betradar ids), so it looked
    like a free third bookmaker for generation — and it was already listed in
    `SHARE_POST_URLS`, which `convert_code`/`merge_codes` trusted. But POSTing
    the identical selections body SportyBet had just accepted returns
    `bizCode 19999` on `/api/ng|gh|ke/orders/share` and 503 elsewhere.
    Removed from SHARE_POST_URLS with the probe recorded inline.
    Also fixed: `generate_code` had its OWN hardcoded provider dict instead of
    reading SHARE_POST_URLS, so the two lists could drift (they had).
    **Decoding and encoding are different permissions on the same API — never
    infer one from the other. One dict per capability, read by everyone.**
    Bet9ja generation is now BUILT (see the booking-code decision below);
    Betano still needs one, and uses its own ids too.

13. **A pulsing LIVE badge on matches that finished 12 hours earlier (Aug 2026).**
    `matches.live_state` is a SNAPSHOT kickoff_times.py writes at scrape time,
    not a live feed — but the API and the web app both read it as current
    truth. A match in play when the pipeline ran keeps `live_state='in'`
    forever, until some later run happens to re-scrape that fixture. The
    pipeline runs a couple of times a day, so it never does. Verified 9 Aug:
    ten fixtures still 'in', kicked off 10.7-12.4h earlier (Boca Juniors v
    Velez, New England v Houston...). The most confidently-wrong element on
    the page: a red pulsing "LIVE — being played right now" on last night's
    football. FIX: `effective_live_state()` in api/server.py ages the field —
    past LIVE_MAX_HOURS (3.5h, covering 90min + HT + stoppage + ET + pens)
    'in' becomes 'post'. Applied at every point live_state leaves the API, and
    mirrored in api.js's isLive() because the service worker can serve a
    cached payload carrying a stale 'in'.
    **More scraping would NOT have fixed this — no daily pipeline can track a
    90-minute state. A snapshot masquerading as live state needs an EXPIRY,
    not a faster refresh. Any field named like a live status should be asked:
    what happens to it when nothing runs for 12 hours?**

    Second pass (same day, after "check very well") found two more:
    - **'pre' goes stale exactly like 'in'.** The first fix only aged 'in',
      but the snapshot is just as often taken BEFORE kickoff and never
      updated — 45 rows with a kickoff >3.5h past still claiming 'pre', one
      of them already GRADED with a final score. effective_live_state now
      ages 'pre' too; only 'post' (terminal) and NULL (unknown) pass through.
      The dangerous version of this is a stale 'pre' on a fixture with NULL
      kickoff_utc: nothing would ever move it out of the bettable list. None
      exist today (checked) — the aging closes it before one does.
    - **"Kicked off today" was mostly YESTERDAY.** The started query spans
      `date('now','-1 day')` on purpose, so late kickoffs don't vanish (23:30
      UTC is 00:30 in Lagos). But the heading said "today" while 19 of the 20
      rows had kicked off the previous day, and no row showed its own date.
      Now "Recently kicked off", with a per-row day label ("Yesterday 23:15").
      **When a query deliberately spans two days, the LABEL has to say so.**

14. **The whole product went dark for a day — and the pipeline was working
    (11 Aug 2026).** Boss: "the pipeline did not run this morning, telegram not
    working, website no work." All three symptoms, one silent cause, and two of
    the three faults were introduced by the previous day's "improvement".

    The 10 Aug run did its job: PredictZ scraped 63 matches, kickoff times
    landed, predictions were generated, the ledger was committed locally. Then:

        error: cannot pull with rebase: You have unstaged changes.
        ##[error]Ledger conflict — another writer touched the DB.

    **That error message was a lie and cost diagnosis time.** There was no
    second writer. `data/historical/*.csv` are TRACKED (92 files) and the
    "Refresh training CSVs" step rewrites them, so the worktree had unstaged
    changes and `git pull --rebase` refused. The push never happened, so a full
    day of fixtures and predictions was computed and thrown away. The site then
    had 0 upcoming matches and honestly showed nothing.
    FIX: stage the refreshed CSVs (they are part of what the job produces) and
    `git pull --rebase --autostash`.
    **Never write an error message that asserts a cause you have not checked.**

    SECOND FAULT (mine, from bug-13's severity work): "Send picks to Telegram"
    and "Publish bot feed" carried no `if:`, so they inherit GitHub's default
    "run only if every previous step succeeded". The moment DEGRADED steps
    started exiting non-zero, a tolerable data-source outage silently became a
    total user-facing one — the picks existed, nobody was told. Both are now
    `if: always()`.
    **Making a run RED is only safe once you have checked what a red run
    SKIPS. I verified exit codes and never looked at the skip list.**

    THIRD: API-Football was DEGRADED, so it failed the run every single day —
    and that daily red is what skipped Telegram. Now OPTIONAL, and dropped from
    source_health's expected set. Its coverage is already replaced (fixtures ->
    PredictZ, odds -> ESPN via kickoff_times). What is genuinely lost is
    INDEPENDENCE: ESPN and PredictZ both price off the same bookmaker markets,
    so consensus between them is two views of one market, not two opinions.
    **A step that is red every day is not a signal, it is noise — and noise is
    not harmless, it hid a real outage here.**

    Related: the "consensus is blind" health check now grades by COUNT —
    0 sources fails the run, 1 warns, 2+ is healthy — instead of failing daily
    for a condition we already know about.

15. **The Dockerfile allowlist, THREE times — now checked automatically
    (11 Aug 2026).** `deploy/render/Dockerfile` copies source file-by-file
    (`COPY models/__init__.py models/markets.py ... ./models/`). Add a module
    the API imports without adding it there and the container dies on import —
    at which point **Render keeps serving the PREVIOUS image**. The site stays
    up, the API answers every existing endpoint, and the new field is simply
    absent. A green deploy and a feature that silently did not ship.
      - `models/leagues.py`      → `league_display` missing in production
      - `webapp/`                → the static mount silently did nothing; the
                                   Render root 404'd for months
      - `models/gradeability.py` → `/api/stats` returned no `coverage` block
    The rule "remember to update the Dockerfile" has been written in this file
    since the FIRST occurrence and did not prevent the second or the third.
    FIX: `tools/check_docker_copies.py` walks the first-party imports reachable
    from api/server.py and asserts each is copied. Static — no Docker, no
    build, one second. Wired into run_daily.py as DEGRADED. It caught
    `models/draws.py` within an hour of being written.
    **Three occurrences of one mistake is a missing check, not carelessness.
    An allowlist maintained by hand will eventually be wrong, and this one
    fails INVISIBLY because the old image keeps answering. Prefer copying a
    directory wholesale; where you cannot, check the list mechanically.**

16. **Grading COVERAGE is now published, not just monitored (11 Aug 2026).**
    Bug #8 established that a tier percentage is a claim about the picks we
    could VERIFY, and that the unverifiable ones are not a random sample —
    when the backfill finally landed, HIGH fell 57% → 38% because what was
    missing was mostly losses. That was fixed operationally but never made
    visible, so the same drift could recur unnoticed.
    `models/gradeability.py` now derives coverage from the ledger:
    `/api/stats` carries a `coverage` block, record.html states it in words
    (including what it could NOT check), and run_daily.py alerts below 75%.
    The "untracked league" list is EVIDENCE-BASED and self-updating — 4+
    gradeable picks and not one ever scored. A hardcoded list of leagues ESPN
    won't score was the tempting version and would have been wrong: 16 leagues
    had never been scored but covered only 24 picks between them, mostly n=1.
    The 75% floor is measured, not chosen: a full ESPN backfill reached 83.7%
    and could go no further, so ~84% IS normal and a floor at 85% would fire
    daily — the noise trap of bug #14. **A number that only reports its
    successes at being checked is the same shape as reporting only the picks
    that won. Publish the denominator.**

17. **The live score was neither live nor scoring (11 Aug 2026).** Boss: "the
    live score part is not live scoring." Two independent faults, one on each
    side of the API, and each alone was enough to produce the symptom.

    **Client: the page asked ONCE.** `/api/picks` attaches a live score per
    started match (`models/live_scores.py`, memoised 60s), but index.html
    fetched at load and never again. So the score was a still frame from
    whenever the tab was opened, under a pulsing red dot insisting it was
    current — the bug-#13 shape exactly, one layer up: not a stale snapshot in
    the DB this time, but a stale snapshot in the browser. FIX: poll every 60s
    (= `CACHE_SECONDS`; faster only re-fetches the same memoised payload)
    while any leg is actually in play, refreshing ONLY the started list — the
    main feed is left alone so a re-render can't reset the tier chips, the
    league filter or the scroll position under someone reading it. The timer
    stops dead when nothing is live and while the tab is hidden, and fires
    immediately on returning to the tab or expanding the list. A live count
    now shows in the section header, because the list is collapsed by default
    and the one genuinely live thing on the page was hidden behind a chevron.

    **Server: a weak matcher downstream of a strong one.** live_scores.py
    matched teams with `models.consensus.team_sim` (plain string similarity)
    while `scrapers/kickoff_times.py` had a much better one. This matters
    because live_scores keys off `espn_league`, a column kickoff_times WRITES —
    so every row it looks up is by definition one the strong matcher already
    succeeded on, and the weak one could only lose them again. Measured live:
    "Union Saint Gilloise" vs ESPN's "Union St.-Gilloise" scored 0.65 against
    a 0.80 floor, so Bodo/Glimt v Union SG sat at "no score source" while ESPN
    had it at 2-2, 90'+3. FIX: the matcher moved to `models/team_match.py` and
    both callers read it (bug #12's rule about one dict per capability, applied
    to matching). Also added the ±1 day window to the scoreboard fetch — asking
    ESPN for the wrong side of midnight returns nothing, which reads as "no
    source" rather than "wrong date" (bug #2's rule, missed here).
    Left alone deliberately: consensus.py, dedupe_fixtures.py, results_api.py
    and clubelo.py still use the looser matcher. They GROUP and GRADE, where a
    looser match risks merging two fixtures or scoring a pick against the wrong
    match — bug #1 territory. That switch wants its own before/after count.

    Verified end to end in a real browser against the local API: the row went
    "no score source" -> "2-2 94'", the minute advancing between polls.
    `models/team_match.py` was of course missing from the Dockerfile —
    `tools/check_docker_copies.py` caught it before it shipped, which is the
    fourth time that allowlist would have failed silently (bug #15).

    **A number that cannot change is not live. If a UI claims something is
    happening now, something has to ask again — and when two components match
    the same feed, the weaker one silently caps what the whole chain can find.**

    Still open (honest limitation, not a regression): only ~110 of 263 upcoming
    matches have an `espn_league` at all, so the rest genuinely have no live
    source and correctly say so. That is coverage, not a bug.

18. **The model was trained on odds it mostly didn't have — 60.5% of rows got a
    constant (23 Aug 2026).** Bug #7 fixed the SERVE side of the train/serve
    odds mismatch: predict.py stopped feeding Forebet percentages into features
    meant for real bookmaker odds. The TRAIN side was never checked.

    `train.py` read odds from exactly three columns (`B365H/D/A`) and
    substituted a flat `0.33/0.33/0.33` when they were absent. Absent was the
    majority case: **42,886 of 70,916 rows (60.5%)**. Those three columns are
    the model's three most important features, so on six rows in ten the
    strongest signal available was a constant asserting all outcomes equally
    likely — on rows whose actual outcome was known and usually wasn't a draw.

    The odds were mostly THERE, under other names. football-data.co.uk
    publishes closing prices as `B365CH/CD/CA`, plus Pinnacle (`PSC*`),
    market-average (`Avg*`) and best-available (`Max*`) families. 85 of 102
    files carry Bet365 closing, and **101 of 102 carry some closing family**.
    A one-family loader threw away data already sitting in the repo.

    FIX: seven column families tried in order, **closing before opening** so
    the feature carries ONE meaning across the whole set rather than mixing
    price points file by file. Plus a new `odds_is_market` feature — 1.0 for a
    real price, 0.0 for the fallback prior — because the serve-time fallback
    (bug #7 kept it deliberately) means the placeholder still reaches the model
    in production, and a model that cannot distinguish a placeholder from a
    price is being told the placeholder IS a price. Coverage 39.5% → 94.1%.

    Measured on the same time-aware hold-out, `rf_v1e` → `rf_v2`: **≥65% tier
    74.2% on 8.4% of matches → 74.6% on 12.5%** — same accuracy, 49% more
    matches served. Overall 49.7% → 51.3%. Odds share of feature importance
    41.3% → 66.6%.

    And the pathology fell out: **draws predicted went from ~0 (recall 0.00) to
    129 at 32% precision** against a 25.7% base rate. "Never predicts a draw"
    had several plausible causes; a constant 0.33 draw prior on 60% of training
    rows was demonstrably one of them.

    Overall accuracy is STILL below the ~53–55% a bookmaker's favourite gets.
    This fix does not change that and must not be described as if it did.

    **A default that fires on the majority of your data is not a fallback, it
    is your dataset. Count how often the fallback fires before trusting the
    feature — `train.py` now logs coverage and warns below 85%.** Editing
    `train.py` invalidates the Actions cache key, so the cloud retrains itself.

19. **`db/cleanup.py` would have deleted the entire track record, unprompted.**
    It ran `DELETE FROM model_predictions` with no WHERE clause, and separately
    "deduplicated" `scraped_predictions` by grouping on `match_id` **alone with
    no `source`** — which is not a dedupe, it is collapsing every source's
    opinion per fixture into one row and destroying the evidence consensus is
    built from. A file named cleanup.py, one careless invocation from erasing
    the thing the product's credibility rests on.

    FIX: dry-run by default, `--apply` required, only touches
    `match_id LIKE 'test_%'`, honours `DB_PATH`, and refuses when the DB file is
    absent (sqlite3.connect would otherwise CREATE an empty one and cheerfully
    report "0 test rows deleted" while the real ledger sat untouched elsewhere).

20. **`dedupe_fixtures.py` kept the OLDEST duplicate row (`MIN(id)`).** It runs
    with `--apply` daily, BEFORE predict and market_snapshot. Every serving and
    stats query in the codebase reads the LATEST row per group via `MAX(id)`
    (rule from bug #3, refined by bug #11), and the newer row is the one
    carrying `book_odds`, `draw_analysis` and late odds movement. So the daily
    dedupe was deleting exactly the rows everything downstream wanted.
    FIX: `MIN(id)` → `MAX(id)`, both collapse queries.

    Same day, same rule, two more places that published an UN-deduped track
    record: `bot/telegram_bot.py::fetch_stats` and
    `scrapers/results.py::show_scoreboard`. On the 23 Aug ledger the bot
    reported **152/243 = 62.6%** while the API reported **136/216 = 63.0%** —
    two different published accuracies for the same day, because 80 matches
    held more than one prediction row. **"Latest per group" is not an
    optimisation, it is the definition of what we predicted.**

21. **`draw_analysis` column existed only if an OPTIONAL step had run.** The
    column was created by `analysis/draw_analysis.py`, never by
    `init_db()` — but `api/server.py:406` SELECTs it unconditionally. On a
    fresh clone `/api/picks/draws` therefore died with `no such column`, and
    since draw_analysis is an OPTIONAL pipeline step (never red), nothing
    pointed at the cause. FIX: the `ALTER TABLE` lives in `init_db()` with the
    other migrations. **Schema belongs to the schema file, not to whichever
    script happened to write the column first.**

22. **Every team matcher scored "Barcelona" vs "Barcelona U20" at 0.92
    (23 Aug 2026).** Bug #12's one-implementation rule was applied to booking
    providers and to live scores, but the matchers themselves still had a shared
    blind spot: containment plus first-word agreement says YES to senior-vs-youth
    and senior-vs-women's, because the short name really is inside the long one
    and the leading word really does agree.

    This is not cosmetic. SportyBet's events feed lists senior, youth and
    women's fixtures side by side, and `booking_codes.py::_teams_match` was the
    LOOSEST matcher in the codebase (`if na in nb or nb in na: return 1.0`)
    sitting on the one code path where a false positive is **real money on the
    wrong match**. Separately, `team_match.py` is what `kickoff_times.py` writes
    ESPN **odds** off — and odds are model FEATURES, so a crossed fixture there
    poisons a prediction rather than merely showing a wrong kickoff.

    FIX: squad qualifiers (`u20`, `under20`, `youth`, `junior`, `academy`,
    `women`, `ladies`, `fem*`, `reserves`, `ii`, `iii`, `b`) extracted as a SET
    and required to be **equal**, else a hard 0.0. Compared as sets, so
    women-vs-women and U20-vs-U20 still match each other — only mismatches are
    rejected. `jr` deliberately excluded (indistinguishable from an initial);
    clubs genuinely named Junior are unaffected because both sides carry the
    token. Verified: all six senior/squad pairs now 0.00, while Chelsea
    Women/Chelsea FC Women stays 1.00 and Cardiff/Cardiff City stays 0.92.

    Consolidated at the same time: `booking_codes._teams_match` now delegates to
    the shared matcher; `map_leg_to_sportybet` uses the shared floor-AND-mean
    `pair_score` instead of a bare 0.72 mean; `generate_bet9ja` raised from a
    raw 0.62 bar (below the 0.65 the shared matcher CLAMPS Man Utd/Man City to,
    i.e. it would have accepted a pair the matcher explicitly rejects).
    `consensus._same_match` switched from raw `_sim > 0.8` on unnormalised names
    to `same_teams`. `st`/`saint` noise words added to consensus, which GRADING
    uses — `team_match.py` had them all along, so grading could fail on a
    fixture kickoff_times had already matched.

    **A containment rule cannot tell a subset from a subsidiary. When the same
    lexical shape means "same club" and "different club", the qualifier has to
    be compared explicitly, not similarity-scored.**

23. **The LLM was told a 2.76% chance of over 2.5 goals on a fixture expected to
    produce 2.76 goals (23 Aug 2026).** `scraped_predictions.over_2_5_pct` does
    not hold a percentage. `scrapers/forebet.py:214` writes Forebet's average
    total goals into it, and `models/markets.py:171` correctly reads it back as
    `avg_goals`. Ledger values confirm: n=1602, min 0.0, **max 8.67**, mean 2.76
    — no probability has that shape.

    `gemma_analyst.build_prompt` labelled it `Over 2.5 Goals Probability:
    {value}%`. So the analyst was handed a false statement of fact — a 2.76%
    chance of over 2.5 goals implies a near-certain 0-0 — and invited to argue
    for a goalless draw on a high-scoring fixture, in prose published under our
    name. FIX: the label is now `Expected Total Goals`. The COLUMN NAME is left
    alone deliberately: it is load-bearing across the schema, and renaming it is
    a migration, not a doc fix. **A misleading column name is a latent bug that
    fires the moment someone reads the name instead of the writer.**

    Also fixed in the same file: the failover chain stopped one hop short. The
    gate `check_ollama_running()` was corrected on 11 Aug to ask "can ANY
    provider serve?" and its docstring promised Gemini → Groq → Ollama, but
    `analyze_match` fell over Gemini → Groq and then **stopped** — so a run with
    both hosted providers down produced no analysis at all while a healthy
    Ollama sat unused and the gate had already said yes on its behalf. With no
    `GROQ_API_KEY` it didn't fall back at all. FIX: gate and chain now read the
    same list from `_provider_chain()`, so they cannot disagree, plus a
    two-strike breaker that drops a dead provider for the rest of a run
    (retrying a bad Gemini key costs 4.5s pacing + 90s timeout × ~45 matches).
    **A gate in front of a chain and the chain itself must derive their list
    from one place — a docstring is not an implementation.**

24. **The Checker could price a hand-built slip but not book it** (24 Aug 2026,
    `webapp/checker.html`). The left panel let you search fixtures and pick a
    market off its percentage; the right panel priced the accumulator. There was
    no way to turn that into a booking code — you had to go to the Builder and
    reassemble the same selections by hand. The API needed nothing: the legs the
    Checker already stored (`{home_team, away_team, market, selection}`) are
    exactly `/api/booking/generate`'s input. FIX: SportyBet / MSport / Bet9ja
    buttons on the slip panel, same call the Builder makes. Verified end to end
    against the live endpoint (3-leg payload → code `QFQ1R0`, 2 mapped, 1
    unmapped and reported).

    Two real bugs surfaced while doing it:

    - `addLeg` never toggled off. The comment said "toggle off if same fixture"
      and the code removed the fixture's leg — then unconditionally re-pushed,
      so clicking the same chip twice was a no-op. Now: same chip removes,
      different market on the same fixture swaps.
    - **Only 6 of ~12 markets per fixture were reachable.** The card rendered
      `fx.markets.slice(0,6)` with no "more" control, and `/api/fixtures` sorts
      by probability descending — so the visible six were always the near-certain
      Double Chance and Handicap lines, and Match Result was often off the end.
      "Pick the market you want" was not actually possible. FIX: `+N more`
      expander per fixture plus a market dropdown built from the loaded
      fixtures (no hardcoded list to go stale). **A cap with no affordance to
      lift it is indistinguishable from the data not existing.**

25. **FIXED 26 Aug 2026 (commit 452c876) — this entry was stale until 27 Sep.**
    derive_markets no longer reads the predicted scoreline at all: the split
    comes from the 1X2 probabilities and the TOTAL from models/goals_model.py
    (attack/defence rates fitted on ~98k matches), so Over/Under 1.5/2.5/3.5,
    BTTS and Result & Goals are priced on EVERY priced fixture. Verified 27 Sep:
    a scoreline-less fixture yields all five goals families with per-fixture
    probabilities. The history below is kept for the reasoning.

    **Over/Under and BTTS have silently vanished from the product** (found
    24 Aug 2026 — NOT yet fixed, and not a regression to revert). `/api/fixtures`
    now returns only 4 market families: Match Result, Double Chance, Draw No Bet,
    Handicap. Over/Under, BTTS and Result & Goals are gated on
    `predicted_home_goals`, which reaches `derive_markets` through the plain
    `MAX(id)` join at `api/server.py:199` — deliberately left plain on 9 Aug
    (Boss's call) so goals markets stay RARE, because the scoreline-as-xG
    derivation prices "Under 2.5" at 91-99% against near-even bookmaker money.

    What changed since is that PredictZ carries a scoreline on **100%** of its
    rows (2387/2387) while ESPN carries one on **0%** (0/582) — and ESPN now wins
    the `MAX(id)` race often enough that "rare" has become "never". So a
    deliberate throttle has quietly become a full switch-off. The 9 Aug reasoning
    still holds: do NOT widen the join to bring them back, because that restores
    the mispricing. The real fix is still a genuine goals signal (team
    attack/defence rates, or the bookmaker's own O/U line). **A knob set to
    "rare" is not stable — it depends on the mix of sources, and the mix moved.**

26. **Forebet is not "failing to push" — it is switched off in the cloud**
    (clarified 24 Aug 2026). 17 consecutive days of zero Forebet rows (6→23 Aug)
    with every daily run reporting success. Cause is `PP_SKIP_FOREBET: '1'` in
    `daily.yml:90`, set on purpose because Forebet Cloudflare-403s GitHub's IP
    ranges. Consequences that are easy to miss because nothing goes red:
    Forebet is the ONLY source of `over_2_5_pct` (1602/1602 rows; every other
    source: 0) and supplies 198 of our 298 leagues. So cross-source consensus is
    largely blind in production, and the analyst prompt's Forebet block is all
    "N/A". **No schedule change fixes an IP block** — the options are a proxy, a
    different host, a scheduled local run, or accepting the loss.

27. **One recorded game produces a full-magnitude form signal, and it is
    reshaping a third of the served picks** (found 24 Aug 2026, measured, NOT
    yet fixed). Re-run the measurement with
    `python db/form_depth_health.py` — it exists so this never has to be
    rediscovered by hand.

    Two mechanisms combine. First, `build_current_team_histories()` appends
    **our own graded results** to form (`predict.py:109-134`, deliberately — it
    keeps form fresh after the CSVs end). Second, `form_ppg`, `goals_avg` and
    `venue_ppg` divide by `len(recent)`, **not by the window**
    (`train.py:197-218`). So a club that exists in history solely because we
    once recorded a result for it gets a form feature at full strength from a
    sample of one: one win reads `form_ppg = 3.0`, identical to five straight
    wins; one defeat reads 0.0, identical to five straight defeats.

    The scale is not marginal:
    - **523 of 1604 teams (33%) have ≤5 games; 386 have 1-2.** These are clubs
      from leagues we never trained on at all — Kofu, Schaffhausen, Galway
      United, Rizhao Yuqi, even "American Samoa".
    - **96 of 264 served HIGH/MEDIUM picks (36.4%) involve one.** Example:
      `Besiktas v Kauno Zalgiris`, HIGH, 90% — Kauno Zalgiris has exactly one
      recorded game.

    **But thin history is NOT uniformly bad, and this is the part that matters
    for the fix.** On 635 graded picks:

        thin   HIGH    29/34   85.3%   <- BEST bucket in the whole ledger
        deep   HIGH    32/51   62.7%
        deep   MEDIUM  63/96   65.6%
        thin   MEDIUM  23/47   48.9%   <- below the floor MEDIUM promises
        thin   LOW     64/145  44.1%
        deep   LOW    115/262  43.9%

    Overall the bands are identical (51.3% each). **Read the next three points
    carefully, because the first framing of this finding was wrong and the
    script is what caught it.**

    - **Tier ordering within thin picks is INTACT** (85% > 49% > 44%). It is
      *deep* that shows a mild inversion (HIGH 63% < MEDIUM 66%) — and those two
      intervals overlap heavily, so that is most likely noise, not a defect.
    - **The real, measurable defect is a labelling failure, not an ordering
      one:** thin MEDIUM's 95% interval is 35-63%, which **excludes the claimed
      66%**. For thin picks the MEDIUM badge is not supported by the ledger.
      (`deep HIGH` is borderline too: 49-75% against a claimed 75%.)
    - **Hit rate does NOT decline with less history.** By depth of the shallower
      team: 1g 62%, 2g 50%, 3-5g 51%, 6-10g 52%, 11-25g 50%, 26+g 51%. Flat,
      with heavily overlapping intervals. So the `len(recent)` mechanism is real
      and provable in the code, but its effect on accuracy is **not** yet
      demonstrated — do not claim it is. What is demonstrated is the thin-MEDIUM
      labelling gap above.

    The thin-HIGH 85% (n=34) is a genuine outlier worth watching, not yet
    explained. The plausible story is that a one-game club is usually a minnow in
    a European qualifier, so "we have almost no record of this team" correlates
    with "out of its depth" — but the flat depth curve argues against depth
    itself carrying that signal, so treat this as unexplained.

    **Therefore the obvious fix is the wrong fix.** Refusing to predict without
    N games of history would delete the best-performing bucket in the ledger and,
    given the flat depth curve, on no evidence that depth is what hurts. The
    right fix is a **`has_form_history` / games-count feature** so the forest can
    distinguish "no data" from "dreadful" and learn any split itself — the same
    fix shape as `odds_is_market` in rf_v2, and the same underlying bug as #7 and
    #18: a silent fallback that changes what a feature MEANS without erroring.

    **Do not ship a tier demotion on these numbers either.** thin MEDIUM is
    n=47. The gap is suggestive and now has a CI that excludes the claim, but
    this project already mistook a 13-sample tier inversion for a real one
    ("What's next" item 4) and acted on it. Re-run `form_depth_health.py` as the
    ledger grows.

    **And the finding is threshold-sensitive, which is the strongest argument
    for waiting.** Re-running the same 635 picks with `--depth 3` instead of the
    default 5 moves every conclusion:

        --depth 5:  thin MEDIUM 23/47  49%  CI 35-63%   excludes claim 66%
        --depth 3:  thin MEDIUM 18/31  58%  CI 41-74%   CONTAINS claim 66%
        --depth 5:  deep ordering BROKEN (HIGH 63% < MEDIUM 66%)
        --depth 3:  deep ordering OK     (HIGH 64% > MEDIUM 61%)

    A result that flips when you nudge an arbitrary cut-off from 5 to 3 is not
    yet a result. The one thing stable across both cuts is thin HIGH being
    extraordinary (85% at ≤5, 92% at ≤3, n=34/24) — small, but pointing the same
    way each time. Everything else: wait for more picks.

    **Sequencing note:** the fix needs a retrain, which mints a new
    `MODEL_VERSION`. rf_v2 has not yet been observed in the cloud even once, so
    stacking rf_v3 on top now would make it impossible to attribute either
    change. Watch rf_v2 first — that ordering is the whole reason the era
    comparison in item 4 was possible.

    **TRIED 27 Sep 2026 — the proposed fix does not work as written, and was NOT
    shipped.** What the analysis above missed: `train.py` DROPS every row where
    either team has fewer than FORM_WINDOW (5) games (`home_matches_played >=
    FORM_WINDOW`). So a `has_form_history` feature alone would be a constant in
    training — the forest has never seen a thin-history row at all, while
    serving predicts clubs with one game. The real candidate is: train on rows
    with >=1 game AND add `home_form_n`/`away_form_n` (games in window, capped at
    5). Measured against rf_v2 on the SAME date cutoff and the SAME 14,592 test
    rows: overall acc 51.0% vs 51.0%, log loss 1.0000 vs 1.0002; HIGH (>=65%)
    said 73.9%->won 74.7% vs said 73.7%->won 74.7%; thin rows are only 210 of
    the test set (1.4%) and the new features took 0.2% of importance.
    **No measurable gain, so no new model era.** The production thin-history
    teams exist mostly through OUR OWN recorded results (not the CSVs), a
    distribution the historical data cannot teach. Don't re-run this experiment
    expecting a different answer; the next lever, if thin-MEDIUM keeps missing
    its claim in form_depth_health.py, is a serve-side rule, measured on the
    ledger.

28. **The AI analysis was generic because the prompt made generic the only
    possible answer** (fixed 24 Aug 2026). Boss's words: "is there a way we can
    make the ai analysis not generic, that it actually gives an analysis based on
    facts". It was not a model-quality problem and no amount of provider-swapping
    would have fixed it.

    `build_prompt` supplied three model probabilities, three second-opinion
    percentages, a predicted score, a tier — and then ended:

        Do NOT mention model names or percentages directly.

    Every number in the prompt was banned from the output and nothing else was
    provided. **None of the 13 features the forest actually uses reached the
    prompt**: not form, not goals for or against, not the venue record, not
    head-to-head, not the bookmaker's price. So the LLM wrote around the void,
    correctly: *"the home side look strong and should have enough to see this
    out."* That sentence fits every fixture ever played, which is what made it
    feel worthless.

    The fix is `_facts_block()` in `analysis/gemma_analyst.py` plus
    `build_analysis_facts()` in `models/predict.py`. The prompt now carries an
    EVIDENCE section built from **the same `feats` dict that produced the
    prediction**, passed through unrecomputed — so the prose cannot drift from
    the pick it explains. The ban is narrowed to our own win percentages (the UI
    already shows them next to the text) while form figures, goal averages and
    the real price are explicitly quotable. Before/after on the same fixture:

        before: "Al Hilal look strong at home and should have enough here."
        after:  "Al Hilal comes into this match averaging 2.60 points per game
                 at home and scoring 2.60 goals per match over their last five,
                 while Al Fateh struggles on the road with just 0.60 points per
                 game away. A key risk to this short 1.36 bookmaker price is
                 that Al Hilal still concedes 0.80 goals per game..."

    **Three things this had to get right, each of which failed on the first
    attempt and would have been worse than the generic text.** Specificity
    without truthfulness is not an improvement; it is a fabrication with our name
    on it.

    - **A bare count is invertible.** `h2h_home_wins` is 0 both for "met five
      times, won none" and for "never met". Given "won 0 of the last 5", Gemini
      wrote *"Al Fateh has won all of the last 5"* — which does not follow (they
      could all have been draws) and in that fixture was simply untrue. Fixed
      with `h2h_record()` returning the full W-D-L split plus the number of
      meetings that actually happened, and saying "never met in our records" when
      there are none. Same defect and same fix as `odds_is_market`: make absence
      of data distinguishable from a zero.
    - **One game reads as five.** `form_ppg` divides by `len(recent)`, not by the
      window (bug #27), so a single win is 3.00 ppg — identical to five straight
      wins. Handed "3.00 points per game" with no caveat, an LLM will write
      "unbeaten in their last five" about a club we have seen once. The evidence
      block therefore emits a `!! SMALL SAMPLE` line *before* the figures it
      qualifies, naming the actual count, and the rules require admitting it.
      This is the first thing in the codebase that turns bug #27's measured
      finding into user-visible honesty rather than a silent risk.
    - **"Argument against" gets read backwards.** Asked for "the strongest
      argument AGAINST this pick", the model supplied the *opponent's* weakness —
      an argument FOR it. Reworded to ask for a RISK, with "the opponent being
      weak is not a risk" stated explicitly and a list of what does count (the
      favourite's own goals conceded, a dropped result in the run, a poor venue
      record, a thin sample, a short price).

    Also: when `odds_is_market` is 0.0 the block says so and forbids referring to
    bookmakers or prices at all, because those figures are another model's
    percentages — "the bookmakers rate them at" would be a straight falsehood.
    And `Invent NOTHING` enumerates what the project has no data about (injuries,
    suspensions, transfers, managers, league position, congestion, crowds,
    weather, motivation), because the moment a prompt asks for specifics an LLM
    will happily supply all of them.

    `_facts_block()` returns `""` when no facts are supplied, so
    `backfill_analysis.py` (which would need a full history replay to build them,
    for a handful of stragglers) and `draw_analysis.py` keep working on the
    probabilities-only prompt. The rules block is shared by both branches so the
    Draws tab cannot drift to a different standard of truthfulness.

29. **An emoji in a `print()` crashed the CRITICAL pipeline step on Windows**
    (found and fixed 24 Aug 2026, while verifying bug #28). `predict.py` ended
    its summary with `print(f"  💬 {p['gemma_analysis'][:150]}...")`. The Windows
    console is cp1252, `print` raises `UnicodeEncodeError` on U+1F4AC rather than
    substituting, and the exception propagated out of `run()` — so a completely
    successful run (46 predictions saved, all 46 on real bookmaker prices) died
    on its own display code. Because "Generate predictions" is the one `CRITICAL`
    step, `run_daily.py` aborts with it.

    Why it hid for so long, which is the instructive part:
    - **The cloud never sees it.** GitHub Actions runs UTF-8, so this is
      Windows-local only — i.e. it breaks exactly the manual run you'd do to
      check something before pushing.
    - **`--no-gemma` skips the line.** The guard is `if p.get("gemma_analysis")`,
      and local runs mostly pass `--no-gemma` to save time. The crash needs a
      local run WITH analysis, which is rare.
    - **loguru is not affected.** `logger.info("✅ ...")` escapes unencodable
      characters instead of raising, so the same emoji in a log line is harmless.
      That inconsistency is why the codebase is full of emoji log lines that work
      and made a `print()` look equally safe.

    Swept the rest: 5 more crashing `print()` lines existed (`main.py` ×4,
    `db/purge_results.py` ×1, all ✅/⚠️), now ASCII. **Rule: emoji are fine in
    `logger.*`, never in `print()`.** Third instance of the same cp1252 family —
    see the `webapp/*.html` PowerShell rule and `db/form_depth_health.py`.

30. **Non-soccer rows would have been graded as football (caught before
    shipping, 27 Sep 2026).** The NBA/NFL ledger PRs (#14, #20) made
    `results_espn.py` football-only — they had verified a Celtics v Spurs row
    matching an invented football result — but FOUR other readers of `matches`
    still took every row and fuzzy-matched it against a FOOTBALL feed:
      - `results_api.py`   grades by team name        -> bug #1's shape
      - `kickoff_times.py` writes ODDS + espn_league   -> bug #22's (odds are features)
      - `clubelo.py`       attaches an opinion row     -> a rating on the wrong match
      - `dedupe_fixtures`  merges and DELETES a row    -> an NFL fixture merged away
    And `run_daily.ledger_health` counted upcoming fixtures and recent results
    across ALL sports, so a weekly NFL slate would have kept it green on a day
    football produced nothing — bugs #5/#9 exactly. All five now carry
    `sport_sql(FOOTBALL)`. Verified on a ledger copy: 31 NFL fixtures + 16
    finals inserted, every football step run, NFL rows byte-identical after.
    **Fixing the one path that was demonstrated is not fixing the class. When a
    new kind of row enters a shared table, grep every reader of that table.**

31. **What ESPN's non-soccer data gets wrong, all probed (27 Sep 2026).** Each of
    these would have put fake or misleading rows into a ledger whose only job is
    verification. All live in `espn_ledger._parse_event`, shared by the ledger
    and the history download so the two cannot disagree.
    - **The final includes overtime and the shootout.** ESPN credits the NHL
      shootout winner +1 goal: NYI @ NSH "Final/SO" 2-1 was 1-1 after
      regulation — a DRAW on the regulation 1X2 bookmakers settle these sports
      on (item 10's trap). `results.decided_in` + `reg_*_goals` record both,
      derived from linescores and NULL unless they sum exactly to the final and
      an OT/SO game was level after regulation (6 NHL games since 2010 fail
      that — ESPN's period split is wrong for them). 23% of NHL games, ~9% MLB,
      ~6% NBA/NFL go past regulation.
    - **Preseason is in the feed** (14 NHL games on 26 Sep; NFL fills August):
      season.type 1 skipped.
    - **Exhibitions look like games.** competition.type is an ALLOW-list (STD CC
      RD16 QTR SEMI FINAL QUALIFY); unknown types are skipped AND logged. That
      caught the NHL 2020 bubble qualifiers (real) and MLB `EXH` (the placeholder
      for a rained-out playoff game — the real game has its own id) on first run.
      But the type alone lies: the 2023 NHL All-Star games are typed `SEMI`, like
      a conference final. So event notes are checked too (All-Star, Pro Bowl,
      Rising Stars, Face-Off), plus a history-only backstop: a team with <10
      games in a completed season.
    - **MLB home side does not bat in the 9th when ahead**, so its linescore is
      one inning SHORT — 174 of 376 July 2025 games. Requiring equal lengths
      would have blanked the regulation score on 46% of MLB. Allowed only in the
      9th and only when home leads. Rain-shortened games stay NULL.
    - **Fetching:** date RANGES are refused for these sports (400/empty).
      `dates=YYYYMM` works but is silently CAPPED AT 100 events without `limit`
      (Jan 2026 NBA stopped on the 14th) — a response AT the limit now fails
      (bug #10). Some whole months 504 server-side every time (NHL Sept 2010/13/14)
      and fall back to day-by-day.
    **An allow-list that logs what it rejects is how every one of these was
    found. A block-list would have let each through silently.**

32. **A queued workflow ran on a stale ledger (28 Sep 2026).** daily.yml,
    results.yml and closing.yml share the `daily-pipeline` concurrency group,
    so they never run at once - but `actions/checkout@v4` checks out
    `github.sha`, the commit at TRIGGER time. A run that waited in the queue
    started from the ledger as it was when it was queued, missed the commit
    the previous run had just pushed, and its binary-DB rebase conflicted.
    Seen when a results sweep was dispatched right behind a manual pipeline
    run. FIX: every ledger-writing checkout (and the guard, which reads
    today's commits) uses `ref: ${{ github.ref }}`, i.e. the branch head when
    the job STARTS. **A queue serialises runs; it does not refresh them.**

33. **A quarter of the graded record was predicted AFTER kickoff (28 Sep 2026).**
    predict.py selected fixtures by `match_date >= date('now')` only. A late
    kickoff (23:00 UTC) that one source dates "tomorrow" was predicted again the
    next morning - after the final whistle - and because every stats query
    counts the LATEST prediction per match (bug #3), that row replaced the real
    pre-match pick. Worse, `build_current_team_histories()` appends OUR recorded
    results to form, so a match whose result was already in `results` fed its
    own outcome into its own features. Measured: 341 of 1,439 graded football
    picks (83 served) counted a post-kickoff prediction; 226 were made with the
    result already recorded. It did NOT flatter the numbers (late served picks
    won 61% vs 68% overall) - it was still wrong: a record may only count a pick
    someone could have bet. Found while checking the claim "labelled before
    kickoff" before publishing it on record.html.
    FIX: predict.py refuses any fixture with kickoff_utc in the past or a result
    already recorded. `db/quarantine_late_predictions.py` MOVES rows predicted
    at/after kickoff (or after the result) to `model_predictions_after_kickoff`
    - kept, not deleted - so MAX(id) everywhere falls back to the latest
    pre-kickoff prediction without touching ~15 queries. Runs daily (pipeline +
    results sweep) because kickoff_times.py can correct a kickoff after a pick.
    Effect: served 310/457 = 67.8% -> 265/385 = 68.8%; all tiers 51.0% -> 50.4%;
    both tiers still within noise of their claims.
    **Before publishing a sentence about the data, check the data makes it
    true. "Latest per match" is only the definition of what we predicted if
    every row was a prediction.**

34. **PredictZ blocks GitHub's IPs - five red mornings in a row (found 4 Oct
    2026).** PredictZ wrote nothing after 29 Sep. Every 06:00 run failed its
    DEGRADED step and source_health ("predictz has written nothing in 2 days"),
    and the 'backup' GitHub cron did not help: its guard skips once today has
    run, red or not. Probed: Cloudflare-blocked from Actions at 06:00 AND 19:15
    UTC (so NOT the old time-of-day effect), while the same scraper got 36
    matches from Boss's connection the same evening. predictz.py had not
    changed since August - the block is theirs. FIX: PP_SKIP_PREDICTZ=1 in
    daily.yml (same treatment as Forebet), dropped from source_health's
    expected set when skipped, and ESPN fixtures run from +0 when it is.
    **What was actually lost, measured:** NOT today's ESPN games - all 26 of
    today/tomorrow's ESPN fixtures were already in the ledger, inserted at
    +2..+5. The loss is PredictZ's leagues that ESPN does not cover; football
    still produced 74-76 predictions (9-13 served) on 3-4 Oct from ESPN alone.
    Getting them back needs a non-datacenter IP (a scheduled local run feeding
    the ledger, or a paid proxy ~$50/mo). **DECIDED 4 Oct 2026, Boss: "leave
    it."** ESPN is the cloud's only football fixture source; do not re-propose
    a local run or proxy unless ESPN coverage itself becomes the problem.
    **Follow-up (6 Oct):** the mornings stayed red - source_health's "NO source
    has usable H/D/A percentages ... cannot price anything" counted pct columns
    only, and ESPN fixture rows carry ODDS with NULL pcts (319 priceable
    fixtures, 41 served picks, same morning). It now tests odds OR pcts for
    "can we price", and keeps the pct count for the consensus warning only.
    **Second follow-up (7 Oct) - the real cost of losing PredictZ:** ESPN spells
    clubs in full ("Manchester United", "Wolverhampton Wanderers") where the
    football-data.co.uk history abbreviates ("Man United", "Wolves"), and PredictZ
    had used the short forms. The 0.95 matcher correctly refused, so 120 PRICED
    fixtures in 10 days (EPL, all EFL divisions, 2. Bundesliga, Ligue 1, MLS...)
    were never predicted ("93 skipped - teams not in historical data") and served
    picks fell to 0-2 a day. FIX: 65 hand-checked ESPN->history pairs in
    TEAM_ALIASES, each target confirmed in team_history, no shared targets ->
    107 of the 120 became predictable. **When a source changes, re-check the
    name funnel: priced fixtures -> predicted -> served, not just "rows landed".** **A green 'backup' run is not a second attempt if its
    guard skips: check what the backup actually DID, not just its colour.**

35. **The site was slow on a WARM server - measured, then fixed at four layers
    (8 Oct 2026).** "Waking the prediction server" showed on a server the Worker
    pings every 10 min. Live: /api/stats 2.6 s, /fixtures 2.1-3.1 s, /slip 1.9 s;
    Render's free CPU is ~7x slower than a laptop, and a page's parallel calls
    queue behind each other until one passes the 5 s notice threshold.
    - **No indexes.** The only index was on market_lines, so every
      `MAX(id) ... WHERE match_id = ?` (the rule from bugs #3/#11/#20) scanned
      its table once per match. `db/database.py READ_INDEXES`: stats 437->45 ms,
      tiers 260->16, trend 253->19, picks 137->41, every response byte-identical.
      Created by init_db (daily pipeline) AND by the API's ledger sync on each
      downloaded copy, so speed never depends on which writer touched the file.
      Adding them changed one query's ORDER - `_predictions_with_goals` had no
      ORDER BY and relied on rowid scan order, which build_slip's tie-breaks
      depend on (item 9). Pinned to `ORDER BY m.id`; fixtures output verified
      identical to pre-index. **An index can change the order of a query with
      no ORDER BY. If order matters, say so in SQL.**
    - **No response cache.** `api/cache.py`: GET answers cached as encoded bytes,
      keyed by endpoint + args + LEDGER VERSION (file mtime+size; the sync swaps
      it atomically), plus a per-endpoint TTL for clock-dependent answers
      (picks 30 s, slip/fixtures 60 s, stats 10 min). Single-flight lock per
      key; ETag + 304; Cache-Control. Hits ~7 ms. A new ledger shows on the next
      request (tested). /healthz reports hit/miss counts. The sync now sends
      If-None-Match, so an unchanged ledger is a 304 and does NOT flush the cache.
    - **Static assets revalidated every view** (`max-age=0`, ~6 x 500 ms).
      `webapp/_headers`: js/css 1 h + stale-while-revalidate (NOT a year/
      immutable: a manual unstamped deploy would then serve stale api.js for a
      year - the "Couldn't book" bug). Preconnect to the API and gstatic.
    - **The icon font was 1,137,972 bytes.** Subset via `&icon_names=` to ~70 KB,
      same axes, zero visual change (checked: every icon on 6 pages renders as a
      glyph). The list is DERIVED by `tools/icon_subset.py` (source tokens ∩
      official names), re-run on every deploy. **Never build an icon name from
      pieces** - the scanner only sees whole names, same as the Tailwind rule.

36. **Security pass on a public, unauthenticated API (8 Oct 2026).** Clean
    already: SQL is fully parameterised, every page escapes via the shared
    escapeHtml, Supabase RLS limits each user to their own rows and
    delete_my_account pins search_path, no secrets in the repo. Fixed:
    - **`?api=` hijack (the serious one).** Any link with `?api=<url>` was saved
      to localStorage and pointed that visitor's app - picks, records, booking
      codes - at a stranger's server until they cleared site data. Now honoured
      on localhost only, and a saved value is wiped on the live site.
    - **Booking codes went into the bookmaker's URL path checked only for
      length** - `../` could steer our server to other endpoints on that host.
      Now `[A-Z0-9]{3,N}` only.
    - `api/security.py`: 256 KB body cap (411 without Content-Length), list
      caps (40 legs, 10 codes), per-IP token buckets - booking 20/min (each
      calls a bookmaker FROM OUR IP; see the API-Football shared-IP
      suspension), slip 60/min, settle/odds/check 60/min. IP from
      CF-Connecting-IP, else the LAST X-Forwarded-For entry (earlier ones are
      client-claimed).
    - CORS: our Pages origin (+ preview subdomains) and localhost, not `*`.
    - Worker webhook check fails CLOSED when WEBHOOK_SECRET is unset.
    - Pages: X-Frame-Options DENY / frame-ancestors, nosniff, Referrer-Policy,
      Permissions-Policy. **No full CSP yet** - inline scripts/handlers need
      nonces first or a CSP breaks the site. That is the next step.

## Decisions & their reasoning
- **Pipeline steps have a SEVERITY, and DEGRADED is the default (Aug 2026).**
  `run_daily.py` used to have one flag, `required`, and only "Generate
  predictions" set it — so every other failure exited 0, GitHub went green,
  and the log printed "Pipeline complete". The deduper crashed on six
  consecutive runs, printing a traceback each time, and every one of those
  runs is green in the Actions history.
    - `CRITICAL` — aborts. Only "Generate predictions", plus the group gate
      "every fixture source failed".
    - `DEGRADED` — continues, run ends RED and alerts. **The default.** A step
      that fails is a bug until proven otherwise.
    - `OPTIONAL` — logged, never red. Needs a written justification at the
      call site. Only 5 steps qualify: Forebet fixtures/results (IP-blocked by
      design, redundant), CSV refresh (source lags anyway), analysis backfill
      (prose, not prediction), consensus report (only prints).
  **Do not add a step as OPTIONAL to quiet a failure — fix the step.** The bar
  is "there is a real fallback", not "it fails a lot". Group gates exist for
  "any one is fine, none is fatal", which per-step severity cannot express.
  Dedupe is DEGRADED not CRITICAL deliberately: its failure corrupts the
  ledger but RECOVERABLY (the next run merged 115 duplicate rows), and
  aborting would cost a day of picks to fix what the next run repairs.
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
- **Combo markets (Home & Over, Draw & GG…): DERIVED, NOT BOOKABLE, NOT
  GRADED — add them LATER, and grade before booking (probed 11 Aug 2026).**
  markets.py already builds "Result & Goals" combos, and SportyBet does offer
  them — probed live: **35** = 1X2 & GG/NG ("Home & yes"), **36** = O/U &
  GG/NG, **37** = 1X2 & Over/Under ("Home & Over 2.5"), **547** = Double
  Chance & O/U, plus **19** = team totals and **26** = Odd/Even. None are in
  `GEN_MARKET_IDS` (1,10,11,16,18,29), so a combo leg comes back "unmapped".
  Adding the ids is an hour's work — and that is the trap. The ledger has only
  ever stored `best_bet` values 'Home Win' and 'Away Win': **1X2 is the only
  thing we have ever graded**, never even a Draw. So every non-1X2 market in
  the app is already a claim with no track record, and adding four more
  families multiplies the claims without adding a single unit of evidence.
  **Grade what we already show BEFORE making more of it bookable.** The order
  is: scoring logic per market type -> weeks of ledger -> then the booking ids.
  Reversed, a month of ungraded combo picks accumulates and proves nothing.
- **Slip-checker = user picks from existing fixtures** (reliable, no parsing). Free-text
  parsing is a later enhancement. Bookie-code GENERATION is still Phase 4, but code
  READING is built (July 2026): scrapers/booking_codes.py decodes SportyBet share
  codes via their unofficial endpoint (www.sportybet.com/api/ng/orders/share/<CODE>,
  bizCode 10000=ok / 19000=invalid); POST /api/booking/decode attaches our model's
  verdict per leg; paste box on checker.html. Valid-payload parsing is defensive and
  dumps unknown shapes to data/raw/ — needs a REAL code to validate the parse.
  MSport is stubbed pending a captured payload. Unofficial API = ToS grey area;
  fine for personal phase, reassess before commercial.
  **Bet9ja READ + GENERATE — both DONE (9 Aug 2026).** Bet9ja runs its own
  palimpsest of event/market/sign ids, NOT Betradar, so none of the
  Sporty/MSport/football.com mapping applies. `generate_bet9ja()` in
  scrapers/booking_codes.py; `generate_code(legs, "bet9ja")` routes to it.

  How the contract was obtained — this is the reusable part. The plan on file
  was "capture a real BookABetV2 request with devtools". That route FAILED:
  the browser tool returns URL/method/status but NOT request bodies. What
  worked was reading their own JS bundle
  (cnt.bet9ja.com/cdn/bet9ja/sportsbook/js/desktop/bundle_<ver>.min.js) and
  grepping for the endpoint name. **A minified bundle beats a capture — a
  capture gives one example, the source gives the field names.**

      cw = t => post("/PlacebetAjax/BookABetV2",
                     form({BETSLIP: JSON.stringify(t.BETSLIP), IS_PASSBET: 0}))
      Df = (BSTYPE, TAB, NUMLINES, COMB, TYPE, STAKE, POTWINMIN, POTWINMAX,
            BONUSMIN, BONUSMAX, ODDMIN, ODDMAX, ODDS, FIXED) => ({...})
      Tf = betslip => ({BETS:[Df(...)], EVS:{...}, IMPERSONIZE:0})

  Form-encoded POST, `BETSLIP` is an **OBJECT** (their code type-checks it),
  not the array every guess assumed — which is why blind probing only ever
  returned an opaque ERROR_CODE 100. **The shape was wrong before the contents
  could matter; an opaque error usually means the envelope, not the payload.**

  Hard-won specifics:
  - **The shareable code is `RIS`.** `COUPONCODE` is a UUID and useless to a
    punter; the bundle prefers RIS and only falls back to COUPONCODE.
  - **BSTYPE 3 = single, BSTYPE 1 = accumulator**, mutually exclusive — each
    returns ERROR_CODE 103 in the other's place. Values 4-7 are ACCEPTED and
    return STATUS 100 with NO code: a silent success. Always assert a code
    came back.
  - A fixture within ~10 min of kickoff returns STATUS 1xx and no code.

  Events feed (the bit CLAUDE.md previously called uncracked): GetSports needs
  `DISP` (1); GetEventsInGroup needs `DISP` + `GROUPID`. GetEventsInDailyBundleV3
  still errors 319 — don't bother, GetEventsInGroup is the working path. Their
  errors name the missing parameter in ERROR_DATA.parameterName, which is how
  the contract was walked out one field at a time.

  **decode_bet9ja is FIXED** — it had never seen a real code and probed for an
  ODDS/EVENTS list that does not exist. Legs live under `D.O`, keyed
  `"<EVENT_ID>$<MARKET>_<SIGN>"`, the same key shape the booking payload uses.

  **Market coverage, surveyed live across 24 events (not assumed): Bet9ja has
  NO Draw No Bet and NO Handicap.** It offers 1X2, Double Chance, GG/NG,
  Over/Under, Odd/Even and half-time variants. Since our slips are DC/DNB/
  Handicap-heavy, a typical slip only PARTIALLY maps. Unmappable legs are
  returned as `unmapped` and never bent onto a neighbouring market — putting
  money on a bet nobody chose is worse than admitting we could not place it.

  Verified end to end: 4-leg mixed slip booked as 5MKSVML and decoded back to
  the same four legs at the same odds; a manually-booked code (5MKRXK8) also
  decodes. Everything is anonymous coupon creation — no account, no stake, no
  wager placed, same as the SportyBet integration.

  **1xbet: still blocked.** .com is behind Cloudflare ("Just a moment");
  .ng's /service-api/LineFeed/ exists but returns HTTP 406 NotAcceptable with
  "Contact the developer". Needs a different approach, not more probing.
- **Build engine/API before frontend** so screens map to real endpoints.
- **Consensus** should eventually be a badge on Picks + a confidence booster in the
  engine, not just a standalone screen. Standalone page kept for the transparency story.

- **The analysis has a CLOUD fallback now (11 Aug 2026).** Ollama was the only
  fallback and it runs on a laptop — useless to a pipeline executing in GitHub
  Actions, so a Gemini outage meant no analysis at all with nothing to take
  over. Added Groq (free tier, OPEN-WEIGHT Llama, OpenAI-compatible API), and
  the chain FAILS OVER at call time rather than only at startup:
  `Gemini -> Groq -> Ollama`. A provider consulted only when the primary's KEY
  is missing is no use during an outage, which is the only case that matters.
  Chosen for being open-weights and OpenAI-shaped: if Groq disappears, the same
  code points at any compatible host by changing `GROQ_BASE`. Safe to swap
  freely — analysis is EXPLANATION ONLY and can never move a probability.
  Secret: `GROQ_API_KEY`.

- **Everything is now hands-off — what runs itself, and when.**
  | What | Trigger |
  |---|---|
  | Pipeline (fixtures -> predictions -> Telegram -> bot feed -> ledger commit) | `daily.yml`, dispatched 06:00 UTC by the Worker cron; GitHub cron 08:50 UTC as backup (guard job skips it once today ran) |
  | Worker (bot + the cron above) | `deploy-worker.yml`, on any push touching `worker/` |
  | API keep-warm (Render free tier sleeps after ~15 min) | Worker cron `*/10 * * * *` pings /healthz |
  | Evening results sweep | `results.yml`, cron 22:00 UTC |
  | Closing prices (CLV) + NHL/NFL/MLB lines | `closing.yml`, dispatched at :20 every hour by the Worker cron (since 2 Oct 2026 - GitHub's own hourly cron ran every 3-6 h in practice and stays as a backup) (~1-3 min/run) |
  | API redeploy | Render, on every push to main |
  | Web app deploy + cache-bust | `deploy-webapp.yml`, on any push touching `webapp/` |
  The only human step left is a one-time pair of Cloudflare secrets; until they
  exist the deploy workflow skips with a notice rather than failing.

## What's next (priority order)

0. **Saudi Pro League — history DONE 24 Aug 2026, fixtures still BLOCKED.**
   Asked 23 Aug, investigated, and the investigation was wrong twice before it
   was right. Read the corrections below before touching this.

   **What now exists** (shipped 24 Aug 2026):
   - `scrapers/espn_history.py` — a reusable downloader for ANY ESPN league slug
     into a football-data.co.uk-shaped CSV. ESPN's scoreboard accepts a date
     RANGE (`?dates=20250301-20250331`), so a month costs one request and four
     seasons cost ~40. No API key, and ESPN is the one source proven unblocked
     in Actions.
   - `data/historical/SaudiProLeague.csv` — 1,179 finished matches, 28 clubs.
     **ESPN has no Saudi data before 2022/23** regardless of `--start`, so this
     is four seasons, not eight. Per-league coverage is not uniform; check the
     per-season counts the script prints rather than trusting `--start`.
   - `"ksa.1"` in `espn_leagues.EXTRA`, so Saudi games get kickoff times, live
     state and — the point — **grading**. (`ksa.2` returns HTTP 400.)
   - Retrained: **28/28 Saudi clubs now match** `match_team_name`, most with
     130+ games (`team_history` 1604 -> 1632 teams). Overall accuracy 51.2%,
     tier calibration unmoved (65% -> 74.3%, 55% -> 65.2%), so nothing was
     traded away for this.

   **What is still missing, and it is the part that matters: ODDS.** A league
   needs an upcoming fixture WITH a price to produce a pick.

   **PredictZ already delivers Saudi FIXTURES** — under the league name
   **"Professional League"**. This entry previously said "PredictZ does not
   cover Saudi (142 leagues, none)" and that '"Pro League" in the ledger is
   Iran/UAE'. Both were wrong, and the reason they were wrong is worth knowing:
   the ledger contains *three* near-identical league names —

       "Professional League"  -> SAUDI      (Al Nassr, Al Ettifaq, Neom SC)
       "Pro League"           -> UAE        (Al Wasl, Al Ittihad Kalba)
       "Stars League"         -> QATAR      (Al Gharrafa, Al Arabi)

   — so a name-based check for Saudi found "nothing" while Saudi fixtures were
   arriving daily. Never conclude a league is absent from a league-name search
   alone; check the club names.

   What those rows do NOT have is **any price or any percentage**: every Saudi
   `scraped_predictions` row has `odds_home IS NULL` *and*
   `home_win_pct IS NULL` (only `predicted_*_goals`). So `build_live_features`
   returns None and `predict.py:381` skips them — correctly, by bug #7. Saudi is
   now *predictable* (form solved, permanently) but not yet *served*, and the
   single remaining blocker is an odds source that covers it:
   - `scrapers/fixtures_api.py` (api-sports.io v3) is the only live fixture+odds
     route — and **that account is SUSPENDED.** `{'access': 'Your account is
     suspended, check on https://dashboard.api-football.com.'}`, returned even
     by `/status`. Its last ledger row is **9 Aug 2026**. This is a live
     production regression that predates the Saudi work and is not caused by it;
     it needs Boss to log in. Combined with Forebet's IP block (bug #26) the
     pipeline is down from four sources to two: `predictz` and `espn`.
   - Forebet covers Saudi and is Cloudflare-403'd from datacenter IPs.

   **Saudi club spellings differ between PredictZ and ESPN, and the difference
   lands in the near-miss band** (0.85-0.95), so those fixtures would be skipped
   as unmatched even with odds. `predict.py` already reports them for curation
   into `TEAM_ALIASES`; measured 24 Aug 2026:

       "Al Taawon": "Al Taawoun",        # 0.95
       "Al Hazm": "Al Hazem",            # 0.93
       "Al Shabab Riyadh": "Al Shabab",  # 0.92
       "Al Qadisiyah": "Al Qadsiah",     # 0.91
       "Al Faiha": "Al Fayha",           # 0.88

   plus `Ittihad Jeddah`, `Abha Club`, `Al Faisaly`, `Diriyah Club`. These are
   deliberately NOT added yet: while the odds are missing every one of these
   fixtures is skipped anyway, so adding them changes nothing observable and
   would be a guess nobody could check. Add them in the same change that fixes
   the odds, and confirm against real served picks.

   **One EXACT-name collision was created by adding this CSV, and it is
   measured, not hypothetical.** All 28 Saudi club names were new to
   `team_history` (1604 -> 1632 teams), and one of them, `Al Ittihad`, is also
   an Egyptian club: `Zamalek v Al Ittihad [Premier League]`. `match_team_name`
   step 2 is an exact-string match, which **bypasses the NAME_MATCH_MIN=0.95
   similarity guard entirely** — so that fixture would silently be predicted on
   the SAUDI club's form. Scope right now: exactly **1 fixture in the whole
   ledger**, and it has no odds, so it has never been predicted and cannot be.
   That is luck, not a defence. The real fix is league-aware matching (the
   matcher is global by name and knows nothing about country), which is its own
   piece of work. Until then, expect this class of collision to grow with every
   Gulf/North-African league added — `Al Ahli`, `Al Hilal`, `Al Shabab` and
   `Al Wehda` all exist in several countries.

   **Three claims that were in this entry before 24 Aug 2026 were wrong.** All
   are corrected here, because acting on any of them would have wasted a day.

   *Wrong claim 1: "ESPN is the viable route — adding `"ksa.1"` to `EXTRA` is a
   one-line change."* `espn_leagues.ALL_LEAGUES` is imported in exactly two
   places — `kickoff_times.py` and `results_espn.py` — and **neither inserts a
   fixture**. `kickoff_times.py` only ever `UPDATE`s rows already in `matches`;
   the sole `INSERT ... INTO matches` in the repo is `db/database.py:192`. ESPN
   is an *enrichment, history and grading* source, not a fixture source.

   *Wrong claim 2: "Saudi picks would be built on nine features asserting both
   teams are the worst in football."* They would not — `predict.py:370`
   **skips** a match when either team fails to match history. Before the CSV,
   adding Saudi fixtures yielded **zero** Saudi picks: pointless, not dangerous.

   *Wrong claim 3: "a Saudi history source with no odds re-creates the exact gap
   rf_v2 just closed."* It does not. `train.py:extract_odds` already returns the
   flat prior with `odds_is_market=0.0` for odds-less rows — rf_v2 added that
   flag precisely so the forest can discount them — and
   `InternationalResults.csv` (4,589 rows, no odds columns at all) has been in
   the training set all along. At serve time bug #7 refuses to predict without
   odds, so a flat-prior row can never reach a user. Odds-less history is a
   shape the pipeline already accepts, and demanding odds would have blocked the
   whole thing for no reason.

   **CAUSE FOUND 28 Sep 2026 (API-Football support):** shared-IP anti-abuse.
   GitHub Actions runners share Azure outbound IPs with thousands of users; a
   PAID PLAN DOES NOT EXEMPT YOU - only a DEDICATED outbound IP does. The
   laptop is no fix either (Nigerian ISPs are mostly carrier-grade NAT). The
   step is now OFF in the cloud (`PP_SKIP_APIFOOTBALL=1`, daily.yml) so a
   reinstated account isn't instantly re-suspended. Path back: a ~$4-6/mo VPS
   with its own IPv4 running fixtures_api.py, THEN ask support to reinstate.
   Deferred by Boss's call until testers are on the site.
   Remaining order of work: (a) unsuspend the API-Football account (needs the
   dedicated-IP server first - see above); (b) resolve
   the Saudi league id via `fixtures_api.py --discover` and add it to
   `LEAGUE_IDS`; (c) add the `TEAM_ALIASES` entries above *in that same change*,
   and verify against real served picks; (d) bug #27's `has_form_history`
   feature, so the forest can tell "no data" from "dreadful" — note this is NOT
   a blocker for Saudi any more, since Saudi clubs now have real depth rather
   than one game.

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
4. **REFINE REVIEW — 6 Oct 2026 (Boss: "look at the records and refine"). Result:
   NOTHING CHANGED, on evidence.** 395 served graded: HIGH 77.5% (claim 75.0),
   MEDIUM 61.0% (60.6); ROI -0.2% [-7.8, +7.5] at pick-time prices; CLV -0.57
   [-1.39, +0.25]. `db/refine_report.py` (new) found one stable lead: price
   1.50-1.80 below claim in both halves, significant in neither. MEDIUM ROI was
   +6.0% then -15.9% by half - noise, do NOT drop MEDIUM on it. A model+bookmaker
   blend (weights book 0.90 / model 0.32) did not beat the model out of sample
   (-0.0042, CI [-0.0101, +0.0020]) - the market carries almost all the
   information, so a blend would just copy it. Thin-history MEDIUM (bug #27) was
   re-checked on post-25-Aug picks only: 5/9 - too few to replicate yet.
   Re-run refine_report.py when the served record has doubled (~800).

   **CALIBRATION — LATEST: 27 Sep 2026, 452 graded HIGH+MEDIUM picks, via
   `python db/calibration_health.py`. The tiers HOLD their claims:**
     HIGH   166/219  75.8%  claim 74.6%  CI [69.7, 81.0]
     MEDIUM 141/233  60.5%  claim 60.7%  CI [54.1, 66.6]
   Every band within noise; sign test 2/4 above claim (p = 1.0), i.e. the
   11 Aug "systematically under-confident" lead did NOT survive the larger
   sample. Grading coverage 94.4% (the coverage caveat below is now much
   weaker). Eras: **rf_v2 160/224 = 71.4%** [65.2, 76.9] — the best era, and
   the first ledger evidence for bug #18's fix; rf_v1e 65.9%. Re-run the script
   rather than editing these numbers by hand. Everything below is history.

   **HIGH-tier inversion: RESOLVED ITSELF as the sample grew (re-measured
   9 Aug 2026 — do not act on the old numbers below).** The inversion that was
   top priority (HIGH 5/13 = 38% < MEDIUM 15/20 = 75%) was a 13-sample artefact.
   On 184 graded 1X2 picks the tiers now rank in the right order:
     HIGH 17/25 (68.0%) > MEDIUM 26/44 (59.1%) > LOW 65/115 (56.5%)
   And rf_v1e is the best era by a distance: 41/62 (66.1%) vs rf_v1d 54.2%,
   rf_v1c 44.4%, rf_v1b 54.5%, rf_v1 60.0% — so disabling the consensus boost
   looks vindicated.

   **RE-MEASURED 11 Aug 2026 at 265 graded picks — and the answer FLIPPED.
   Do not act on the 9 Aug numbers above; they are kept only to show the
   direction of travel.** The sample passed the ~30-HIGH bar this file set as
   the precondition (HIGH now has 39 graded), so the question was finally
   answerable. Current tiers: HIGH 30/39 (76.9%) > MEDIUM 40/65 (61.5%) >
   LOW 85/161 (52.8%).

     band     n   actual   claimed   95% CI          verdict
     35-40%  38   47.4%    37.8%     [31.5, 63.2]    within noise
     40-45%  48   58.3%    42.6%     [44.4, 72.3]    MISCALIBRATED
     45-50%  39   51.3%    48.0%     [35.6, 67.0]    within noise
     50-55%  36   52.8%    52.6%     [36.5, 69.1]    within noise
     55-60%  40   67.5%    57.7%     [53.0, 82.0]    within noise
     60-65%  30   50.0%    62.0%     [32.1, 67.9]    within noise
     65-70%  12   75.0%    66.6%     [50.5, 99.5]    within noise
     >=70%   22   86.4%    77.3%     [72.0, 100.0]   within noise

   **DO NOT raise the HIGH threshold to 70%.** That was the standing
   recommendation and it is now unsupported: the 65-70% band went from 57.1%
   (n=7) to 75.0% (n=12) as the sample grew, and HIGH overall is 28/34 (82.4%)
   on the probability cut. Raising the bar would throw away a band that is
   doing its job. The 60-65% dip that looked like "the worst band" is still
   50% but its CI [32.1, 67.9] comfortably contains the claimed 62% — at n=30
   it is not distinguishable from noise. **Two consecutive re-measurements have
   now reversed the conclusion drawn from thin data. The rule earned its
   keep.**

   **The real signal is one nobody was looking for: the model is
   SYSTEMATICALLY UNDER-confident.** 7 of the 8 bands come in ABOVE what the
   model claimed (sign test p ~ 0.035 — individually each band is noise, but
   the one-directional pattern is not). Only 40-45% is significant alone, and
   it is under-confident by +15.7 points. That band sits in LOW, i.e. **picks
   we hide as bets may be winning more often than we admit.** The suspicion
   for two weeks was over-confidence at the top; the evidence points the other
   way, at the bottom.

   **BUT — and this is why nothing was changed — that pattern is exactly what
   a COVERAGE BIAS looks like.** Grading coverage is 84.4%; bug #8 established
   that the picks we fail to grade are NOT a random sample (when that backfill
   landed, HIGH fell 57% -> 38% because the missing rows were mostly losses).
   If the ungraded 15.6% still skews to losses, every "actual" above is
   inflated and the apparent under-confidence is an artefact of the
   denominator, not a property of the model. Recalibrating on it would bake a
   measurement error into the engine. **Distinguish these BEFORE touching any
   threshold: get coverage materially above 84%, or show the ungraded picks
   resemble the graded ones. Until then this is a lead, not a finding.**

   Era check (rf_v1e is current): rf_v1e 82/135 = 60.7% [52.5, 69.0];
   rf_v1d 41/76 = 53.9%; rf_v1 21/33 = 63.6% (thin). rf_v1e remains the best
   era with a real sample, so disabling the consensus boost still looks right.

   **Rule stands, and has now been vindicated twice: don't hard-change
   calibration on thin samples.**
   `db/track_record_health.py` wants ~30+ HIGH picks; we have 25. Re-run this
   bucket analysis at 30+ before touching the threshold.
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
8. **User accounts + per-user records — BUILT 28 Sep 2026 (PR #36).** Supabase
   project `vtltrjsjhfdhlrqrswvt` (Boss's account). Google sign-in ONLY: Supabase's
   built-in email delivers only to the project's own team members (2/hour), so
   magic links would have failed for every tester. `webapp/js/auth.js` (every
   page; publishable key is public by design) + `webapp/account.html` (My
   Record: tracked picks graded via /api/results/lookup from the SAME results as
   the public record, optional naira stakes + P/L, model record beside it, delete
   account). Schema + row-level security + `delete_my_account()`:
   `db/supabase/001_tracked_picks.sql`. GOTCHA: new Supabase projects do not
   auto-grant tables to the API roles — without `grant ... to authenticated`
   even signed-in users get "permission denied". Legal pages required by
   Google's consent screen: `webapp/privacy.html`, `webapp/terms.html`
   (contact potentialproteam@gmail.com). NEVER put user data in
   db/sports_predictor.db.
   **Accumulators (28 Sep 2026):** `tracked_slips` (db/supabase/002_tracked_slips.sql,
   run once in the SQL editor) stores a Builder/Checker slip as ONE row with its
   legs as JSON. Graded as one bet - any lost leg loses it, Draw No Bet on a draw
   is VOID at 1.00 - never as "4 of 5 legs won". models/settle.py settles every
   market derive_markets() emits (asserted: all 22) and returns None, never a
   guess, for anything else. P/L counts a slip only if EVERY leg carried a price,
   win or lose, so unpriced wins can't be dropped while unpriced losses count.
   Signed out, "Track this slip" parks the slip in sessionStorage and
   account.html saves it right after Google sign-in. The design notes below still hold.

   **Original design notes (Aug 2026):** DESIGNED, DEFERRED.
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

10. **Multi-sport — STATUS 27 Sep 2026: the LEDGER and the HISTORY exist for
    NFL, NBA, NHL and MLB; no model, odds, markets or picks for any of them.**
    **UPDATE 29 Sep 2026 (NHL, Boss's call: "private preview, NHL only"):** NHL
    now has priced markets (moneyline, 60-min 1X2, puck line, totals), facts-only
    AI analysis and a verdict per game on webapp/hockey.html.
    **PUBLIC since 2 Oct 2026** (Boss: "I can't be waiting for 3 weeks"; chose
    "analysis + labelled alerts"). What made that honest rather than premature:
    models/nhl_market_backtest.py answered the edge question on 5,000 past games
    instead of waiting for live ones — NO proven edge — so the page is framed as
    ANALYSIS, not tips: an "Experimental · separate from our football record"
    tag, a disclosure that states the backtest, and leans only on 10+ point
    moneyline disagreements, labelled experimental. The NHL record is reported
    on that page only, never blended into football's (docs/basketball.md §5.6).
    Do NOT upgrade the leans to tips unless the live ledger (db/shadow_report.py)
    and a re-run backtest both support it.
    Starting goalies are the biggest known gap (the book has them, we don't);
    api-web.nhle.com is the candidate source. NFL/MLB get spread + total lines
    recorded but no markets or analysis yet.
    | League | Ledger live from | History (data/sports_history) |
    |---|---|---|
    | NFL | now (mid-season) | 4,396 games, 2010- |
    | MLB | now (postseason next) | 38,799 games, 2010- |
    | NHL | 29 Sep 2026 (season opener) | 20,579 games, 2010-11- |
    | NBA | 22 Oct 2026 | 20,497 games, 2010-11- |
    **Elo baseline BUILT (27 Sep 2026, `models/elo.py`)** — held-out seasons only:
    | League | K | HFA | revert | held-out | Elo acc | log loss | home-always |
    |---|---|---|---|---|---|---|---|
    | NBA | 18 | 60 | 0.33 | 2024-25, 2025-26 (2,643 g) | 66.9% | 0.608 | 55.0% / 0.689 |
    | NFL | 20 | 50 | 0.50 | 2024, 2025 (570 g) | 67.0% | 0.623 | 53.1% / 0.692 |
    | NHL | 6 | 25 | 0.25 | 2024-25, 2025-26 (2,792 g) | 56.2% | 0.679 | 54.1% / 0.690 |
    | MLB | 4 | 20 | 0.33 | 2025, 2026* (4,888 g) | 55.7% | 0.682 | 53.6% / 0.691 |
    (*MLB 2026 was 2,411 of ~2,430 regular-season games in.) Every calibration
    band within a few points of its claim. NBA/NFL ~67% matches published
    538-style Elo; NHL/MLB ~56% is normal — single games there are close to a
    coin flip. NOT compared with the bookmaker: the history has no odds.
    Next, per sport and in this order (docs/basketball.md §5 generalised):
    (1) in season, count how many upcoming games ESPN PRICES — no price, no pick
    (bug #7); (2) a sport-specific trainer on data/sports_history with 2-way
    features (no draw), NOT train.py; (3) grade on the ledger for weeks before
    serving; (4) only then SportyBet market ids — and use the FULL-GAME ids, not
    the regulation ones (the trap below; `results.decided_in` exists for it).

    **Reference repos reviewed 27 Sep 2026** (Boss supplied them), then the
    CODE of the promising ones audited against docs/basketball.md §7 — the
    READMEs overstated several. Licence decides what we may take; no licence
    means ideas only:
    - `gschwaeb/NHL_Game_Prediction` — **CC0. The soundest method of the lot.**
      Rolling features all `.rolling(n).sum().shift()` (point-in-time), trains
      2017-20 and tests the unseen 2021 season, scores LOG LOSS against a
      home-win baseline AND against the bookmaker's implied odds — and admits
      the market won (0.6529 vs 0.6555). Profit test bets vs the closing line
      incl. vig (a hard bar); +2.04% ROI on one season is noise, not claimed
      as more. One leak: a game's goalie is whoever played >=28.5 min OF THAT
      GAME, so a pulled starter is replaced by the reliever — outcome leaks
      in. Use the announced starter instead. Features: goalie GSAx/HD SV%,
      5v5 xGF%, Elo, back-to-back. 2021, unmaintained.
    - `skrt0215/gridiron-prophet` — MIT. **Downgraded on audit.** The base RF is
      clean (pre-game cumulative stats, chronological split) but weak. The
      served "predicted spread" is HAND-WEIGHTED — `(p-0.5)*20 + record*8 +
      history*5 + points*0.3 + injuries*0.3 + 2.5`, never fitted — so its
      "3+ point edge vs DraftKings" is uncalibrated. Its weekly "spread
      accuracy" compares abs(predicted margin) to abs(actual margin), so it is
      direction-blind. Worth taking: `roi_tracker.py`'s ATS grading is
      correct (cover margin, pushes, -110), and picks are logged pre-game.
    - `gmalbert/hockey-predictions` — no licence. Training features are
      point-in-time (`compute_team_stats_up_to`, strict `< date`) with
      isotonic calibration and log loss/Brier — good. But the test split is
      RANDOM, and train/serve differ: PP%/PK% are "simplified" estimates from
      goals in training but real NHL-API figures at serve time (bug #7's
      shape). Ideas: rest advantage; ESPN as an NHL odds source (verify).
    - `gmalbert/baseball-predictions` — no licence. **Two codebases in one.**
      `src/features/asof.py` + `src/evaluation/walk_forward.py` +
      `src/models/training.py` are the best evaluation framework seen:
      as-of joins that RAISE on a future observation, purged chronological
      train/calibration/test folds with an embargo day, metrics against a
      constant AND the market's own probability, hashed model manifests. But
      the models the site serves bypass it: `src/models/features.py` joins
      "same-season ... full-season aggregates" (its own docstring) — WPct,
      RS/G, starter ERA for the whole season, including the game itself. Its
      published results are inflated by exactly §7 rule 1. Copy the
      framework's IDEAS, not its model numbers. `clv.py` defines CLV as
      open-vs-close line movement.
    - `nealmick/Sports-Betting-ML-Tools-NBA` — GPL-2.0 file in a subfolder,
      none at root. Methodology inflated by end-of-season averages and
      box-score lineups; full write-up is docs/basketball.md §7.
    - `whisdev/NBA-prediction-sports-betting` — no licence; same "~69%" pitch as
      kyleskom's repo below. 69% is roughly what always backing the NBA
      favourite scores, so it demonstrates nothing. Skip.
    - `abzdel/NBA_Over_Under_Prediction_App` — CC0 but a plain linear regression
      its own author says not to bet on. Skip.
    - `whisdev` re-checked in code: NO training code at all — a TypeScript port
      of kyleskom's live pipeline loading kyleskom's pre-trained models
      (`XGBoost_68.9%_ML...`, and a totals model literally named `50.1%_UO`).

    **NBA search, 27 Sep 2026** (141 repos across eight GitHub queries, five
    audited in code). Summary in docs/basketball.md §8:
    - `nicidob/nba_elo` — **MIT, the one to use.** Reimplements FiveThirtyEight's
      NBA Elo (K=20, home advantage 100, margin-of-victory multiplier, season
      carry-over) — the standard public benchmark — plus a logistic-regression
      version. Build it on data/sports_history/nba.csv as the NBA BASELINE.
    - `luke-lite/NBA-Prediction-Modeling` — no licence, but the key evidence:
      plain Elo 65.3% beat every ML model (best 63.5%) on 12k games.
    - `cmunch1/nba-prediction` (310 stars) — no licence. Sound: rolling
      features `closed="left"`, latest season held out, log loss + calibration,
      honest result 61.5% vs home 58% vs a public model's 65.6%.
    - `atticusdrex/nba-moneyline` — MIT. Point-in-time features
      (`expanding().shift(1)`) but a RANDOM train/test split.
    - `yirgareminick/NBA-Betting-Model` — MIT, active, but LEAKS:
      `season_win_pct` etc. are `shift(1).mean().over(team, season)` — a
      WHOLE-SEASON mean, commented "excluding current game" — and are model
      inputs. Do not trust its numbers.
    **Ideas worth building, from the audit:** (a) CLOSING-LINE VALUE — snapshot
    the price again near kickoff and compare with `book_prob` at pick time.
    CLV needs no result, so it tests "do we beat the market?" far faster and
    with less noise than waiting for graded outcomes; it applies to FOOTBALL
    today. (b) Report model log loss against the market's log loss on the
    same picks (gschwaeb's test). (c) As-of assertions in any new feature
    builder (baseball's `point_in_time_join`), per §7 rule 1.
    Features we can already build from data/sports_history: rest days and
    back-to-backs (dates), home advantage (neutral_site), Elo from results.
    Goalie / starting pitcher need a new source (api-web.nhle.com,
    statsapi.mlb.com — free, unprobed from the cloud).

    **Basketball groundwork is DONE and written up — see `docs/basketball.md`
    (11 Aug 2026).** Research only; nothing is wired into the pipeline. Probed
    live, so it is fact not plan: ESPN's NBA scoreboard has the IDENTICAL
    payload shape we already parse (so kickoff_times/results_espn/live_scores
    port with a slug change) and carries completed scores back to 2010 (~18k
    games, free training history); SportyBet basketball works off the same
    endpoint and Betradar ids with `sportId=sr:sport:2`; `stats.nba.com` — the
    source the popular reference repo depends on — TIMES OUT even from Boss's
    own IP. The reference repo (kyleskom) has **no license**, so its code
    cannot be reused commercially; take the ideas (days-rest feature, a
    separate totals model), not the code.
    **THE TRAP, confirmed by probe:** SportyBet basketball reuses market ids
    1 / 11 / 18, but for basketball those are **REGULATION ONLY** — full-game
    markets are 219 (Winner incl. OT), 223 (Handicap), 225 (O/U). The tell is
    that basketball market 1 has a DRAW outcome, which is impossible unless
    the clock stops at the 4th quarter. Reusing the football ids would book
    real codes on regulation markets and grade them against final scores that
    include overtime — bug #1's shape, silent, and only wrong on OT games.
    Load-bearing unknown, answerable only in season (NBA starts late Oct 2026):
    does ESPN carry NBA ODDS? If not, predict.py rightly refuses to invent a
    prior and would generate zero picks. Check that FIRST.

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
  PredictZ = WORKS (166 matches — Firefox stealth survives datacenter IPs)
  **— NO LONGER: blocked from Actions since ~30 Sep 2026, see bug #34;**
  football-data.org API = WORKS; ESPN API = WORKS. So the cloud can run
  everything EXCEPT Forebet fixture scraping. Re-probe with the
  "Probe scrapers" workflow before assuming this still holds.
