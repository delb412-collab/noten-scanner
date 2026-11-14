/* Noten-Scanner – Handy-App.
 * Fotos aufnehmen oder auswählen, jede Seite wird sofort im Hintergrund bereinigt
 * (worker.js), danach ein PDF erstellen und teilen/sichern. Seiten und Einstellungen
 * werden auf dem Gerät gespeichert, damit nichts verloren geht, wenn das iPhone die App
 * zwischendurch beendet. */
"use strict";

const VERSION = "__VERSION__";
const MAX_KANTE = 3200;            // Fotos vor der Bereinigung auf höchstens 3200 px verkleinern
const STANDARD = { modus: "glatt", linien_reparieren: true, bleistift_entfernen: false, empfindlichkeit: 0.8, dpi: 300 };

const $ = (auswahl) => document.querySelector(auswahl);
const zustand = { seiten: [], einstellungen: { ...STANDARD }, pdf: null, pdfName: "" };
let worker = null;
let workerBereit = false;
let laeuft = null;                 // id der Seite, die gerade bereinigt wird, oder "pdf"
let neustarts = 0;

/* ---------- Start ---------- */

document.addEventListener("DOMContentLoaded", async () => {
  verbinden();
  Partitur.verbinden();
  await laden();
  zeichnen();
  Partitur.letzteZeigen();
  workerStarten();
  installHinweis();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => { /* ohne Offline-Modus weiter */ });
  }
});

function verbinden() {
  $("#eingabe-kamera").addEventListener("change", fotosHinzufuegen);
  $("#eingabe-fotos").addEventListener("change", fotosHinzufuegen);
  $("#knopf-pdf").addEventListener("click", pdfAnfordern);
  $("#knopf-teilen").addEventListener("click", pdfTeilen);
  $("#knopf-ansehen").addEventListener("click", pdfAnsehen);
  $("#knopf-neu").addEventListener("click", neuBeginnen);
  $("#knopf-einstellungen").addEventListener("click", einstellungenOeffnen);
  $("#dialog-einstellungen").addEventListener("close", einstellungenSchliessen);
  $("#dialog-einstellungen").querySelector("[name=empfindlichkeit]").addEventListener("input", (e) => {
    $("#wert-empfindlichkeit").textContent = Number(e.target.value).toFixed(2);
  });
  enterSchliesst($("#dialog-einstellungen"), "ok");
  $("#knopf-umschalten").addEventListener("click", ansichtUmschalten);
  $("#knopf-erneut").addEventListener("click", () => { neustarts = 0; workerStarten(); });
  $("#pdf-name").addEventListener("change", () => { zustand.pdfName = $("#pdf-name").value.trim(); metaSpeichern(); });
  for (const knopf of document.querySelectorAll("[data-schliesst]")) {
    knopf.addEventListener("click", () => {
      $("#" + knopf.dataset.schliesst).classList.add("versteckt");
      try { localStorage.setItem("hinweis-" + knopf.dataset.schliesst, "weg"); } catch (_) { /* egal */ }
    });
  }
}

/* ---------- Worker (Python im Hintergrund) ---------- */

function workerStarten() {
  if (worker) worker.terminate();
  Partitur.workerAbbruch();          // offene PNG-Anfragen der Notenerkennung nicht ewig warten lassen
  workerBereit = false;
  $("#laden").classList.remove("versteckt", "fehler");
  $("#laden-text").textContent = "App wird vorbereitet …";
  $("#laden-detail").textContent = "Beim ersten Start werden ca. 30 MB geladen. Danach funktioniert die App auch ohne Internet.";
  $("#knopf-erneut").classList.add("versteckt");
  worker = new Worker("worker.js?v=" + VERSION);
  worker.onmessage = workerNachricht;
  worker.onerror = () => {
    // Absturz (meist zu wenig Speicher): neu starten, aktuelle Seite sparsamer wiederholen
    Partitur.workerAbbruch();
    const seite = finden(laeuft);
    if (seite) wiederholenSparsam(seite, "Die Bereinigung wurde abgebrochen");
    laeuft = null;
    if (neustarts++ < 3) setTimeout(workerStarten, 500);
  };
}

async function workerNachricht(ereignis) {
  const n = ereignis.data;
  if (n.id && String(n.id).startsWith("png-") && Partitur.workerAntwort(n)) return;
  if (n.typ === "status") {
    $("#laden-text").textContent = n.text;
  } else if (n.typ === "bereit") {
    workerBereit = true;
    $("#laden").classList.add("versteckt");
    weiter();
  } else if (n.typ === "startfehler") {
    $("#laden").classList.add("fehler");
    $("#laden-text").textContent = "Die App konnte nicht vollständig geladen werden.";
    $("#laden-detail").textContent = "Beim ersten Start braucht sie Internet. Bitte Verbindung prüfen und erneut versuchen. (Technisch: " + n.text + ")";
    $("#knopf-erneut").classList.remove("versteckt");
  } else if (n.typ === "seite_fertig") {
    const seite = finden(n.id);
    if (seite) {
      try {
        seite.seite = n.seite;
        seite.vorschauBlob = await vorschauErstellen(new Uint8Array(n.vorschau), n.vb, n.vh);
        seite.infos = n.infos;
        seite.status = "fertig";
        seite.fehler = "";
        urlsErneuern(seite);
        await seiteSpeichern(seite);
      } catch (fehler) {             // sonst bliebe die Warteschlange für immer stehen
        seite.status = "fehler";
        seite.fehler = "Vorschau konnte nicht erstellt werden";
      }
    }
    laeuft = null;
    zeichnen();
    weiter();
  } else if (n.typ === "pdf_fertig") {
    laeuft = null;
    const name = dateiname();
    zustand.pdf = new File([n.pdf], name + ".pdf", { type: "application/pdf" });
    $("#pdf-info").textContent = `„${name}.pdf“ – ${fertigeSeiten().length} Seite(n), ${(n.pdf.byteLength / 1e6).toFixed(1)} MB`;
    $("#pdf-ergebnis").classList.remove("versteckt");
    zeichnen();
    weiter();
  } else if (n.typ === "fehler") {
    if (n.id === "pdf") {
      laeuft = null;
      melden("PDF konnte nicht erstellt werden: " + n.text);
      zeichnen();
    } else {
      const seite = finden(n.id);
      laeuft = null;
      if (seite && n.speicher) {
        wiederholenSparsam(seite, "Speicher knapp");
        workerStarten();          // nach einem Speicherfehler Python sauber neu starten
        return;
      }
      if (seite) {
        seite.status = "fehler";
        seite.fehler = n.text;
        await seiteSpeichern(seite);
      }
      zeichnen();
      weiter();
    }
  }
}

function wiederholenSparsam(seite, grund) {
  if ((seite.einstellungenGenutzt?.dpi ?? 300) > 200) {
    seite.dpiZwang = 200;
    seite.status = "wartet";
    melden(grund + " – die Seite wird mit 200 dpi erneut bereinigt.");
  } else {
    seite.status = "fehler";
    seite.fehler = grund + ". Bitte ein kleineres Foto versuchen.";
  }
  zeichnen();
}

/* Nächste wartende Seite an den Worker geben (immer nur eine zur Zeit) */
async function weiter() {
  if (!workerBereit || laeuft) return;
  const seite = zustand.seiten.find((s) => s.status === "wartet");
  if (!seite) { zeichnen(); return; }
  laeuft = seite.id;
  seite.status = "arbeitet";
  zeichnen();
  try {
    const { rgba, breite, hoehe } = await fotoLesen(seite.original, MAX_KANTE);
    seite.einstellungenGenutzt = { ...zustand.einstellungen, ...(seite.dpiZwang ? { dpi: seite.dpiZwang } : {}) };
    worker.postMessage({ typ: "seite", id: seite.id, rgba, breite, hoehe, einstellungen: seite.einstellungenGenutzt }, [rgba]);
  } catch (fehler) {
    seite.status = "fehler";
    seite.fehler = "Foto konnte nicht gelesen werden";
    laeuft = null;
    zeichnen();
    weiter();
  }
}

/* ---------- Fotos ---------- */

async function fotosHinzufuegen(ereignis) {
  const dateien = [...ereignis.target.files].filter((d) => d.type.startsWith("image/") || /\.(jpe?g|png|heic|heif|webp)$/i.test(d.name));
  ereignis.target.value = "";
  if (!dateien.length) return;
  zustand.pdf = null;
  $("#pdf-ergebnis").classList.add("versteckt");
  for (const datei of dateien) {
    const seite = { id: "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7),
                    original: datei, status: "wartet", fehler: "" };
    try {
      seite.daumenBlob = await daumenErstellen(datei);
    } catch (_) {
      seite.status = "fehler";
      seite.fehler = "Dieses Bild kann nicht gelesen werden";
    }
    urlsErneuern(seite);
    zustand.seiten.push(seite);
    await seiteSpeichern(seite);
  }
  await metaSpeichern();
  zeichnen();
  weiter();
  if (dateien.length > 1) melden(dateien.length + " Fotos hinzugefügt");
}

async function bildOeffnen(blob) {
  const url = URL.createObjectURL(blob);
  const bild = new Image();
  bild.src = url;
  try {
    await bild.decode();     // der Browser dreht das Foto dabei richtig (EXIF)
  } finally {
    URL.revokeObjectURL(url);
  }
  return bild;
}

async function fotoLesen(blob, maxKante) {
  const bild = await bildOeffnen(blob);
  const f = Math.min(1, maxKante / Math.max(bild.naturalWidth, bild.naturalHeight));
  const breite = Math.max(1, Math.round(bild.naturalWidth * f));
  const hoehe = Math.max(1, Math.round(bild.naturalHeight * f));
  const leinwand = document.createElement("canvas");
  leinwand.width = breite;
  leinwand.height = hoehe;
  const ctx = leinwand.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(bild, 0, 0, breite, hoehe);
  const rgba = ctx.getImageData(0, 0, breite, hoehe).data.buffer;
  leinwand.width = leinwand.height = 0;        // Speicher sofort freigeben (wichtig auf dem iPhone)
  return { rgba, breite, hoehe };
}

async function daumenErstellen(blob) {
  const bild = await bildOeffnen(blob);
  const f = 360 / Math.max(bild.naturalWidth, bild.naturalHeight);
  return leinwandAlsJpeg(Math.round(bild.naturalWidth * f), Math.round(bild.naturalHeight * f),
                         (ctx, b, h) => ctx.drawImage(bild, 0, 0, b, h));
}

async function vorschauErstellen(grau, breite, hoehe) {
  return leinwandAlsJpeg(breite, hoehe, (ctx) => {
    const bild = ctx.createImageData(breite, hoehe);
    const d = bild.data;
    for (let i = 0, j = 0; i < grau.length; i++, j += 4) {
      d[j] = d[j + 1] = d[j + 2] = grau[i];
      d[j + 3] = 255;
    }
    ctx.putImageData(bild, 0, 0);
  });
}

function leinwandAlsJpeg(breite, hoehe, malen) {
  const leinwand = document.createElement("canvas");
  leinwand.width = breite;
  leinwand.height = hoehe;
  malen(leinwand.getContext("2d"), breite, hoehe);
  return new Promise((fertig, fehler) => leinwand.toBlob((blob) => {
    leinwand.width = leinwand.height = 0;
    blob ? fertig(blob) : fehler(new Error("Bild konnte nicht erstellt werden"));
  }, "image/jpeg", 0.85));
}

/* ---------- Seitenliste ---------- */

function zeichnen() {
  const liste = $("#seiten");
  liste.textContent = "";
  zustand.seiten.forEach((seite, index) => liste.appendChild(seitenKarte(seite, index)));
  const anzahl = zustand.seiten.length;
  $("#leer").classList.toggle("versteckt", anzahl > 0);
  $("#bereich-pdf").classList.toggle("versteckt", anzahl === 0);
  if (!$("#pdf-name").value) $("#pdf-name").value = zustand.pdfName || standardName();
  const offen = zustand.seiten.some((s) => s.status === "wartet" || s.status === "arbeitet");
  const knopf = $("#knopf-pdf");
  knopf.disabled = laeuft === "pdf" || offen || fertigeSeiten().length === 0;
  knopf.textContent = laeuft === "pdf" ? "PDF wird erstellt …"
    : offen ? "Bitte warten – Seiten werden bereinigt …" : "📄 PDF erstellen";
  $("#knopf-erkennen").disabled = offen || fertigeSeiten().length === 0;
  if (!zustand.pdf) $("#pdf-ergebnis").classList.add("versteckt");
}

function seitenKarte(seite, index) {
  const karte = document.createElement("li");
  karte.className = "seite";
  const bild = document.createElement("div");
  bild.className = "bild" + (seite.status === "fertig" ? "" : " arbeitet");
  const img = document.createElement("img");
  img.alt = "Seite " + (index + 1);
  img.src = seite.status === "fertig" ? seite.vorschauUrl : (seite.daumenUrl || "");
  bild.appendChild(img);
  if (seite.status === "wartet" || seite.status === "arbeitet") {
    const kreisel = document.createElement("div");
    kreisel.className = "kreisel";
    bild.appendChild(kreisel);
  }
  const nummer = document.createElement("span");
  nummer.className = "nummer";
  nummer.textContent = index + 1;
  bild.appendChild(nummer);
  bild.addEventListener("click", () => ansichtOeffnen(seite, index));
  karte.appendChild(bild);

  const status = document.createElement("div");
  const [text, art] = statusText(seite);
  status.className = "status " + art;
  status.textContent = text;
  karte.appendChild(status);

  const leiste = document.createElement("div");
  leiste.className = "leiste";
  leiste.append(
    kleinerKnopf("◀", "Nach vorne", index === 0, () => verschieben(index, -1)),
    kleinerKnopf("▶", "Nach hinten", index === zustand.seiten.length - 1, () => verschieben(index, 1)),
    seite.status === "fehler" ? kleinerKnopf("↻", "Erneut versuchen", false, () => erneut(seite)) : "",
    kleinerKnopf("🗑", "Seite entfernen", false, () => entfernen(seite)));
  karte.appendChild(leiste);
  return karte;
}

function kleinerKnopf(zeichen, beschriftung, aus, aktion) {
  const knopf = document.createElement("button");
  knopf.type = "button";
  knopf.textContent = zeichen;
  knopf.setAttribute("aria-label", beschriftung);
  knopf.disabled = aus;
  knopf.addEventListener("click", aktion);
  return knopf;
}

function statusText(seite) {
  if (seite.status === "wartet") return ["Wartet …", ""];
  if (seite.status === "arbeitet") return ["Wird bereinigt …", ""];
  if (seite.status === "fehler") return ["Fehler: " + (seite.fehler || "unbekannt"), "fehler"];
  const i = seite.infos || {};
  const teile = [];
  if (i.linienabstand == null) return ["Keine Notenlinien erkannt – bitte prüfen", "warnung"];
  teile.push(i.blatt_gefunden ? "Blatt entzerrt" : "ohne Entzerrung");
  if (i.drehung) teile.push(i.drehung === 180 ? "lag kopfüber" : "lag quer");
  if (i.notensysteme) teile.push(i.notensysteme + " Notenzeilen");
  if (i.selbstkontrolle) teile.push(i.selbstkontrolle + " Stelle(n) wie im Original");
  if (i.dpi && i.dpi !== 300) teile.push(i.dpi + " dpi");
  if (seite.veraltet) return [teile.join(" · ") + " · alte Einstellungen", "warnung"];
  return [teile.join(" · "), ""];
}

async function verschieben(index, richtung) {
  const ziel = index + richtung;
  if (ziel < 0 || ziel >= zustand.seiten.length) return;
  [zustand.seiten[index], zustand.seiten[ziel]] = [zustand.seiten[ziel], zustand.seiten[index]];
  zustand.pdf = null;
  await metaSpeichern();
  zeichnen();
}

async function entfernen(seite) {
  if (!(await fragen("Diese Seite entfernen?"))) return;
  zustand.seiten = zustand.seiten.filter((s) => s !== seite);
  urlsFreigeben(seite);
  zustand.pdf = null;
  await seiteLoeschen(seite.id);
  await metaSpeichern();
  zeichnen();
}

function erneut(seite) {
  seite.status = "wartet";
  seite.fehler = "";
  delete seite.dpiZwang;
  zeichnen();
  weiter();
}

function finden(id) {
  return zustand.seiten.find((s) => s.id === id);
}

function fertigeSeiten() {
  return zustand.seiten.filter((s) => s.status === "fertig");
}

function urlsErneuern(seite) {
  urlsFreigeben(seite);
  if (seite.daumenBlob) seite.daumenUrl = URL.createObjectURL(seite.daumenBlob);
  if (seite.vorschauBlob) seite.vorschauUrl = URL.createObjectURL(seite.vorschauBlob);
}

function urlsFreigeben(seite) {
  if (seite.daumenUrl) URL.revokeObjectURL(seite.daumenUrl);
  if (seite.vorschauUrl) URL.revokeObjectURL(seite.vorschauUrl);
  seite.daumenUrl = seite.vorschauUrl = "";
}

/* ---------- Großansicht ---------- */

let ansichtSeite = null;
let ansichtVorher = false;

function ansichtOeffnen(seite, index) {
  ansichtSeite = seite;
  ansichtVorher = seite.status !== "fertig";
  $("#ansicht-titel").textContent = "Seite " + (index + 1);
  $("#ansicht-infos").textContent = statusText(seite)[0];
  ansichtZeigen();
  $("#dialog-ansicht").showModal();
}

function ansichtUmschalten() {
  ansichtVorher = !ansichtVorher;
  ansichtZeigen();
}

function ansichtZeigen() {
  const seite = ansichtSeite;
  const vorher = ansichtVorher || seite.status !== "fertig";
  if (seite.ansichtUrl) URL.revokeObjectURL(seite.ansichtUrl);
  seite.ansichtUrl = vorher ? URL.createObjectURL(seite.original) : "";
  $("#ansicht-bild").src = vorher ? seite.ansichtUrl : seite.vorschauUrl;
  $("#knopf-umschalten").textContent = vorher ? "Nachher" : "Vorher";
  $("#knopf-umschalten").disabled = seite.status !== "fertig";
}

/* ---------- PDF ---------- */

function standardName() {
  return "Noten " + new Date().toLocaleDateString("de-DE");
}

function dateiname() {
  const name = ($("#pdf-name").value.trim() || standardName()).normalize("NFC").replace(/[\\/:*?"<>|]+/g, "-");
  return name.slice(0, 120);
}

function pdfAnfordern() {
  const seiten = fertigeSeiten();
  if (!seiten.length || laeuft) return;
  laeuft = "pdf";
  zustand.pdf = null;
  zeichnen();
  worker.postMessage({ typ: "pdf", id: "pdf", titel: dateiname(), seiten: seiten.map((s) => s.seite.slice(0)) });
}

async function pdfTeilen() {
  const datei = zustand.pdf;
  if (!datei) return;
  if (navigator.canShare && navigator.canShare({ files: [datei] })) {
    try {
      await navigator.share({ files: [datei], title: datei.name });
    } catch (fehler) {
      if (fehler.name !== "AbortError") melden("Teilen nicht möglich: " + fehler.message);
    }
  } else {
    herunterladen(datei);
  }
}

function pdfAnsehen() {
  if (!zustand.pdf) return;
  const url = URL.createObjectURL(zustand.pdf);
  const fenster = window.open(url, "_blank");
  if (!fenster) herunterladen(zustand.pdf);
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

function herunterladen(datei) {
  const url = URL.createObjectURL(datei);
  const link = document.createElement("a");
  link.href = url;
  link.download = datei.name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

async function neuBeginnen() {
  if (zustand.seiten.length && !(await fragen("Alle Seiten entfernen und ein neues Stück beginnen?"))) return;
  for (const seite of zustand.seiten) urlsFreigeben(seite);
  zustand.seiten = [];
  zustand.pdf = null;
  zustand.pdfName = "";
  $("#pdf-name").value = "";
  await alleLoeschen();
  await metaSpeichern();
  zeichnen();
}

/* ---------- Einstellungen ---------- */

function einstellungenOeffnen() {
  const formular = $("#dialog-einstellungen form");
  const e = zustand.einstellungen;
  formular.querySelector(`[name=modus][value=${e.modus}]`).checked = true;
  formular.querySelector(`[name=dpi][value="${e.dpi}"]`).checked = true;
  formular.linien_reparieren.checked = e.linien_reparieren;
  formular.bleistift_entfernen.checked = e.bleistift_entfernen;
  formular.empfindlichkeit.value = e.empfindlichkeit;
  $("#wert-empfindlichkeit").textContent = Number(e.empfindlichkeit).toFixed(2);
  const vorhanden = zustand.seiten.length > 0;
  $("#hinweis-neu").classList.toggle("versteckt", !vorhanden);
  $("#knopf-neu-bereinigen").classList.toggle("versteckt", !vorhanden);
  Partitur.einstellungenFuellen();
  $("#dialog-einstellungen").showModal();
}

async function einstellungenSchliessen() {
  Partitur.serverFelderMerken();
  const dialog = $("#dialog-einstellungen");
  const formular = dialog.querySelector("form");
  const neu = {
    modus: formular.querySelector("[name=modus]:checked").value,
    dpi: Number(formular.querySelector("[name=dpi]:checked").value),
    linien_reparieren: formular.linien_reparieren.checked,
    bleistift_entfernen: formular.bleistift_entfernen.checked,
    empfindlichkeit: Number(formular.empfindlichkeit.value),
  };
  // Feld für Feld vergleichen – die Reihenfolge der Schlüssel ist nicht überall dieselbe
  const gleich = (a, b) => Object.keys(STANDARD).every((k) => a[k] === b[k]);
  const geaendert = !gleich(neu, zustand.einstellungen);
  zustand.einstellungen = neu;
  await metaSpeichern();
  for (const seite of zustand.seiten) {
    if (seite.status === "fertig" && seite.einstellungenGenutzt) {
      seite.veraltet = !gleich({ ...seite.einstellungenGenutzt, dpi: neu.dpi }, neu);
    }
  }
  if (dialog.returnValue === "neu") {
    for (const seite of zustand.seiten) {
      seite.status = "wartet";
      seite.veraltet = false;
      delete seite.dpiZwang;
    }
    zustand.pdf = null;
    melden("Alle Seiten werden neu bereinigt");
    weiter();
  } else if (geaendert && zustand.seiten.length) {
    melden("Gilt für neue Fotos – „Alle Seiten neu bereinigen“ in den Einstellungen wendet es auf alle an");
  }
  zeichnen();
}

/* ---------- Kleinigkeiten ---------- */

let meldungsZeit = 0;
function melden(text) {
  const feld = $("#meldung");
  feld.textContent = text;
  feld.classList.add("sichtbar");
  clearTimeout(meldungsZeit);
  meldungsZeit = setTimeout(() => feld.classList.remove("sichtbar"), 3500);
}

/** Return/„Fertig“ auf der Tastatur in einem Textfeld eines Dialogs: Ohne das würde der Browser den
 *  ersten Knopf des Formulars auslösen (dort steht oft „Abbrechen“ oder „Alle Seiten neu bereinigen“). */
function enterSchliesst(dialog, wert) {
  dialog.addEventListener("keydown", (e) => {
    const feld = e.target;
    if (e.key !== "Enter" || feld.tagName !== "INPUT" || ["checkbox", "radio", "button", "submit"].includes(feld.type)) return;
    e.preventDefault();
    dialog.close(wert);
  });
}

function fragen(text) {
  const dialog = $("#dialog-frage");
  $("#frage-text").textContent = text;
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((antwort) => dialog.addEventListener("close", () => antwort(dialog.returnValue === "ja"), { once: true }));
}

function installHinweis() {
  const iphone = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const alsApp = window.navigator.standalone || window.matchMedia("(display-mode: standalone)").matches;
  let weg = false;
  try { weg = localStorage.getItem("hinweis-installieren") === "weg"; } catch (_) { /* egal */ }
  $("#installieren").classList.toggle("versteckt", !iphone || alsApp || weg);
}

/* ---------- Speichern auf dem Gerät (IndexedDB) ---------- */

let datenbank = null;
function db() {
  if (!datenbank) {
    datenbank = new Promise((fertig, fehler) => {
      const anfrage = indexedDB.open("noten-scanner", 1);
      anfrage.onupgradeneeded = () => {
        anfrage.result.createObjectStore("seiten", { keyPath: "id" });
        anfrage.result.createObjectStore("meta");
      };
      anfrage.onsuccess = () => fertig(anfrage.result);
      anfrage.onerror = () => fehler(anfrage.error);
    });
  }
  return datenbank;
}

async function vorgang(speicher, modus, aktion) {
  try {
    const verbindung = await db();
    return await new Promise((fertig, fehler) => {
      const t = verbindung.transaction(speicher, modus);
      const ergebnis = aktion(t.objectStore(speicher));
      t.oncomplete = () => fertig(ergebnis && ergebnis.result);
      t.onerror = () => fehler(t.error);
      t.onabort = () => fehler(t.error);
    });
  } catch (_) {
    return undefined;   // ohne Speicher weiterarbeiten (z. B. privates Surfen)
  }
}

function seiteSpeichern(seite) {
  const eintrag = { id: seite.id, original: seite.original, daumenBlob: seite.daumenBlob, seite: seite.seite,
                    vorschauBlob: seite.vorschauBlob, infos: seite.infos, fehler: seite.fehler,
                    status: seite.status === "fertig" || seite.status === "fehler" ? seite.status : "wartet",
                    einstellungenGenutzt: seite.einstellungenGenutzt };
  return vorgang("seiten", "readwrite", (s) => s.put(eintrag));
}

function seiteLoeschen(id) {
  return vorgang("seiten", "readwrite", (s) => s.delete(id));
}

function alleLoeschen() {
  return vorgang("seiten", "readwrite", (s) => s.clear());
}

function metaSpeichern() {
  const meta = { reihenfolge: zustand.seiten.map((s) => s.id), einstellungen: zustand.einstellungen,
                 pdfName: zustand.pdfName, version: VERSION };
  return vorgang("meta", "readwrite", (s) => s.put(meta, "zustand"));
}

async function laden() {
  const meta = await vorgang("meta", "readonly", (s) => s.get("zustand"));
  if (!meta) return;
  zustand.einstellungen = { ...STANDARD, ...(meta.einstellungen || {}) };
  zustand.pdfName = meta.pdfName || "";
  $("#pdf-name").value = zustand.pdfName;
  const alle = (await vorgang("seiten", "readonly", (s) => s.getAll())) || [];
  const nachId = new Map(alle.map((e) => [e.id, e]));
  for (const id of meta.reihenfolge || []) {
    const e = nachId.get(id);
    if (!e || !e.original) continue;
    const seite = { ...e };
    if (seite.status === "fertig" && !seite.seite) seite.status = "wartet";
    urlsErneuern(seite);
    zustand.seiten.push(seite);
  }
}
