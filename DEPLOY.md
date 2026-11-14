# Deploying PotentialPro

> **Rewritten 23 Aug 2026.** This file still described the laptop-and-tunnel era
> as "use this now", months after the system moved to the cloud. That was not
> just out of date, it was actively wrong in one specific way: it said
> `webapp/config.js` "stays blank", and following that instruction today
> **breaks the live site** — a blank value makes api.js fall back to the page's
> own origin, and the page's own origin is Cloudflare Pages, which serves no
> API. `webapp/config.js` says so in its own header. Deployment docs that
> contradict the code are worse than no docs, because they get followed.

## How it actually runs now

Nothing is on the laptop. Three free tiers, each doing one job:

| Piece | Where | Trigger |
|---|---|---|
| Daily pipeline (scrape → predict → publish) | GitHub Actions | cron `50 8 * * *` UTC ≈ 10:00 Lagos |
| Results grading | GitHub Actions | cron `0 22 * * *` UTC = 23:00 Lagos |
| The ledger (`db/sports_predictor.db`) | committed back to this repo by the pipeline | every run |
| API (FastAPI) | Render, `https://potentialpro-api.onrender.com` | auto-redeploys on push to `main` |
| PWA (`webapp/`) | Cloudflare Pages, `potentialpro.pages.dev` | `.github/workflows/deploy-webapp.yml` on push to `main` |
| Telegram bot | Cloudflare Worker (webhook, not polling) | `wrangler deploy` |

**There is nothing to run by hand.** Push to `main` and the API and the PWA
redeploy themselves; the pipeline runs on its own schedule whether or not any
machine of ours is awake. That was the entire point of moving off the laptop
(docs/history.md bug #9).

The local Task Scheduler job **"PotentialPro Daily Pipeline 1200" is disabled**
and must stay disabled. Two pipelines writing two copies of a committed binary
ledger is how the track record becomes untrustworthy — see the header of
`.github/workflows/daily.yml`.

## The ledger is the one thing to be careful with

`db/sports_predictor.db` is **committed, binary, and is the published track
record**. The cloud is its single writer.

- `git pull` before you touch anything near it.
- **Never merge it.** A binary merge conflict has no good resolution; take one
  side (theirs, i.e. the cloud's) and re-run locally if you need fresh data.
- Never run the local pipeline and the cloud pipeline at the same time.
- Render does not write it — `deploy/render/sync_ledger.py` pulls the committed
  copy down. Render is a read-only consumer.

## Free-tier behaviour worth knowing before you blame a bug

- **Render sleeps after ~15 minutes idle.** The first request after a quiet
  spell takes **~50 seconds** while the container wakes; everything after is
  fast. The PWA is not broken and neither is your connection. `/healthz` is the
  cheapest way to wake it deliberately.
- **GitHub cron is best-effort** and routinely lags 10+ minutes — worst on the
  `:00` mark, because that is when everyone else schedules. The daily cron is
  deliberately set 10 minutes *early* (`50 8` UTC = 09:50 Lagos) so that the
  usual lag lands it near the 10:00 Lagos target. Every insert is
  `INSERT OR IGNORE`, so a late run — or a repeated one — is harmless.
- **Cloudflare Pages** serves the last successful deploy, so a failed frontend
  deploy leaves the previous version live rather than a blank page.

## Frontend deploys: don't bump the cache-bust by hand

`deploy-webapp.yml` stamps `js/api.js?v=<content hash>` into `webapp/*.html`
itself. This used to be a manual bump, and forgetting it served **old
JavaScript against new HTML** — the "Couldn't book" bug. A content hash cannot
be forgotten and cannot be wrong.

One-time secrets, if the deploy step is skipping:

```bash
gh secret set CLOUDFLARE_API_TOKEN --body "<token>"
```

```bash
gh secret set CLOUDFLARE_ACCOUNT_ID --body "<account id>"
```

Use `--body`, never a pipe: PowerShell 5.1 prepends a BOM that breaks the value
server-side. Until both exist the workflow skips cleanly instead of failing.

Also: never edit `webapp/*.html` with PowerShell `Get-Content`/`Set-Content`.
PS 5.1 corrupts the UTF-8 `•`, `≥` and `–` characters in those files.

---

## Local development

```bash
start_all.bat
```

API on `http://127.0.0.1:8080`, which also serves the PWA from the StaticFiles
mount at the bottom of `api/server.py`. For local work, open the API's own
origin — `config.js` points at Render, but `api.js` accepts a one-off override:
load any page with `?api=http://127.0.0.1:8080` and it is remembered in
localStorage. That is the supported way to point a browser at a different
backend; **do not blank out `config.js` to achieve it**, because that change
gets committed and takes the live site down with it.

## Fallback: exposing the laptop through a tunnel

Only useful if Render is down and testers need something today. Superseded by
the Render deploy — kept because it still works and the scripts are still here.

```bash
start_public.bat
```

Starts the API (which serves the PWA), the bot, waits for health, then opens a
Cloudflare quick tunnel. Share the `https://<random>.trycloudflare.com` URL it
prints — at that point site and API are the same origin, which is the one
situation where a blank `PP_API_BASE` would have been correct. Reach it with
`?api=<tunnel url>` instead and leave `config.js` alone.

Two flags in `start_public.bat` were learned the hard way (July 2026):

- `--protocol http2` — this network blocks cloudflared's default QUIC/UDP path
  (port 7844); without it the tunnel keeps dropping ("control stream failure").
- `http://127.0.0.1:8080`, not `localhost` — cloudflared resolves localhost to
  IPv6 `::1` first, but uvicorn listens on IPv4, giving a 530 origin error.

Quick tunnels mint a new URL every run. A *named* tunnel keeps a fixed
hostname, but needs a domain on Cloudflare's free plan:

```bash
cloudflared tunnel login
```

```bash
cloudflared tunnel create potentialpro
```

```bash
cloudflared tunnel route dns potentialpro app.YOURDOMAIN.com
```

```bash
cloudflared tunnel run --url http://127.0.0.1:8080 potentialpro
```

---

## Still open before a real launch

- **CORS is `allow_origins=["*"]`** in `api/server.py:56`. Tighten it to the
  Pages domain. Right now any site on the internet can read the API from a
  visitor's browser.
- **Rotate the Telegram bot token** via BotFather — it was exposed in chat once
  and has not been regenerated. Standing item in docs/history.md.
- A paid host (~$5/mo) removes Render's cold start, which is the single most
  visible flaw a tester will hit.
- Don't invite testers until the two items above are done.
