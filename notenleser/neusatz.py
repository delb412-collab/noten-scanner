"""MusicXML (vom Notenleser) -> sauber gesetztes PDF über Verovio.
Vorher: Balken nach Taktart (music21), Titel/Instrument setzen."""
import sys, io
import music21 as m21
import verovio, cairosvg
from pypdf import PdfWriter, PdfReader


def vorbereiten(quelle, titel, instrument):
    s = m21.converter.parse(quelle)
    for teil in s.parts:
        teil.partName = instrument
        for takt in teil.getElementsByClass(m21.stream.Measure):
            stimmen = list(takt.voices) or [takt]
            for st in stimmen:
                for n in st.notes:
                    n.beams = m21.beam.Beams()
                    n.stemDirection = None
                try:
                    st.makeBeams(inPlace=True)
                except Exception:
                    pass
    s.metadata = m21.metadata.Metadata()
    s.metadata.title = titel
    s.metadata.composer = ""
    ziel = quelle.replace(".musicxml", ".satz.musicxml")
    s.write("musicxml", fp=ziel)
    return ziel


def setzen(musicxml, pdf_ziel):
    tk = verovio.toolkit()
    tk.setOptions({"pageWidth": 2100, "pageHeight": 2970, "scale": 45, "adjustPageHeight": False,
                   "footer": "none", "header": "auto", "breaks": "auto", "pageMarginLeft": 100,
                   "pageMarginRight": 100, "pageMarginTop": 80, "pageMarginBottom": 80})
    tk.loadFile(musicxml)
    schreiber = PdfWriter()
    for i in range(1, tk.getPageCount() + 1):
        pdf = cairosvg.svg2pdf(bytestring=tk.renderToSVG(i).encode())
        for seite in PdfReader(io.BytesIO(pdf)).pages:
            schreiber.add_page(seite)
    with open(pdf_ziel, "wb") as f:
        schreiber.write(f)
    return tk.getPageCount()


if __name__ == "__main__":
    quelle, titel, instrument, ziel = sys.argv[1:5]
    vorbereitet = vorbereiten(quelle, titel, instrument)
    print(ziel, setzen(vorbereitet, ziel), "Seite(n)")
