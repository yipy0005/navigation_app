/* Offline support. Mobile data in the old town's narrow streets can drop out,
   so the help screens must still open: pages are network-first with a short
   timeout, and fall back to the last copy saved on the phone. */
const VERSION = 'companion-v8';
const PRECACHE = [
  '/', '/lost', '/translate', '/show?dest=home', '/show?dest=home&purpose=taxi', '/go',
  '/static/css/app.css', '/static/js/app.js', '/static/js/livemap.js',
  '/static/vendor/leaflet/leaflet.js', '/static/vendor/leaflet/leaflet.css', '/static/icon.svg', '/static/manifest.webmanifest',
];
const NO_CACHE = [/^\/api\//, /^\/setup/, /^\/s\//, /^\/lang\//, /^\/sw\.js$/];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(VERSION);
    // One failing URL must not stop the rest from being saved.
    await Promise.all(PRECACHE.map((u) => cache.add(new Request(u, { credentials: 'same-origin' })).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

function timeout(ms) {
  return new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), ms));
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;
  if (NO_CACHE.some((re) => re.test(url.pathname))) return;

  if (url.pathname.startsWith('/static/')) {
    // stale-while-revalidate
    event.respondWith((async () => {
      const cache = await caches.open(VERSION);
      const hit = await cache.match(req);
      const net = fetch(req).then((res) => { if (res.ok) cache.put(req, res.clone()); return res; }).catch(() => hit);
      return hit || net;
    })());
    return;
  }

  if (req.mode === 'navigate') {
    event.respondWith((async () => {
      const cache = await caches.open(VERSION);
      try {
        const res = await Promise.race([fetch(req), timeout(6000)]);
        if (res.ok && !res.redirected) cache.put(req, res.clone());
        return res;
      } catch (_) {
        return (await cache.match(req)) || (await cache.match(url.pathname)) || (await cache.match('/lost'))
          || new Response('Offline', { status: 503, headers: { 'Content-Type': 'text/plain' } });
      }
    })());
  }
});
