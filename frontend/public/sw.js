// Service Worker – Telecom Tower Power PWA
const CACHE_NAME = "ttp-v2";
const TILE_CACHE = "ttp-tiles-v1";
const STATIC_ASSETS = [
  "/",
  "/manifest.json",
  "/favicon.svg",
];

// Max tile entries to avoid unbounded storage
const MAX_TILE_ENTRIES = 2000;

// Install: pre-cache the app shell
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

// Activate: clean old caches
self.addEventListener("activate", (event) => {
  const keepCaches = new Set([CACHE_NAME, TILE_CACHE]);
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => !keepCaches.has(k)).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

/**
 * Detect map tile requests (OpenStreetMap, CartoDB, Mapbox, etc.)
 */
function isTileRequest(url) {
  return (
    url.hostname.includes("tile.openstreetmap.org") ||
    url.hostname.includes("basemaps.cartocdn.com") ||
    url.hostname.includes("tiles.mapbox.com") ||
    // Generic z/x/y pattern: /{z}/{x}/{y}.png
    /\/\d+\/\d+\/\d+\.\w+$/.test(url.pathname)
  );
}

/**
 * Trim tile cache to MAX_TILE_ENTRIES (FIFO).
 */
async function trimTileCache() {
  const cache = await caches.open(TILE_CACHE);
  const keys = await cache.keys();
  if (keys.length > MAX_TILE_ENTRIES) {
    const toDelete = keys.slice(0, keys.length - MAX_TILE_ENTRIES);
    await Promise.all(toDelete.map((k) => cache.delete(k)));
  }
}

// Fetch handler
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Skip non-GET
  if (event.request.method !== "GET") return;

  // ── Map tiles: Cache-First strategy ──────────────────────
  if (isTileRequest(url)) {
    event.respondWith(
      caches.open(TILE_CACHE).then((cache) =>
        cache.match(event.request).then((cached) => {
          if (cached) return cached;
          return fetch(event.request).then((response) => {
            if (response.ok) {
              cache.put(event.request, response.clone());
              trimTileCache();
            }
            return response;
          }).catch(() => {
            // Return a transparent 1x1 PNG as tile placeholder when offline
            return new Response(
              Uint8Array.from(atob(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQAB" +
                "Nl7BcQAAAABJRU5ErkJggg=="
              ), c => c.charCodeAt(0)),
              { headers: { "Content-Type": "image/png" } }
            );
          });
        })
      )
    );
    return;
  }

  // ── API calls: Network-only (app-level IndexedDB handles offline) ──
  if (url.pathname.startsWith("/api")) return;

  // ── Static assets: Stale-While-Revalidate ────────────────
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const fetchPromise = fetch(event.request)
        .then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        })
        .catch(() => cached); // offline fallback

      return cached || fetchPromise;
    })
  );
});
