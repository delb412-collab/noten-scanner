/* Noten-Scanner: Bildbereinigung im Hintergrund.
 * Läuft als Web Worker, damit die Oberfläche bedienbar bleibt. Die eigentliche Arbeit macht
 * dasselbe Python-Programm wie auf dem PC (Paket "notenscanner"), hier über Pyodide
 * (Python im Browser) mit numpy und OpenCV. Alles bleibt auf dem Gerät. */
"use strict";

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
const VERSION = "__VERSION__";
let web = null;

const bereit = starten();

async function starten() {
  try {
    melden({ typ: "status", text: "Python wird geladen …" });
    importScripts(PYODIDE + "pyodide.js");
    const pyodide = await loadPyodide({ indexURL: PYODIDE });
    melden({ typ: "status", text: "Bildverarbeitung wird geladen …" });
    await pyodide.loadPackage(["numpy", "opencv-python"]);
    const antwort = await fetch("notenscanner.zip?v=" + VERSION);
    if (!antwort.ok) throw new Error("notenscanner.zip nicht gefunden (" + antwort.status + ")");
    pyodide.unpackArchive(await antwort.arrayBuffer(), "zip", { extractDir: "/home/pyodide/app" });
    pyodide.runPython("import sys; sys.path.insert(0, '/home/pyodide/app')");
    web = pyodide.pyimport("notenscanner.web");
    melden({ typ: "bereit" });
  } catch (fehler) {
    melden({ typ: "startfehler", text: fehlertext(fehler) });
    throw fehler;
  }
}

self.onmessage = async (ereignis) => {
  const auftrag = ereignis.data;
  try {
    await bereit;
    if (auftrag.typ === "seite") seiteBereinigen(auftrag);
    else if (auftrag.typ === "pdf") pdfErstellen(auftrag);
    else if (auftrag.typ === "png") pngErstellen(auftrag);
  } catch (fehler) {
    melden({ typ: "fehler", id: auftrag.id, text: fehlertext(fehler), speicher: istSpeicherfehler(fehler) });
  }
};

function seiteBereinigen(auftrag) {
  const rgba = new Uint8Array(auftrag.rgba);
  const ergebnis = web.seite_bereinigen(rgba, auftrag.breite, auftrag.hoehe, JSON.stringify(auftrag.einstellungen));
  try {
    const [gepackt, vorschau, vb, vh, infos] = ergebnis.toJs();
    const seite = alsBytes(gepackt);
    const klein = alsBytes(vorschau);
    melden({ typ: "seite_fertig", id: auftrag.id, seite: seite.buffer, vorschau: klein.buffer,
             vb: Number(vb), vh: Number(vh), infos: JSON.parse(String(infos)) },
           [seite.buffer, klein.buffer]);
  } finally {
    ergebnis.destroy();
  }
}

function pdfErstellen(auftrag) {
  const seiten = auftrag.seiten.map((puffer) => new Uint8Array(puffer));
  const pdf = alsBytes(web.pdf_erstellen(seiten, auftrag.titel));
  melden({ typ: "pdf_fertig", id: auftrag.id, pdf: pdf.buffer }, [pdf.buffer]);
}

/* Bereinigte Seite als PNG für die Notenerkennung auf dem Server */
function pngErstellen(auftrag) {
  const png = alsBytes(web.seite_als_png(new Uint8Array(auftrag.seite)));
  melden({ typ: "png_fertig", id: auftrag.id, png: png.buffer }, [png.buffer]);
}

/* Python-bytes (je nach Pyodide-Version Uint8Array oder Proxy) -> eigenständiges Uint8Array */
function alsBytes(wert) {
  if (wert instanceof Uint8Array) {
    return wert.byteOffset === 0 && wert.byteLength === wert.buffer.byteLength ? wert : wert.slice();
  }
  if (wert && typeof wert.getBuffer === "function") {        // Python-Objekt mit Pufferschnittstelle
    const puffer = wert.getBuffer("u8");
    try {
      return new Uint8Array(puffer.data);                    // Kopie aus dem Python-Speicher
    } finally {
      puffer.release();
      if (typeof wert.destroy === "function") wert.destroy();
    }
  }
  if (wert && typeof wert.toJs === "function") {
    const umgewandelt = wert.toJs();
    if (typeof wert.destroy === "function") wert.destroy();
    if (umgewandelt instanceof Uint8Array || ArrayBuffer.isView(umgewandelt)) return alsBytes(umgewandelt);
  }
  if (ArrayBuffer.isView(wert)) {
    return new Uint8Array(wert.buffer.slice(wert.byteOffset, wert.byteOffset + wert.byteLength));
  }
  throw new Error("Unerwartete Antwort aus Python");
}

function melden(nachricht, uebertragen) {
  self.postMessage(nachricht, uebertragen || []);
}

function fehlertext(fehler) {
  const text = String((fehler && fehler.message) || fehler);
  // Bei Python-Fehlern die letzte Zeile (Fehlerart und Meldung) zeigen
  const zeilen = text.trim().split("\n");
  return zeilen[zeilen.length - 1].slice(0, 300);
}

function istSpeicherfehler(fehler) {
  return /MemoryError|out of memory|Cannot enlarge memory|Out of bounds memory/i.test(String((fehler && fehler.message) || fehler));
}
