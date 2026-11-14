// Ende-zu-Ende-Test der Web-App in Chromium mit iPhone-Größe.
// Ablauf: python app/bauen.py; python app/test/fotos_erzeugen.py; python app/test/server.py 8765 &;
//         node app/test/test.js   (braucht Playwright: npm i -D playwright)
// Statt Pyodide rechnet hier über den Testserver das echte Python-Programm (worker-test.js).
const { chromium, devices } = require("playwright");
const fs = require("fs");
const BASIS = "http://127.0.0.1:8765/";
const path = require("path");
const HIER = __dirname;
const TESTWORKER = fs.readFileSync(path.join(HIER, "worker-test.js"), "utf8");
const AUSGABE = path.join(HIER, "ausgabe");
fs.mkdirSync(AUSGABE, { recursive: true });
const fotos = ["foto1.jpg", "foto2_exif6.jpg", "foto3.jpg", "foto4_quer.jpg"].map((f) => path.join(HIER, "fotos", f));
const ergebnis = [];
const pruefen = (bedingung, text) => { ergebnis.push((bedingung ? "OK    " : "FEHLER") + "  " + text); };

async function kontext(browser, extra = {}) {
  const k = await browser.newContext({ ...devices["iPhone 13"], acceptDownloads: true, serviceWorkers: "block", ...extra });
  return k;
}
async function testWorker(seite, speicherfehler = false) {
  await seite.route("**/worker.js*", (route) => route.fulfill({ contentType: "text/javascript",
    body: (speicherfehler ? "self.SPEICHERFEHLER=1;\n" : "") + TESTWORKER.replace('self.location.search.includes("speicherfehler")', String(speicherfehler)) }));
}
async function warteBisFertig(seite, anzahl, ms = 240000) {
  await seite.waitForFunction((n) => {
    const karten = document.querySelectorAll(".seite");
    return karten.length === n && [...karten].every((k) => !k.querySelector(".kreisel"));
  }, anzahl, { timeout: ms });
}

(async () => {
  const browser = await chromium.launch();
  try {
    // ---------- 1. Normaler Ablauf ----------
    const k = await kontext(browser);
    const s = await k.newPage();
    s.on("pageerror", (e) => ergebnis.push("JS-FEHLER " + e.message));
    await testWorker(s);
    await s.goto(BASIS);
    await s.waitForSelector("#laden.versteckt", { state: "attached", timeout: 20000 });
    await s.screenshot({ path: path.join(AUSGABE, "01_start.png") });
    pruefen(await s.isVisible("#leer"), "Startbildschirm zeigt Hinweis 'Noch keine Seiten'");

    await s.setInputFiles("#eingabe-fotos", fotos);
    await s.waitForTimeout(800);
    await s.screenshot({ path: path.join(AUSGABE, "02_verarbeitung.png") });
    const zeitStart = Date.now();
    await warteBisFertig(s, 4);
    pruefen(true, `4 Fotos bereinigt (${((Date.now() - zeitStart) / 1000).toFixed(0)} s auf dem Testrechner)`);
    await s.screenshot({ path: path.join(AUSGABE, "03_fertig.png"), fullPage: true });
    const status = await s.$$eval(".seite .status", (e) => e.map((x) => x.textContent));
    pruefen(status.every((t) => t.includes("Blatt entzerrt")), "Alle Seiten: Blatt entzerrt – " + JSON.stringify(status));
    pruefen(status[1].includes("9 Notenzeilen") && !status[1].includes("lag quer"), "EXIF-gedrehtes Foto richtig herum gelesen");
    pruefen(status[3].includes("lag quer"), "Quer fotografiertes Blatt erkannt und gedreht");

    // Reihenfolge ändern: erste Seite nach hinten
    const vorher = await s.$$eval(".seite .status", (e) => e.map((x) => x.textContent));
    await s.click(".seite:nth-child(1) .leiste button[aria-label='Nach hinten']");
    const nachher = await s.$$eval(".seite .status", (e) => e.map((x) => x.textContent));
    pruefen(nachher[0] === vorher[1] && nachher[1] === vorher[0], "Seite verschieben funktioniert");
    // Seite entfernen (4. Seite)
    await s.click(".seite:nth-child(4) .leiste button[aria-label='Seite entfernen']");
    await s.click("#dialog-frage button[value=ja]");
    await s.waitForFunction(() => document.querySelectorAll(".seite").length === 3);
    pruefen(true, "Seite entfernen mit Rückfrage funktioniert");

    // Großansicht
    await s.click(".seite:nth-child(1) .bild");
    await s.waitForTimeout(400);
    await s.screenshot({ path: path.join(AUSGABE, "04_ansicht.png") });
    await s.click("#knopf-umschalten");
    await s.waitForTimeout(400);
    await s.screenshot({ path: path.join(AUSGABE, "05_ansicht_vorher.png") });
    await s.click("#dialog-ansicht button[value=zu]");

    // PDF
    await s.fill("#pdf-name", "Test: 1./2. Stimme");
    await s.dispatchEvent("#pdf-name", "change");
    await s.click("#knopf-pdf");
    await s.waitForSelector("#pdf-ergebnis:not(.versteckt)", { timeout: 120000 });
    const info = await s.textContent("#pdf-info");
    pruefen(/3 Seite\(n\)/.test(info), "PDF erstellt: " + info);
    await s.screenshot({ path: path.join(AUSGABE, "06_pdf.png"), fullPage: true });
    const [download] = await Promise.all([s.waitForEvent("download"), s.click("#knopf-teilen")]);
    await download.saveAs(path.join(AUSGABE, "ergebnis.pdf"));
    pruefen(download.suggestedFilename() === "Test- 1.-2. Stimme.pdf", "Dateiname: " + download.suggestedFilename());

    // Neu laden: alles noch da?
    await s.reload();
    await s.waitForSelector("#laden.versteckt", { state: "attached", timeout: 20000 });
    await s.waitForTimeout(500);
    const nachNeuladen = await s.$$eval(".seite .status", (e) => e.map((x) => x.textContent));
    pruefen(nachNeuladen.length === 3 && nachNeuladen.every((t) => t.includes("Blatt entzerrt")), "Nach Neuladen sind alle 3 Seiten noch da");
    pruefen((await s.inputValue("#pdf-name")) === "Test: 1./2. Stimme", "PDF-Name bleibt gespeichert");

    // Einstellungen: Schwarz/Weiß + alle neu bereinigen
    await s.click("#knopf-einstellungen");
    await s.waitForTimeout(300);
    await s.screenshot({ path: path.join(AUSGABE, "07_einstellungen.png") });
    await s.check("#dialog-einstellungen [name=modus][value=hart]");
    await s.click("#knopf-neu-bereinigen");
    await warteBisFertig(s, 3);
    await s.click("#knopf-pdf");
    await s.waitForSelector("#pdf-ergebnis:not(.versteckt)", { timeout: 120000 });
    const [download2] = await Promise.all([s.waitForEvent("download"), s.click("#knopf-teilen")]);
    await download2.saveAs(path.join(AUSGABE, "ergebnis_hart.pdf"));
    pruefen(true, "Neu bereinigen in Schwarz/Weiß + zweites PDF: " + await s.textContent("#pdf-info"));

    // Neues Stück
    await s.click("#knopf-neu");
    await s.click("#dialog-frage button[value=ja]");
    await s.waitForTimeout(300);
    pruefen((await s.$$(".seite")).length === 0 && await s.isVisible("#leer"), "Neues Stück beginnen leert die Liste");
    await k.close();

    // ---------- 2. Speicherfehler -> automatisch 200 dpi ----------
    const k2 = await kontext(browser);
    const s2 = await k2.newPage();
    await testWorker(s2, true);
    await s2.goto(BASIS);
    await s2.waitForSelector("#laden.versteckt", { state: "attached", timeout: 20000 });
    await s2.setInputFiles("#eingabe-fotos", [fotos[0]]);
    await warteBisFertig(s2, 1);
    const st2 = await s2.textContent(".seite .status");
    pruefen(st2.includes("200 dpi"), "Speicherfehler bei 300 dpi -> automatisch mit 200 dpi: " + st2);
    await k2.close();

    // ---------- 3. Echter Worker ohne Internetzugang zu Pyodide: verständliche Fehlermeldung ----------
    const k3 = await kontext(browser);
    const s3 = await k3.newPage();
    await s3.goto(BASIS);
    await s3.waitForSelector("#laden.fehler", { timeout: 60000 });
    pruefen(true, "Ohne Pyodide-Zugang: " + (await s3.textContent("#laden-text")).slice(0, 110) + " …");
    await s3.screenshot({ path: path.join(AUSGABE, "08_startfehler.png") });
    await k3.close();

    // ---------- 4. Service Worker (Offline-Zwischenspeicher) ----------
    const k4 = await browser.newContext({ ...devices["iPhone 13"], serviceWorkers: "allow" });
    const s4 = await k4.newPage();
    await testWorker(s4);
    await s4.goto(BASIS);
    await s4.evaluate(async () => { await navigator.serviceWorker.ready; });
    await s4.waitForFunction(async () => {
      const name = (await caches.keys()).find((n) => n.startsWith("noten-scanner-app-"));
      return name && (await (await caches.open(name)).keys()).length >= 14 && navigator.serviceWorker.controller;
    }, null, { timeout: 30000 });
    const inhalt = await s4.evaluate(async () => {
      const name = (await caches.keys()).find((n) => n.startsWith("noten-scanner-app-"));
      return (await (await caches.open(name)).keys()).map((r) => new URL(r.url).pathname + new URL(r.url).search);
    });
    pruefen(inhalt.length === 14, "Service Worker speichert " + inhalt.length + " App-Dateien für den Offline-Betrieb");
    await k4.setOffline(true);
    await s4.reload();
    pruefen(await s4.isVisible("h1"), "App startet offline (aus dem Zwischenspeicher)");
    await k4.close();
  } catch (fehler) {
    ergebnis.push("ABBRUCH: " + fehler.message);
  } finally {
    await browser.close();
    console.log(ergebnis.join("\n"));
  }
})();
