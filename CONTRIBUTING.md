# Working on PotentialPro together

Read `CLAUDE.md` first — it is the real project memory (architecture, the bugs
already fixed, and *why* decisions were made). This file only covers getting set
up and not stepping on each other's toes.

---

## 1. First-time setup

```bash
git clone <repo-url>
cd files

python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # macOS/Linux

pip install -r requirements.txt
playwright install chromium firefox   # BOTH — predictz.py needs firefox specifically

cp .env.example .env             # copy .env.example -> .env, then fill it in
python db/database.py            # create the SQLite schema
```

### Filling in `.env`
| Key | Where to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | @BotFather on Telegram (make your own test bot — don't share one) |
| `TELEGRAM_CHAT_ID` | @userinfobot — YOUR id, not the bot's |
| `FOOTBALL_DATA_TOKEN` | free key at football-data.org/client/register |
| `OLLAMA_MODEL` / `OLLAMA_HOST` | defaults are fine if Ollama runs locally |

`.env` is gitignored and must stay that way. **Never commit real credentials,
not even in `.env.example`.**

### Ollama (only needed for the Gemma analysis text)
Install Ollama, then `ollama pull gemma3:4b`. Without it, run the pipeline with
`--no-gemma` — everything else works.

---

## 2. What is NOT in the repo (and how to regenerate it)

| Missing | Why | How to get it |
|---|---|---|
| `models/*.joblib` | 27 MB binary, regenerated on every retrain | `python models/train.py` (~a few minutes; reads `data/historical/`) |
| `data/raw/`, `*.log` | debug dumps and noise | created as you run things |

The training CSVs in `data/historical/` **are** committed, so `train.py` works on
a fresh clone with no downloads.

### The ledger lives in the repo (changed 6 Aug 2026)

`db/sports_predictor.db` **is committed**, and the GitHub Actions pipeline
(`.github/workflows/daily.yml`) writes it every day at 08:50 UTC / ~10:00 Lagos.
That file is the authoritative track record — the thing the product's
credibility rests on.

Practical consequences:

- **Pull before you work.** The bot commits the ledger daily, so `main` moves
  without either of you touching it.
- **Don't commit your own DB changes.** Running the pipeline locally will
  modify the file. Unless you're deliberately backfilling, restore it before
  committing: `git checkout db/sports_predictor.db`.
- **It's a binary file — git can't merge it.** If both the workflow and a human
  change it, you get a conflict that has to be resolved by choosing one side.
  The workflow fails loudly rather than guessing; prefer taking the bot's copy
  and re-running whatever you needed.
- `db/*.db` is still gitignored in general, so a scratch database of your own is
  never committed by accident. Only this one file is force-added.

---

## 3. Running it

```bash
python run_daily.py                  # full pipeline (add --no-gemma to skip AI text)
uvicorn api.server:app --reload --port 8080     # API + docs at /docs
cd webapp && python -m http.server 3000         # web app — http, NEVER file://
python bot/telegram_bot.py                      # Telegram bot
```

Port 8000 is blocked on Boss's Windows box — the project standardizes on **8080**.

---

## 4. Branching — how we commit concurrently

`main`/`master` should always be runnable. Nobody commits straight to it.

```bash
git checkout main
git pull                            # always start from current main
git checkout -b fix/espn-coverage   # short, descriptive branch name

# ...work, commit in small logical chunks...

git push -u origin fix/espn-coverage
```

Then open a Pull Request on GitHub and let the other person look before merging.

**Before you push, always:**
```bash
git pull --rebase origin main       # replay your work on top of theirs
```
Rebase rather than merge keeps the history readable, and rebasing your *own
unpushed* branch is always safe.

### Never rewrite shared history
`git push --force`, `git rebase` on a branch someone else has pulled, and
`filter-branch` on `main` will break the other person's clone. If history ever
genuinely must be rewritten (e.g. a leaked secret), agree on it first, then both
re-clone.

### Areas that conflict easily
- **`db/sports_predictor.db`** — **is tracked** and is binary, so git cannot
  merge it; the daily workflow rewrites it whether or not anyone is working.
  It is the worst conflict in the repo and the easiest to cause: run the
  pipeline locally, forget, and commit. `git checkout db/sports_predictor.db`
  before committing anything else. Full rules in §2. (This section used to say
  "the DB file isn't tracked" — the opposite of §2, dating from before 6 Aug
  2026. Believing it is exactly how the track record gets clobbered.)
- **`db/database.py`** — schema changes are tracked and can invalidate the other
  person's local data. Flag them loudly. Every new column needs its `ALTER
  TABLE` in `init_db()`, not only in whatever script first writes it, or a
  fresh clone gets a `no such column` at serve time.
- **`webapp/js/api.js`** — shared by all five HTML pages. Coordinate before big edits.
- **`CLAUDE.md`** — both of us append to it. Small, focused edits merge cleanly;
  wholesale reorganizations do not.

---

## 5. Project rules that bite if you don't know them

These are learned-the-hard-way, not preferences. `CLAUDE.md` has the full list.

1. **Never read a prediction column as a result, and never grade a match before
   full-time.** This once produced a fake 100% hit rate. Data integrity outranks
   every feature.
2. **A scraper that gets 0 rows must exit non-zero.** Silent "success" on empty
   scrapes hid a two-week gap in the ledger.
3. **Never detect a block with substrings that also appear on healthy pages.**
   Normal pages embed Cloudflare's beacon; a sloppy check discarded good data for days.
4. **Match fixtures with fuzzy team names + a ±1 day window.** Sources disagree on
   both spelling and date; exact matching creates duplicate fixtures.
5. **Bump `?v=N` on `js/api.js` in all five HTML files whenever you edit it**, or
   browsers serve stale JS.
6. **Never edit `webapp/*.html` with PowerShell `Get-Content`/`Set-Content`.**
   PS 5.1 reads UTF-8 as ANSI and corrupts `•`, `≥`, `–`. Use a real editor.
7. **`sw.js` is network-first on purpose.** Do not "optimize" it to cache-first.
8. **Don't invite testers yet.** The track record must mature first — that's a
   product decision, not an oversight.

---

## 6. Checking the ledger is healthy

```bash
python db/track_record_health.py    # grading COVERAGE, not just cadence
```

A tier's hit-rate is meaningless if the ungraded picks are the ones that lost —
this has already happened once. Run it before trusting any accuracy number.
