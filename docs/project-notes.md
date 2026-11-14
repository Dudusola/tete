# PotentialPro — Project Notes

> The short version. The full history (every bug's
> story, every experiment, the reference-repo audits) is in
> [docs/history.md](history.md). **Before changing an area, read its entry
> there**: most of the rules below were learned the hard way.

## What this is
A zero-budget betting **prediction** app for Nigerian bettors: football first,
plus NHL, NBA and NFL. Solo developer ("Boss"). Testers on the site since
11 Oct 2026. Its credibility rests entirely on an **honest track record**, so
data integrity outranks features.

## Commits and PRs
Workflow: branch -> PR -> merge only when Boss says so. Boss is the only author.

## Architecture
1. **Engine**: scrapers -> SQLite ledger -> models -> markets -> written analysis
2. **API**: FastAPI (`api/server.py`, port 8080), serves what the pipeline last
   wrote; nothing is predicted on demand
3. **Frontends**: web app/PWA (`webapp/`), Android TWA (the live site in a
   window), Telegram (daily push + Worker bot)

## Where it runs (nothing depends on Boss's laptop)
| Piece | Host | Entry |
|---|---|---|
| Daily pipeline | GitHub Actions | `daily.yml`, 06:00 UTC via Worker cron (GitHub 08:50 cron = backup; guard skips once today ran) |
| Hourly closing prices, US lines, analysis | GitHub Actions | `closing.yml`, :20 via Worker cron |
| Evening results | GitHub Actions | `results.yml`, 22:00 UTC |
| API | Render free tier | https://potentialpro-api.onrender.com (sleeps ~15 min idle; Worker pings every 10 min) |
| Web app | Cloudflare Pages | https://potentialpro.pages.dev, auto-deployed by `deploy-webapp.yml` |
| Bot, crons, web push | Cloudflare Worker | `worker/`, auto-deployed by `deploy-worker.yml` |
| Android APK | `android-apk.yml` | signing key: GitHub secrets + Boss's Documents only. Lose it = no updates ever |
| Accounts, tracked picks, push subs | Supabase `vtltrjsjhfdhlrqrswvt` | `db/supabase/*.sql`. NEVER put user data in the ledger |
| Repo | GitHub (private) | https://github.com/Dudusola/tete |

- **The ledger `db/sports_predictor.db` is COMMITTED** and rewritten by the
  workflows. One writer. `git pull` before touching it; never run the local
  pipeline against it while the cloud is live; local Task Scheduler jobs stay
  DISABLED.
- **Testing anything that writes the DB: use a COPY** and `DB_PATH=<copy>`.
- Workflows that write the ledger check out `ref: ${{ github.ref }}` (bug #32)
  and share the `daily-pipeline` concurrency group.
- Deploy stamps `js/api.js?v=` and `css/app.css?v=` with content hashes; the
  Tailwind CSS and the icon-font subset are rebuilt on every deploy.
- Worker secrets are write-only. Set GitHub secrets with `gh secret set X --body $v`,
  never a PowerShell pipe (adds a BOM).

## Sources (cloud reality, Nov 2026)
- **ESPN is the only football fixture + odds source in the cloud.** PredictZ and
  Forebet Cloudflare-block GitHub's IPs (`PP_SKIP_PREDICTZ/FOREBET=1`); Boss
  decided 4 Oct: "leave it", so don't re-propose a proxy or local run unless
  ESPN coverage itself becomes the problem.
- API-Football: suspended by a shared-IP rule; off (`PP_SKIP_APIFOOTBALL=1`).
  Path back is a dedicated-IP VPS, deferred by Boss.
- SportyBet blocks Actions too; jobs read it through the API (`/api/sporty/events`,
  `SPORTY_EVENTS_VIA`). Render is not blocked.
- ESPN team names are long-form; `TEAM_ALIASES` in `models/predict.py` maps them to
  the history CSVs. When a source changes, re-check the funnel
  priced -> predicted -> served (`db/daily_health.py`), not just "rows landed".
- ClubElo is the only opinion not derived from bookmaker prices.
- Analysis LLM chain: Gemini -> Groq -> Ollama (`_provider_chain()`). Explanation
  only, never moves a probability.

## Run commands
- Pipeline: `python run_daily.py` (`--no-gemma` skips AI text)
- API: `uvicorn api.server:app --reload --port 8080` (port 8000 is blocked here)
- Web app: `cd webapp; python -m http.server 3000` (never file://)
- CSS after adding classes: `cd tools/tailwind && npm ci && npm run build`
- Daily health: `python db/daily_health.py` (also sent to Telegram each morning)
- Calibration: `python db/calibration_health.py` (don't re-derive tiers by hand)
- Market calibration (every Builder market): `python db/market_calibration.py` (~2 min; monthly)
- Coverage: `python db/track_record_health.py`, `python models/gradeability.py`
- Edge / price: `db/edge_report.py`, `db/clv_report.py`, `db/sporty_value_report.py`
- Anything to refine: `python db/refine_report.py`
- US sports: `scrapers/espn_ledger.py`, `scrapers/sports_history.py`,
  `db/shadow_report.py`, `models/elo.py`
- Dedupe: `python db/dedupe_fixtures.py` (dry run) then `--apply`
- Docker allowlist check: `python tools/check_docker_copies.py`

## Key files
- `models/train.py` / `models/predict.py`: football Random Forest (~72k matches),
  `MODEL_VERSION` marks ledger eras. Closing-odds families tried in order, plus
  `odds_is_market` (bug #18).
- `models/tiers.py`: football tiers HIGH >=65%, MEDIUM >=60%, LOW not served.
- `models/markets.py` + `models/goals_model.py`: ~22 derived markets; goals from
  attack/defence ratings with a 90-day league level (bug #39). `build_slip` is
  DETERMINISTIC on purpose: variety comes from user preferences, not randomness.
- `models/settle.py`: grades every market; returns None, never a guess.
- `models/us_sports.py`: NHL/NBA/NFL picks, Builder legs, own tiers (HIGH >=65%,
  MEDIUM >=55%) and each sport's OWN record, never blended with football. Market
  names are a contract with `scrapers/booking_codes.US_SPORTS`.
- `models/elo_shadow.py`, `nhl_markets.py`, `nfl_markets.py` (also NBA),
  `nhl_form.py`, `nba_stars.py`, `nfl_qb.py`: the US-sports engine. Warnings
  (price moved, QB out, star out) never change probabilities; `record()` keeps
  warned picks apart.
- `analysis/gemma_analyst.py`, `analysis/nhl_analyst.py`: facts-only prose with
  guards against invented figures; on failure store nothing.
- `models/team_match.py`: THE matcher for live scores, kickoff times, booking.
  Squad qualifiers (U20, Women, II, B...) must be equal or the score is 0.
- `models/sports.py`: `sport_sql()`. Every reader of `matches` that matches
  against a football feed must carry `sport_sql(FOOTBALL)` (bug #30).
- `scrapers/booking_codes.py`: SportyBet/MSport (Betradar ids) and Bet9ja (own
  ids) create + read; football.com read only. US sports use FULL-GAME market ids
  only (NHL 406/410/412, NFL/NBA 219/223/225); never the regulation ones.
- `scrapers/market_snapshot.py` / `closing_snapshot.py` / `sporty_lines.py`:
  prices at pick time and before kickoff. Must run on time; odds can't be backfilled.
- `api/cache.py`, `api/security.py`: response cache keyed on ledger version;
  body/list caps, per-IP rate limits, CORS allowlist.
- `deploy/render/Dockerfile` + `tools/check_docker_copies.py` (bug #15).
- `webapp/`: Matches is the home page (`index.html`), then Picks, Builder,
  Checker, Record. `js/api.js` holds shared glue: dark mode, accessibility,
  escapeHtml, prettyLeague, launch screen. `sw.js` is NETWORK-FIRST, never
  cache-first. CSP in `_headers` via `tools/csp.py`: no inline `onclick`.

## What the record says (re-run the scripts; don't edit numbers here)
- Football served tiers hold their claims (13 Nov 2026: HIGH 379/477 = 79.5%,
  MEDIUM 240/388 = 61.9%, coverage 96.4%).
- **No proven edge over the bookmaker in any sport.** The model ~= the market
  (log loss 0.678 vs 0.676); blends, opening-odds training, form-depth features,
  Elo extras (except NBA back-to-back) all tested and NOT shipped. Any edge is in
  PRICE, not the model. Claim nothing until a CI clears 0.
- NBA spread/total legs are served at the BOOK's fair probability (ours was worse).
- **Don't change tier thresholds on thin samples.** Three past "findings" reversed
  as the sample grew.

## Rules learned from fixed bugs (full stories: docs/history.md)
1. Never read a prediction column as a result; never grade before full-time.
2. Duplicate fixtures across sources: match fuzzy teams + **±1 day**, never exact date.
3. One verdict per match: latest prediction, `MAX(id)` per `match_id`.
4. Cloudflare blocks: keep a Cloudflare-immune results source.
5. A scraper that got 0 rows must exit non-zero.
6. Detect blocks only by signatures unique to the challenge page.
7. Model features take real bookmaker odds, never another source's percentages.
8. Grading COVERAGE is a health metric; the ungraded picks are not random.
9. Health = "did fresh rows land today?" checked in the DB, not "did the run succeed?".
10. Derive page counts from the feed's response; never hardcode a third-party limit.
11. "Latest per group" over nullable columns: filter BEFORE taking the max.
12. Reading and creating codes are separate capabilities; one dict per capability.
13. A snapshot status (live_state) needs an expiry (`effective_live_state`).
14. Check what a red run SKIPS (`if: always()`); never write an error that asserts an unchecked cause.
15. Every module the API imports must be COPYed in the Dockerfile; the check enforces it.
16. Publish the grading denominator with every accuracy figure.
17. One matcher per feed; poll if the UI claims something is live.
18. Count how often a fallback fires before trusting a feature.
19. Destructive scripts: dry-run by default, `--apply`, narrow WHERE, refuse a missing DB.
20. Dedupe keeps `MAX(id)`, the row everything downstream reads.
21. Schema lives in `init_db()`, not in whichever script wrote a column first.
22. Compare squad qualifiers explicitly; containment can't tell a club from its U20 side.
23. Read the column's writer, not its name (`over_2_5_pct` holds average goals).
24. A cap with no way to lift it looks like missing data.
25. Goals markets come from goals_model, never the predicted scoreline.
26. Forebet is off in the cloud by design (IP block).
27. Thin team history: measured, no model fix helped; don't re-run the experiment.
28. Analysis prompts carry the real features; absence of data must read as absence, not zero.
29. Emoji are fine in `logger.*`, never in `print()` (cp1252 crash).
30. When a new kind of row enters a shared table, grep every reader.
31. ESPN non-soccer quirks: OT/SO in finals, preseason, exhibitions, MLB short 9th; allow-list and log rejects.
32. A queued workflow must check out the branch head at start.
33. Only count picks made before kickoff; late ones are quarantined, not deleted.
34. PredictZ is blocked in the cloud; a green backup run may just be its guard skipping.
35. Indexes can change row order: say ORDER BY when order matters. Never build icon or class names from pieces.
36. Security: `?api=` only on localhost, booking codes `[A-Z0-9]`, rate limits, CSP by hash.
37. Market blend and opening-odds training didn't help; don't re-run.
38. SportyBet is read through the API proxy; a red-every-day step hides real failures.
39. Goals level uses a 90-day half-life; Under 3.5 >=70% still ~5 pts high, re-check monthly.

## Decisions
- Pipeline step severity: CRITICAL aborts (only "Generate predictions" and
  "every fixture source failed"), DEGRADED (default) continues but ends red,
  OPTIONAL needs a written reason at the call site. Never make a step OPTIONAL to
  quiet a failure.
- Combo markets (Home & Over etc.): grade before making them bookable.
- Unmappable booking legs are reported, never bent onto a different market.
- The model's record is GLOBAL; a user's record is per-user (Supabase).
- Web app/PWA + Android TWA, not React Native.
- Free options before paid ones; no paid data budget.

## Gotchas
- Never edit `webapp/*.html` with PowerShell Get-Content/Set-Content (mojibake).
- PowerShell `Out-File` writes null bytes; PS 5.1 pipes add a BOM.
- Ollama uses `/api/chat`, response in `message.content`.
- PredictZ needs Playwright FIREFOX locally (Chromium gets challenged); no
  timezone/viewport overrides.
- New Supabase tables need `grant ... to authenticated`.
