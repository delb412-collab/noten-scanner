/* Noten-Scanner – Notenerkennung, Partitur-Ansicht, Korrektur, Abspielen, Transponieren, Bibliothek.
 * Die Erkennung läuft auf dem Vereins-Server (Hugging Face Space), alles andere im Browser.
 * Verwendet aus app.js: $, melden, fragen, herunterladen, worker, zustand, fertigeSeiten, VERSION. */
"use strict";

const Partitur = (() => {
  const P = {
    doc: null,              // MusicXML (DOM) – gelesen bzw. korrigiert, nicht transponiert
    takte: new Map(),       // Taktnummer -> {status, gruende, seite, box}
    bestaetigt: new Set(),  // vom Benutzer als richtig bestätigte Takte
    meta: { titel: "", stimme: "", instrument: "c", komponist: "" },
    quelle: null,           // {art: "scan"|"bibliothek", auftrag, id}
    bilder: [],             // Seitenbilder (Blob) für die Original-Ausschnitte
    anzeige: { nach: null, oktave: 0 },
    zoom: 40,
    rueckgaengig: [],
    geaendert: false,
  };
  let vrv = null;           // Verovio für die ganze Partitur
  let vrvTakt = null;       // Verovio für die Vorschau eines Takts
  const spieler = new Spieler();
  let editorTakt = null;
  let editorAuswahl = 0;
  let idZuTakt = new Map(); // Verovio-ID eines Takts -> Taktnummer

  /* ---------- Server ---------- */

  const VOREINGESTELLT = "__SERVER__";          // beim Bauen eingetragen (GitHub-Variable HF_SPACE)

  function server() {
    let adresse = "";
    let kennwort = "";
    try {
      adresse = (localStorage.getItem("server-adresse") || "").trim().replace(/\/+$/, "");
      kennwort = localStorage.getItem("server-kennwort") || "";
    } catch (_) { /* kein Speicher */ }
    if (!adresse && VOREINGESTELLT.startsWith("https://")) adresse = VOREINGESTELLT;
    return { adresse, kennwort };
  }

  async function anfrage(pfad, optionen = {}) {
    const { adresse, kennwort } = server();
    if (!adresse) throw new Error("Bitte in den Einstellungen die Server-Adresse eintragen.");
    const kopf = { ...(optionen.headers || {}), "X-Kennwort": kennwort };
    let antwort;
    try {
      antwort = await fetch(adresse + pfad, { ...optionen, headers: kopf });
    } catch (_) {
      throw new Error("Der Server ist nicht erreichbar. Internetverbindung und Server-Adresse prüfen.");
    }
    if (antwort.status === 401) throw new Error("Das Vereinskennwort stimmt nicht (Einstellungen).");
    if (!antwort.ok) {
      let text = `Fehler ${antwort.status}`;
      try { text = (await antwort.json()).detail || text; } catch (_) { /* keine JSON-Antwort */ }
      const f = new Error(text);
      f.status = antwort.status;
      throw f;
    }
    return antwort;
  }

  async function json(pfad, optionen) {
    return (await anfrage(pfad, optionen)).json();
  }

  /** Wartet, bis der Server antwortet (ein schlafender Hugging-Face-Space braucht 1–2 Minuten). */
  async function serverWecken(melde) {
    const { adresse } = server();
    if (!adresse) throw new Error("Bitte in den Einstellungen die Server-Adresse eintragen.");
    const ende = Date.now() + 240000;
    let versuche = 0;
    while (Date.now() < ende) {
      try {
        const a = await fetch(adresse + "/api/status", { cache: "no-store" });
        if (a.ok) {
          const s = await a.json();
          if (s.bereit) return s;
          melde("Server startet, Notenerkennung wird geladen …");
        } else {
          melde("Server wird geweckt … (beim ersten Mal 1–2 Minuten)");
        }
      } catch (_) {
        melde(versuche < 2 ? "Verbindung zum Server …" : "Server wird geweckt … (beim ersten Mal 1–2 Minuten)");
      }
      versuche++;
      await new Promise((r) => setTimeout(r, 3000));
    }
    throw new Error("Der Server antwortet nicht. Bitte später noch einmal versuchen.");
  }

  /* ---------- Bildschirme ---------- */

  function zeigen(name) {
    for (const b of ["#start", "#partitur", "#bibliothek"]) $(b).classList.toggle("versteckt", b !== name);
    window.scrollTo(0, 0);
  }

  const DAUER_HINWEIS = "Die Notenerkennung braucht je Seite etwa eine halbe bis eine Minute.";
  function fortschritt(text, an = true, detail = DAUER_HINWEIS) {
    $("#partitur-fortschritt").classList.toggle("versteckt", !an);
    $("#partitur-fortschritt-text").textContent = text || "";
    $("#partitur-fortschritt-detail").textContent = an ? detail : "";
    $("#partitur").classList.toggle("laedt", an);       // solange keine leere Notenkarte und kein Tipp
  }

  /* ---------- Verovio laden ---------- */

  let verovioVersprechen = null;
  function verovioLaden() {
    if (!verovioVersprechen) {
      verovioVersprechen = new Promise((fertig, fehler) => {
        const s = document.createElement("script");
        s.src = "verovio/verovio-toolkit-wasm.js?v=__VEROVIO__";
        s.onerror = () => { verovioVersprechen = null; fehler(new Error("Notensatz konnte nicht geladen werden (Internet?)")); };
        s.onload = () => {
          const bereit = () => {
            vrv = new verovio.toolkit();
            vrvTakt = new verovio.toolkit();
            fertig();
          };
          if (verovio.module.calledRun) bereit();
          else verovio.module.onRuntimeInitialized = bereit;
        };
        document.head.appendChild(s);
      });
    }
    return verovioVersprechen;
  }

  /* ---------- Erkennen (vom Scan) ---------- */

  async function pngVonSeite(seite) {
    return new Promise((fertig, fehler) => {
      const id = "png-" + seite.id + "-" + Date.now();
      warten.set(id, { fertig, fehler });
      worker.postMessage({ typ: "png", id, seite: seite.seite.slice(0) });
    });
  }
  const warten = new Map();

  /** Der Hintergrund-Worker wurde neu gestartet oder ist abgestürzt: offene Anfragen abbrechen. */
  function workerAbbruch() {
    for (const w of warten.values()) w.fehler(new Error("Die Seitenaufbereitung wurde unterbrochen – bitte noch einmal versuchen."));
    warten.clear();
  }

  function workerAntwort(n) {
    // von app.js aufgerufen
    const w = warten.get(n.id);
    if (!w) return false;
    warten.delete(n.id);
    if (n.typ === "png_fertig") w.fertig(new Blob([n.png], { type: "image/png" }));
    else w.fehler(new Error(n.text || "Fehler"));
    return true;
  }

  async function erkennen() {
    const seiten = fertigeSeiten();
    if (!seiten.length) return;
    if (!server().adresse) {
      melden("Bitte zuerst den Vereins-Server eintragen.");
      einstellungenOeffnen();            // aus app.js
      return;
    }
    spieler.stoppen();
    zeigen("#partitur");
    const eigener = eigenerName();
    $("#partitur-titel").textContent = eigener || "Noten erkennen";
    $("#partitur-noten").innerHTML = "";
    $("#partitur-werkzeuge").classList.add("versteckt");
    $("#partitur-pruefen").classList.add("versteckt");
    try {
      fortschritt("Seiten werden vorbereitet …");
      const bilder = [];
      for (const s of seiten) bilder.push(await pngVonSeite(s));
      await serverWecken((t) => fortschritt(t));
      fortschritt("Seiten werden hochgeladen …");
      const daten = new FormData();
      bilder.forEach((b, k) => daten.append("seiten", b, `seite_${k + 1}.png`));
      daten.append("titel", eigener);                  // leer: der Server nimmt den Titel vom Blatt
      daten.append("stimme", "");
      const { auftrag } = await json("/api/lesen", { method: "POST", body: daten });
      const verovioBereit = verovioLaden();
      let ergebnis = null;
      let aussetzer = 0;
      while (!ergebnis) {
        await new Promise((r) => setTimeout(r, 2000));
        let a;
        try {
          a = await json("/api/auftrag/" + auftrag);
          aussetzer = 0;
        } catch (f) {
          if (f.status || ++aussetzer > 15) throw f;   // Serverantwort (z. B. 404) oder 30 s kein Netz
          fortschritt("Verbindung unterbrochen – neuer Versuch …");
          continue;
        }
        if (a.status === "fehler") throw new Error(a.text);
        if (a.status === "fertig") ergebnis = a.ergebnis;
        else fortschritt(a.text + (a.fortschritt ? ` (${Math.round(a.fortschritt * 100)} %)` : ""));
      }
      await verovioBereit;
      // Titel, Stimme und Instrument stehen meist im Kopf der Seite: Der Server liest sie mit. Ein selbst
      // eingetippter Name geht vor.
      const v = ergebnis.vorschlag || {};
      const instrument = Instrumente.LISTE.some((i) => i.id === v.instrument) ? v.instrument : "b";
      oeffnen({
        musicxml: ergebnis.musicxml, takte: ergebnis.takte, bilder,
        meta: { titel: eigener || v.titel || "Neues Stück", stimme: v.stimme || "", instrument, komponist: "" },
        quelle: { art: "scan", auftrag },
      });
      P.geaendert = true;
      await merken();
      if (!eigener && v.titel) {
        $("#pdf-name").value = v.titel;
        $("#pdf-name").dispatchEvent(new Event("change"));
      }
      const gelesen = [!eigener && v.titel ? `„${v.titel}“` : "", v.stimme || ""].filter(Boolean).join(" – ");
      if (gelesen) melden(`Vom Blatt gelesen: ${gelesen}. Ändern kannst du das beim Speichern.`);
    } catch (fehler) {
      fortschritt("", false);
      $("#partitur-noten").innerHTML = `<p class="hinweis fehler">${textSicher(fehler.message)}</p>`;
    }
  }

  /** Der Name, den der Nutzer selbst eingetippt hat – "" beim vorbelegten „Noten 10.11.2026“. */
  function eigenerName() {
    const name = $("#pdf-name").value.trim();
    return /^Noten \d{1,2}\.\d{1,2}\.\d{4}$/.test(name) ? "" : name;
  }

  function textSicher(t) {
    return String(t).replace(/[&<>"]/g, (z) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[z]));
  }

  /* ---------- Öffnen und Zeichnen ---------- */

  function oeffnen({ musicxml, takte, bilder, meta, quelle, bestaetigt }) {
    P.doc = NotenXML.laden(musicxml);
    NotenXML.balkenSetzen(P.doc);
    NotenXML.vorzeichenSetzen(P.doc);
    bilderVergessen();
    P.takte = new Map((takte || []).map((t) => [t.nr, t]));
    P.bestaetigt = new Set(bestaetigt || []);
    P.bilder = bilder || [];
    P.meta = { titel: "", stimme: "", instrument: "b", komponist: "", ...meta };
    P.quelle = quelle;
    P.anzeige = { nach: P.meta.instrument, oktave: "auto" };
    P.rueckgaengig = [];
    P.geaendert = false;
    NotenXML.titelSetzen(P.doc, P.meta.titel, P.meta.stimme);
    $("#partitur-titel").textContent = P.meta.titel || "Partitur";
    fortschritt("", false);
    $("#partitur-werkzeuge").classList.remove("versteckt");
    zeigen("#partitur");
    zeichnen();
  }

  /** MusicXML für die Anzeige: bei Bedarf mit anderem Schlüssel (Transposition in den Bassschlüssel). */
  function anzeigeText() {
    const u = umschrift();
    const quelle = Instrumente.finden(P.meta.instrument);
    let doc = NotenXML.fuerVerovio(P.doc);             // Kopie nur, wenn etwas anzupassen ist
    if (u.schluessel[0] !== quelle.schluessel[0] || u.schluessel[1] !== quelle.schluessel[1]) {
      if (doc === P.doc) doc = P.doc.cloneNode(true);
      NotenXML.schluesselSetzen(doc, u.schluessel[0], u.schluessel[1]);
    }
    return NotenXML.alsText(doc);
  }

  function umschrift() {
    return Instrumente.umschreiben(P.meta.instrument, P.anzeige.nach || P.meta.instrument, oktaveFuerAnzeige());
  }

  function oktaveFuerAnzeige() {
    if (P.anzeige.oktave !== "auto") return Number(P.anzeige.oktave) || 0;
    const nach = P.anzeige.nach || P.meta.instrument;
    if (nach === P.meta.instrument) return 0;
    // Lage so wählen, dass die Noten im Mittel um die Mittellinie des neuen Systems liegen
    const midis = [];
    const basis = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
    for (const p of P.doc.querySelectorAll("note > pitch")) {
      const st = p.querySelector("step"), ok = p.querySelector("octave"), al = p.querySelector("alter");
      if (st && ok) midis.push(12 * (Number(ok.textContent) + 1) + basis[st.textContent.trim()] + (al ? Number(al.textContent) : 0));
    }
    if (!midis.length) return 0;
    midis.sort((a, b) => a - b);
    const median = midis[Math.floor(midis.length / 2)];
    const verschiebung = Instrumente.umschreiben(P.meta.instrument, nach, 0).halbtoene;
    const k = Math.round((Instrumente.mittellinie(nach) - (median + verschiebung)) / 12);
    return Math.max(-2, Math.min(2, k));
  }

  function zeichnen() {
    if (!vrv || !P.doc) return;
    const feld = $("#partitur-noten");
    const breite = Math.max(300, feld.clientWidth || 360);
    const u = umschrift();
    vrv.setOptions({
      pageWidth: Math.round(breite * 100 / P.zoom), pageHeight: 60000, scale: P.zoom, adjustPageHeight: true,
      breaks: "auto", footer: "none", header: "auto", svgViewBox: true, transpose: u.text,
      pageMarginLeft: 20, pageMarginRight: 20, pageMarginTop: 20, pageMarginBottom: 20,
    });
    vrv.loadData(anzeigeText());
    let svg = "";
    for (let i = 1; i <= vrv.getPageCount(); i++) svg += vrv.renderToSVG(i);
    feld.innerHTML = svg;
    idZuTakt = new Map();
    for (const g of feld.querySelectorAll("g.measure")) {
      const n = Number(vrv.getElementAttr(g.id).n);
      idZuTakt.set(g.id, n);
    }
    markieren();
    pruefLeiste();
    const anders = P.anzeige.nach && P.anzeige.nach !== P.meta.instrument;
    $("#partitur-transposition").textContent = (u.text || anders)
      ? "Angezeigt für: " + Instrumente.finden(P.anzeige.nach || P.meta.instrument).kurz + (u.text && !anders ? " (Oktave verschoben)" : "")
      : "";
  }

  function markieren() {
    const feld = $("#partitur-noten");
    for (const r of feld.querySelectorAll("rect.takt-hinterlegung")) r.remove();
    for (const g of feld.querySelectorAll("g.measure")) {
      const n = idZuTakt.get(g.id);
      const t = P.takte.get(n);
      let art = null;
      const pruefung = NotenXML.pruefen(P.doc, n);
      if (!P.bestaetigt.has(n)) {
        if ((t && t.status === "unsicher") || !pruefung.ok) art = "unsicher";
        else if (t && t.status === "korrigiert") art = "korrigiert";
      }
      g.classList.toggle("takt-unsicher", art === "unsicher");
      g.classList.toggle("takt-korrigiert", art === "korrigiert");
      if (!art) continue;
      const band = taktBand(g);
      if (!band) continue;
      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("class", "takt-hinterlegung " + art);
      rect.setAttribute("x", band.x);
      rect.setAttribute("y", band.y);
      rect.setAttribute("width", band.breite);
      rect.setAttribute("height", band.hoehe);
      rect.setAttribute("rx", band.rund);
      g.insertBefore(rect, g.firstChild);
    }
  }

  /** Band eines Takts für die farbige Hinterlegung: so breit wie die Notenlinien des Takts, oben und
   *  unten anderthalb Zwischenräume Rand – in jeder Zeile gleich hoch, ohne die Nachbarzeile zu berühren.
   *  (Die Umrandung des Taktinhalts taugt nicht: Bögen, Zahlen und hohe Noten machen sie riesig.) */
  function taktBand(g) {
    const staff = g.querySelector("g.staff");
    if (!staff) return null;
    const linien = Array.from(staff.children).filter((k) => k.tagName.toLowerCase() === "path");
    if (linien.length < 2) return null;
    let oben, unten;
    try { oben = linien[0].getBBox(); unten = linien[linien.length - 1].getBBox(); } catch (_) { return null; }
    const raum = Math.abs(unten.y - oben.y) / (linien.length - 1);
    if (!(raum > 0) || !(oben.width > 0)) return null;
    return { x: oben.x, breite: oben.width, y: Math.min(oben.y, unten.y) - 1.5 * raum,
             hoehe: Math.abs(unten.y - oben.y) + 3 * raum, rund: raum * 0.6 };
  }

  function offeneTakte() {
    const liste = [];
    for (const m of NotenXML.takte(P.doc)) {
      const n = Number(m.getAttribute("number"));
      if (P.bestaetigt.has(n)) continue;
      const t = P.takte.get(n);
      if ((t && t.status === "unsicher") || !NotenXML.pruefen(P.doc, n).ok) liste.push(n);
    }
    return liste;
  }

  function pruefLeiste() {
    const offen = offeneTakte();
    const leiste = $("#partitur-pruefen");
    leiste.classList.toggle("versteckt", !offen.length);
    $("#partitur-pruefen-text").textContent = offen.length === 1 ? "1 Takt bitte prüfen" : `${offen.length} Takte bitte prüfen`;
  }

  function zumNaechsten() {
    const offen = offeneTakte();
    if (!offen.length) return;
    const nach = offen.find((n) => editorTakt === null || n > editorTakt) ?? offen[0];
    taktOeffnen(nach);
  }

  /* ---------- Takt bearbeiten ---------- */

  function taktAusKlick(ereignis) {
    const g = ereignis.target.closest && ereignis.target.closest("g.measure");
    if (!g) return;
    if (spieler.laeuft) abspielen(idZuTakt.get(g.id));      // beim Abspielen: dort weiterspielen
    else taktOeffnen(idZuTakt.get(g.id));
  }

  function taktOeffnen(n) {
    if (n === undefined || n === null) return;
    editorTakt = n;
    editorAuswahl = 0;
    const g = Array.from($("#partitur-noten").querySelectorAll("g.measure")).find((x) => idZuTakt.get(x.id) === n);
    if (g) g.scrollIntoView({ block: "center", behavior: "smooth" });
    editorZeichnen();
    const d = $("#dialog-takt");
    if (!d.open) d.show();
  }

  function editorZeichnen() {
    const n = editorTakt;
    const m = NotenXML.takt(P.doc, n);
    if (!m) return;
    const block = NotenXML.taktBlock(P.doc, n);
    const alleTakte = NotenXML.takte(P.doc);
    const bis = block && block.ende > block.start ? Number(alleTakte[block.ende].getAttribute("number")) : n;
    $("#takt-titel").textContent = bis !== n ? `Takt ${n}–${bis}` : "Takt " + n;
    const pausen = NotenXML.pausenLaenge(P.doc, n);
    $("#takt-pausen").classList.toggle("versteckt", !pausen);
    $("#takt-pausen-zahl").textContent = String(pausen || 1);
    $("#takt-pausen-wort").textContent = pausen === 1 ? "Takt" : "Takte";
    $("#takt-pausen-weniger").disabled = pausen <= 1;
    $("#takt-entfernen").disabled = alleTakte.length < 2 || (block && block.ende - block.start + 1 >= alleTakte.length);
    const texte = NotenXML.texte(P.doc, n).length;
    $("#takt-texte").textContent = texte ? `Text (${texte}) …` : "Text …";
    const pruefung = NotenXML.pruefen(P.doc, n);
    const info = P.takte.get(n);
    const zeilen = [];
    zeilen.push(`<li class="${pruefung.ok ? "gut" : "schlecht"}">${pruefung.ok ? "✓" : "✗"} ${textSicher(pruefung.text)}</li>`);
    for (const g of (info && info.gruende) || []) zeilen.push(`<li>${textSicher(g)}</li>`);
    if (P.bestaetigt.has(n)) zeilen.push(`<li class="gut">Von dir als richtig bestätigt</li>`);
    $("#takt-gruende").innerHTML = zeilen.join("");
    originalZeigen(info);
    taktVorschau(n);
    const noten = NotenXML.noten(m);
    if (editorAuswahl >= noten.length) editorAuswahl = Math.max(0, noten.length - 1);
    const leiste = $("#takt-noten");
    leiste.innerHTML = "";
    noten.forEach((x, i) => {
      const b = NotenXML.beschriftung(x);
      const knopf = document.createElement("button");
      knopf.type = "button";
      knopf.className = "notenchip" + (i === editorAuswahl ? " gewaehlt" : "") + (x.akkord ? " akkord" : "");
      knopf.innerHTML = `<span class="wert">${b.wert}</span><span class="name">${textSicher(b.name)}</span>` +
        (b.zeichen ? `<span class="art">${textSicher(b.zeichen)}</span>` : "");
      knopf.addEventListener("click", () => { editorAuswahl = i; editorZeichnen(); });
      leiste.appendChild(knopf);
    });
    const gewaehlt = noten[editorAuswahl];
    for (const k of document.querySelectorAll("#takt-werkzeug [data-wert]")) {
      k.classList.toggle("an", !!gewaehlt && k.dataset.wert === gewaehlt.typ);
    }
    $("#takt-punkt").classList.toggle("an", !!gewaehlt && gewaehlt.punkte > 0);
    $("#takt-triole").classList.toggle("an", !!gewaehlt && gewaehlt.triole);
    $("#takt-pause").textContent = gewaehlt && gewaehlt.pause ? "→ Note" : "→ Pause";
    $("#takt-pause").setAttribute("aria-label", gewaehlt && gewaehlt.pause ? "Pause in Note umwandeln" : "Note in Pause umwandeln");
    for (const k of document.querySelectorAll("#takt-hoch, #takt-runter, #takt-werkzeug [data-alter]")) {
      k.disabled = !gewaehlt || gewaehlt.pause;
    }
    for (const k of document.querySelectorAll("#takt-werkzeug [data-alter]")) {
      k.classList.toggle("an", !!gewaehlt && !gewaehlt.pause && Number(k.dataset.alter) === gewaehlt.alter);
    }
    const arten = gewaehlt ? NotenXML.artikulationen(gewaehlt.el) : [];
    for (const k of document.querySelectorAll("#takt-werkzeug [data-artikulation]")) {
      k.classList.toggle("an", arten.includes(k.dataset.artikulation));
      k.setAttribute("aria-pressed", arten.includes(k.dataset.artikulation) ? "true" : "false");
      k.disabled = !gewaehlt || (gewaehlt.pause && k.dataset.artikulation !== "fermata");
    }
    $("#takt-rueckgaengig").disabled = !P.rueckgaengig.length;
  }

  /** Position eines Takts im Notenbild (1, 2, …). Verovio wählt Takte nach Position aus, nicht nach
   *  Nummer – und eine Mehrtaktpause ist dort nur ein Takt. Liegt n in einer Mehrtaktpause, zählt sie. */
  function taktPosition(n) {
    const alle = Array.from($("#partitur-noten").querySelectorAll("g.measure"));
    let pos = 0;
    alle.forEach((g, i) => { if (idZuTakt.get(g.id) <= n) pos = i + 1; });
    return Math.max(1, pos);
  }

  function taktVorschau(n) {
    if (!vrvTakt) return;
    vrvTakt.setOptions({ pageWidth: 2400, adjustPageHeight: true, adjustPageWidth: true, scale: 60, footer: "none",
                         header: "none", svgViewBox: true, transpose: umschrift().text, pageMarginTop: 10,
                         pageMarginBottom: 10, pageMarginLeft: 10, pageMarginRight: 10 });
    const pos = taktPosition(n);
    vrvTakt.select({ measureRange: `${pos}-${pos}` });
    vrvTakt.loadData(anzeigeText());
    $("#takt-vorschau").innerHTML = vrvTakt.renderToSVG(1);
    const kopf = Array.from($("#takt-vorschau").querySelectorAll("g.note, g.rest, g.mRest"));
    const nr = Array.from(NotenXML.noten(NotenXML.takt(P.doc, n)));
    if (kopf[editorAuswahl] && nr.length === kopf.length) kopf[editorAuswahl].classList.add("gewaehlt");
  }

  let ausschnittNr = 0;
  async function originalZeigen(info) {
    const leinwand = $("#takt-original");
    const box = info && info.box;
    const nr = ++ausschnittNr;
    let bild = null;
    if (box) bild = await seitenbild(info.seite || 0);
    if (nr !== ausschnittNr) return;               // inzwischen ein anderer Takt gewählt
    leinwand.classList.toggle("versteckt", !bild);
    $("#takt-original-fehlt").classList.toggle("versteckt", !!bild);
    if (!bild) return;
    const [x0, y0, x1, y1] = box;
    const breite = leinwand.clientWidth || 340;
    const faktor = (breite * (window.devicePixelRatio || 1)) / (x1 - x0);
    leinwand.width = Math.round((x1 - x0) * faktor);
    leinwand.height = Math.round((y1 - y0) * faktor);
    const c = leinwand.getContext("2d");
    c.fillStyle = "#fff";
    c.fillRect(0, 0, leinwand.width, leinwand.height);
    c.drawImage(bild, x0, y0, x1 - x0, y1 - y0, 0, 0, leinwand.width, leinwand.height);
  }

  const bildCache = new Map();
  function bilderVergessen() {
    for (const bild of bildCache.values()) URL.revokeObjectURL(bild.src);
    bildCache.clear();
  }

  async function seitenbild(k) {
    if (bildCache.has(k)) return bildCache.get(k);
    let blob = P.bilder[k] || null;
    try {
      if (!blob && P.quelle && P.quelle.art === "bibliothek" && P.quelle.id) {
        blob = await (await anfrage(`/api/bibliothek/${P.quelle.id}/seite/${k}`)).blob();
      } else if (!blob && P.quelle && P.quelle.auftrag) {
        blob = await (await anfrage(`/api/auftrag/${P.quelle.auftrag}/seite/${k}`)).blob();
      }
    } catch (_) { blob = null; }
    if (!blob) return null;
    const bild = new Image();
    bild.src = URL.createObjectURL(blob);
    try { await bild.decode(); } catch (_) { return null; }
    bildCache.set(k, bild);
    return bild;
  }

  function aendern(aktion) {
    const m = NotenXML.takt(P.doc, editorTakt);
    if (!m) return;
    const liste = NotenXML.noten(m);
    const n = liste[editorAuswahl];
    if (!n) return;
    P.rueckgaengig.push(NotenXML.alsText(P.doc));
    if (P.rueckgaengig.length > 50) P.rueckgaengig.shift();
    const z = NotenXML.zustandBei(P.doc, editorTakt);
    if (NotenXML.faulenzerAufloesen(P.doc, editorTakt)) melden("Faulenzer aufgelöst – der Takt wird ausgeschrieben");
    aktion(n, z, liste);
    NotenXML.balkenTakt(m, z);
    NotenXML.vorzeichenTakt(m, z.fifths);
    NotenXML.faulenzerAktualisieren(P.doc);          // Wiederholungen folgen dem geänderten Vortakt
    P.bestaetigt.delete(editorTakt);
    P.geaendert = true;
    zeichnen();
    editorZeichnen();
    merken();
  }

  /** Ganze Takte einfügen, löschen oder die Länge einer Pause ändern. Die Takte danach bekommen neue
   *  Nummern – Bildausschnitt, Markierung und „bestätigt“ wandern mit. */
  function taktUmbauen(aktion, meldung) {
    if (editorTakt === null) return;
    P.rueckgaengig.push({ xml: NotenXML.alsText(P.doc), takte: Array.from(P.takte.values()),
                          bestaetigt: Array.from(P.bestaetigt), takt: editorTakt });
    if (P.rueckgaengig.length > 50) P.rueckgaengig.shift();
    const ergebnis = aktion();
    if (!ergebnis) { P.rueckgaengig.pop(); return; }
    const neu = new Map();
    for (const [nr, t] of P.takte) {
      const n = ergebnis.karte.get(nr);
      if (n !== undefined) neu.set(n, { ...t, nr: n });
    }
    P.takte = neu;
    P.bestaetigt = new Set(Array.from(P.bestaetigt).map((n) => ergebnis.karte.get(n)).filter((n) => n !== undefined));
    editorTakt = ergebnis.nr;
    editorAuswahl = 0;
    P.geaendert = true;
    zeichnen();
    editorZeichnen();
    merken();
    if (meldung) melden(meldung);
  }

  /* Textangaben und Studierzeichen eines Takts: ansehen, löschen, hinzufügen. */
  function textAendern(aktion) {
    P.rueckgaengig.push(NotenXML.alsText(P.doc));
    if (P.rueckgaengig.length > 50) P.rueckgaengig.shift();
    aktion();
    P.geaendert = true;
    zeichnen();
    editorZeichnen();
    merken();
  }

  function textListeZeichnen() {
    const liste = $("#text-liste");
    const texte = NotenXML.texte(P.doc, editorTakt);
    liste.innerHTML = texte.length ? "" : `<li class="klein">In diesem Takt steht noch kein Text.</li>`;
    texte.forEach((t) => {
      const li = document.createElement("li");
      const wort = document.createElement("span");
      wort.textContent = t.text;
      if (t.zeichen) wort.className = "rahmen";
      const weg = document.createElement("button");
      weg.type = "button";
      weg.className = "knopf";
      weg.textContent = "Löschen";
      weg.setAttribute("aria-label", `„${t.text}“ löschen`);
      weg.addEventListener("click", () => {
        textAendern(() => NotenXML.textEntfernen(t.el));
        textListeZeichnen();
      });
      li.append(wort, weg);
      liste.appendChild(li);
    });
  }

  function texteOeffnen() {
    if (editorTakt === null) return;
    const d = $("#dialog-text");
    $("#text-titel").textContent = $("#takt-titel").textContent + ": Texte";
    d.querySelector("[name=neu]").value = "";
    textListeZeichnen();
    d.returnValue = "";
    d.showModal();
  }

  function texteSchliessen() {
    const d = $("#dialog-text");
    const neu = d.querySelector("[name=neu]").value.trim();
    if (d.returnValue !== "ok" || !neu || editorTakt === null) return;
    const m = NotenXML.takt(P.doc, editorTakt);
    const note = m && NotenXML.noten(m)[editorAuswahl];
    textAendern(() => NotenXML.textEinfuegen(P.doc, editorTakt, neu, note ? note.el : null));
    melden(NotenXML.istStudierzeichen(neu) ? `Studierzeichen ${neu} gesetzt` : `„${neu}“ eingetragen`);
  }

  function editorVerbinden() {
    $("#takt-texte").addEventListener("click", texteOeffnen);
    $("#dialog-text").addEventListener("close", texteSchliessen);
    enterSchliesst($("#dialog-text"), "ok");
    $("#takt-einfuegen").addEventListener("click", () =>
      taktUmbauen(() => NotenXML.taktEinfuegenNach(P.doc, editorTakt), "Leerer Takt eingefügt – jetzt die Noten eintragen"));
    $("#takt-entfernen").addEventListener("click", () =>
      taktUmbauen(() => NotenXML.taktLoeschen(P.doc, editorTakt), "Takt gelöscht – „Rückgängig“ holt ihn zurück"));
    for (const [id, schritt] of [["#takt-pausen-weniger", -1], ["#takt-pausen-mehr", 1]]) {
      $(id).addEventListener("click", () =>
        taktUmbauen(() => NotenXML.mehrtaktpauseSetzen(P.doc, editorTakt, NotenXML.pausenLaenge(P.doc, editorTakt) + schritt)));
    }
    $("#takt-hoch").addEventListener("click", () => aendern((n, z) => NotenXML.stufe(n.el, 1, z.fifths)));
    $("#takt-runter").addEventListener("click", () => aendern((n, z) => NotenXML.stufe(n.el, -1, z.fifths)));
    for (const k of document.querySelectorAll("#takt-werkzeug [data-alter]")) {
      k.addEventListener("click", () => aendern((n) => NotenXML.vorzeichen(n.el, Number(k.dataset.alter))));
    }
    for (const k of document.querySelectorAll("#takt-werkzeug [data-wert]")) {
      k.addEventListener("click", () => aendern((n) => NotenXML.notenwertSetzen(n.el, k.dataset.wert, n.punkte, n.triole)));
    }
    for (const k of document.querySelectorAll("#takt-werkzeug [data-artikulation]")) {
      k.addEventListener("click", () => aendern((n) => NotenXML.artikulationUmschalten(n.el, k.dataset.artikulation)));
    }
    $("#takt-punkt").addEventListener("click", () => aendern((n) => NotenXML.notenwertSetzen(n.el, n.typ, n.punkte ? 0 : 1, n.triole)));
    $("#takt-triole").addEventListener("click", () => aendern((n) => NotenXML.notenwertSetzen(n.el, n.typ, n.punkte, !n.triole)));
    $("#takt-pause").addEventListener("click", () => aendern((n, z, liste) => {
      const vorlage = liste.slice(0, editorAuswahl).reverse().find((x) => !x.pause);
      NotenXML.pauseUmschalten(n.el, vorlage);
    }));
    $("#takt-neu").addEventListener("click", () => aendern((n) => { NotenXML.einfuegenNach(n.el); editorAuswahl += 1; }));
    $("#takt-loeschen").addEventListener("click", () => aendern((n) => {
      NotenXML.loeschen(n.el);
      editorAuswahl = Math.max(0, editorAuswahl - 1);
    }));
    $("#takt-rueckgaengig").addEventListener("click", () => {
      const alt = P.rueckgaengig.pop();
      if (!alt) return;
      if (typeof alt === "string") {
        P.doc = NotenXML.laden(alt);
      } else {                                          // Umbau ganzer Takte: auch die Zuordnung zurück
        P.doc = NotenXML.laden(alt.xml);
        P.takte = new Map(alt.takte.map((t) => [t.nr, t]));
        P.bestaetigt = new Set(alt.bestaetigt);
        editorTakt = alt.takt;
        editorAuswahl = 0;
      }
      zeichnen();
      editorZeichnen();
      merken();
    });
    $("#takt-richtig").addEventListener("click", () => {
      P.bestaetigt.add(editorTakt);
      P.geaendert = true;
      markieren();
      pruefLeiste();
      merken();
      const offen = offeneTakte();
      if (offen.length) taktOeffnen(offen.find((n) => n > editorTakt) ?? offen[0]);
      else { $("#dialog-takt").close(); melden("Alle markierten Takte sind geprüft."); }
    });
    $("#takt-weiter").addEventListener("click", zumNaechsten);
    $("#takt-abspielen").addEventListener("click", () => {
      const n = editorTakt;
      $("#dialog-takt").close();
      if (n !== null) abspielen(n);
    });
    // „close“ kommt zeitversetzt: nicht zurücksetzen, wenn inzwischen schon ein anderer Takt offen ist
    $("#dialog-takt").addEventListener("close", () => { if (!$("#dialog-takt").open) editorTakt = null; });
  }

  /* ---------- Abspielen ---------- */

  let tempo = 100;
  /** Ohne Angabe: starten bzw. stoppen (Knopf). Mit Taktnummer: ab diesem Takt spielen – auch wenn
   *  schon gespielt wird (Antippen eines Takts während des Abspielens). */
  async function abspielen(abTakt) {
    const knopf = $("#knopf-abspielen");
    const start = typeof abTakt === "number" ? abTakt : null;
    if (start === null && spieler.laeuft) {
      spieler.stoppen();
      markierungLoeschen();
      knopf.textContent = "▶ Abspielen";
      return;
    }
    if (!vrv) return;
    const { noten, takteZeit, faktor } = notenZumAbspielen();
    if (!noten.length) return;
    let ab = 0;
    const von = start !== null ? start : editorTakt;
    if (von !== null) {
      // eine Mehrtaktpause ist im Notenbild ein Takt: der letzte Takt mit Nummer <= von
      const takte = Array.from($("#partitur-noten").querySelectorAll("g.measure")).filter((x) => idZuTakt.get(x.id) <= von);
      const g = takte[takte.length - 1];
      const tk = g && takteZeit.find((x) => x.id === g.id);
      if (tk) ab = (tk.t / 1000) * faktor;
    }
    markierungLoeschen();
    abspielenAb(noten, ab, knopf);
  }

  /** Verovio spielt Wiederholungen aus; die Noten des zweiten Durchgangs heißen dann „…-rend2“.
   *  Im Notenbild gibt es sie nur einmal – dies ist die Kennung dort. */
  function basisId(id) {
    return String(id).replace(/-rend\d+$/, "");
  }

  /** Noten fürs Abspielen aus dem gesetzten Notenbild: [{id, zeit, dauer, midi}] (Sekunden). */
  function notenZumAbspielen() {
    const zeitmap = vrv.renderToTimemap({ includeMeasures: true });
    const ereignisse = typeof zeitmap === "string" ? JSON.parse(zeitmap) : zeitmap;
    const faktor = 120 / tempo;                     // Verovio rechnet ohne Tempoangabe mit ♩ = 120
    const verschiebung = Instrumente.klingend(P.anzeige.nach || P.meta.instrument);
    const noten = [];
    const takteZeit = [];                           // Taktanfänge (ms bei ♩ = 120)
    for (const e of ereignisse) {
      if (e.measureOn) takteZeit.push({ id: e.measureOn, t: e.tstamp });
      for (const id of e.on || []) {
        const w = vrv.getMIDIValuesForElement(id);
        if (!w || w.pitch === undefined) continue;
        noten.push({ id, roh: w.time, rohDauer: w.duration, midi: w.pitch + verschiebung });
      }
    }
    // Faulenzer: Verovio spielt Wiederholungstakte nicht – die Noten des Vortakts dort einsetzen
    takteZeit.forEach((tk, i) => { tk.ende = i + 1 < takteZeit.length ? takteZeit[i + 1].t : Infinity; });
    for (let i = 1; i < takteZeit.length; i++) {
      const g = document.getElementById(basisId(takteZeit[i].id));
      const zeichen = g && g.querySelector(".mRpt");
      if (!zeichen) continue;
      const vor = takteZeit[i - 1], versatz = takteZeit[i].t - vor.t;
      const kopien = noten.filter((x) => x.roh >= vor.t && x.roh < vor.ende)
        .map((x) => ({ ...x, roh: x.roh + versatz, id: zeichen.id || x.id }));
      noten.push(...kopien);
    }
    for (const x of noten) {
      x.zeit = (x.roh / 1000) * faktor;
      x.dauer = Math.max(0.05, (x.rohDauer / 1000) * faktor);
      Object.assign(x, spielweise(x.id));
    }
    noten.sort((a, b) => a.zeit - b.zeit);
    return { noten, takteZeit, faktor };
  }

  // Artikulation beim Abspielen: Staccato/Keil kürzer, Akzent/Marcato lauter (MEI-Namen von Verovio)
  const SPIELWEISE = { stacc: { kurz: 0.5 }, stacciss: { kurz: 0.3 }, spicc: { kurz: 0.3 },
                       acc: { lautstaerke: 0.3 }, marc: { lautstaerke: 0.34 } };
  function spielweise(id) {
    const el = document.getElementById(basisId(id));
    if (!el) return {};
    const akkord = el.closest("g.chord");
    const artics = Array.from(el.querySelectorAll("g.artic"));
    if (akkord) artics.push(...Array.from(akkord.children).filter((c) => c.classList.contains("artic")));
    const ergebnis = {};
    for (const a of artics) {
      let attr = {};
      try { attr = vrv.getElementAttr(a.id) || {}; if (typeof attr === "string") attr = JSON.parse(attr); } catch (_) { attr = {}; }
      for (const name of String(attr.artic || "").split(/\s+/)) Object.assign(ergebnis, SPIELWEISE[name] || {});
    }
    return ergebnis;
  }

  async function abspielenAb(noten, ab, knopf) {
    knopf.textContent = "⏹ Stopp";
    let letzteZeile = null;
    await spieler.spielen(noten, {
      ab,
      beiNote: (id, an) => {
        const el = document.getElementById(basisId(id));
        if (!el) return;
        el.classList.toggle("spielt", an);
        if (an) {
          const zeile = el.closest("g.system");
          if (zeile && zeile !== letzteZeile) {
            letzteZeile = zeile;
            zeile.scrollIntoView({ block: "center", behavior: "smooth" });
          }
        }
      },
      fertig: () => { knopf.textContent = "▶ Abspielen"; markierungLoeschen(); },
    });
  }

  function markierungLoeschen() {
    for (const el of document.querySelectorAll("#partitur-noten .spielt")) el.classList.remove("spielt");
  }

  /* ---------- Transponieren ---------- */

  function transponierenOeffnen() {
    const d = $("#dialog-transponieren");
    for (const auswahl of [d.querySelector("[name=von]"), d.querySelector("[name=nach]")]) {
      if (!auswahl.options.length) {
        for (const i of Instrumente.LISTE) auswahl.add(new Option(i.name, i.id));
      }
    }
    d.querySelector("[name=von]").value = P.meta.instrument;
    d.querySelector("[name=nach]").value = P.anzeige.nach || P.meta.instrument;
    (d.querySelector(`[name=oktave][value="${P.anzeige.oktave}"]`) || d.querySelector("[name=oktave][value=auto]")).checked = true;
    d.returnValue = "";
    d.showModal();
  }

  function transponierenSchliessen() {
    const d = $("#dialog-transponieren");
    if (d.returnValue !== "ok") return;
    const von = d.querySelector("[name=von]").value;
    if (von !== P.meta.instrument) { P.meta.instrument = von; P.geaendert = true; }
    P.anzeige.nach = d.querySelector("[name=nach]").value;
    const okt = d.querySelector("[name=oktave]:checked").value;
    P.anzeige.oktave = okt === "auto" ? "auto" : Number(okt);
    zeichnen();
    merken();
  }

  /* ---------- PDF und MusicXML ---------- */

  function dateiName(zusatz = "") {
    const name = [P.meta.titel || "Noten", P.meta.stimme, zusatz].filter(Boolean).join(" – ");
    return name.normalize("NFC").replace(/[\\/:*?"<>|]+/g, "-").slice(0, 120);
  }

  async function teilen(datei) {
    if (navigator.canShare && navigator.canShare({ files: [datei] })) {
      try { await navigator.share({ files: [datei], title: datei.name }); return; } catch (f) {
        if (f.name === "AbortError") return;
      }
    }
    herunterladen(datei);
  }

  async function pdfErstellen() {
    const knopf = $("#knopf-partitur-pdf");
    knopf.disabled = true;
    const alt = knopf.textContent;
    knopf.textContent = "PDF wird erstellt …";
    try {
      await serverWecken((t) => { knopf.textContent = t.length > 30 ? "Server wird geweckt …" : t; });
      const u = umschrift();
      const zusatz = u.text ? Instrumente.finden(P.anzeige.nach).kurz : "";
      const name = dateiName(zusatz);
      const antwort = await anfrage("/api/pdf", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ musicxml: anzeigeText(), transponieren: u.text, dateiname: name }),
      });
      const datei = new File([await antwort.blob()], name + ".pdf", { type: "application/pdf" });
      await teilen(datei);
    } catch (fehler) {
      melden(fehler.message);
    } finally {
      knopf.disabled = false;
      knopf.textContent = alt;
    }
  }

  async function musicxmlTeilen() {
    const name = dateiName();
    const datei = new File([NotenXML.alsText(P.doc)], name + ".musicxml", { type: "application/vnd.recordare.musicxml+xml" });
    await teilen(datei);
  }

  /* ---------- Bibliothek ---------- */

  function speichernOeffnen() {
    const d = $("#dialog-speichern");
    const auswahl = d.querySelector("[name=instrument]");
    if (!auswahl.options.length) for (const i of Instrumente.LISTE) auswahl.add(new Option(i.name, i.id));
    d.querySelector("[name=titel]").value = P.meta.titel || "";
    d.querySelector("[name=stimme]").value = P.meta.stimme || "";
    d.querySelector("[name=komponist]").value = P.meta.komponist || "";
    auswahl.value = P.meta.instrument;
    const offen = offeneTakte().length;
    $("#speichern-hinweis").textContent = offen ? `Noch ${offen} Takt(e) nicht geprüft – du kannst trotzdem speichern und später korrigieren.` : "";
    d.returnValue = "";
    d.showModal();
  }

  async function speichernSchliessen() {
    const d = $("#dialog-speichern");
    if (d.returnValue !== "ok") return;
    P.meta.titel = d.querySelector("[name=titel]").value.trim() || P.meta.titel || "Ohne Titel";
    P.meta.stimme = d.querySelector("[name=stimme]").value.trim();
    P.meta.komponist = d.querySelector("[name=komponist]").value.trim();
    P.meta.instrument = d.querySelector("[name=instrument]").value;
    NotenXML.titelSetzen(P.doc, P.meta.titel, P.meta.stimme);
    $("#partitur-titel").textContent = P.meta.titel;
    try {
      melden("Wird in der Bibliothek gespeichert …");
      const takte = Array.from(P.takte.values()).map((t) => ({ ...t, bestaetigt: P.bestaetigt.has(t.nr) }));
      const korrigiert = takte.filter((t) => t.status === "korrigiert").length;
      const sid = P.quelle && P.quelle.art === "bibliothek" ? P.quelle.id : null;
      const antwort = await json("/api/bibliothek" + (sid ? "?sid=" + encodeURIComponent(sid) : ""), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...P.meta, musicxml: NotenXML.alsText(P.doc), auftrag: (P.quelle && P.quelle.auftrag) || "",
                               unsicher: offeneTakte().length, korrigiert, takte }),
      });
      if (!sid && !antwort.bilder && P.bilder.length) {
        const daten = new FormData();
        P.bilder.forEach((b, k) => daten.append("seiten", b, `seite_${k + 1}.png`));
        await anfrage(`/api/bibliothek/${antwort.id}/seiten`, { method: "POST", body: daten });
      }
      P.quelle = { art: "bibliothek", id: antwort.id };
      P.geaendert = false;
      await merken();
      melden("In der Bibliothek gespeichert.");
    } catch (fehler) {
      melden(fehler.message);
    }
  }

  let bibliotheksListe = [];
  async function bibliothekOeffnen() {
    zeigen("#bibliothek");
    const feld = $("#bibliothek-liste");
    if (!server().adresse) {
      feld.innerHTML = `<p class="hinweis">Für die Vereins-Bibliothek bitte in den Einstellungen den Server eintragen.</p>`;
      return;
    }
    feld.innerHTML = `<p class="hinweis"><span class="kreisel"></span> Bibliothek wird geladen …</p>`;
    try {
      await serverWecken((t) => { feld.innerHTML = `<p class="hinweis"><span class="kreisel"></span> ${textSicher(t)}</p>`; });
      bibliotheksListe = await json("/api/bibliothek");
      bibliothekZeichnen();
    } catch (fehler) {
      feld.innerHTML = `<p class="hinweis fehler">${textSicher(fehler.message)}</p>`;
    }
  }

  function vereinfachen(t) {
    return String(t || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }

  function bibliothekZeichnen() {
    const suche = vereinfachen($("#bibliothek-suche").value);
    const feld = $("#bibliothek-liste");
    const liste = bibliotheksListe.filter((s) => !suche || vereinfachen(`${s.titel} ${s.stimme} ${s.komponist}`).includes(suche));
    if (!liste.length) {
      feld.innerHTML = `<p class="leer">${bibliotheksListe.length ? "Nichts gefunden." : "Noch keine Stücke in der Bibliothek."}</p>`;
      return;
    }
    feld.innerHTML = "";
    for (const s of liste) {
      const knopf = document.createElement("button");
      knopf.type = "button";
      knopf.className = "stueck";
      const unsicher = Number(s.unsicher) > 0 ? `<span class="marke">${Number(s.unsicher)} Takt(e) ungeprüft</span>` : "";
      knopf.innerHTML = `<strong>${textSicher(s.titel || "Ohne Titel")}</strong>
        <span class="klein">${textSicher([s.stimme, s.komponist].filter(Boolean).join(" · "))}</span>
        <span class="klein grau">${textSicher(s.geaendert || "")}</span>${unsicher}`;
      knopf.addEventListener("click", () => stueckLaden(s.id));
      feld.appendChild(knopf);
    }
  }

  async function stueckLaden(sid) {
    zeigen("#partitur");
    $("#partitur-noten").innerHTML = "";
    $("#partitur-werkzeuge").classList.add("versteckt");
    fortschritt("Stück wird geladen …", true, "");
    try {
      const [daten] = await Promise.all([json("/api/bibliothek/" + encodeURIComponent(sid)), verovioLaden()]);
      oeffnen({
        musicxml: daten.musicxml, takte: daten.takte || [], bilder: [],
        bestaetigt: (daten.takte || []).filter((t) => t.bestaetigt).map((t) => t.nr),
        meta: daten.meta, quelle: { art: "bibliothek", id: sid },
      });
      await merken();
    } catch (fehler) {
      fortschritt("", false);
      $("#partitur-noten").innerHTML = `<p class="hinweis fehler">${textSicher(fehler.message)}</p>`;
    }
  }

  async function loeschen() {
    if (!P.quelle || P.quelle.art !== "bibliothek") return;
    if (!(await fragen(`„${P.meta.titel}“ aus der Vereins-Bibliothek löschen?`))) return;
    try {
      await anfrage("/api/bibliothek/" + encodeURIComponent(P.quelle.id), { method: "DELETE" });
      melden("Gelöscht.");
      P.quelle = { art: "scan" };
      P.geaendert = true;                           // nur noch auf diesem Gerät vorhanden
      await merken();
      bibliothekOeffnen();
    } catch (fehler) {
      melden(fehler.message);
    }
  }

  /* ---------- Merken (damit nichts verloren geht, wenn das iPhone die App beendet) ---------- */

  async function merken() {
    if (!P.doc) return;
    const eintrag = { musicxml: NotenXML.alsText(P.doc), takte: Array.from(P.takte.values()),
                      bestaetigt: Array.from(P.bestaetigt), meta: P.meta, quelle: P.quelle, bilder: P.bilder,
                      anzeige: P.anzeige, geaendert: P.geaendert };
    await vorgang("meta", "readwrite", (s) => s.put(eintrag, "partitur"));
  }

  async function wiederherstellen() {
    const e = await vorgang("meta", "readonly", (s) => s.get("partitur"));
    if (!e || !e.musicxml) return false;
    await verovioLaden();
    oeffnen({ musicxml: e.musicxml, takte: e.takte, bilder: e.bilder || [], meta: e.meta, quelle: e.quelle,
              bestaetigt: e.bestaetigt });
    P.anzeige = e.anzeige || P.anzeige;
    P.geaendert = !!e.geaendert;
    zeichnen();
    return true;
  }

  async function zurueck() {
    spieler.stoppen();
    $("#knopf-abspielen").textContent = "▶ Abspielen";
    if ($("#dialog-takt").open) $("#dialog-takt").close();
    if (P.quelle && P.quelle.art === "bibliothek" && !P.geaendert) { bibliothekOeffnen(); return; }
    if (P.geaendert && P.doc && !(await fragen("Die Änderungen sind noch nicht in der Bibliothek gespeichert. Trotzdem zurück? (Sie bleiben auf diesem Gerät erhalten.)"))) return;
    zeigen("#start");
    letzteZeigen();
  }

  /* ---------- Einstellungen ---------- */

  function einstellungenFuellen() {
    const { adresse, kennwort } = server();
    $("#server-adresse").value = adresse;
    $("#server-kennwort").value = kennwort;
    $("#server-status").textContent = "";
  }

  function serverFelderMerken() {
    try {
      localStorage.setItem("server-adresse", $("#server-adresse").value.trim());
      localStorage.setItem("server-kennwort", $("#server-kennwort").value);
    } catch (_) { /* egal */ }
  }

  async function serverTesten() {
    serverFelderMerken();
    const status = $("#server-status");
    status.textContent = "Verbindung wird geprüft …";
    try {
      await serverWecken((t) => { status.textContent = t; });
      await json("/api/anmelden", { method: "POST" });
      status.textContent = "✓ Verbunden – Kennwort stimmt.";
    } catch (fehler) {
      status.textContent = "✗ " + fehler.message;
    }
  }

  /* ---------- Start ---------- */

  function verbinden() {
    $("#knopf-erkennen").addEventListener("click", erkennen);
    $("#knopf-letzte").addEventListener("click", async () => {
      try { await wiederherstellen(); } catch (f) { melden(f.message); }
    });
    $("#knopf-bibliothek").addEventListener("click", bibliothekOeffnen);
    $("#partitur-zurueck").addEventListener("click", zurueck);
    $("#bibliothek-zurueck").addEventListener("click", () => zeigen("#start"));
    $("#bibliothek-suche").addEventListener("input", bibliothekZeichnen);
    $("#partitur-noten").addEventListener("click", taktAusKlick);
    $("#knopf-abspielen").addEventListener("click", abspielen);
    $("#knopf-transponieren").addEventListener("click", transponierenOeffnen);
    $("#dialog-transponieren").addEventListener("close", transponierenSchliessen);
    $("#knopf-partitur-pdf").addEventListener("click", pdfErstellen);
    $("#knopf-speichern").addEventListener("click", speichernOeffnen);
    $("#dialog-speichern").addEventListener("close", speichernSchliessen);
    enterSchliesst($("#dialog-speichern"), "ok");
    $("#knopf-pruefen-weiter").addEventListener("click", zumNaechsten);
    $("#knopf-partitur-menue").addEventListener("click", () => {
      $("#menue-loeschen").classList.toggle("versteckt", !(P.quelle && P.quelle.art === "bibliothek"));
      $("#dialog-menue").querySelector("[name=tempo]").value = tempo;
      $("#wert-tempo").textContent = tempo;
      $("#dialog-menue").showModal();
    });
    $("#dialog-menue").querySelector("[name=tempo]").addEventListener("input", (e) => {
      tempo = Number(e.target.value);
      $("#wert-tempo").textContent = tempo;
    });
    $("#menue-musicxml").addEventListener("click", () => { $("#dialog-menue").close(); musicxmlTeilen(); });
    $("#menue-loeschen").addEventListener("click", () => { $("#dialog-menue").close(); loeschen(); });
    $("#menue-groesser").addEventListener("click", () => { P.zoom = Math.min(80, P.zoom + 8); zeichnen(); });
    $("#menue-kleiner").addEventListener("click", () => { P.zoom = Math.max(24, P.zoom - 8); zeichnen(); });
    $("#server-testen").addEventListener("click", serverTesten);
    $("#server-adresse").addEventListener("change", serverFelderMerken);
    $("#server-kennwort").addEventListener("change", serverFelderMerken);
    editorVerbinden();
    let breiteVorher = 0;
    window.addEventListener("resize", () => {
      const b = $("#partitur-noten").clientWidth;
      if (!$("#partitur").classList.contains("versteckt") && Math.abs(b - breiteVorher) > 40) { breiteVorher = b; zeichnen(); }
    });
  }

  async function letzteZeigen() {
    const e = await vorgang("meta", "readonly", (s) => s.get("partitur"));
    const da = !!(e && e.musicxml);
    $("#letzte-partitur").classList.toggle("versteckt", !da);
    if (da) $("#letzte-titel").textContent = [e.meta && e.meta.titel, e.meta && e.meta.stimme].filter(Boolean).join(" – ") || "Ohne Titel";
  }

  return { verbinden, workerAntwort, workerAbbruch, wiederherstellen, einstellungenFuellen, serverFelderMerken,
           letzteZeigen, _P: P, _abspielNoten: () => notenZumAbspielen().noten, _spieler: spieler };
})();
