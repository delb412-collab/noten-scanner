// Browsertest: Wiederholungen beim Abspielen, Sprung zum angetippten Takt, ganze Takte einfügen/löschen,
// Länge einer Mehrtaktpause, einheitliche Takt-Markierungen.
// Aufruf: node test_takte.js [app-url]   (App vorher bauen und app/test/server.py starten)
const { chromium, devices } = require("playwright");
const fs = require("fs");
const path = require("path");
const TESTWORKER = fs.readFileSync(path.join(__dirname, "worker-test.js"), "utf8");

const BASIS = process.argv[2] || "http://127.0.0.1:8765/";
const viertel = (s, o) => `<note><pitch><step>${s}</step><octave>${o}</octave></pitch><duration>1</duration><type>quarter</type><voice>1</voice></note>`;
const ganze = (s, o) => `<note><pitch><step>${s}</step><octave>${o}</octave></pitch><duration>4</duration><type>whole</type><voice>1</voice></note>`;
const pause = '<note><rest measure="yes"/><duration>4</duration><voice>1</voice></note>';
const XML = `<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Test</part-name></score-part></part-list><part id="P1">
<measure number="1"><barline location="left"><bar-style>heavy-light</bar-style><repeat direction="forward"/></barline><attributes><divisions>1</divisions><key><fifths>0</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>${viertel("C", 5)}${viertel("D", 5)}${viertel("E", 5)}${viertel("F", 5)}</measure>
<measure number="2"><barline location="left"><ending number="1" type="start"/></barline>${ganze("G", 5)}<barline location="right"><bar-style>light-heavy</bar-style><ending number="1" type="stop"/><repeat direction="backward"/></barline></measure>
<measure number="3"><barline location="left"><ending number="2" type="start"/></barline>${ganze("A", 5)}<barline location="right"><ending number="2" type="discontinue"/></barline></measure>
<measure number="4"><attributes><measure-style><multiple-rest>3</multiple-rest></measure-style></attributes>${pause}</measure>
<measure number="5">${pause}</measure><measure number="6">${pause}</measure>
<measure number="7">${ganze("B", 5)}<barline location="right"><bar-style>light-heavy</bar-style></barline></measure>
</part></score-partwise>`;
const TAKTE = [
  { nr: 2, seite: 0, box: [10, 10, 200, 100], status: "unsicher", gruende: ["Notenwert unsicher gelesen"] },
  { nr: 7, seite: 0, box: [300, 10, 500, 100], status: "korrigiert", gruende: ["Automatisch korrigiert: Test"] },
];

const ergebnis = [];
const pruefen = (ok, text) => ergebnis.push((ok ? "OK    " : "FEHLER") + "  " + text);

(async () => {
  const browser = await chromium.launch();
  try {
    const k = await browser.newContext({ ...devices["iPhone 13"], serviceWorkers: "block" });
    const s = await k.newPage();
    s.on("pageerror", (e) => ergebnis.push("JS-FEHLER " + e.message));
    await s.route("**/worker.js*", (route) => route.fulfill({ contentType: "text/javascript", body: TESTWORKER }));
    await s.goto(BASIS);
    await s.waitForFunction(() => typeof Partitur !== "undefined" && typeof vorgang === "function");
    await s.evaluate(async ([xml, takte]) => {
      await vorgang("meta", "readwrite", (st) => st.put({ musicxml: xml, takte, bestaetigt: [], bilder: [],
        meta: { titel: "Takte-Test", stimme: "", instrument: "c", komponist: "" }, quelle: { art: "scan" },
        anzeige: { nach: "c", oktave: "auto" }, geaendert: false }, "partitur"));
      await Partitur.wiederherstellen();
    }, [XML, TAKTE]);
    await s.waitForSelector("#partitur-noten svg", { timeout: 30000 });

    // ---------- Markierungen: gleich hoch, am Notensystem ----------
    const baender = await s.$$eval("rect.takt-hinterlegung", (l) => l.map((r) => {
      const linien = Array.from(r.closest("g.measure").querySelector("g.staff").children).filter((c) => c.tagName.toLowerCase() === "path");
      const o = linien[0].getBBox(), u = linien[linien.length - 1].getBBox();
      return { h: Number(r.getAttribute("height")), system: u.y - o.y, y: Number(r.getAttribute("y")), oben: o.y };
    }));
    pruefen(baender.length === 2 && baender.every((b) => Math.abs(b.h - baender[0].h) < 1 && b.h < 2 * b.system && b.y < b.oben),
            `Markierungen einheitlich hoch (${baender.map((b) => Math.round(b.h)).join(", ")} bei Systemhöhe ${Math.round(baender[0].system)})`);

    // ---------- Abspielen folgt Wiederholung und Volten ----------
    const spiel = await s.evaluate(() => Partitur._abspielNoten().map((x) => ({ m: x.midi, t: Math.round(x.zeit * 10) / 10, id: x.id })));
    const folge = spiel.map((x) => x.m).join(",");
    pruefen(folge === "72,74,76,77,79,72,74,76,77,81,83", "Abspielfolge mit Wiederholung und 2. Volte: " + folge);
    const pauseDauer = spiel[spiel.length - 1].t - spiel[spiel.length - 2].t;
    pruefen(Math.abs(pauseDauer - 4 * 2.4) < 0.2, `nach der 2. Volte drei Takte Pause vor dem Schlusstakt (${pauseDauer.toFixed(1)} s Abstand)`);
    const zweiter = spiel[5];
    pruefen(/-rend\d+$/.test(zweiter.id), "zweiter Durchgang hat eigene Kennungen (" + zweiter.id.replace(/^.*(-rend\d+)$/, "…$1") + ")");

    // Beim Abspielen Takt 2 antippen: Wiedergabe springt dorthin; danach kommt Takt 1 zum zweiten Mal
    // und wird wieder markiert (die Noten des zweiten Durchgangs gibt es im Notenbild nur einmal)
    await s.click("#knopf-abspielen");
    await s.waitForTimeout(400);
    await s.evaluate(() => Array.from(document.querySelectorAll("#partitur-noten g.measure"))[1].dispatchEvent(new MouseEvent("click", { bubbles: true })));
    const spur = [];
    for (let i = 0; i < 36; i++) {
      await s.waitForTimeout(100);
      spur.push(await s.evaluate(() => {
        const el = document.querySelector("#partitur-noten .spielt");
        const takte = Array.from(document.querySelectorAll("#partitur-noten g.measure"));
        return el ? takte.indexOf(el.closest("g.measure")) + 1 : 0;
      }));
    }
    const kurz = spur.filter((t, i) => t && t !== spur[i - 1]).join(" ");
    const zustand = await s.evaluate(() => ({ laeuft: Partitur._spieler.laeuft, editor: document.querySelector("#dialog-takt").open }));
    pruefen(zustand.laeuft && !zustand.editor && spur.slice(2, 8).every((t) => t === 2 || t === 0) && spur.slice(2, 8).includes(2),
            `Antippen beim Abspielen springt zu Takt 2 (Editor bleibt zu: ${!zustand.editor})`);
    pruefen(/^(1 )?2 1/.test(kurz), `nach Takt 2 wird Takt 1 im zweiten Durchgang wieder markiert (Folge der markierten Takte: ${kurz})`);
    await s.click("#knopf-abspielen");

    // ---------- Editor: Takt einfügen, Rückgängig, löschen ----------
    await s.evaluate(() => Array.from(document.querySelectorAll("#partitur-noten g.measure"))[1].dispatchEvent(new MouseEvent("click", { bubbles: true })));
    await s.waitForSelector("#dialog-takt[open]");
    await s.click("#takt-einfuegen");
    await s.waitForTimeout(400);
    let z = await s.evaluate(() => ({ titel: document.querySelector("#takt-titel").textContent, n: NotenXML.takte(Partitur._P.doc).length,
      info: Array.from(Partitur._P.takte.keys()).sort((a, b) => a - b), pausen: document.querySelector("#takt-pausen-zahl").textContent,
      sichtbar: !document.querySelector("#takt-pausen").classList.contains("versteckt") }));
    pruefen(z.titel === "Takt 3" && z.n === 8 && JSON.stringify(z.info) === "[2,8]" && z.sichtbar && z.pausen === "1",
            `Takt eingefügt: ${z.titel}, ${z.n} Takte, Bildausschnitte jetzt bei Takt ${JSON.stringify(z.info)}, Pausenlänge ${z.pausen}`);
    await s.click("#takt-pausen-mehr");
    await s.waitForTimeout(300);
    z = await s.evaluate(() => ({ titel: document.querySelector("#takt-titel").textContent, n: NotenXML.takte(Partitur._P.doc).length,
      info: Array.from(Partitur._P.takte.keys()).sort((a, b) => a - b), mr: Partitur._P.doc.querySelectorAll("multiple-rest").length }));
    pruefen(z.titel === "Takt 3–4" && z.n === 9 && JSON.stringify(z.info) === "[2,9]" && z.mr === 2,
            `Pause auf zwei Takte verlängert: ${z.titel}, ${z.n} Takte, Ausschnitte bei ${JSON.stringify(z.info)}`);
    await s.click("#takt-rueckgaengig");
    await s.click("#takt-rueckgaengig");
    await s.waitForTimeout(300);
    z = await s.evaluate(() => ({ titel: document.querySelector("#takt-titel").textContent, n: NotenXML.takte(Partitur._P.doc).length,
      info: Array.from(Partitur._P.takte.keys()).sort((a, b) => a - b) }));
    pruefen(z.titel === "Takt 2" && z.n === 7 && JSON.stringify(z.info) === "[2,7]", `zweimal Rückgängig: ${z.titel}, ${z.n} Takte, Ausschnitte bei ${JSON.stringify(z.info)}`);
    await s.click("#takt-entfernen");
    await s.waitForTimeout(400);
    z = await s.evaluate(() => ({ titel: document.querySelector("#takt-titel").textContent, n: NotenXML.takte(Partitur._P.doc).length,
      info: Array.from(Partitur._P.takte.keys()).sort((a, b) => a - b), volten: Partitur._P.doc.querySelectorAll("ending").length,
      wdh: Array.from(Partitur._P.doc.querySelectorAll("repeat")).map((r) => r.getAttribute("direction") + r.closest("measure").getAttribute("number")).join(" ") }));
    pruefen(z.n === 6 && JSON.stringify(z.info) === "[6]" && z.wdh === "forward1 backward1",
            `Takt 2 (1. Volte) gelöscht: ${z.n} Takte, Ausschnitt nur noch bei ${JSON.stringify(z.info)}, Wiederholung: ${z.wdh}`);
    // „Ab hier“ startet die Wiedergabe und schließt den Editor
    await s.click("#takt-abspielen");
    await s.waitForTimeout(500);
    z = await s.evaluate(() => ({ offen: document.querySelector("#dialog-takt").open, laeuft: Partitur._spieler.laeuft }));
    pruefen(!z.offen && z.laeuft, `„Ab hier“: Editor zu, Wiedergabe läuft (${z.laeuft})`);
    await s.click("#knopf-abspielen");
  } catch (fehler) {
    ergebnis.push("ABBRUCH: " + fehler.message.split("\n")[0]);
  } finally {
    await browser.close();
    console.log(ergebnis.join("\n"));
  }
})();
