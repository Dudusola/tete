/* config.js — deployment config, loaded BEFORE api.js on every page.
   When you deploy the PWA, set this to your public API URL (the tunnel or
   cloud host that fronts your laptop's FastAPI). Locally it can stay blank —
   api.js falls back to http://localhost:8080. */
window.PP_API_BASE = "";   // blank on purpose: the API serves the PWA too, so
                           // api.js uses the page's own origin. One tunnel = whole
                           // app, no rewrite when the tunnel URL changes.
