// Offline support. Pages are fetched from the network first, so updates always show;
// the saved copy is used only when the device is offline.
const CACHE = "the-truth-v1";
const ROOT = new URL("./", self.location).href;

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    try { await cache.add(new Request(ROOT, {cache: "reload"})); } catch (e) {}
    try {
      // Save every page listed in the site's search index so the whole site reads offline.
      const res = await fetch(ROOT + "search.json", {cache: "reload"});
      const items = await res.json();
      const pages = [...new Set(items.map((i) => i.href.split("#")[0]))];
      await Promise.all(pages.map((p) => cache.add(new Request(ROOT + p, {cache: "reload"})).catch(() => {})));
    } catch (e) {}
    try { await cache.add(new Request(ROOT + "the-truth.epub", {cache: "reload"})); } catch (e) {}
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.protocol !== "https:" && url.hostname !== "localhost") return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    try {
      const res = await fetch(req);
      if (res && (res.ok || res.type === "opaque")) cache.put(req, res.clone());
      return res;
    } catch (e) {
      const hit = await cache.match(req, {ignoreSearch: true});
      if (hit) return hit;
      if (req.mode === "navigate") { const home = await cache.match(ROOT); if (home) return home; }
      throw e;
    }
  })());
});
