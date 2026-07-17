/* PotentialPro service worker.
   Strategy: network-first with cache fallback for everything same-origin.
   Network-first on purpose — cache-first for HTML/JS would resurrect the
   "stale api.js" bug class. The cache only serves when the network fails,
   so the app shell still opens offline with the last good copy.
   API calls (port 8080) are cross-origin and never touched here. */
const CACHE = "pp-shell-v1";

self.addEventListener("install", e => self.skipWaiting());
self.addEventListener("activate", e => {
    e.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", e => {
    const url = new URL(e.request.url);
    if (e.request.method !== "GET" || url.origin !== location.origin) return;
    e.respondWith(
        fetch(e.request).then(res => {
            const copy = res.clone();
            caches.open(CACHE).then(c => c.put(e.request, copy));
            return res;
        }).catch(() => caches.match(e.request, {ignoreSearch: url.pathname.endsWith(".js")}))
    );
});
