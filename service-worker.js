// service-worker.js
const VERSION = "v2";
const SHELL_CACHE = `gold-shell-${VERSION}`;

const SHELL_FILES = [
  "./",
  "./index.html",
  "./style.css",
  "./app.js",
  "./manifest.webmanifest",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

// All JSON data files get network-first treatment (same as prices.json).
const DATA_FILES = [
  "prices.json",
  "forecast.json",
  "backtest.json",
  "commentary.json",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(SHELL_CACHE).then((c) =>
      // Don't fail install if one optional asset misses.
      Promise.all(SHELL_FILES.map((f) => c.add(f).catch(() => null)))
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

function isDataFile(url) {
  return DATA_FILES.some(
    (f) => url.pathname.endsWith(`/${f}`) || url.pathname.endsWith(`data/${f}`)
  );
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);

  if (isDataFile(url)) {
    // Network-first for all data files; fall back to cache when offline.
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(SHELL_CACHE).then((c) => c.put(e.request, copy));
          return res;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // Cache-first for shell assets.
  e.respondWith(
    caches.match(e.request).then((cached) => cached || fetch(e.request))
  );
});
