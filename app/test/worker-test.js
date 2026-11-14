/* Test-Ersatz für worker.js: gleiches Nachrichtenformat, rechnet aber über den Testserver mit
   dem echten Python-Programm (statt Pyodide im Browser). */
const speicherfehler = self.location.search.includes("speicherfehler");
self.postMessage({ typ: "status", text: "Python wird geladen …" });
setTimeout(() => self.postMessage({ typ: "bereit" }), 300);
const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const zuB64 = (puffer) => { let s = ""; const u = new Uint8Array(puffer); for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000)); return btoa(s); };
self.onmessage = async (e) => {
  const a = e.data;
  if (a.typ === "seite") {
    const r = await fetch("/__test/seite", { method: "POST", body: a.rgba, headers: { "X-Breite": a.breite, "X-Hoehe": a.hoehe,
      "X-Einstellungen": JSON.stringify(a.einstellungen), "X-Speicherfehler": speicherfehler ? "1" : "0" } });
    const j = await r.json();
    if (j.fehler) { self.postMessage({ typ: "fehler", id: a.id, text: j.fehler, speicher: true }); return; }
    const seite = b64(j.seite), vorschau = b64(j.vorschau);
    self.postMessage({ typ: "seite_fertig", id: a.id, seite: seite.buffer, vorschau: vorschau.buffer, vb: j.vb, vh: j.vh, infos: JSON.parse(j.infos) }, [seite.buffer, vorschau.buffer]);
  } else if (a.typ === "png") {
    const r = await fetch("/__test/png", { method: "POST", body: a.seite });
    const png = await r.arrayBuffer();
    self.postMessage({ typ: "png_fertig", id: a.id, png }, [png]);
  } else if (a.typ === "pdf") {
    const r = await fetch("/__test/pdf", { method: "POST", body: JSON.stringify({ titel: a.titel, seiten: a.seiten.map(zuB64) }) });
    const pdf = await r.arrayBuffer();
    self.postMessage({ typ: "pdf_fertig", id: a.id, pdf }, [pdf]);
  }
};
