// Browsertest: Artikulationen im Takt-Editor setzen, anzeigen und abspielen.
// Aufruf: node test_artikulation.js [app-url]   (App vorher bauen und app/test/server.py starten)
const { chromium, devices } = require("playwright");
const fs = require("fs");
const path = require("path");
const TESTWORKER = fs.readFileSync(path.join(__dirname, "worker-test.js"), "utf8");

const BASIS = process.argv[2] || "http://127.0.0.1:8765/";
const note = (s, o, art = "") => `<note><pitch><step>${s}</step><octave>${o}</octave></pitch><duration>1</duration><type>quarter</type><voice>1</voice>${art ? `<notations><articulations><${art}/></articulations></notations>` : ""}</note>`;
const XML = `<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Test</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>1</divisions><key><fifths>0</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>${note("C", 5)}${note("D", 5, "staccato")}${note("E", 5, "accent")}${note("F", 5)}</measure>
<measure number="2">${note("G", 5)}${note("A", 5)}${note("B", 5)}<note><pitch><step>C</step><octave>6</octave></pitch><duration>1</duration><type>quarter</type><voice>1</voice></note><barline location="right"><bar-style>light-heavy</bar-style></barline></measure>
</part></score-partwise>`;

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
    await s.evaluate(async (xml) => {
      await vorgang("meta", "readwrite", (st) => st.put({ musicxml: xml, takte: [], bestaetigt: [], bilder: [],
        meta: { titel: "Artikulation-Test", stimme: "", instrument: "c", komponist: "" }, quelle: { art: "scan" },
        anzeige: { nach: "c", oktave: "auto" }, geaendert: false }, "partitur"));
      await Partitur.wiederherstellen();
    }, XML);
    await s.waitForSelector("#partitur-noten svg", { timeout: 30000 });

    const vorher = await s.$$eval("#partitur-noten g.artic", (l) => l.length);
    pruefen(vorher === 2, `Notenbild zeigt die gelesenen Artikulationen (${vorher})`);

    // Abspielen: Staccato kürzer, Akzent lauter
    const spiel = await s.evaluate(() => Partitur._abspielNoten().slice(0, 4).map((x) => ({ m: x.midi, kurz: x.kurz || 1, laut: x.lautstaerke || 0 })));
    pruefen(spiel[0].kurz === 1 && spiel[1].kurz === 0.5 && spiel[2].laut > 0 && spiel[3].kurz === 1,
            "Abspielen: Staccato kürzer, Akzent lauter " + JSON.stringify(spiel));

    // Takt 2 öffnen, zweite Note: Tenuto setzen, dann Fermate auf die letzte
    await s.evaluate(() => {
      const g = Array.from(document.querySelectorAll("#partitur-noten g.measure"))[1];
      g.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await s.waitForSelector("#dialog-takt[open]");
    await s.click("#takt-noten .notenchip >> nth=1");
    await s.click('#takt-werkzeug [data-artikulation="tenuto"]');
    await s.waitForTimeout(300);
    const an = await s.$eval('#takt-werkzeug [data-artikulation="tenuto"]', (b) => b.classList.contains("an"));
    const chip = await s.$eval("#takt-noten .notenchip >> nth=1", (c) => (c.querySelector(".art") || {}).textContent || "");
    pruefen(an && chip === "–", `Tenuto gesetzt: Knopf an=${an}, Notenknopf zeigt "${chip}"`);
    await s.click('#takt-werkzeug [data-artikulation="staccato"]');
    await s.click('#takt-werkzeug [data-artikulation="staccatissimo"]');
    await s.waitForTimeout(300);
    const knoepfe = await s.$$eval("#takt-werkzeug [data-artikulation].an", (l) => l.map((b) => b.dataset.artikulation));
    pruefen(JSON.stringify(knoepfe) === JSON.stringify(["tenuto", "staccatissimo"]), "Keil ersetzt Staccato: " + JSON.stringify(knoepfe));
    const nachher = await s.$$eval("#partitur-noten g.artic", (l) => l.length);
    pruefen(nachher === 4, `Notenbild zeigt die neuen Artikulationen (${nachher})`);
    await s.click("#takt-rueckgaengig");
    await s.waitForTimeout(300);
    const zurueck = await s.$$eval("#takt-werkzeug [data-artikulation].an", (l) => l.map((b) => b.dataset.artikulation));
    pruefen(JSON.stringify(zurueck) === JSON.stringify(["staccato", "tenuto"]), "Rückgängig: " + JSON.stringify(zurueck));
    // Pause: nur Fermate wählbar
    await s.click("#takt-pause");
    await s.waitForTimeout(300);
    const frei = await s.$$eval("#takt-werkzeug [data-artikulation]", (l) => l.filter((b) => !b.disabled).map((b) => b.dataset.artikulation));
    pruefen(JSON.stringify(frei) === JSON.stringify(["fermata"]), "Bei einer Pause nur Fermate: " + JSON.stringify(frei));
  } catch (fehler) {
    ergebnis.push("ABBRUCH: " + fehler.message.split("\n")[0]);
  } finally {
    await browser.close();
    console.log(ergebnis.join("\n"));
  }
})();
