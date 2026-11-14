// Ende-zu-Ende-Test: Scan -> Notenerkennung (echter Server) -> Partitur, Korrektur, Abspielen,
// Transponieren, PDF, Bibliothek. Aufruf: node test_partitur.js <foto> [app-url] [server-url]
const { chromium, devices } = require("playwright");
const fs = require("fs");
const path = require("path");

const FOTO = process.argv[2] || "/tmp/alle_ein/how_deep.jpg";
const BASIS = process.argv[3] || "http://127.0.0.1:8765/";
const SERVER = process.argv[4] || "http://127.0.0.1:7861";
const AUSGABE = path.join(__dirname, "ausgabe");
const TESTWORKER = fs.readFileSync(path.join(__dirname, "worker-test.js"), "utf8");
fs.mkdirSync(AUSGABE, { recursive: true });
const ergebnis = [];
const pruefen = (ok, text) => ergebnis.push((ok ? "OK    " : "FEHLER") + "  " + text);

(async () => {
  // UTF-8, sonst nennt Chromium Downloads mit Umlauten nur „download“ (nur im Testcontainer)
  const browser = await chromium.launch({ env: { ...process.env, LANG: "C.UTF-8", LC_ALL: "C.UTF-8" } });
  try {
    const k = await browser.newContext({ ...devices["iPhone 13"], acceptDownloads: true, serviceWorkers: "block" });
    const s = await k.newPage();
    s.on("pageerror", (e) => ergebnis.push("JS-FEHLER " + e.message));
    s.on("console", (m) => { if (m.type() === "error") ergebnis.push("KONSOLE " + m.text().slice(0, 200)); });
    await s.route("**/worker.js*", (route) => route.fulfill({ contentType: "text/javascript", body: TESTWORKER }));
    await s.addInitScript((srv) => {
      localStorage.setItem("server-adresse", srv);
      localStorage.setItem("server-kennwort", "test");
    }, SERVER);
    await s.goto(BASIS);
    await s.waitForSelector("#laden.versteckt", { state: "attached", timeout: 20000 });
    await s.setInputFiles("#eingabe-fotos", [FOTO]);
    await s.waitForFunction(() => document.querySelectorAll(".seite").length === 1 &&
      !document.querySelector(".seite .kreisel"), null, { timeout: 120000 });
    await s.fill("#pdf-name", "How Deep Is Your Love");
    await s.dispatchEvent("#pdf-name", "change");
    pruefen(!(await s.isDisabled("#knopf-erkennen")), "Knopf „Noten erkennen“ ist aktiv");

    // ---------- Erkennung ----------
    const t0 = Date.now();
    await s.click("#knopf-erkennen");
    await s.waitForTimeout(1500);
    await s.screenshot({ path: path.join(AUSGABE, "10_erkennung_laeuft.png") });
    await s.waitForSelector("#partitur-noten svg", { timeout: 240000 });
    pruefen(true, `Noten erkannt und gesetzt (${((Date.now() - t0) / 1000).toFixed(0)} s)`);
    await s.waitForTimeout(500);
    await s.screenshot({ path: path.join(AUSGABE, "11_partitur.png") });
    const takte = await s.$$eval("#partitur-noten g.measure", (l) => l.length);
    const unsicher = await s.$$eval("rect.takt-hinterlegung.unsicher", (l) => l.length);
    const korrigiert = await s.$$eval("rect.takt-hinterlegung.korrigiert", (l) => l.length);
    pruefen(takte > 40, `Partitur zeigt ${takte} Takte; markiert: ${unsicher} unsicher, ${korrigiert} korrigiert`);
    const leiste = await s.textContent("#partitur-pruefen-text");
    pruefen(/Takt/.test(leiste), "Prüfleiste: " + leiste);
    // Sichtbare Vorzeichen: jedes <accidental> der Noten muss als Zeichen im Notenbild stehen
    const vz = await s.evaluate(() => ({
      xml: Partitur._P.doc.querySelectorAll("note > accidental").length,
      gezeichnet: Array.from(document.querySelectorAll("#partitur-noten g.accid")).filter((g) => g.querySelector("use, text, path")).length,
    }));
    pruefen(vz.gezeichnet >= vz.xml, `Vorzeichen außerhalb der Tonart: ${vz.xml} im Notentext, ${vz.gezeichnet} gezeichnet`);

    // ---------- Takt korrigieren ----------
    await s.click("#knopf-pruefen-weiter");
    await s.waitForSelector("#dialog-takt[open]");
    await s.waitForTimeout(800);
    await s.screenshot({ path: path.join(AUSGABE, "12_takt_editor.png") });
    const titel = await s.textContent("#takt-titel");
    const chips = await s.$$eval("#takt-noten .notenchip", (l) => l.map((c) => c.textContent.trim()));
    const leinwand = await s.$eval("#takt-original", (c) => ({ b: c.width, h: c.height, sichtbar: !c.classList.contains("versteckt") }));
    pruefen(chips.length > 0, `${titel}: Noten ${JSON.stringify(chips)}`);
    pruefen(leinwand.sichtbar && leinwand.b > 50, `Original-Ausschnitt ${leinwand.b}×${leinwand.h} px`);
    const vorher = await s.textContent("#takt-gruende");
    await s.click("#takt-noten .notenchip >> nth=0");
    await s.click("#takt-werkzeug [data-wert=whole]");
    await s.waitForTimeout(300);
    const nachher = await s.textContent("#takt-gruende");
    pruefen(nachher !== vorher && /Zu lang|Zu kurz|passt/.test(nachher), "Nach Änderung des Notenwerts: " + nachher.slice(0, 80));
    await s.click("#takt-rueckgaengig");
    await s.waitForTimeout(300);
    pruefen((await s.textContent("#takt-gruende")) === vorher, "Rückgängig stellt den Takt wieder her");
    const notenIndex = chips.findIndex((c) => !c.includes("Pause"));
    await s.click(`#takt-noten .notenchip >> nth=${notenIndex}`);
    const nameVorher = await s.textContent("#takt-noten .notenchip.gewaehlt .name");
    await s.click("#takt-hoch");
    await s.waitForTimeout(200);
    const nameNachher = await s.textContent("#takt-noten .notenchip.gewaehlt .name");
    pruefen(nameVorher !== nameNachher, `Tonhöhe ändern: ${nameVorher} -> ${nameNachher}`);
    await s.click("#takt-rueckgaengig");
    const offenVorher = await s.$$eval("rect.takt-hinterlegung.unsicher", (l) => l.length);
    await s.click("#takt-richtig");
    await s.waitForTimeout(500);
    const offenNachher = await s.$$eval("rect.takt-hinterlegung.unsicher", (l) => l.length);
    pruefen(offenNachher === offenVorher - 1, `„Takt ist richtig“: markierte Takte ${offenVorher} -> ${offenNachher}`);
    await s.screenshot({ path: path.join(AUSGABE, "13_naechster_takt.png") });
    if (await s.isVisible("#dialog-takt[open]")) await s.click("#dialog-takt .blatt-kopf button:last-child");

    // ---------- Abspielen ----------
    await s.click("#knopf-abspielen");
    await s.waitForTimeout(1800);
    const spielt = await s.$$eval("#partitur-noten .spielt", (l) => l.length);
    const audio = await s.evaluate(() => typeof AudioContext !== "undefined");
    pruefen(spielt > 0 && audio, `Abspielen: ${spielt} Note(n) gerade markiert`);
    await s.screenshot({ path: path.join(AUSGABE, "14_abspielen.png") });
    await s.click("#knopf-abspielen");
    pruefen((await s.textContent("#knopf-abspielen")).includes("Abspielen"), "Stopp funktioniert");

    // ---------- Transponieren ----------
    await s.click("#knopf-transponieren");
    await s.selectOption("#dialog-transponieren [name=von]", "b");
    await s.selectOption("#dialog-transponieren [name=nach]", "es");
    await s.click("#dialog-transponieren button[value=ok]");
    await s.waitForTimeout(800);
    const tr = await s.textContent("#partitur-transposition");
    pruefen(/Es-Stimme/.test(tr), "Transponiert: " + tr);
    await s.screenshot({ path: path.join(AUSGABE, "15_transponiert.png") });
    await s.click("#knopf-transponieren");
    await s.selectOption("#dialog-transponieren [name=nach]", "c-bass");
    await s.click("#dialog-transponieren button[value=ok]");
    await s.waitForTimeout(800);
    const bass = await s.evaluate(() => !!document.querySelector("#partitur-noten g.clef use[*|href*='E062'], #partitur-noten g.clef use[href*='E062']"));
    const lage = await s.evaluate(() => {
      // Anteil der Notenköpfe innerhalb des Notensystems (zwischen oberster und unterster Linie ± 1 Linie)
      let drin = 0, alle = 0;
      for (const st of document.querySelectorAll("#partitur-noten g.staff")) {
        const linien = Array.from(st.querySelectorAll(":scope > path")).map((p) => p.getBBox().y);
        if (linien.length < 5) continue;
        const oben = Math.min(...linien), unten = Math.max(...linien), ab = (unten - oben) / 4;
        for (const k of st.querySelectorAll("g.note")) {
          const y = k.getBBox().y + k.getBBox().height / 2;
          alle++;
          if (y >= oben - 1.5 * ab && y <= unten + 1.5 * ab) drin++;
        }
      }
      return alle ? drin / alle : 0;
    });
    pruefen(bass && lage > 0.8, `Bassschlüssel (Posaune): F-Schlüssel, ${(lage * 100).toFixed(0)} % der Noten im System`);
    await s.screenshot({ path: path.join(AUSGABE, "16_bassschluessel.png") });

    // ---------- PDF ----------
    const [dl] = await Promise.all([s.waitForEvent("download", { timeout: 120000 }), s.click("#knopf-partitur-pdf")]);
    const pdfPfad = path.join(AUSGABE, "partitur.pdf");
    await dl.saveAs(pdfPfad);
    const groesse = fs.statSync(pdfPfad).size;
    pruefen(groesse > 10000 && fs.readFileSync(pdfPfad).slice(0, 4).toString() === "%PDF" && /How Deep/.test(dl.suggestedFilename()),
            `PDF (${dl.suggestedFilename()}, ${(groesse / 1024).toFixed(0)} KB)`);

    // zurück zur B-Stimme
    await s.click("#knopf-transponieren");
    await s.selectOption("#dialog-transponieren [name=nach]", "b");
    await s.click("#dialog-transponieren button[value=ok]");

    // ---------- Bibliothek ----------
    await s.click("#knopf-speichern");
    await s.fill("#dialog-speichern [name=stimme]", "3. Flügelhorn");
    await s.fill("#dialog-speichern [name=komponist]", "Bee Gees");
    await s.press("#dialog-speichern [name=komponist]", "Enter");      // Return = Speichern (nicht Abbrechen)
    await s.waitForFunction(() => document.querySelector("#meldung").textContent.includes("gespeichert"), null, { timeout: 30000 });
    pruefen(true, "In der Bibliothek gespeichert");
    await s.click("#partitur-zurueck");             // gespeichertes Stück: zurück führt in die Bibliothek
    await s.waitForSelector("#bibliothek-liste .stueck", { timeout: 30000 });
    await s.screenshot({ path: path.join(AUSGABE, "17_bibliothek.png") });
    const eintrag = await s.textContent("#bibliothek-liste .stueck");
    pruefen(/How Deep/.test(eintrag) && /Flügelhorn/.test(eintrag), "Bibliothek: " + eintrag.replace(/\s+/g, " ").trim());
    await s.fill("#bibliothek-suche", "bee gees");
    pruefen((await s.$$("#bibliothek-liste .stueck")).length >= 1 && !(await s.textContent("#bibliothek-liste")).includes("Marsch"), "Suche nach Komponist findet das Stück");
    await s.fill("#bibliothek-suche", "Marsch");
    pruefen((await s.$$("#bibliothek-liste .stueck")).length === 0, "Suche ohne Treffer");
    await s.fill("#bibliothek-suche", "");
    await s.click("#bibliothek-liste .stueck");
    await s.waitForSelector("#partitur-noten svg", { timeout: 30000 });
    const offenGeladen = await s.$$eval("rect.takt-hinterlegung.unsicher", (l) => l.length);
    pruefen(offenGeladen === offenNachher, `Aus der Bibliothek geöffnet, bestätigte Takte bleiben bestätigt (${offenGeladen} markiert)`);
    await s.click("#knopf-pruefen-weiter");
    await s.waitForSelector("#dialog-takt[open]");
    await s.waitForTimeout(800);
    const bildDa = await s.$eval("#takt-original", (c) => !c.classList.contains("versteckt") && c.width > 50);
    pruefen(bildDa, "Original-Ausschnitt kommt bei Bibliotheksstücken vom Server");
    await s.screenshot({ path: path.join(AUSGABE, "18_bibliothek_takt.png") });
    await k.close();
  } catch (fehler) {
    ergebnis.push("ABBRUCH: " + fehler.message.split("\n")[0]);
  } finally {
    await browser.close();
    console.log(ergebnis.join("\n"));
  }
})();
