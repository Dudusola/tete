/* config.js — deployment config, loaded BEFORE api.js on every page.

   The frontend and the API now live in different places:
     frontend -> Cloudflare Pages (static, instant, free)
     API      -> Render           (the real FastAPI, so /api/slip and the
                                   booking codes work rather than being
                                   reimplemented in JavaScript)

   So this can no longer be blank — a blank value makes api.js fall back to the
   page's own origin, which on Pages serves no API at all.

   Note on the free Render plan: the service sleeps after ~15 minutes idle, so
   the FIRST request after a quiet spell takes ~50s while it wakes. Subsequent
   requests are fast. api.js's ?api= override still wins if you ever need to
   point a browser at a different backend for testing. */
window.PP_API_BASE = "https://potentialpro-api.onrender.com";
