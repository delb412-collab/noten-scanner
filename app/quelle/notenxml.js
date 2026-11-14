/* Noten-Scanner – Bearbeiten von MusicXML im Browser.
 * Laden/Speichern, Taktprüfung, Ändern einzelner Noten, Balken nach Taktart, deutsche Tonnamen.
 * Alle Dauern werden beim Laden auf 96 Einheiten je Viertel umgerechnet, damit auch
 * Triolen und Zweiunddreißigstel ganzzahlig bleiben. */
"use strict";

const NotenXML = (() => {
  const TEILE = 96;                                   // divisions je Viertel
  const WERTE = { whole: 4, half: 2, quarter: 1, eighth: 0.5, "16th": 0.25, "32nd": 0.125, "64th": 0.0625 };
  const FAEHNCHEN = { eighth: 1, "16th": 2, "32nd": 3, "64th": 4 };
  const STUFEN = ["C", "D", "E", "F", "G", "A", "B"];
  const KREUZE = "FCGDAEB";

  function laden(text) {
    const doc = new DOMParser().parseFromString(text, "application/xml");
    if (doc.querySelector("parsererror")) throw new Error("Die Noten-Datei ist beschädigt");
    normalisieren(doc);
    return doc;
  }

  function alsText(doc) {
    return new XMLSerializer().serializeToString(doc);
  }

  function kinder(el, name) {
    return Array.from(el.children).filter((k) => k.tagName === name);
  }

  function kind(el, name) {
    return Array.from(el.children).find((k) => k.tagName === name) || null;
  }

  function text(el, name, standard = "") {
    const k = el && kind(el, name);
    return k ? k.textContent.trim() : standard;
  }

  function setzeText(el, name, wert, vor = null) {
    let k = kind(el, name);
    if (!k) {
      k = el.ownerDocument.createElement(name);
      const vorEl = vor && vor.map((n) => kind(el, n)).find(Boolean);
      el.insertBefore(k, vorEl || null);
    }
    k.textContent = String(wert);
    return k;
  }

  /* ---------- Struktur ---------- */

  function teil(doc) {
    return doc.querySelector("part");
  }

  function takte(doc) {
    const t = teil(doc);
    return t ? kinder(t, "measure") : [];
  }

  function takt(doc, nr) {
    return takte(doc).find((m) => m.getAttribute("number") === String(nr)) || null;
  }

  /** Dauern auf TEILE je Viertel umrechnen (divisions kann sich im Stück ändern). */
  function normalisieren(doc) {
    let faktor = TEILE;                               // MusicXML-Standard ohne Angabe: divisions = 1
    let gesehen = false;
    for (const m of takte(doc)) {
      for (const attr of kinder(m, "attributes")) {
        const div = kind(attr, "divisions");
        if (div) {
          faktor = TEILE / (Number(div.textContent) || 1);
          div.textContent = String(TEILE);
          gesehen = true;
        }
      }
      if (!gesehen) {
        let attr = kind(m, "attributes");
        if (!attr) { attr = m.ownerDocument.createElement("attributes"); m.insertBefore(attr, m.firstChild); }
        attr.insertBefore(Object.assign(m.ownerDocument.createElement("divisions"), { textContent: String(TEILE) }), attr.firstChild);
        gesehen = true;
      }
      for (const el of m.querySelectorAll("note > duration, backup > duration, forward > duration")) {
        el.textContent = String(Math.round(Number(el.textContent) * faktor));
      }
      for (const p of m.querySelectorAll("note > pitch")) {   // MusicXML: step, alter, octave
        const a = kind(p, "alter"), o = kind(p, "octave");
        if (a && o && a.previousElementSibling === o) p.insertBefore(a, o);
      }
    }
  }

  /** Taktart, Tonart und Schlüssel, die in Takt nr gelten. */
  function zustandBei(doc, nr) {
    const z = { beats: 4, beatType: 4, fifths: 0, schluessel: "G2" };
    for (const m of takte(doc)) {
      for (const attr of kinder(m, "attributes")) {
        const t = kind(attr, "time");
        if (t) { z.beats = Number(text(t, "beats", "4")); z.beatType = Number(text(t, "beat-type", "4")); }
        const k = kind(attr, "key");
        if (k) z.fifths = Number(text(k, "fifths", "0"));
        const c = kind(attr, "clef");
        if (c) z.schluessel = text(c, "sign", "G") + text(c, "line", "2");
      }
      if (m.getAttribute("number") === String(nr)) break;
    }
    return z;
  }

  function tonartAlter(fifths, schritt) {
    if (fifths > 0) return KREUZE.slice(0, fifths).includes(schritt) ? 1 : 0;
    if (fifths < 0) return KREUZE.split("").reverse().join("").slice(0, -fifths).includes(schritt) ? -1 : 0;
    return 0;
  }

  /* ---------- Noten lesen ---------- */

  function info(note) {
    const p = kind(note, "pitch");
    const tm = kind(note, "time-modification");
    return {
      el: note,
      pause: !!kind(note, "rest"),
      schritt: p ? text(p, "step") : null,
      oktave: p ? Number(text(p, "octave")) : null,
      alter: p ? Number(text(p, "alter", "0")) : 0,
      typ: text(note, "type", "quarter"),
      punkte: kinder(note, "dot").length,
      triole: tm ? Number(text(tm, "actual-notes")) === 3 && Number(text(tm, "normal-notes")) === 2 : false,
      akkord: !!kind(note, "chord"),
      vorschlag: !!kind(note, "grace"),
      stimme: text(note, "voice", "1"),
      dauer: Number(text(note, "duration", "0")),
      ganztakt: !!(kind(note, "rest") && kind(note, "rest").getAttribute("measure") === "yes"),
    };
  }

  function noten(m) {
    return kinder(m, "note").map(info);
  }

  /** Taktprüfung: Dauer je Stimme gegen die Taktart. */
  function pruefen(doc, nr) {
    const m = takt(doc, nr);
    if (!m) return { ok: true, text: "" };
    const z = zustandBei(doc, nr);
    const soll = Math.round(TEILE * 4 * z.beats / z.beatType);
    if (m.querySelector("multiple-rest")) return { ok: true, soll, text: "Mehrtaktpause" };
    const wie = faulenzer(doc, nr);
    if (wie !== null) return { ok: true, soll, text: `Faulenzer – wie Takt ${wie}` };
    const stimmen = {};
    let nurGanzpause = true;
    for (const n of noten(m)) {
      if (!n.ganztakt) nurGanzpause = false;
      if (n.akkord || n.vorschlag) continue;
      stimmen[n.stimme] = (stimmen[n.stimme] || 0) + n.dauer;
    }
    if (nurGanzpause) return { ok: true, soll, text: "Ganztaktpause" };
    // Kürzer erlaubt: Auftakt (implicit), erster/letzter Takt, neue Taktart, an Wiederholung/Doppelstrich
    const alle = takte(doc), k = alle.indexOf(m);
    const strich = (t) => t && Array.from(t.querySelectorAll("barline")).some((b) =>
      b.querySelector("repeat") || ["light-light", "light-heavy", "heavy-light"].includes(text(b, "bar-style")));
    const grenze = m.getAttribute("implicit") === "yes" || k === 0 || k === alle.length - 1 ||
      !!m.querySelector("attributes > time") || strich(m) || strich(alle[k - 1]);
    const fehler = [];
    let teiltakt = false;
    for (const [v, ist] of Object.entries(stimmen)) {
      if (ist < soll && grenze) { teiltakt = true; continue; }
      if (ist !== soll) {
        const wer = Object.keys(stimmen).length > 1 ? (v === Object.keys(stimmen).sort()[0] ? " (obere Stimme)" : " (untere Stimme)") : "";
        fehler.push(`${ist > soll ? "Zu lang" : "Zu kurz"} um ${dauerName(Math.abs(ist - soll))}${wer}`);
      }
    }
    const taktart = `${z.beats}/${z.beatType}`;
    return fehler.length ? { ok: false, soll, text: fehler.join(", ") + ` (${taktart}-Takt)` }
                         : { ok: true, soll, text: teiltakt ? `Auftakt bzw. Teiltakt (${taktart})` : `Takt passt (${taktart})` };
  }

  function dauerName(d) {
    const namen = { 384: "eine Ganze", 288: "eine punktierte Halbe", 192: "eine Halbe", 144: "eine punktierte Viertel",
                    96: "eine Viertel", 72: "eine punktierte Achtel", 64: "eine Triolenviertel", 48: "eine Achtel",
                    32: "eine Triolenachtel", 24: "eine Sechzehntel", 12: "eine Zweiunddreißigstel" };
    if (namen[d]) return namen[d];
    return `${(d / TEILE).toLocaleString("de-DE", { maximumFractionDigits: 2 })} Viertel`;
  }

  /* ---------- Noten ändern ---------- */

  function dauerVon(typ, punkte, triole) {
    let d = WERTE[typ] * TEILE;
    let zusatz = d / 2;
    for (let i = 0; i < punkte; i++) { d += zusatz; zusatz /= 2; }
    if (triole) d = d * 2 / 3;
    return Math.round(d);
  }

  const REIHENFOLGE_NACH_DAUER = ["tie", "instrument", "footnote", "level", "voice", "type", "dot", "accidental",
                                  "time-modification", "stem", "notehead", "notehead-text", "staff", "beam",
                                  "notations", "lyric", "play", "listen"];

  function einordnen(note, el) {
    // Element an die vom MusicXML-Schema verlangte Stelle setzen
    const rang = REIHENFOLGE_NACH_DAUER.indexOf(el.tagName);
    const nach = Array.from(note.children).find((k) => {
      const r = REIHENFOLGE_NACH_DAUER.indexOf(k.tagName);
      return r > rang;
    });
    note.insertBefore(el, nach || null);
  }

  function notenwertSetzen(note, typ, punkte, triole) {
    const doc = note.ownerDocument;
    let t = kind(note, "type");
    if (!t) { t = doc.createElement("type"); einordnen(note, t); }
    t.textContent = typ;
    for (const d of kinder(note, "dot")) d.remove();
    for (let i = 0; i < punkte; i++) einordnen(note, doc.createElement("dot"));
    const tm = kind(note, "time-modification");
    if (tm) tm.remove();
    if (triole) {
      const neu = doc.createElement("time-modification");
      setzeText(neu, "actual-notes", 3);
      setzeText(neu, "normal-notes", 2);
      einordnen(note, neu);
    }
    const r = kind(note, "rest");
    if (r) r.removeAttribute("measure");
    setzeText(note, "duration", dauerVon(typ, punkte, triole), ["tie", "voice", "type"]);
    // Akkordpartner bekommen denselben Wert
    let naechste = note.nextElementSibling;
    while (naechste && naechste.tagName === "note" && kind(naechste, "chord")) {
      notenwertSetzen(naechste, typ, punkte, triole);
      naechste = naechste.nextElementSibling;
    }
  }

  function tonhoeheSetzen(note, schritt, oktave, alter) {
    const doc = note.ownerDocument;
    let p = kind(note, "pitch");
    const r = kind(note, "rest");
    if (r) {
      p = doc.createElement("pitch");
      note.replaceChild(p, r);
    }
    setzeText(p, "step", schritt);
    const a = kind(p, "alter");
    if (alter) {
      if (a) a.textContent = String(alter);
      else p.insertBefore(Object.assign(doc.createElement("alter"), { textContent: String(alter) }), kind(p, "octave"));
    } else if (a) {
      a.remove();
    }
    setzeText(p, "octave", oktave);
    const acc = kind(note, "accidental");
    if (acc) acc.remove();               // Darstellung der Vorzeichen übernimmt der Notensatz
  }

  /** Vorzeichen, das an dieser Stelle des Takts für den Ton gilt: das letzte Vorzeichen desselben Tons
   *  (Stufe und Oktave) weiter vorn im Takt, sonst das der Tonart. */
  function geltendesAlter(note, schritt, oktave, fifths) {
    let el = note.previousElementSibling;
    const staff = text(note, "staff", "1");
    while (el) {
      if (el.tagName === "note" && text(el, "staff", "1") === staff) {
        const p = kind(el, "pitch");
        if (p && text(p, "step") === schritt && Number(text(p, "octave")) === oktave) return Number(text(p, "alter", "0"));
      }
      el = el.previousElementSibling;
    }
    return tonartAlter(fifths, schritt);
  }

  /** Tonhöhe um eine Stufe hoch (+1) oder runter (-1); das Vorzeichen richtet sich nach Tonart und
   *  nach Vorzeichen, die vorher im selben Takt für diesen Ton stehen. */
  function stufe(note, richtung, fifths) {
    const n = info(note);
    if (n.pause) return;
    let i = STUFEN.indexOf(n.schritt) + richtung;
    let okt = n.oktave;
    if (i > 6) { i = 0; okt += 1; }
    if (i < 0) { i = 6; okt -= 1; }
    const s = STUFEN[i];
    tonhoeheSetzen(note, s, okt, geltendesAlter(note, s, okt, fifths));
  }

  /* ---------- Sichtbare Vorzeichen ---------- */

  const VORZEICHEN_NAME = { 1: "sharp", "-1": "flat", 0: "natural", 2: "double-sharp", "-2": "flat-flat" };

  /** Sichtbare Vorzeichen (<accidental>) eines Takts aus Tonart und Tonhöhen: Ohne sie zeichnet der
   *  Notensatz kein Vorzeichen. Ein Vorzeichen gilt für denselben Ton bis zum Taktende, übergebundene
   *  Noten bekommen keines. Gibt die am Taktende gültige Tonart zurück. */
  function vorzeichenTakt(m, fifths) {
    const stand = new Map();
    for (const el of Array.from(m.children)) {
      if (el.tagName === "attributes") {
        const k = kind(el, "key");
        if (k) fifths = Number(text(k, "fifths", String(fifths)));
        continue;
      }
      if (el.tagName !== "note") continue;
      for (const a of kinder(el, "accidental")) a.remove();
      const p = kind(el, "pitch");
      if (!p) continue;
      const schritt = text(p, "step"), alter = Number(text(p, "alter", "0"));
      const schl = `${text(el, "staff", "1")}|${schritt}|${text(p, "octave")}`;
      const gilt = stand.has(schl) ? stand.get(schl) : tonartAlter(fifths, schritt);
      if (kinder(el, "tie").some((t) => t.getAttribute("type") === "stop")) continue;
      if (alter !== gilt && VORZEICHEN_NAME[alter] !== undefined) {
        const z = el.ownerDocument.createElement("accidental");
        z.textContent = VORZEICHEN_NAME[alter];
        einordnen(el, z);
      }
      stand.set(schl, alter);
    }
    return fifths;
  }

  function vorzeichenSetzen(doc) {
    let fifths = 0;
    for (const m of takte(doc)) fifths = vorzeichenTakt(m, fifths);
  }

  function vorzeichen(note, alter) {
    const n = info(note);
    if (n.pause) return;
    tonhoeheSetzen(note, n.schritt, n.oktave, alter);
  }

  function pauseUmschalten(note, vorlage) {
    const doc = note.ownerDocument;
    const n = info(note);
    if (n.pause) {
      const v = vorlage && !vorlage.pause ? vorlage : { schritt: "B", oktave: 4, alter: 0 };
      tonhoeheSetzen(note, v.schritt, v.oktave, v.alter);
      if (!kind(note, "type")) {                       // Ganztaktpause hatte keinen Notenwert
        const passend = [["whole", 0], ["half", 1], ["half", 0], ["quarter", 1], ["quarter", 0], ["eighth", 1], ["eighth", 0]]
          .find(([t, p]) => dauerVon(t, p, false) === n.dauer) || ["whole", 0];
        notenwertSetzen(note, passend[0], passend[1], false);
      }
    } else {
      const p = kind(note, "pitch");
      note.replaceChild(doc.createElement("rest"), p);
      const acc = kind(note, "accidental");
      if (acc) acc.remove();
      for (const a of artikulationen(note)) if (a !== "fermata") artikulationEntfernen(note, a);
      // Akkordpartner entfernen – eine Pause hat keine
      let naechste = note.nextElementSibling;
      while (naechste && naechste.tagName === "note" && kind(naechste, "chord")) {
        const weg = naechste;
        naechste = naechste.nextElementSibling;
        weg.remove();
      }
    }
  }

  function loeschen(note) {
    const naechste = note.nextElementSibling;
    if (!kind(note, "chord") && naechste && naechste.tagName === "note" && kind(naechste, "chord")) {
      kind(naechste, "chord").remove();       // nächster Akkordton wird Hauptnote
    }
    note.remove();
  }

  function einfuegenNach(note) {
    const kopie = note.cloneNode(true);
    const c = kind(kopie, "chord");
    if (c) c.remove();
    for (const b of kinder(kopie, "beam")) b.remove();
    for (const n of Array.from(kopie.querySelectorAll("tied, slur, tuplet"))) n.remove();
    // hinter den Akkord der Vorlage setzen
    let ziel = note;
    while (ziel.nextElementSibling && ziel.nextElementSibling.tagName === "note" && kind(ziel.nextElementSibling, "chord")) {
      ziel = ziel.nextElementSibling;
    }
    ziel.parentNode.insertBefore(kopie, ziel.nextSibling);
    return kopie;
  }

  /* ---------- Ganze Takte: einfügen, löschen, Länge einer Mehrtaktpause ---------- */

  /** Block, zu dem Takt nr gehört: eine Mehrtaktpause (N Takte in der Datei) oder der Takt allein.
   *  {start, ende} als Indizes in takte(doc), oder null. */
  function taktBlock(doc, nr) {
    const alle = takte(doc);
    const i = alle.findIndex((m) => m.getAttribute("number") === String(nr));
    if (i < 0) return null;
    for (let k = 0; k <= i; k++) {
      const mr = alle[k].querySelector("attributes > measure-style > multiple-rest");
      const n = mr ? Math.max(1, Math.min(Number(mr.textContent) || 1, alle.length - k)) : 1;
      if (i <= k + n - 1) return { start: k, ende: k + n - 1 };
      k += n - 1;
    }
    return { start: i, ende: i };
  }

  /** Takte fortlaufend nummerieren (Auftakt bleibt Takt 0). Rückgabe: Map alte Nummer -> neue Nummer
   *  für die Takte, die es vorher schon gab (neue Takte tragen keine alte Nummer). */
  function nummerieren(doc, alteNummern) {
    const alle = takte(doc);
    const karte = new Map();
    const auftakt = alle.length && alle[0].getAttribute("implicit") === "yes";
    alle.forEach((m, k) => {
      const neu = auftakt ? k : k + 1;
      const alt = alteNummern.get(m);
      if (alt !== undefined) karte.set(alt, neu);
      m.setAttribute("number", String(neu));
    });
    return karte;
  }

  function alteNummern(doc) {
    return new Map(takte(doc).map((m) => [m, Number(m.getAttribute("number"))]));
  }

  function pausentakt(doc, vorlageNr) {
    const z = zustandBei(doc, vorlageNr);
    const m = doc.createElement("measure");
    m.setAttribute("number", "0");
    const note = doc.createElement("note");
    const rest = doc.createElement("rest");
    rest.setAttribute("measure", "yes");
    note.appendChild(rest);
    setzeText(note, "duration", Math.round(TEILE * 4 * z.beats / z.beatType));
    setzeText(note, "voice", 1);
    m.appendChild(note);
    return m;
  }

  function strich(m, ort) {
    return kinder(m, "barline").find((b) => (b.getAttribute("location") || "right") === ort) || null;
  }

  /** Inhalt eines Taktstrichs in den gleichartigen Taktstrich von ziel übernehmen. */
  function strichUebernehmen(b, ziel, ort) {
    let z = strich(ziel, ort);
    if (!z) {
      z = ziel.ownerDocument.createElement("barline");
      z.setAttribute("location", ort);
      if (ort === "left") ziel.insertBefore(z, ziel.firstChild); else ziel.appendChild(z);
    }
    for (const k of Array.from(b.children)) {
      if (k.tagName === "bar-style") { for (const alt of kinder(z, "bar-style")) alt.remove(); z.insertBefore(k, z.firstChild); }
      else if (k.tagName === "repeat" && kinder(z, "repeat").some((r) => r.getAttribute("direction") === k.getAttribute("direction"))) k.remove();
      else z.appendChild(k);
    }
    // MusicXML-Reihenfolge: Strichart, Volte, Wiederholung
    const rang = { "bar-style": 0, ending: 1, repeat: 2 };
    for (const k of Array.from(z.children).sort((a, c) => (rang[a.tagName] ?? 3) - (rang[c.tagName] ?? 3))) z.appendChild(k);
    b.remove();
  }

  /** Neuen Takt (Ganztaktpause) hinter Takt nr einfügen – bei einer Mehrtaktpause hinter dem ganzen
   *  Block. Rückgabe: {nr: Nummer des neuen Takts, karte: alte -> neue Nummern}. */
  function taktEinfuegenNach(doc, nr) {
    const block = taktBlock(doc, nr);
    if (!block) return null;
    const alle = takte(doc);
    const ref = alle[block.ende], danach = alle[block.ende + 1] || null;
    faulenzerAufloesen(doc, ref.getAttribute("number"));
    if (danach) faulenzerAufloesen(doc, danach.getAttribute("number"));
    const alt = alteNummern(doc);
    const neu = pausentakt(doc, ref.getAttribute("number"));
    ref.parentNode.insertBefore(neu, danach);
    // Schlussstrich bleibt am Ende des Stücks
    const rechts = strich(ref, "right");
    if (!danach && rechts && !kind(rechts, "repeat") && !kind(rechts, "ending")) strichUebernehmen(rechts, neu, "right");
    const karte = nummerieren(doc, alt);
    return { nr: Number(neu.getAttribute("number")), karte };
  }

  /** Takt nr löschen (eine Mehrtaktpause als Ganzes). Taktart, Tonart, Schlüssel, Zeilenumbruch und
   *  Wiederholungszeichen wandern in die Nachbartakte. Rückgabe: {nr: Takt, der an die Stelle rückt,
   *  karte} oder null (der letzte verbliebene Takt lässt sich nicht löschen). */
  function taktLoeschen(doc, nr) {
    const block = taktBlock(doc, nr);
    let alle = takte(doc);
    if (!block || block.ende - block.start + 1 >= alle.length) return null;
    faulenzerAufloesen(doc, alle[block.start].getAttribute("number"));
    if (alle[block.ende + 1]) faulenzerAufloesen(doc, alle[block.ende + 1].getAttribute("number"));
    alle = takte(doc);
    const erster = alle[block.start], letzter = alle[block.ende];
    const davor = alle[block.start - 1] || null, danach = alle[block.ende + 1] || null;
    const alt = alteNummern(doc);
    // Volte, die nur diesen Block umfasst, entfällt
    const l = strich(erster, "left"), r = strich(letzter, "right");
    const anfang = l ? kinder(l, "ending") : [], ende = r ? kinder(r, "ending") : [];
    for (const a of anfang) {
      const partner = ende.find((e) => e.getAttribute("number") === a.getAttribute("number"));
      if (partner) { a.remove(); partner.remove(); }
    }
    // ebenso eine Wiederholung, die nur aus diesem Block besteht
    const vor = l && kinder(l, "repeat").find((x) => x.getAttribute("direction") === "forward");
    const zurueck = r && kinder(r, "repeat").find((x) => x.getAttribute("direction") === "backward");
    if (vor && zurueck) {
      for (const [b, x] of [[l, vor], [r, zurueck]]) {
        x.remove();
        for (const st of kinder(b, "bar-style")) if (["heavy-light", "light-heavy"].includes(st.textContent.trim())) st.remove();
      }
    }
    if (danach) {
      if (l && l.children.length) strichUebernehmen(l, danach, "left");
      const stelle = () => Array.from(danach.children).find((k) => !["print", "barline"].includes(k.tagName) || (k.tagName === "barline" && k.getAttribute("location") !== "left")) || null;
      for (const p_ of kinder(erster, "print")) if (!kind(danach, "print")) danach.insertBefore(p_, danach.firstChild);
      // Angaben, die ab hier gelten (im gelöschten Block gesetzt), gelten weiter
      for (const m of alle.slice(block.start, block.ende + 1)) {
        for (const attr of kinder(m, "attributes")) {
          for (const name of ["divisions", "key", "time", "clef"]) {
            const el = kind(attr, name);
            if (!el || kinder(danach, "attributes").some((a) => kind(a, name))) continue;
            let ziel = kind(danach, "attributes");
            if (!ziel) { ziel = doc.createElement("attributes"); danach.insertBefore(ziel, stelle()); }
            const ordnung = ["divisions", "key", "time", "staves", "part-symbol", "instruments", "clef", "staff-details", "transpose", "measure-style"];
            const nachfolger = Array.from(ziel.children).find((k) => ordnung.indexOf(k.tagName) > ordnung.indexOf(name)) || null;
            ziel.insertBefore(el, nachfolger);
          }
        }
      }
      if (erster.getAttribute("implicit") === "yes") danach.removeAttribute("implicit");
    }
    if (davor && r && r.children.length) strichUebernehmen(r, davor, "right");
    for (const m of alle.slice(block.start, block.ende + 1)) m.remove();
    const karte = nummerieren(doc, alt);
    const bleibt = danach || davor;
    return { nr: Number(bleibt.getAttribute("number")), karte };
  }

  /** Länge der Pause ab Takt nr setzen (1 = einzelne Ganztaktpause). Geht für eine Mehrtaktpause und
   *  für einen Takt, der nur eine Ganztaktpause enthält. Rückgabe: {nr, karte} oder null. */
  function mehrtaktpauseSetzen(doc, nr, anzahl) {
    const block = taktBlock(doc, nr);
    if (!block || !(anzahl >= 1) || anzahl > 200) return null;
    const alle = takte(doc);
    const erster = alle[block.start];
    const nurPause = noten(erster).every((n) => n.pause && n.ganztakt) && noten(erster).length > 0;
    if (!nurPause) return null;
    const jetzt = block.ende - block.start + 1;
    anzahl = Math.round(anzahl);
    if (anzahl === jetzt) return { nr: Number(erster.getAttribute("number")), karte: nummerieren(doc, alteNummern(doc)) };
    const alt = alteNummern(doc);
    const rechts = strich(alle[block.ende], "right");
    let letzter = alle[block.ende];
    if (anzahl > jetzt) {
      for (let k = jetzt; k < anzahl; k++) {
        const neu = pausentakt(doc, erster.getAttribute("number"));
        letzter.parentNode.insertBefore(neu, letzter.nextSibling);
        letzter = neu;
      }
    } else {
      for (let k = jetzt; k > anzahl; k--) {
        const weg = letzter;
        letzter = takte(doc)[block.start + k - 2];
        weg.remove();
      }
    }
    if (rechts && rechts.parentNode !== letzter) {
      if (rechts.parentNode) rechts.remove();
      const ziel = strich(letzter, "right");
      if (ziel) ziel.remove();
      letzter.appendChild(rechts);
    }
    let stil = erster.querySelector("attributes > measure-style > multiple-rest");
    if (anzahl === 1) {
      if (stil) { const ms = stil.parentNode; stil.remove(); if (!ms.children.length) { const a = ms.parentNode; ms.remove(); if (!a.children.length) a.remove(); } }
    } else {
      if (!stil) {
        let attr = kind(erster, "attributes");
        if (!attr) {
          attr = doc.createElement("attributes");
          const vor = Array.from(erster.children).find((k) => !["print", "barline"].includes(k.tagName) || (k.tagName === "barline" && k.getAttribute("location") !== "left")) || null;
          erster.insertBefore(attr, vor);
        }
        const ms = doc.createElement("measure-style");
        stil = doc.createElement("multiple-rest");
        ms.appendChild(stil);
        attr.appendChild(ms);
      }
      stil.textContent = String(anzahl);
    }
    const karte = nummerieren(doc, alt);
    return { nr: Number(erster.getAttribute("number")), karte };
  }

  /** Länge der Mehrtaktpause, in der Takt nr liegt (1 = einzelner Takt); 0, wenn der Takt Noten hat. */
  function pausenLaenge(doc, nr) {
    const block = taktBlock(doc, nr);
    if (!block) return 0;
    const erster = takte(doc)[block.start];
    const n = noten(erster);
    return n.length && n.every((x) => x.pause && x.ganztakt) ? block.ende - block.start + 1 : 0;
  }

  /* ---------- Anpassung für den Notensatz (Verovio) ---------- */

  /** Verovio fasst eine Mehrtaktpause zu einem Takt zusammen: Vom ersten Takt des Blocks liest es nur,
   *  was vor der Pausen-Angabe steht, von den übrigen nur den Taktstrich. Endet im letzten Takt eine
   *  Volta-Klammer, bleibt sie offen (die zweite Volte bekommt die falsche Nummer); Textangaben und
   *  Studierzeichen im Block verschwinden. Für die Anzeige wandert deshalb das Ende der Klammer an den
   *  ersten Takt des Blocks und die Texte vor die Pausen-Angabe.
   *  Außerdem setzt Verovio den Namen der Stimme links neben die erste Zeile; ein langer Name nimmt ihr
   *  am Handy die halbe Breite. Er steht deshalb wie auf gedruckten Stimmen als kleinere Zeile unter dem
   *  Titel. Die gespeicherte Datei bleibt, wie MusicXML es verlangt.
   *  Rückgabe: doc selbst oder eine angepasste Kopie. */
  function fuerVerovio(doc) {
    const stimmen = (d) => Array.from(d.querySelectorAll("part-name"))
      .filter((pn) => pn.textContent.trim() && pn.getAttribute("print-object") !== "no");
    const rechts = (m) => kinder(m, "barline").find((b) => (b.getAttribute("location") || "right") === "right");
    const angabe = (m) => kinder(m, "attributes").find((a) => a.querySelector("measure-style > multiple-rest"));
    // Texte, die Verovio nicht lesen würde: hinter der Pausen-Angabe oder in den Folgetakten des Blocks
    const verdeckt = (block) => block.flatMap((m, i) => {
      const alle = kinder(m, "direction");
      if (i > 0) return alle;
      const a = angabe(m);
      return alle.filter((d) => a.compareDocumentPosition(d) & 4);     // 4 = d steht hinter a
    });
    const bloecke = (liste) => {
      const aus = [];
      liste.forEach((m, k) => {
        const mr = m.querySelector("attributes > measure-style > multiple-rest");
        const n = mr ? Math.min(Number(mr.textContent) || 1, liste.length - k) : 1;
        if (n >= 2) aus.push(liste.slice(k, k + n));
      });
      return aus;
    };
    const noetig = (block) => {
      const ende = rechts(block[block.length - 1]);
      return (ende && kind(ende, "ending")) || verdeckt(block).length > 0;
    };
    if (!bloecke(takte(doc)).some(noetig) && !stimmen(doc).length) return doc;
    const kopie = doc.cloneNode(true);
    const namen = stimmen(kopie);
    if (namen.length) {
      const wurzel = kopie.documentElement;
      let work = kind(wurzel, "work");
      if (!work) {
        work = kopie.createElement("work");
        wurzel.insertBefore(work, wurzel.firstElementChild);
      }
      if (!kind(work, "work-number")) {                 // Verovio setzt sie als Untertitel
        const zeile = kopie.createElement("work-number");
        zeile.textContent = namen[0].textContent.trim();
        work.insertBefore(zeile, work.firstElementChild);
      }
      for (const pn of namen) pn.setAttribute("print-object", "no");
    }
    for (const block of bloecke(takte(kopie))) {
      const erster = block[0];
      const quelle = rechts(block[block.length - 1]);
      if (quelle && kind(quelle, "ending")) {
        let ziel = rechts(erster);
        if (!ziel) {
          ziel = kopie.createElement("barline");
          ziel.setAttribute("location", "right");
          erster.appendChild(ziel);
        }
        for (const e of kinder(quelle, "ending")) ziel.insertBefore(e, ziel.firstChild);
        if (!quelle.children.length) quelle.remove();
      }
      const a = angabe(erster);
      for (const d of verdeckt(block)) erster.insertBefore(d, a);
    }
    return kopie;
  }

  /* ---------- Textangaben und Studierzeichen ---------- */

  /** Textangaben („Trio“, „rit.“, „D.C. al Fine“) und Studierzeichen (eingerahmt) eines Takts – bei einer
   *  Mehrtaktpause des ganzen Blocks. Lautstärke-Zeichen gehören nicht dazu.
   *  Rückgabe: [{ el, text, zeichen }] (el = das <direction>-Element). */
  function texte(doc, nr) {
    const block = taktBlock(doc, nr);
    if (!block) return [];
    const aus = [];
    for (const m of takte(doc).slice(block.start, block.ende + 1)) {
      for (const d of kinder(m, "direction")) {
        const typ = kind(d, "direction-type");
        const el = typ && (kind(typ, "rehearsal") || kind(typ, "words"));
        if (el && el.textContent.trim()) aus.push({ el: d, text: el.textContent.trim(), zeichen: el.tagName === "rehearsal" });
      }
    }
    return aus;
  }

  /** Ein kurzes Zeichen aus Großbuchstaben oder Ziffern („A“, „B2“, „17“) ist ein Studierzeichen. */
  function istStudierzeichen(text) {
    return /^([A-Z]{1,2}\d?|\d{1,3})$/.test(text);
  }

  /** Text über die Note setzen (ohne Note: an den Taktanfang). Ein Studierzeichen wird eingerahmt, Text
   *  mit großem Anfangsbuchstaben fett (Tempo, Abschnitt), sonst kursiv (Vortrag). */
  function textEinfuegen(doc, nr, text, vorNote) {
    text = String(text || "").replace(/\s+/g, " ").trim().slice(0, 60);
    const m = takt(doc, nr);
    if (!m || !text) return null;
    const d = doc.createElement("direction");
    d.setAttribute("placement", "above");
    const typ = doc.createElement("direction-type");
    const el = doc.createElement(istStudierzeichen(text) ? "rehearsal" : "words");
    if (el.tagName === "words") {
      if (/^[A-ZÄÖÜ]/.test(text)) el.setAttribute("font-weight", "bold");
      else el.setAttribute("font-style", "italic");
    }
    el.textContent = text;
    typ.appendChild(el);
    d.appendChild(typ);
    let ziel = vorNote && vorNote.parentNode === m ? vorNote : kinder(m, "note")[0] || null;
    while (ziel && kind(ziel, "chord") && ziel.previousElementSibling && ziel.previousElementSibling.tagName === "note") {
      ziel = ziel.previousElementSibling;             // vor die Hauptnote des Akkords
    }
    if (!ziel) ziel = kinder(m, "barline").find((b) => (b.getAttribute("location") || "right") === "right") || null;
    m.insertBefore(d, ziel);
    return d;
  }

  function textEntfernen(el) {
    if (el && el.parentNode) el.remove();
  }

  /* ---------- Artikulationen ---------- */

  // MusicXML-Name, Anzeige und Zeichen; die Fermate steht direkt in <notations>, die anderen in <articulations>
  const ARTIKULATIONEN = [
    { name: "staccato", text: "Staccato", zeichen: "·" },
    { name: "tenuto", text: "Tenuto", zeichen: "–" },
    { name: "accent", text: "Akzent", zeichen: ">" },
    { name: "strong-accent", text: "Marcato", zeichen: "^" },
    { name: "staccatissimo", text: "Keil", zeichen: "▾" },
    { name: "fermata", text: "Fermate", zeichen: "𝄐" },
  ];
  const ART_NAMEN = ARTIKULATIONEN.map((a) => a.name);
  // schließen sich gegenseitig aus (Punkt oder Keil, Akzent oder Marcato)
  const ART_GEGEN = { staccato: ["staccatissimo"], staccatissimo: ["staccato"], accent: ["strong-accent"],
                      "strong-accent": ["accent"] };

  /** Artikulationen einer Note (Namen aus ARTIKULATIONEN, feste Reihenfolge). */
  function artikulationen(note) {
    const da = new Set();
    for (const nt of kinder(note, "notations")) {
      for (const a of kinder(nt, "articulations")) for (const x of Array.from(a.children)) da.add(x.tagName);
      if (kinder(nt, "fermata").length) da.add("fermata");
    }
    return ART_NAMEN.filter((x) => da.has(x));
  }

  function artikulationEntfernen(note, name) {
    for (const nt of kinder(note, "notations")) {
      if (name === "fermata") for (const f of kinder(nt, "fermata")) f.remove();
      for (const a of kinder(nt, "articulations")) {
        if (name !== "fermata") for (const x of kinder(a, name)) x.remove();
        if (!a.children.length) a.remove();
      }
      if (!nt.children.length) nt.remove();
    }
  }

  /** Artikulation an- oder ausschalten; Rückgabe: jetzt an? Punkt/Keil und Akzent/Marcato schließen
   *  sich aus. Pausen tragen nur eine Fermate. */
  function artikulationUmschalten(note, name) {
    if (!ART_NAMEN.includes(name)) return false;
    if (artikulationen(note).includes(name)) {
      artikulationEntfernen(note, name);
      return false;
    }
    if (kind(note, "rest") && name !== "fermata") return false;
    for (const g of ART_GEGEN[name] || []) artikulationEntfernen(note, g);
    const doc = note.ownerDocument;
    let nt = kind(note, "notations");
    if (!nt) { nt = doc.createElement("notations"); einordnen(note, nt); }
    if (name === "fermata") {
      nt.appendChild(doc.createElement("fermata"));
    } else {
      let a = kind(nt, "articulations");
      if (!a) { a = doc.createElement("articulations"); nt.appendChild(a); }
      a.appendChild(doc.createElement(name));
    }
    return true;
  }

  /* ---------- Faulenzer (Taktwiederholung) ---------- */

  /** Wiederholungs-Läufe: [{start, ende}] als Indizes in takte(doc) (MusicXML measure-repeat). */
  function faulenzerLaeufe(doc) {
    const alle = takte(doc), laeufe = [];
    let start = -1;
    alle.forEach((m, i) => {
      for (const mr of m.querySelectorAll("attributes > measure-style > measure-repeat")) {
        if (mr.getAttribute("type") === "start") {
          if (start >= 0) laeufe.push({ start, ende: i - 1 });
          start = i;
        } else if (start >= 0) {
          laeufe.push({ start, ende: i - 1 });
          start = -1;
        }
      }
    });
    if (start >= 0) laeufe.push({ start, ende: alle.length - 1 });
    return laeufe.filter((l) => l.start > 0 && l.ende >= l.start);
  }

  /** Nummer des Takts, den Takt nr wiederholt (Faulenzer), sonst null. */
  function faulenzer(doc, nr) {
    const alle = takte(doc);
    const i = alle.findIndex((m) => m.getAttribute("number") === String(nr));
    if (i < 0) return null;
    return faulenzerLaeufe(doc).some((l) => i >= l.start && i <= l.ende) ? alle[i - 1].getAttribute("number") : null;
  }

  /** Noten der Faulenzer-Takte aus dem jeweils vorigen Takt neu übernehmen (nach einer Änderung dort). */
  function faulenzerAktualisieren(doc) {
    const alle = takte(doc);
    for (const l of faulenzerLaeufe(doc)) {
      for (let i = l.start; i <= l.ende; i++) {
        const m = alle[i], quelle = alle[i - 1];
        const alt = Array.from(m.children).filter((k) => ["note", "backup", "forward"].includes(k.tagName));
        const vor = alt.length ? alt[0] : Array.from(m.children).find((k) => k.tagName === "barline") || null;
        for (const k of Array.from(quelle.children)) {
          if (!["note", "backup", "forward"].includes(k.tagName)) continue;
          const kopie = k.cloneNode(true);
          for (const x of Array.from(kopie.querySelectorAll("tie, tied, slur"))) x.remove();
          m.insertBefore(kopie, vor);
        }
        for (const k of alt) k.remove();
      }
    }
  }

  /** Taktwiederholung um Takt nr aufheben: Die Takte des Laufs zeigen danach ihre Noten (wenn ein
   *  Faulenzer-Takt selbst geändert wird, ist er keine reine Wiederholung mehr). */
  function faulenzerAufloesen(doc, nr) {
    const alle = takte(doc);
    const i = alle.findIndex((m) => m.getAttribute("number") === String(nr));
    const lauf = faulenzerLaeufe(doc).find((l) => i >= l.start && i <= l.ende);
    if (!lauf) return false;
    for (const k of [lauf.start, lauf.ende + 1]) {
      const m = alle[k];
      if (!m) continue;
      for (const mr of Array.from(m.querySelectorAll("attributes > measure-style > measure-repeat"))) {
        const stil = mr.parentNode;
        mr.remove();
        if (!stil.children.length) stil.remove();
      }
    }
    return true;
  }

  /* ---------- Balken ---------- */

  function gruppenLaenge(z) {
    if (z.beatType === 8 && z.beats % 3 === 0) return 3 * TEILE / 2;        // 6/8, 9/8, 12/8
    if (z.beatType === 2) return 2 * TEILE;                                    // Alla breve: halbe Takte
    return 4 * TEILE / z.beatType;                                             // x/4: je Viertel
  }

  function balkenTakt(m, z) {
    for (const b of m.querySelectorAll("note > beam")) b.remove();
    for (const s of m.querySelectorAll("note > stem")) s.remove();
    const laenge = gruppenLaenge(z);
    const proStimme = {};
    const zeit = {};
    for (const note of kinder(m, "note")) {
      const n = info(note);
      if (n.akkord) continue;
      const t = zeit[n.stimme] || 0;
      if (!n.vorschlag) zeit[n.stimme] = t + n.dauer;
      (proStimme[n.stimme] = proStimme[n.stimme] || []).push({ ...n, start: t });
    }
    for (const liste of Object.values(proStimme)) {
      let gruppe = [];
      const abschliessen = () => {
        if (gruppe.length >= 2) balkenSchreiben(gruppe);
        gruppe = [];
      };
      for (const n of liste) {
        const balkbar = !n.pause && !n.vorschlag && FAEHNCHEN[n.typ];
        if (!balkbar) { abschliessen(); continue; }
        if (gruppe.length && Math.floor(n.start / laenge) !== Math.floor(gruppe[0].start / laenge)) abschliessen();
        gruppe.push(n);
      }
      abschliessen();
    }
  }

  function balkenSchreiben(gruppe) {
    const doc = gruppe[0].el.ownerDocument;
    const tiefe = Math.max(...gruppe.map((n) => FAEHNCHEN[n.typ]));
    for (let stufe = 1; stufe <= tiefe; stufe++) {
      for (let i = 0; i < gruppe.length; i++) {
        if (FAEHNCHEN[gruppe[i].typ] < stufe) continue;
        const vorher = i > 0 && FAEHNCHEN[gruppe[i - 1].typ] >= stufe;
        const nachher = i + 1 < gruppe.length && FAEHNCHEN[gruppe[i + 1].typ] >= stufe;
        let wert;
        if (stufe === 1) wert = i === 0 ? "begin" : i === gruppe.length - 1 ? "end" : "continue";
        else if (vorher && nachher) wert = "continue";
        else if (nachher) wert = "begin";
        else if (vorher) wert = "end";
        else wert = i === 0 ? "forward hook" : "backward hook";
        const b = doc.createElement("beam");
        b.setAttribute("number", String(stufe));
        b.textContent = wert;
        einordnen(gruppe[i].el, b);
      }
    }
  }

  function balkenSetzen(doc) {
    for (const m of takte(doc)) balkenTakt(m, zustandBei(doc, m.getAttribute("number")));
  }

  /* ---------- Schlüssel (für Transposition in den Bassschlüssel) ---------- */

  function schluesselSetzen(doc, zeichen, linie) {
    let vorhanden = false;
    for (const c of doc.querySelectorAll("attributes > clef")) {
      setzeText(c, "sign", zeichen);
      setzeText(c, "line", linie);
      const okt = kind(c, "clef-octave-change");
      if (okt) okt.remove();
      vorhanden = true;
    }
    if (!vorhanden) {
      const erster = takte(doc)[0];
      if (!erster) return;
      let attr = kind(erster, "attributes");
      if (!attr) { attr = doc.createElement("attributes"); erster.insertBefore(attr, erster.firstChild); }
      const c = doc.createElement("clef");
      setzeText(c, "sign", zeichen);
      setzeText(c, "line", linie);
      attr.appendChild(c);
    }
  }

  /* ---------- Titel ---------- */

  function titelSetzen(doc, titel, stimme) {
    const wurzel = doc.documentElement;
    let work = kind(wurzel, "work");
    if (!work) {
      work = doc.createElement("work");
      wurzel.insertBefore(work, wurzel.firstElementChild);
    }
    setzeText(work, "work-title", titel || "");
    for (const pn of doc.querySelectorAll("part-name, score-instrument > instrument-name")) pn.textContent = stimme || "";
  }

  function titelLesen(doc) {
    const wt = doc.querySelector("work > work-title");
    return wt ? wt.textContent.trim() : "";
  }

  /* ---------- Deutsche Tonnamen ---------- */

  function tonname(schritt, alter, oktave) {
    const grund = { C: "c", D: "d", E: "e", F: "f", G: "g", A: "a", B: "h" }[schritt];
    let name;
    if (alter === -1) name = { E: "es", A: "as", B: "b" }[schritt] || grund + "es";
    else if (alter === -2) name = { E: "eses", A: "asas", B: "heses" }[schritt] || grund + "eses";
    else if (alter === 1) name = grund + "is";
    else if (alter === 2) name = grund + "isis";
    else name = grund;
    // Helmholtz: c (kleine Oktave) = C3, c' = C4, c'' = C5 …
    if (oktave >= 3) return name + "'".repeat(oktave - 3);
    return name.charAt(0).toUpperCase() + name.slice(1) + ",".repeat(Math.max(0, 2 - oktave));
  }

  const ZEICHEN = { whole: "Ganze", half: "Halbe", quarter: "♩", eighth: "♪", "16th": "16tel", "32nd": "32tel", "64th": "64tel" };

  function beschriftung(n) {
    const zeichen = n.el ? artikulationen(n.el).map((a) => ARTIKULATIONEN.find((x) => x.name === a).zeichen).join(" ") : "";
    if (n.pause && n.ganztakt) return { wert: "Takt", name: "Ganztaktpause", zeichen };
    const wert = (ZEICHEN[n.typ] || "?") + ".".repeat(n.punkte) + (n.triole ? "³" : "");
    if (n.pause) return { wert, name: "Pause", zeichen };
    return { wert, name: tonname(n.schritt, n.alter, n.oktave) + (n.vorschlag ? " (Vorschlag)" : ""), zeichen };
  }

  return {
    TEILE, laden, alsText, takte, takt, noten, info, pruefen, zustandBei, tonartAlter,
    notenwertSetzen, tonhoeheSetzen, stufe, vorzeichen, pauseUmschalten, loeschen, einfuegenNach,
    balkenSetzen, balkenTakt, schluesselSetzen, titelSetzen, titelLesen, tonname, beschriftung, dauerVon,
    vorzeichenTakt, vorzeichenSetzen, geltendesAlter, faulenzer, faulenzerAktualisieren, faulenzerAufloesen,
    ARTIKULATIONEN, artikulationen, artikulationUmschalten, fuerVerovio,
    taktBlock, taktEinfuegenNach, taktLoeschen, mehrtaktpauseSetzen, pausenLaenge,
    texte, textEinfuegen, textEntfernen, istStudierzeichen,
  };
})();
