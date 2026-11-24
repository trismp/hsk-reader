/* Service worker: makes the site installable and readable offline.
 *
 * Strategy: "network first, cache as backup". When online you always get the
 * newest stories; every page and story you open is also copied into the cache,
 * so it still opens on the metro with no signal.
 * Bump VERSION when the app shell changes to clear old caches.
 */
const VERSION = "hsk-reader-v28";
const SHELL = ["./", "index.html", "style.css?v=25", "app.js?v=21", "manifest.webmanifest",
  "data/library.json", "icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;
  e.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(VERSION).then((c) => c.put(req, copy)); }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match("index.html")))
  );
});
