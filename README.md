# PotentialPro ⚽ (Sports Predictor)

AI-powered multi-sport prediction engine — personal testing phase.
Scrapes Forebet, trains Random Forest on 65k+ historical matches,
Gemma 3 (local, via Ollama) writes the analysis, Telegram delivers the picks.

**Model calibration (12 Jun 2026, 12.4k unseen test matches):**
| Tier | Model confidence | Historical accuracy |
|------|-----------------|---------------------|
| HIGH | ≥65% | ~75% |
| MEDIUM | ≥55% | ~66% |
| LOW | below | hidden — not worth betting |

---

## Daily Usage (one command)

```bash
python run_daily.py            # scrape -> score results -> predict (+ Gemma)
python run_daily.py --no-gemma # faster, skips AI analysis
```

Bot (keep running for Telegram to respond):
```bash
python bot/telegram_bot.py
```
Telegram commands: `/start` `/picks` `/stats`

---

## Automation (Windows Task Scheduler)

Two .bat files exist in the project root:
- `run_daily.bat` — full pipeline (schedule daily ~07:30)
- `start_bot.bat` — Telegram bot (schedule at logon)

Setup: Task Scheduler -> Create Basic Task -> point to the .bat file.
See "Task Scheduler setup" section below.

---

## Project Structure

```
files/
├── run_daily.py             ← one-command daily pipeline
├── run_daily.bat            ← Task Scheduler entry (pipeline)
├── start_bot.bat            ← Task Scheduler entry (bot)
├── main.py                  ← legacy runner (kept for --test mode)
│
├── scrapers/
│   ├── forebet.py           ← ✅ fixtures scraper (Playwright, today+tomorrow)
│   ├── results.py           ← ✅ final scores + pick scoring (selectors unverified*)
│   ├── fetch_international.py ← ✅ national-team data downloader
│   ├── debug_forebet.py     ← HTML inspection helper
│   └── debug_pages.py       ← page-level debug helper
│
├── models/
│   ├── train.py             ← ✅ Random Forest trainer (v2, calibrated tiers)
│   ├── predict.py           ← ✅ live predictions + Gemma analysis (v2)
│   └── random_forest_v1.joblib ← trained model
│
├── analysis/
│   └── gemma_analyst.py     ← ✅ Gemma 3 via Ollama (/api/chat)
│
├── bot/
│   └── telegram_bot.py      ← ✅ /picks /stats + daily 08:00 send
│
├── db/
│   ├── database.py          ← ✅ SQLite schema + helpers
│   └── cleanup.py           ← test-data/duplicate cleanup utility
│
└── data/
    ├── historical/          ← football-data.co.uk CSVs + InternationalResults.csv
    └── raw/                 ← scraped page dumps for debugging
```
*results.py selectors are best-guess until a predicted match finishes — verify then.

---

## Setup From Scratch

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install "python-telegram-bot[job-queue]"
playwright install chromium
copy .env.example .env       # then fill in real values
python db/database.py        # init DB
python main.py --test        # system check
```

`.env` needs:
```
TELEGRAM_BOT_TOKEN=...   (from @BotFather)
TELEGRAM_CHAT_ID=...     (from @userinfobot - YOUR id, not the bot's)
OLLAMA_MODEL=gemma3:4b
OLLAMA_HOST=http://localhost:11434
DB_PATH=./db/sports_predictor.db
```

---

## Maintenance Routine

- **Weekly**: re-download CSVs for in-season leagues from football-data.co.uk
  (overwrite files in data/historical/), re-run fetch_international.py,
  then retrain: `python models/train.py`
- **As needed**: check skip warnings in predict output -> add name mismatches
  to TEAM_ALIASES in models/predict.py
- **August 2026**: download new 2026-27 season files when European leagues restart

---

## Roadmap

### Phase 1 — Personal Engine ✅ DONE (12 Jun 2026)
- [x] Project structure, SQLite, scrapers, Gemma layer
- [x] Random Forest trained on 65k matches (incl. internationals)
- [x] Calibrated confidence tiers
- [x] Results tracker (auto-scoring)
- [x] Telegram bot

### Phase 2 — Validation (current)
- [ ] Task Scheduler automation (Level 2)
- [ ] Verify results.py selectors when first predicted match finishes
- [ ] Accumulate 2-4 weeks of scored track record
- [ ] /subscribe command -> invite 3-5 friends as witnesses

### Phase 3 — Expand
- [ ] Basketball (NBA), Tennis, more sports
- [ ] Odds scraping (Sportybet) -> value-bet detection
- [ ] xG features, neutral-venue flag for internationals

### Phase 4 — Commercial
- [ ] Cloud deployment (Level 3 — 24/7)
- [ ] React Native app (frontend dev + Google Stitch for UI)
- [ ] Booking codes: Sportybet, MSport, BetKing
- [ ] Claude API swap for production reasoning
- [ ] Subscriptions

---

## Session Log
- **09 Jun 2026** — Project foundation: structure, DB, scraper v1, Gemma layer
- **12 Jun 2026** — BIG DAY: fixed scraper (Playwright + selectors), trained model
  (45% -> calibrated tiers: 75% on HIGH), internationals added, predict.py live,
  results tracker, Telegram bot running. Phase 1 complete.
- **Next session** — Task Scheduler setup; watch first scored results

## Current Track Record
Scored picks: 0 (started 12 Jun 2026 — accumulating)