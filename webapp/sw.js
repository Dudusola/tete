/* PotentialPro service worker.
   Strategy: network-first with cache fallback for everything same-origin.
   Network-first on purpose — cache-first for HTML/JS would resurrect the
   "stale api.js" bug class. The cache only serves when the network fails,
   so the app shell still opens offline with the last good copy.
   API calls are cross-origin; only the read endpoints below are touched, and
   only as an OFFLINE FALLBACK (23 Oct 2026): network first, always; the saved
   copy is served only when the API can't be reached or answers with an error,
   and it carries _pp_saved so the page can say how old it is. */
const CACHE = "pp-shell-v1";
const API_CACHE = "pp-api-v1";
// What someone opens the app to see. Never booking, slips or anything POSTed.
const OFFLINE_API = /\/api\/(picks|picks\/sports|stats|stats\/trend|stats\/tiers|matches|roundups)$/;

self.addEventListener("install", e => self.skipWaiting());
self.addEventListener("activate", e => {
    e.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE && k !== API_CACHE).map(k => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

async function apiWithFallback(req) {
    const cache = await caches.open(API_CACHE);
    try {
        const res = await fetch(req);
        if (res.ok) {
            // The saved copy carries its own timestamp IN the JSON: a custom
            // header does not survive to the page on a cross-origin response
            // (tried and checked 23 Oct 2026), a body field always does.
            res.clone().json().then(data => {
                if (data && typeof data === "object" && !Array.isArray(data)) {
                    data._pp_saved = new Date().toISOString();
                    cache.put(req, new Response(JSON.stringify(data), {headers: {"Content-Type": "application/json"}}));
                }
            }).catch(() => {});
            return res;
        }
        return (await cache.match(req)) || res;    // server error: the last good copy beats an error
    } catch (err) {
        const saved = await cache.match(req);      // offline / unreachable
        if (saved) return saved;
        throw err;
    }
}

self.addEventListener("fetch", e => {
    const url = new URL(e.request.url);
    if (e.request.method === "GET" && url.origin !== location.origin && OFFLINE_API.test(url.pathname)) {
        e.respondWith(apiWithFallback(e.request));
        return;
    }
    if (e.request.method !== "GET" || url.origin !== location.origin) return;
    e.respondWith(
        fetch(e.request).then(res => {
            const copy = res.clone();
            caches.open(CACHE).then(c => c.put(e.request, copy));
            return res;
        }).catch(() => caches.match(e.request, {ignoreSearch: url.pathname.endsWith(".js")}))
    );
});

/* Web push for tracked picks/slips (worker/push.js sends; js/auth.js
   subscribes). Display only - caching above is untouched, so a push can
   never change the network-first rule. */
self.addEventListener("push", e => {
    let m = {};
    try { m = e.data ? e.data.json() : {}; } catch (_) {}
    e.waitUntil(self.registration.showNotification(m.title || "PotentialPro", {
        body: m.body || "",
        icon: "icons/icon-192.png",
        badge: "icons/icon-192.png",
        tag: m.tag,                      // the same pick never stacks twice
        data: { url: m.url || "account.html" },
    }));
});

self.addEventListener("notificationclick", e => {
    e.notification.close();
    const url = new URL(e.notification.data && e.notification.data.url || "account.html", self.registration.scope).href;
    e.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
        const open = list.find(c => c.url.startsWith(self.registration.scope));
        return open ? open.navigate(url).then(c => (c || open).focus()) : clients.openWindow(url);
    }));
});
