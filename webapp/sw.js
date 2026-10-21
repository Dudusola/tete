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
