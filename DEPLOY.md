# Deploying PotentialPro for testers

## Current setup (single-origin tunnel — SIMPLEST, use this now)

The FastAPI now **also serves the web app** (StaticFiles mount at the bottom of
api/server.py). So ONE tunnel exposes the whole thing — site + API at the same
origin — and `webapp/config.js` stays blank (api.js falls back to the page's
own origin). A changing quick-tunnel URL needs **no edits and no redeploy**;
just share the new URL.

**To go live:**
```
start_public.bat
```
It starts the API (which serves the PWA), the bot, waits for health, then opens
the cloudflare tunnel. Copy the `https://<random>.trycloudflare.com` URL it
prints and share THAT — it's the whole app.

Notes baked into start_public.bat (learned the hard way, July 2026):
- `--protocol http2` — this network blocks cloudflared's default QUIC/UDP path
  (port 7844); without this the tunnel keeps dropping ("control stream failure").
- `http://127.0.0.1:8080` not `localhost` — cloudflared resolves localhost to
  IPv6 ::1 first, but uvicorn listens on IPv4, giving a 530 origin error.

Quick tunnels mint a NEW url each run. That's the only downside — fixed with a
named tunnel once you own a domain (~$1-12/yr). See "Named tunnel" below.

---

## Alternative: separate static host (needs redeploy on URL change)

Only if you'd rather host the PWA on Cloudflare Pages/Netlify instead of serving
it from the API. Then the two pieces are:

1. **The PWA** (webapp/) → a free static host (Cloudflare Pages / Netlify).
2. **The FastAPI** → exposed from the laptop through a secure tunnel so the
   deployed PWA can reach it.

The API serves whatever the laptop pipeline last wrote, so testers always see
fresh picks as long as the laptop ran that day. When the laptop is off, the
PWA still loads (it's static) but shows "couldn't reach the engine" until the
tunnel is back.

---

## One-time setup

### A. Expose the API from the laptop (tunnel)

Two easy options — pick one:

**Cloudflare Tunnel (recommended — stable URL, free):**
```
winget install --id Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:8080
```
It prints a `https://<something>.trycloudflare.com` URL. That's your public
API base. (Quick tunnels get a new URL each run; a *named* tunnel keeps a
fixed URL — set that up once you're past testing.)

**localtunnel (npm, dead simple):**
```
npx localtunnel --port 8080 --subdomain potentialpro-api
```
→ `https://potentialpro-api.loca.lt`

Run `start_all.bat` FIRST (API must be up on 8080), then the tunnel.
`start_tunnel.bat` does the localtunnel version for you.

### B. Point the PWA at that API

Edit `webapp/config.js`:
```js
window.PP_API_BASE = "https://<your-tunnel-url>";
```
Commit it. (Locally it stays blank and api.js falls back to localhost:8080.)

You can also test without editing anything by opening the deployed site with
`?api=https://<tunnel-url>` once — it's remembered in localStorage.

### C. Deploy the PWA (static host)

**Cloudflare Pages:**
1. Push this repo to GitHub (already a git repo).
2. Cloudflare dashboard → Pages → Connect to Git → pick the repo.
3. Build settings: **Framework = None**, **Build command = (blank)**,
   **Output directory = `webapp`**.
4. Deploy → you get `https://potentialpro.pages.dev`. Share THAT with testers.

**Netlify (drag-and-drop, no Git needed):** drag the `webapp/` folder onto
app.netlify.com/drop. Done.

---

## Daily reality

- The Task Scheduler job "PotentialPro Daily Pipeline 1200" runs the pipeline
  at noon. Nothing changes there.
- Keep `start_all.bat` (API + web + bot) and the tunnel running for testers to
  have access. If the laptop sleeps, access pauses — that's the tradeoff we
  accepted vs. paying for a VPS (~$5/mo) for true 24/7. See CLAUDE.md.

## Named tunnel (fixed URL — do once you have a domain)

Quick tunnels change URL each run. A named tunnel keeps a permanent hostname,
but Cloudflare needs a domain you own on its (free) plan:
```
cloudflared tunnel login                       # browser auth; pick your domain
cloudflared tunnel create potentialpro         # makes a persistent tunnel + creds
cloudflared tunnel route dns potentialpro app.YOURDOMAIN.com
cloudflared tunnel run --url http://127.0.0.1:8080 potentialpro
```
Then `app.YOURDOMAIN.com` always points at the laptop's API. A `.xyz`/`.site`
domain is often ~$1-3 first year; Cloudflare Registrar sells `.com` at ~$10/yr
wholesale.

## Before commercial / real launch
- Move to a small VPS (~$5/mo) so it's laptop-independent.
- Lock CORS in api/server.py from `["*"]` to the Pages domain.
- Named Cloudflare tunnel (fixed URL) or host the API on the VPS directly.
- Revoke/rotate the Telegram token (it was exposed in chat once).
