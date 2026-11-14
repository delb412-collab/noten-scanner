// Browsertest: Textangaben und Studierzeichen (Anzeige auch in Mehrtaktpausen, im Takt-Editor ansehen,
// löschen, hinzufügen) und der Titel-Vorschlag vom Seitenkopf (Server-Antwort nachgestellt).
// Aufruf: node test_texte.js [app-url]   (App vorher bauen und app/test/server.py starten)
const { chromium, devices } = require("playwright");
const fs = require("fs");
const path = require("path");
const TESTWORKER = fs.readFileSync(path.join(__dirname, "worker-test.js"), "utf8");

const BASIS = process.argv[2] || "http://127.0.0.1:8765/";
const FOTO = path.join(__dirname, "fotos", "foto1.jpg");
const SERVER = "http://vereinsserver.test";
const ganze = (s) => `<note><pitch><step>${s}</step><octave>5</octave></pitch><duration>4</duration><type>whole</type><voice>1</voice></note>`;
const pause = '<note><rest measure="yes"/><duration>4</duration><voice>1</voice></note>';
const text = (wort, art = "words") => `<direction placement="above"><direction-type><${art}>${wort}</${art}></direction-type></direction>`;
const KOPF = '<attributes><divisions>1</divisions><key><fifths>0</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>';
const stueck = (name, takte) => `<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>${name}</part-name></score-part></part-list><part id="P1">${takte}</part></score-partwise>`;
// Takt 1–3: Mehrtaktpause, Tempo hinter der Pausen-Angabe und ein Studierzeichen im zweiten Pausentakt –
// beides würde der Notensatz ohne Aufbereitung verschlucken. Takt 4: „dolce“ an der Note.
const XML = stueck("Test", `
<measure number="1">${KOPF}<attributes><measure-style><multiple-rest>3</multiple-rest></measure-style></attributes>${text("Lento")}${pause}</measure>
<measure number="2">${text("B", "rehearsal")}${pause}</measure><measure number="3">${pause}</measure>
<measure number="4">${text("dolce")}${ganze("C")}</measure>
<measure number="5">${ganze("D")}<barline location="right"><bar-style>light-heavy</bar-style></barline></measure>`);

const ergebnis = [];
const pruefen = (ok, meldung) => ergebnis.push((ok ? "OK    " : "FEHLER") + "  " + meldung);

(async () => {
  const browser = await chromium.launch();
  try {
    const k = await browser.newContext({ ...devices["iPhone 13"], serviceWorkers: "block" });
    const s = await k.newPage();
    s.on("pageerror", (e) => ergebnis.push("JS-FEHLER " + e.message));
    await s.route("**/worker.js*", (route) => route.fulfill({ contentType: "text/javascript", body: TESTWORKER }));
    await s.addInitScript((srv) => {
      localStorage.setItem("server-adresse", srv);
      localStorage.setItem("server-kennwort", "test");
    }, SERVER);
    await s.goto(BASIS);
    await s.waitForFunction(() => typeof Partitur !== "undefined" && typeof vorgang === "function");
    await s.evaluate(async (xml) => {
      await vorgang("meta", "readwrite", (st) => st.put({ musicxml: xml, takte: [], bestaetigt: [], bilder: [],
        meta: { titel: "Texte-Test", stimme: "", instrument: "c", komponist: "" }, quelle: { art: "scan" },
        anzeige: { nach: "c", oktave: "auto" }, geaendert: false }, "partitur"));
      await Partitur.wiederherstellen();
    }, XML);
    await s.waitForSelector("#partitur-noten svg", { timeout: 30000 });
    const bild = () => s.$eval("#partitur-noten", (n) => ({
      text: Array.from(n.querySelectorAll("g.dir, g.tempo")).map((g) => g.textContent.trim()).join("|"),
      zeichen: Array.from(n.querySelectorAll("g.reh")).map((g) => g.textContent.trim()).join("|") }));

    // ---------- Anzeige ----------
    let b = await bild();
    pruefen(b.text.includes("Lento") && b.text.includes("dolce") && b.zeichen === "B",
            `Texte stehen im Notenbild, auch die in der Mehrtaktpause (Text: ${b.text}; Studierzeichen: ${b.zeichen})`);
    const unveraendert = await s.evaluate(() => {
      const m = NotenXML.takte(Partitur._P.doc);
      return m[1].querySelector("direction") !== null && m[0].lastElementChild.tagName === "note";
    });
    pruefen(unveraendert, "Die gespeicherte Datei bleibt, wie sie war (nur die Anzeige ist aufbereitet)");

    // ---------- Editor: ansehen und löschen ----------
    const takt = (i) => s.evaluate((n) => Array.from(document.querySelectorAll("#partitur-noten g.measure"))[n]
      .dispatchEvent(new MouseEvent("click", { bubbles: true })), i);
    await takt(0);
    await s.waitForSelector("#dialog-takt[open]");
    pruefen((await s.textContent("#takt-texte")) === "Text (2) …", "Mehrtaktpause Takt 1–3: Knopf zeigt " + (await s.textContent("#takt-texte")));
    await takt(1);
    await s.waitForFunction(() => document.querySelector("#takt-titel").textContent === "Takt 4");
    pruefen((await s.textContent("#takt-texte")) === "Text (1) …", "Takt 4: Knopf zeigt " + (await s.textContent("#takt-texte")));
    await s.click("#takt-texte");
    await s.waitForSelector("#dialog-text[open]");
    await s.screenshot({ path: path.join(__dirname, "ausgabe", "30_text_dialog.png") });
    const liste = await s.$$eval("#text-liste li > span", (l) => l.map((x) => x.textContent));
    pruefen(JSON.stringify(liste) === '["dolce"]', "Dialog „" + (await s.textContent("#text-titel")) + "“ listet " + JSON.stringify(liste));
    await s.click("#text-liste li button");
    await s.waitForTimeout(300);
    b = await bild();
    pruefen(!b.text.includes("dolce") && /kein Text/.test(await s.textContent("#text-liste")), "„dolce“ gelöscht: im Notenbild weg, Liste leer");

    // ---------- hinzufügen: Text und Studierzeichen ----------
    await s.fill("#dialog-text [name=neu]", "Trio");
    await s.press("#dialog-text [name=neu]", "Enter");
    await s.waitForSelector("#dialog-text:not([open])", { state: "attached" });
    await s.waitForTimeout(300);
    b = await bild();
    let x = await s.evaluate(() => { const w = NotenXML.takt(Partitur._P.doc, 4).querySelector("words"); return w && w.getAttribute("font-weight"); });
    pruefen(b.text.includes("Trio") && x === "bold", `„Trio“ mit der Eingabetaste eingetragen (fett: ${x === "bold"}), Meldung: ${await s.textContent("#meldung")}`);
    await s.click("#takt-texte");
    await s.fill("#dialog-text [name=neu]", "C");
    await s.click("#dialog-text button[value=ok]");
    await s.waitForTimeout(300);
    b = await bild();
    pruefen(b.zeichen === "B|C", "„C“ wird ein eingerahmtes Studierzeichen (im Bild: " + b.zeichen + ")");
    await s.click("#takt-texte");
    await s.fill("#dialog-text [name=neu]", "rit.");
    await s.click("#dialog-text button[value=nein]");
    await s.waitForTimeout(200);
    pruefen(!(await bild()).text.includes("rit."), "„Schließen“ trägt nichts ein");
    await s.click("#takt-rueckgaengig");
    await s.waitForTimeout(300);
    b = await bild();
    pruefen(b.zeichen === "B" && b.text.includes("Trio"), "Rückgängig nimmt das letzte Zeichen wieder weg");
    await s.screenshot({ path: path.join(__dirname, "ausgabe", "31_editor_mit_text.png") });
    await s.evaluate(() => document.querySelector("#dialog-takt").close());

    // ---------- Titel-Vorschlag vom Blatt ----------
    const antwort = (vorschlag, name) => ({ status: "fertig", text: "Fertig", fortschritt: 1, ergebnis: {
      musicxml: stueck(name, `<measure number="1">${KOPF}${ganze("C")}</measure>`), takte: [], seiten: [{ breite: 100, hoehe: 100 }],
      protokoll: [], vorschlag } });
    let naechste = null, gesendet = null;
    await s.route(SERVER + "/api/**", async (route) => {
      const url = route.request().url();
      const json = (o) => route.fulfill({ contentType: "application/json", headers: { "Access-Control-Allow-Origin": "*" }, body: JSON.stringify(o) });
      if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*" } });
      if (url.endsWith("/api/status")) return json({ bereit: true });
      if (url.endsWith("/api/lesen")) { gesendet = (route.request().postData() || "").match(/name="titel"\r\n\r\n([^\r]*)/); return json({ auftrag: "a1" }); }
      if (url.includes("/api/auftrag/")) return json(naechste);
      return json({});
    });
    const zumStart = () => s.evaluate(() => {
      for (const b of ["#start", "#partitur", "#bibliothek"]) document.querySelector(b).classList.toggle("versteckt", b !== "#start");
    });
    await zumStart();
    await s.setInputFiles("#eingabe-fotos", [FOTO]);
    await s.waitForFunction(() => document.querySelectorAll(".seite").length === 1 && !document.querySelector(".seite .kreisel"), null, { timeout: 120000 });
    const vorbelegt = await s.inputValue("#pdf-name");
    naechste = antwort({ titel: "Lummerland", stimme: "1. Trompete Bb", instrument: "b" }, "1. Trompete Bb");
    await s.click("#knopf-erkennen");
    await s.waitForFunction(() => document.querySelector("#partitur-titel").textContent === "Lummerland", null, { timeout: 30000 });
    let z = await s.evaluate(() => ({ meta: Partitur._P.meta, feld: document.querySelector("#pdf-name").value, meldung: document.querySelector("#meldung").textContent }));
    pruefen(/^Noten \d/.test(vorbelegt) && gesendet && gesendet[1] === "", `Ohne eigenen Namen („${vorbelegt}“ ist nur vorbelegt) geht kein Titel an den Server`);
    pruefen(z.meta.titel === "Lummerland" && z.meta.stimme === "1. Trompete Bb" && z.meta.instrument === "b" && z.feld === "Lummerland",
            `Vorschlag übernommen: ${z.meta.titel} / ${z.meta.stimme} / Instrument ${z.meta.instrument}; Namensfeld: ${z.feld}`);
    pruefen(/Vom Blatt gelesen: „Lummerland“ – 1\. Trompete Bb/.test(z.meldung), "Meldung: " + z.meldung);
    await s.screenshot({ path: path.join(__dirname, "ausgabe", "32_titel_vorschlag.png") });
    // Mit eigenem Namen: der bleibt, die Stimme kommt trotzdem vom Blatt; unbekanntes Instrument -> B
    await zumStart();
    await s.fill("#pdf-name", "Mein Marsch");
    await s.dispatchEvent("#pdf-name", "change");
    naechste = antwort({ titel: "Anderer Titel", stimme: "2. Flügelhorn", instrument: "gibtsnicht" }, "2. Flügelhorn");
    await s.click("#knopf-erkennen");
    await s.waitForFunction(() => Partitur._P.meta.titel === "Mein Marsch" && document.querySelector("#partitur-noten svg"), null, { timeout: 30000 });
    z = await s.evaluate(() => ({ meta: Partitur._P.meta, feld: document.querySelector("#pdf-name").value }));
    pruefen(gesendet && gesendet[1] === "Mein Marsch" && z.meta.titel === "Mein Marsch" && z.meta.stimme === "2. Flügelhorn" && z.meta.instrument === "b" && z.feld === "Mein Marsch",
            `Eigener Name geht vor: ${z.meta.titel} / ${z.meta.stimme} / Instrument ${z.meta.instrument}`);
    // Ohne Vorschlag (alter Server oder kein lesbarer Kopf): wie bisher
    await zumStart();
    await s.fill("#pdf-name", "");
    await s.dispatchEvent("#pdf-name", "change");
    naechste = antwort(undefined, "Voice");
    await s.click("#knopf-erkennen");
    await s.waitForFunction(() => Partitur._P.meta.titel === "Neues Stück" && document.querySelector("#partitur-noten svg"), null, { timeout: 30000 });
    z = await s.evaluate(() => Partitur._P.meta);
    pruefen(z.titel === "Neues Stück" && z.stimme === "", "Ohne Vorschlag bleibt es bei „Neues Stück“");
  } catch (fehler) {
    ergebnis.push("ABBRUCH: " + fehler.message.split("\n")[0]);
  } finally {
    await browser.close();
    console.log(ergebnis.join("\n"));
  }
})();
