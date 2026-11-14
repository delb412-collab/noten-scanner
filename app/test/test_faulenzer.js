// Browsertest: Faulenzer (Taktwiederholung) und Mehrtaktpause in der Partitur-Ansicht.
// Anzeige als „%“, Abspielen spielt den Vortakt, Takt-Vorschau nach einer Mehrtaktpause zeigt den
// richtigen Takt, Bearbeiten eines Faulenzer-Takts schreibt ihn aus.
// Aufruf: node test_faulenzer.js [app-url]   (App vorher bauen und app/test/server.py starten)
const { chromium, devices } = require("playwright");
const fs = require("fs");
const path = require("path");
const TESTWORKER = fs.readFileSync(path.join(__dirname, "worker-test.js"), "utf8");

const BASIS = process.argv[2] || "http://127.0.0.1:8765/";
const viertel = (s, o) => `<note><pitch><step>${s}</step><octave>${o}</octave></pitch><duration>1</duration><type>quarter</type><voice>1</voice></note>`;
const pause = '<note><rest measure="yes"/><duration>4</duration><voice>1</voice></note>';
const XML = `<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Test</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>1</divisions><key><fifths>0</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>${viertel("C", 5)}${viertel("D", 5)}${viertel("E", 5)}${viertel("F", 5)}</measure>
<measure number="2"><attributes><measure-style><measure-repeat type="start" slashes="1">1</measure-repeat></measure-style></attributes>${viertel("C", 5)}${viertel("D", 5)}${viertel("E", 5)}${viertel("F", 5)}</measure>
<measure number="3"><attributes><measure-style><measure-repeat type="stop"/></measure-style><measure-style><multiple-rest>4</multiple-rest></measure-style></attributes>${pause}</measure>
<measure number="4">${pause}</measure><measure number="5">${pause}</measure><measure number="6">${pause}</measure>
<measure number="7">${viertel("A", 4)}${viertel("B", 4)}${viertel("C", 5)}${viertel("D", 5)}</measure>
<measure number="8"><note><pitch><step>E</step><octave>5</octave></pitch><duration>4</duration><type>whole</type><voice>1</voice></note><barline location="right"><bar-style>light-heavy</bar-style></barline></measure>
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
        meta: { titel: "Faulenzer-Test", stimme: "", instrument: "c", komponist: "" }, quelle: { art: "scan" },
        anzeige: { nach: "c", oktave: "auto" }, geaendert: false }, "partitur"));
      await Partitur.wiederherstellen();
    }, XML);
    await s.waitForSelector("#partitur-noten svg", { timeout: 30000 });

    const zeichen = await s.$$eval("#partitur-noten g.mRpt", (l) => l.length);
    pruefen(zeichen === 1, `Faulenzer als Wiederholungszeichen gezeigt (${zeichen})`);

    const spiel = await s.evaluate(() => Partitur._abspielNoten().map((x) => ({ t: Math.round(x.zeit * 100) / 100, m: x.midi })));
    const takt1 = spiel.filter((x) => x.t < 2.4).map((x) => x.m);
    const takt2 = spiel.filter((x) => x.t >= 2.4 && x.t < 4.8).map((x) => x.m);
    pruefen(takt1.length === 4 && JSON.stringify(takt1) === JSON.stringify(takt2),
            `Abspielen: Faulenzer-Takt spielt den Vortakt (${JSON.stringify(takt2)})`);
    const nachPause = spiel.filter((x) => x.t >= 4.8).map((x) => x.m);
    pruefen(nachPause[0] === 69 && Math.min(...spiel.filter((x) => x.m === 69).map((x) => x.t)) > 4.8 * 2,
            `Mehrtaktpause dauert vier Takte, danach a' (${JSON.stringify(nachPause.slice(0, 5))})`);

    // Takt 7 (nach der Mehrtaktpause) im Editor: Vorschau muss genau diesen Takt zeigen
    await s.evaluate(() => {
      const g = Array.from(document.querySelectorAll("#partitur-noten g.measure")).find((x) => x.querySelector("g.note") &&
        Array.from(x.querySelectorAll("g.note")).length === 4 && !x.querySelector(".mRpt") && x !== document.querySelector("#partitur-noten g.measure"));
      g.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await s.waitForSelector("#dialog-takt[open]");
    await s.waitForTimeout(300);
    const titel = await s.textContent("#takt-titel");
    const chips = await s.$$eval("#takt-noten .notenchip .name", (l) => l.map((c) => c.textContent));
    const vorschau = await s.$$eval("#takt-vorschau g.note", (l) => l.length);
    pruefen(titel === "Takt 7" && vorschau === chips.length && chips[0] === "a'",
            `${titel}: Vorschau zeigt ${vorschau} Noten, Auswahl ${JSON.stringify(chips)}`);
    await s.click("#dialog-takt .blatt-kopf button:last-child");

    // Faulenzer-Takt bearbeiten: wird ausgeschrieben
    await s.evaluate(() => {
      const g = document.querySelector("#partitur-noten g.mRpt").closest("g.measure");
      g.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await s.waitForSelector("#dialog-takt[open]");
    const gruende = await s.textContent("#takt-gruende");
    pruefen(/Faulenzer – wie Takt 1/.test(gruende), "Editor meldet: " + gruende.slice(0, 60));
    await s.click("#takt-noten .notenchip >> nth=0");
    await s.click("#takt-hoch");
    await s.waitForTimeout(400);
    const nachher = await s.$$eval("#partitur-noten g.mRpt", (l) => l.length);
    const noten2 = await s.evaluate(() => {
      const takte = Array.from(document.querySelectorAll("#partitur-noten g.measure"));
      return takte[1].querySelectorAll("g.note").length;
    });
    pruefen(nachher === 0 && noten2 === 4, `Nach Änderung ausgeschrieben: ${noten2} Noten, Wiederholungszeichen ${nachher}`);
  } catch (fehler) {
    ergebnis.push("ABBRUCH: " + fehler.message.split("\n")[0]);
  } finally {
    await browser.close();
    console.log(ergebnis.join("\n"));
  }
})();
