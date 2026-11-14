"""Notensatz: MusicXML -> PDF (A4) über Verovio, optional transponiert."""
from __future__ import annotations

import io
import os

OPTIONEN = {"pageWidth": 2100, "pageHeight": 2970, "scale": 45, "adjustPageHeight": False,
            "footer": "none", "header": "auto", "breaks": "auto", "pageMarginLeft": 100,
            "pageMarginRight": 100, "pageMarginTop": 80, "pageMarginBottom": 80}


def fuer_verovio(musicxml: str) -> str:
    """Verovio fasst eine Mehrtaktpause zu einem Takt zusammen: Vom ersten Takt des Blocks liest es nur,
    was vor der Pausen-Angabe steht, von den übrigen nur den Taktstrich. Zweierlei geht dabei verloren:
    - Endet im letzten Takt eine Volta-Klammer, bleibt sie offen, und die zweite Volte bekommt die
      falsche Nummer. Das Ende der Klammer wandert deshalb an den ersten Takt des Blocks.
    - Textangaben und Studierzeichen im Block verschwinden. Sie wandern vor die Pausen-Angabe.
    Außerdem setzt Verovio den Namen der Stimme links neben die erste Zeile; ein langer Name („Trumpet 2,
    Flugelhorn 2“) nimmt ihr die halbe Breite. Er steht deshalb wie auf gedruckten Stimmen als kleinere
    Zeile unter dem Titel.
    Das gilt nur für den Satz; die gespeicherte Datei bleibt, wie MusicXML es verlangt."""
    import xml.etree.ElementTree as ET
    try:
        wurzel = ET.fromstring(musicxml.encode("utf-8") if isinstance(musicxml, str) else musicxml)
    except ET.ParseError:
        return musicxml
    geaendert = False
    namen = [pn for pn in wurzel.iter("part-name") if (pn.text or "").strip() and pn.get("print-object") != "no"]
    if namen:
        work = wurzel.find("work")
        if work is None:
            work = ET.Element("work")
            wurzel.insert(0, work)
        if work.find("work-number") is None:              # Verovio setzt sie als Untertitel
            zeile = ET.Element("work-number")
            zeile.text = namen[0].text.strip()
            work.insert(0, zeile)
        for pn in namen:
            pn.set("print-object", "no")
        geaendert = True
    for teil in wurzel.iter("part"):
        takte = teil.findall("measure")
        for k, takt in enumerate(takte):
            mr = takt.find("attributes/measure-style/multiple-rest")
            if mr is None or not (mr.text or "").strip().isdigit():
                continue
            n = min(int(mr.text), len(takte) - k)
            if n < 2:
                continue
            for quelle in [b for b in takte[k + n - 1].findall("barline") if b.get("location", "right") == "right"]:
                enden = quelle.findall("ending")
                if not enden:
                    continue
                ziel = next((b for b in takt.findall("barline") if b.get("location", "right") == "right"), None)
                if ziel is None:
                    ziel = ET.SubElement(takt, "barline", location="right")
                for e in enden:
                    quelle.remove(e)
                    ziel.insert(0, e)
                if len(quelle) == 0:
                    takte[k + n - 1].remove(quelle)
                geaendert = True
            angabe = next(a for a in takt.findall("attributes") if a.find("measure-style/multiple-rest") is not None)
            stelle = list(takt).index(angabe)
            texte = [(takt, d) for d in list(takt)[stelle + 1:] if d.tag == "direction"]
            texte += [(m, d) for m in takte[k + 1:k + n] for d in m.findall("direction")]
            for eltern, d in texte:
                eltern.remove(d)
                takt.insert(stelle, d)
                stelle += 1
                geaendert = True
    return ET.tostring(wurzel, encoding="unicode", xml_declaration=True) if geaendert else musicxml


def pdf_erzeugen(musicxml: str, transponieren: str = "") -> bytes:
    import cairosvg
    import verovio
    from pypdf import PdfReader, PdfWriter
    tk = verovio.toolkit()
    # Der Pfad zu Schriften und Symbolen gilt in Verovio nur für den Thread, der das Modul geladen hat.
    # Der Server rechnet in Hintergrund-Threads, deshalb hier ausdrücklich setzen.
    tk.setResourcePath(os.path.join(os.path.dirname(verovio.__file__), "data"))
    optionen = dict(OPTIONEN)
    if transponieren:
        optionen["transpose"] = transponieren
    tk.setOptions(optionen)
    if not tk.loadData(fuer_verovio(musicxml)):
        raise ValueError("Die Noten konnten nicht gelesen werden")
    schreiber = PdfWriter()
    for i in range(1, tk.getPageCount() + 1):
        pdf = cairosvg.svg2pdf(bytestring=tk.renderToSVG(i).encode("utf-8"))
        for seite in PdfReader(io.BytesIO(pdf)).pages:
            schreiber.add_page(seite)
    puffer = io.BytesIO()
    schreiber.write(puffer)
    return puffer.getvalue()
