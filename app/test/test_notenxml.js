// Tests für notenxml.js und instrumente.js im Browser. Aufruf: node test_notenxml.js
const { chromium } = require("playwright");
const path = require("path");

const XML = `<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Test</part-name></score-part></part-list>
<part id="P1">
<measure number="1"><attributes><divisions>2</divisions><key><fifths>-1</fifths></key><time><beats>2</beats><beat-type>4</beat-type></time>
<clef><sign>G</sign><line>2</line></clef></attributes>
<note><pitch><step>E</step><octave>5</octave></pitch><duration>1</duration><type>eighth</type><voice>1</voice></note>
<note><pitch><step>F</step><octave>5</octave></pitch><duration>1</duration><type>eighth</type><voice>1</voice></note>
<note><pitch><step>G</step><octave>5</octave></pitch><duration>1</duration><type>eighth</type><voice>1</voice></note>
<note><pitch><step>A</step><octave>5</octave></pitch><duration>1</duration><type>eighth</type><voice>1</voice></note>
</measure>
<measure number="2">
<note><pitch><step>B</step><alter>-1</alter><octave>4</octave></pitch><duration>2</duration><type>quarter</type><voice>1</voice></note>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>2</duration><type>quarter</type><voice>1</voice></note>
<note><pitch><step>D</step><octave>5</octave></pitch><duration>2</duration><type>quarter</type><voice>1</voice></note>
</measure>
<measure number="3">
<note><pitch><step>C</step><octave>5</octave></pitch><duration>3</duration><type>quarter</type><dot/><voice>1</voice></note>
<note><pitch><step>D</step><octave>5</octave></pitch><duration>1</duration><type>eighth</type><voice>1</voice></note>
</measure>
</part></score-partwise>`;

(async () => {
  const browser = await chromium.launch();
  const ergebnis = [];
  try {
    const s = await browser.newPage();
    await s.setContent("<html><body></body></html>");
    await s.addScriptTag({ path: path.join(__dirname, "..", "quelle", "notenxml.js") });
    await s.addScriptTag({ path: path.join(__dirname, "..", "quelle", "instrumente.js") });
    const r = await s.evaluate((xml) => {
      const pr = [];
      const gleich = (a, b, text) => pr.push([JSON.stringify(a) === JSON.stringify(b), `${text}: ${JSON.stringify(a)}${JSON.stringify(a) === JSON.stringify(b) ? "" : " (erwartet " + JSON.stringify(b) + ")"}`]);
      const I = Instrumente, N = NotenXML;
      gleich([[4, 7], [-1, -2], [3, 5], [-8, -14], [2, 3], [0, 0], [-5, -9], [-12, -21], [7, 12], [-4, -7]].map(([a, b]) => I.intervallText(a, b)),
             ["P5", "-M2", "P4", "-M9", "m3", "", "-M6", "-M13", "P8", "-P5"], "Intervalle");
      gleich([I.umschreiben("b", "es").text, I.umschreiben("b", "c").text, I.umschreiben("b", "f").text,
              I.umschreiben("b-tief", "c-bass").text, I.umschreiben("es", "b").text, I.umschreiben("b", "b", 1).text],
             ["P5", "-M2", "P4", "-M9", "-P5", "P8"], "Umschreiben B→Es, B→C, B→F, Tenorhorn→Posaune, Es→B, B 8va");
      gleich([N.tonname("B", -1, 4), N.tonname("F", 1, 5), N.tonname("C", 0, 3), N.tonname("A", 0, 2), N.tonname("E", -1, 4),
              N.tonname("B", 0, 4), N.tonname("A", -1, 5), N.tonname("G", 0, 1)],
             ["b'", "fis''", "c", "A", "es'", "h'", "as''", "G,"], "Deutsche Tonnamen");
      const doc = N.laden(xml);
      gleich(N.noten(N.takt(doc, 1)).map((n) => n.dauer), [48, 48, 48, 48], "Dauern auf 96 je Viertel umgerechnet");
      gleich([N.pruefen(doc, 1).ok, N.pruefen(doc, 2).ok, N.pruefen(doc, 2).text, N.pruefen(doc, 3).ok],
             [true, false, "Zu lang um eine Viertel (2/4-Takt)", true], "Taktprüfung");
      N.balkenSetzen(doc);
      const balken = (m) => N.noten(N.takt(doc, m)).map((n) => Array.from(n.el.querySelectorAll("beam")).map((b) => b.getAttribute("number") + ":" + b.textContent).join(","));
      gleich(balken(1), ["1:begin", "1:end", "1:begin", "1:end"], "Achtel im 2/4-Takt je Viertel gebalkt");
      // Takt 2 reparieren: letzte Note löschen
      const t2 = N.noten(N.takt(doc, 2));
      N.loeschen(t2[2].el);
      gleich(N.pruefen(doc, 2).ok, true, "Nach Löschen passt Takt 2");
      // Notenwert ändern: Viertel -> Achtel + Punkt und Triole
      const t3 = N.noten(N.takt(doc, 3));
      N.notenwertSetzen(t3[0].el, "eighth", 1, false);
      N.notenwertSetzen(t3[1].el, "16th", 0, false);
      N.balkenTakt(N.takt(doc, 3), N.zustandBei(doc, 3));
      gleich(N.noten(N.takt(doc, 3)).map((n) => [n.typ, n.punkte, n.dauer]), [["eighth", 1, 72], ["16th", 0, 24]], "Punktierte Achtel + Sechzehntel");
      gleich(balken(3), ["1:begin", "1:end,2:backward hook"], "Balken mit Sechzehntel-Fähnchen");
      gleich(N.pruefen(doc, 3).text, "Auftakt bzw. Teiltakt (2/4)", "Letzter Takt darf kürzer sein (Ergänzung zum Auftakt)");
      N.loeschen(N.noten(N.takt(doc, 2))[1].el);
      gleich(N.pruefen(doc, 2).text, "Zu kurz um eine Viertel (2/4-Takt)", "Takt in der Mitte zu kurz");
      N.einfuegenNach(N.noten(N.takt(doc, 2))[0].el);
      N.notenwertSetzen(t3[0].el, "eighth", 0, true);
      gleich(N.info(t3[0].el).dauer, 32, "Triolenachtel = 32");
      // Tonhöhe: E5 einen Schritt hoch in F-Dur -> F5; h -> b nach Tonart
      const e5 = N.noten(N.takt(doc, 1))[0];
      N.stufe(e5.el, 1, -1);
      const f5 = N.info(e5.el);
      N.stufe(e5.el, 1, -1);
      N.stufe(e5.el, 1, -1);
      N.stufe(e5.el, 1, -1);
      const b5 = N.info(e5.el);
      gleich([f5.schritt + f5.oktave, b5.schritt + b5.alter + "/" + b5.oktave], ["F5", "B-1/5"], "Stufen hoch, Vorzeichen nach Tonart (F-Dur: b)");
      N.vorzeichen(e5.el, 0);
      gleich(N.info(e5.el).alter, 0, "Auflösungszeichen");
      // Pause und zurück
      N.pauseUmschalten(e5.el);
      const p = N.info(e5.el);
      N.pauseUmschalten(e5.el, { schritt: "G", oktave: 4, alter: 0, pause: false });
      gleich([p.pause, N.info(e5.el).schritt + N.info(e5.el).oktave], [true, "G4"], "Note -> Pause -> Note");
      // Einfügen
      const anzahl = N.noten(N.takt(doc, 1)).length;
      N.einfuegenNach(e5.el);
      gleich(N.noten(N.takt(doc, 1)).length, anzahl + 1, "Note einfügen");
      // Schlüssel und Titel
      const kopie = doc.cloneNode(true);
      N.schluesselSetzen(kopie, "F", 4);
      N.titelSetzen(kopie, "Marsch", "Tuba");
      gleich([kopie.querySelector("clef sign").textContent, N.titelLesen(kopie), kopie.querySelector("part-name").textContent],
             ["F", "Marsch", "Tuba"], "Schlüssel und Titel");
      // Text hin und zurück
      const wieder = N.laden(N.alsText(doc));
      gleich(N.noten(N.takt(wieder, 1)).length, anzahl + 1, "Speichern und wieder laden");

      // ---------- Sichtbare Vorzeichen (homr schreibt nur <alter>, Verovio braucht <accidental>) ----------
      const V = (inhalt, fifths = 0, extra = "") => N.laden(`<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>T</part-name></score-part></part-list>
<part id="P1"><measure number="1"${extra}><attributes><divisions>1</divisions><key><fifths>${fifths}</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time></attributes>${inhalt}</measure>
<measure number="2"><note><pitch><step>F</step><octave>4</octave><alter>1</alter></pitch><duration>4</duration><type>whole</type></note></measure></part></score-partwise>`);
      const n = (s, o, a) => `<note><pitch><step>${s}</step><octave>${o}</octave>${a !== undefined ? `<alter>${a}</alter>` : ""}</pitch><duration>1</duration><type>quarter</type></note>`;
      const zeichen = (d) => N.takte(d).map((m) => N.noten(m).map((x) => (x.el.querySelector("accidental") || { textContent: "-" }).textContent));
      let d1 = V(n("F", 4, 1) + n("F", 4, 1) + n("F", 4) + n("F", 5, 1));
      gleich(d1.querySelector("pitch").firstElementChild.nextElementSibling.tagName, "alter", "Reihenfolge step, alter, octave hergestellt");
      N.vorzeichenSetzen(d1);
      gleich(zeichen(d1), [["sharp", "-", "natural", "sharp"], ["sharp"]], "Vorzeichen gelten bis Taktende, je Oktave, neuer Takt neu");
      let d2 = V(n("F", 4, 1) + n("B", 4, -1) + n("B", 4) + n("C", 5), 1);
      N.vorzeichenSetzen(d2);
      gleich(zeichen(d2)[0], ["-", "flat", "natural", "-"], "G-Dur: Fis ohne Zeichen, B mit b, danach H mit Auflösung");
      // Stufe: Vorzeichen, das im Takt schon gilt, wird übernommen
      let d3 = V(n("F", 4, 1) + n("G", 4) + n("A", 4) + n("B", 4));
      const t3b = N.noten(N.takt(d3, 1));
      N.stufe(t3b[1].el, -1, 0);
      gleich(N.info(t3b[1].el).alter, 1, "G eine Stufe tiefer nach Fis im selben Takt -> Fis");
      N.stufe(t3b[2].el, -1, 0);
      N.stufe(t3b[2].el, -1, 0);
      gleich([N.info(t3b[2].el).schritt, N.info(t3b[2].el).alter], ["F", 1], "auch zwei Stufen tiefer -> Fis");
      // Taktprüfung: Auftakt (implicit) darf kürzer sein, mittendrin nicht
      let d4 = V(n("C", 5) + n("D", 5), 0, ' implicit="yes"');
      gleich(N.pruefen(d4, 1).ok, true, "Auftakt (implicit) darf kürzer sein");
      // Ganztaktpause -> Note bekommt einen Notenwert
      let d5 = N.laden(`<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>T</part-name></score-part></part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes>${n("C", 5)}${n("D", 5)}${n("E", 5)}${n("F", 5)}</measure>
<measure number="2"><note><rest measure="yes"/><duration>4</duration></note></measure></part></score-partwise>`);
      const p5 = N.noten(N.takt(d5, 2))[0];
      gleich([p5.ganztakt, N.beschriftung(p5).wert], [true, "Takt"], "Ganztaktpause erkannt und beschriftet");
      N.pauseUmschalten(p5.el, { schritt: "G", oktave: 4, alter: 0, pause: false });
      gleich([N.info(p5.el).typ, N.info(p5.el).dauer], ["whole", 384], "Ganztaktpause -> Ganze Note");

      // ---------- Faulenzer (Taktwiederholung) ----------
      const F = N.laden(`<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>T</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes>${n("C", 5)}${n("D", 5)}${n("E", 5)}${n("F", 5)}</measure>
<measure number="2"><attributes><measure-style><measure-repeat type="start" slashes="1">1</measure-repeat></measure-style></attributes>${n("C", 5)}${n("D", 5)}${n("E", 5)}${n("F", 5)}</measure>
<measure number="3">${n("C", 5)}${n("D", 5)}${n("E", 5)}${n("F", 5)}</measure>
<measure number="4"><attributes><measure-style><measure-repeat type="stop"/></measure-style></attributes>${n("G", 5)}${n("G", 5)}${n("G", 5)}${n("G", 5)}</measure></part></score-partwise>`);
      gleich([1, 2, 3, 4].map((t) => N.faulenzer(F, t)), [null, "1", "2", null], "Faulenzer-Takte erkannt (auch der zweite in Folge)");
      gleich(N.pruefen(F, 2).text, "Faulenzer – wie Takt 1", "Taktprüfung meldet den Faulenzer");
      N.stufe(N.noten(N.takt(F, 1))[0].el, 1, 0);       // Vortakt ändern: C5 -> D5
      N.faulenzerAktualisieren(F);
      gleich([2, 3].map((t) => N.noten(N.takt(F, t))[0].schritt), ["D", "D"], "Wiederholungen folgen dem geänderten Vortakt");
      gleich(N.faulenzerAufloesen(F, 3), true, "Faulenzer im geänderten Takt aufgelöst");
      gleich([N.faulenzer(F, 2), N.faulenzer(F, 3), F.querySelectorAll("measure-repeat").length], [null, null, 0],
             "danach keine Taktwiederholung mehr, Noten bleiben");
      gleich(N.noten(N.takt(F, 3)).length, 4, "ausgeschriebene Noten vorhanden");

      // Artikulationen von Hand
      const A = N.laden(xml);
      const a0 = N.noten(N.takt(A, 2))[0].el;
      gleich([N.artikulationUmschalten(a0, "staccato"), N.artikulationen(a0)], [true, ["staccato"]], "Staccato gesetzt");
      gleich(N.artikulationUmschalten(a0, "tenuto"), true, "Tenuto dazu (Portato erlaubt)");
      gleich(N.artikulationen(a0), ["staccato", "tenuto"], "Staccato und Tenuto zusammen");
      N.artikulationUmschalten(a0, "staccatissimo");
      gleich(N.artikulationen(a0), ["tenuto", "staccatissimo"], "Keil ersetzt den Punkt");
      N.artikulationUmschalten(a0, "accent"); N.artikulationUmschalten(a0, "strong-accent");
      gleich(N.artikulationen(a0), ["tenuto", "strong-accent", "staccatissimo"], "Marcato ersetzt den Akzent");
      N.artikulationUmschalten(a0, "fermata");
      gleich(a0.querySelector("notations > fermata") !== null && a0.querySelector("articulations > fermata") === null, true,
             "Fermate direkt in <notations>");
      gleich(N.beschriftung(N.info(a0)).zeichen, "– ^ ▾ 𝄐", "Zeichen im Notenknopf");
      for (const x of ["tenuto", "strong-accent", "staccatissimo", "fermata"]) N.artikulationUmschalten(a0, x);
      gleich([N.artikulationen(a0), a0.querySelector("notations") === null], [[], true], "alles wieder aus, <notations> entfernt");
      const kinderVor = Array.from(a0.children).map((k) => k.tagName);
      N.artikulationUmschalten(a0, "accent");
      const reihe = Array.from(a0.children).map((k) => k.tagName);
      gleich(reihe.indexOf("notations") > reihe.indexOf("type") && reihe.length === kinderVor.length + 1, true,
             "<notations> an der richtigen Stelle (" + reihe.join(",") + ")");
      N.pauseUmschalten(a0, null);
      gleich(N.artikulationen(a0), [], "Pause statt Note: Akzent entfernt");
      gleich([N.artikulationUmschalten(a0, "staccato"), N.artikulationUmschalten(a0, "fermata"), N.artikulationen(a0)],
             [false, true, ["fermata"]], "Pause: nur Fermate möglich");
      gleich(N.laden(N.alsText(A)).querySelectorAll("fermata").length, 1, "Text hin und zurück");

      {
      // Ganze Takte: einfügen, löschen, Länge einer Mehrtaktpause
      const gz = (s_, o) => `<note><pitch><step>${s_}</step><octave>${o}</octave></pitch><duration>4</duration><type>whole</type><voice>1</voice></note>`;
      const gp = '<note><rest measure="yes"/><duration>4</duration><voice>1</voice></note>';
      const T = () => N.laden(`<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0"><part-list><score-part id="P1"><part-name>T</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>1</divisions><key><fifths>-1</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>${gz("C", 5)}</measure>
<measure number="2"><barline location="left"><bar-style>heavy-light</bar-style><repeat direction="forward"/></barline><attributes><key><fifths>2</fifths></key><measure-style><multiple-rest>3</multiple-rest></measure-style></attributes>${gp}</measure>
<measure number="3">${gp}</measure>
<measure number="4">${gp}<barline location="right"><bar-style>light-heavy</bar-style><repeat direction="backward"/></barline></measure>
<measure number="5"><print new-system="yes"/>${gz("D", 5)}</measure>
<measure number="6">${gz("E", 5)}<barline location="right"><bar-style>light-heavy</bar-style></barline></measure>
</part></score-partwise>`);
      const nummern = (d) => N.takte(d).map((m) => m.getAttribute("number")).join(",");
      const striche = (d) => N.takte(d).map((m) => Array.from(m.querySelectorAll("barline")).map((b) =>
        b.getAttribute("location")[0] + (b.querySelector("repeat") ? (b.querySelector("repeat").getAttribute("direction") === "forward" ? "|:" : ":|") : "") +
        (b.querySelector("bar-style") ? "" : "") ).join("")).join(";");
      let D = T();
      gleich([N.taktBlock(D, 3), N.pausenLaenge(D, 2), N.pausenLaenge(D, 5)], [{ start: 1, ende: 3 }, 3, 0], "Mehrtaktpause ist ein Block");
      let erg = N.taktEinfuegenNach(D, 2);
      gleich([erg.nr, nummern(D), erg.karte.get(5), erg.karte.get(4)], [5, "1,2,3,4,5,6,7", 6, 4],
             "Takt hinter der Mehrtaktpause eingefügt, Folgetakte rücken eine Nummer weiter");
      gleich([N.noten(N.takt(D, 5))[0].ganztakt, striche(D).split(";")[3]], [true, "r:|"], "neuer Takt ist eine Ganztaktpause, Wiederholungsende bleibt vor ihm");
      D = T(); erg = N.taktEinfuegenNach(D, 6);
      gleich([erg.nr, striche(D).split(";")[5], striche(D).split(";")[6]], [7, "", "r"], "am Ende eingefügt: Schlussstrich wandert an den neuen letzten Takt");
      D = T(); erg = N.taktLoeschen(D, 3);
      gleich([nummern(D), erg.nr, erg.karte.get(5), N.noten(N.takt(D, 2))[0].schritt], ["1,2,3", 2, 2, "D"], "Mehrtaktpause als Ganzes gelöscht");
      gleich([N.zustandBei(D, 2).fifths, striche(D), N.takt(D, 2).querySelector("print") !== null], [2, ";;r", true],
             "Tonart und Zeilenumbruch des gelöschten Blocks bleiben; die Wiederholung, die nur aus ihm bestand, entfällt");
      D = T(); N.takt(D, 4).querySelector("barline").remove(); N.taktLoeschen(D, 2);
      gleich(striche(D), ";l|:;r", "Wiederholungsanfang des gelöschten Blocks wandert an den Folgetakt");
      D = T(); erg = N.taktLoeschen(D, 1);
      gleich([nummern(D), N.zustandBei(D, 1).fifths, N.takt(D, 1).querySelector("attributes > clef") !== null, N.takt(D, 1).querySelector("attributes > time") !== null],
             ["1,2,3,4,5", 2, true, true], "ersten Takt gelöscht: Schlüssel und Taktart wandern weiter, eigene Tonart des Folgetakts bleibt");
      D = T(); erg = N.mehrtaktpauseSetzen(D, 2, 5);
      gleich([nummern(D), N.pausenLaenge(D, 2), erg.karte.get(5), striche(D).split(";")[5], striche(D).split(";")[3]], ["1,2,3,4,5,6,7,8", 5, 7, "r:|", ""],
             "Mehrtaktpause verlängert: Wiederholungsende am neuen letzten Pausentakt");
      erg = N.mehrtaktpauseSetzen(D, 2, 1);
      gleich([nummern(D), N.pausenLaenge(D, 2), D.querySelectorAll("multiple-rest").length, striche(D).split(";")[1]], ["1,2,3,4", 1, 0, "l|:r:|"],
             "auf einen Takt gekürzt: keine Mehrtaktpause mehr, beide Zeichen am selben Takt");
      erg = N.mehrtaktpauseSetzen(D, 2, 2);
      gleich([nummern(D), N.pausenLaenge(D, 2), D.querySelector("multiple-rest").textContent], ["1,2,3,4,5", 2, "2"], "aus einer Ganztaktpause wird wieder eine Mehrtaktpause");
      gleich(N.mehrtaktpauseSetzen(D, 1, 3), null, "Takt mit Noten: keine Pausenlänge");
      gleich(N.taktLoeschen(N.laden(`<score-partwise><part-list><score-part id="P1"><part-name>T</part-name></score-part></part-list><part id="P1"><measure number="1">${gp}</measure></part></score-partwise>`), 1), null,
             "der letzte Takt lässt sich nicht löschen");
      // Anzeige: Volta-Ende am Ende einer Mehrtaktpause wandert für Verovio an den ersten Takt
      const V = N.laden(`<score-partwise><part-list><score-part id="P1"><part-name>T</part-name></score-part></part-list><part id="P1">
<measure number="1"><barline location="left"><ending number="1" type="start"/></barline><attributes><divisions>1</divisions><measure-style><multiple-rest>3</multiple-rest></measure-style></attributes>${gp}</measure>
<measure number="2">${gp}</measure>
<measure number="3">${gp}<barline location="right"><ending number="1" type="stop"/><repeat direction="backward"/></barline></measure>
<measure number="4"><barline location="left"><ending number="2" type="start"/></barline>${gz("C", 5)}</measure></part></score-partwise>`);
      const A2 = N.fuerVerovio(V);
      gleich([A2 !== V, N.takt(A2, 1).querySelectorAll('barline[location="right"] > ending').length, N.takt(A2, 3).querySelectorAll("ending").length,
              N.takt(A2, 3).querySelectorAll("repeat").length, N.takt(V, 3).querySelectorAll("ending").length], [true, 1, 0, 1, 1],
             "Anzeige-Kopie: Volta-Ende am ersten Takt, Wiederholung bleibt, Original unverändert");
      const ohneName = T(); ohneName.querySelector("part-name").textContent = "";
      gleich(N.fuerVerovio(ohneName) === ohneName, true, "ohne solche Stelle keine Kopie");
      // Name der Stimme: als Zeile unter dem Titel statt als Beschriftung neben der ersten Zeile
      const S = T(); N.titelSetzen(S, "Marsch", "1. Trompete in B");
      const A4 = N.fuerVerovio(S);
      gleich([A4 !== S, A4.querySelector("work > work-number").textContent, A4.querySelector("work").firstElementChild.tagName,
              A4.querySelector("part-name").getAttribute("print-object"), S.querySelector("work-number"), S.querySelector("part-name").getAttribute("print-object"),
              N.fuerVerovio(A4) === A4],
             [true, "1. Trompete in B", "work-number", "no", null, null, true],
             "Anzeige-Kopie: Stimme als Untertitel, Beschriftung der Zeile aus; Original unverändert");
      // Texte und Studierzeichen
      const wort = (t, art = "words") => `<direction placement="above"><direction-type><${art}>${t}</${art}></direction-type></direction>`;
      const X = N.laden(`<score-partwise><part-list><score-part id="P1"><part-name>T</part-name></score-part></part-list><part id="P1">
<measure number="1"><attributes><divisions>1</divisions><measure-style><multiple-rest>2</multiple-rest></measure-style></attributes>${wort("Lento")}${gp}</measure>
<measure number="2">${wort("B", "rehearsal")}${gp}</measure>
<measure number="3"><direction><direction-type><dynamics><f/></dynamics></direction-type></direction>${gz("C", 5)}<note><chord/><pitch><step>E</step><octave>5</octave></pitch><duration>4</duration><type>whole</type><voice>1</voice></note></measure></part></score-partwise>`);
      gleich(N.texte(X, 2).map((t) => t.text + (t.zeichen ? "*" : "")), ["Lento", "B*"], "Texte einer Mehrtaktpause: alle Takte des Blocks, Studierzeichen erkannt");
      gleich(N.texte(X, 3), [], "Lautstärke-Zeichen zählen nicht als Text");
      const A3 = N.fuerVerovio(X);
      gleich([A3 !== X, Array.from(N.takt(A3, 1).children).map((k) => k.tagName).join(" "), N.takt(A3, 2).querySelector("direction") === null,
              N.takt(X, 2).querySelector("direction") !== null, N.fuerVerovio(A3) === A3],
             [true, "direction direction attributes note", true, true, true],
             "Anzeige-Kopie: Texte der Mehrtaktpause stehen vor der Pausen-Angabe, Original unverändert");
      const akkordton = N.takt(X, 3).querySelectorAll("note")[1];
      N.textEinfuegen(X, 3, "  Trio ", akkordton);
      N.textEinfuegen(X, 3, "dolce", null);
      N.textEinfuegen(X, 3, "12", null);
      gleich(Array.from(N.takt(X, 3).children).map((k) => k.tagName + (k.querySelector("words, rehearsal") ? ":" + k.querySelector("words, rehearsal").textContent : "")).join(" "),
             "direction direction:Trio direction:dolce direction:12 note note", "Text steht vor der Hauptnote, auch wenn ein Akkordton gewählt war");
      gleich(N.texte(X, 3).map((t) => [t.text, t.zeichen, t.el.querySelector("words") ? t.el.querySelector("words").getAttribute("font-weight") || t.el.querySelector("words").getAttribute("font-style") : "Rahmen"].join("/")),
             ["Trio/false/bold", "dolce/false/italic", "12/true/Rahmen"], "großer Anfang fett, kleiner kursiv, Zahl als Studierzeichen");
      N.textEntfernen(N.texte(X, 3)[1].el);
      gleich([N.texte(X, 3).map((t) => t.text).join(","), N.textEinfuegen(X, 3, "   ", null), N.textEinfuegen(X, 99, "x", null)], ["Trio,12", null, null],
             "Text entfernt; leerer Text und unbekannter Takt werden abgelehnt");
      gleich(["A", "B2", "17", "AB", "Trio", "a", "1234", "A."].map(N.istStudierzeichen), [true, true, true, true, false, false, false, false], "was als Studierzeichen gilt");
      }
      return pr;
    }, XML);
    for (const [ok, text] of r) ergebnis.push((ok ? "OK    " : "FEHLER") + "  " + text);
  } catch (fehler) {
    ergebnis.push("ABBRUCH: " + fehler.message);
  } finally {
    await browser.close();
    console.log(ergebnis.join("\n"));
  }
})();
