# Deploying PotentialPro for testers

**Chosen shape (July 2026):** the daily pipeline + SQLite DB stay on the
laptop (free, already working, keeps the honest ledger local). Only two things
go public:

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

## Before commercial / real launch
- Move to a small VPS (~$5/mo) so it's laptop-independent.
- Lock CORS in api/server.py from `["*"]` to the Pages domain.
- Named Cloudflare tunnel (fixed URL) or host the API on the VPS directly.
- Revoke/rotate the Telegram token (it was exposed in chat once).
