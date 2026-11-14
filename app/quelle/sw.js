/* Noten-Scanner: Service Worker für den Offline-Betrieb.
 * Die App-Dateien und – nach dem ersten Start – auch Python (Pyodide, ca. 30 MB) werden auf
 * dem Gerät zwischengespeichert. Danach läuft die App ohne Internetverbindung. */
"use strict";

const VERSION = "__VERSION__";
const APP_CACHE = "noten-scanner-app-" + VERSION;
const PY_CACHE = "noten-scanner-pyodide-0.27.7";
const VEROVIO_CACHE = "noten-scanner-verovio-__VEROVIO__";
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
const APP_DATEIEN = [
  "./", "index.html", "manifest.webmanifest",
  "app.css?v=" + VERSION, "app.js?v=" + VERSION, "worker.js?v=" + VERSION, "notenscanner.zip?v=" + VERSION,
  "notenxml.js?v=" + VERSION, "instrumente.js?v=" + VERSION, "klang.js?v=" + VERSION, "partitur.js?v=" + VERSION,
  "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png",
];

self.addEventListener("install", (ereignis) => {
  // cache: "reload" – nicht aus dem Browser-Zwischenspeicher, sonst könnte eine alte index.html in die neue Version geraten
  ereignis.waitUntil(caches.open(APP_CACHE)
    .then((cache) => cache.addAll(APP_DATEIEN.map((url) => new Request(url, { cache: "reload" }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (ereignis) => {
  ereignis.waitUntil(caches.keys()
    .then((namen) => Promise.all(namen
      .filter((name) => name.startsWith("noten-scanner-app-") && name !== APP_CACHE)
      .map((name) => caches.delete(name))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (ereignis) => {
  const anfrage = ereignis.request;
  if (anfrage.method !== "GET") return;
  if (anfrage.url.startsWith(PYODIDE)) {
    ereignis.respondWith(ausCacheOderNetz(PY_CACHE, anfrage));
  } else if (new URL(anfrage.url).origin === self.location.origin && new URL(anfrage.url).pathname.includes("/verovio/")) {
    ereignis.respondWith(ausCacheOderNetz(VEROVIO_CACHE, anfrage));   // 7 MB, bleibt über App-Updates erhalten
  } else if (new URL(anfrage.url).origin === self.location.origin) {
    ereignis.respondWith(ausCacheOderNetz(APP_CACHE, anfrage));
  }
});

async function ausCacheOderNetz(name, anfrage) {
  const cache = await caches.open(name);
  const treffer = await cache.match(anfrage);
  if (treffer) return treffer;
  const antwort = await fetch(anfrage);
  if (antwort.ok || antwort.type === "opaque") cache.put(anfrage, antwort.clone());
  return antwort;
}
