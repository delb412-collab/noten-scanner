"""Kommandozeile: python noten_scanner.py <Ordner oder Dateien> [Optionen]"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__
from .stapel import PDF_MODI, Rueckmeldung, stapel_verarbeiten, standard_jobs
from .verarbeitung import FORMATE, MODI, Einstellungen

BESCHREIBUNG = """\
Bereinigt eingescannte oder mit dem Handy fotografierte Noten:
Blatt finden und entzerren, gerade drehen, Schatten entfernen,
Hintergrund reinweiß, Noten tiefschwarz, Flecken und Ränder weg.
"""

BEISPIELE = """\
Beispiele:
  python noten_scanner.py Fotos/
      bereinigt alle Bilder im Ordner "Fotos" (mit Unterordnern) nach "Fotos_bereinigt"
      und erstellt ein PDF pro Ordner

  python noten_scanner.py Fotos/ -o Archiv/ --modus hart --nur-pdf
      nur PDFs mit reinem Schwarz/Weiß (sehr kleine Dateien) im Ordner "Archiv"

  python noten_scanner.py seite1.jpg seite2.jpg --bleistift-entfernen
      zwei einzelne Fotos, Bleistift-Eintragungen entfernen
"""


def parser_erstellen() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="noten_scanner", description=BESCHREIBUNG, epilog=BEISPIELE,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("eingaben", nargs="+", type=Path, metavar="EINGABE",
                   help="Ordner oder einzelne Dateien (JPG, PNG, TIFF, HEIC, PDF, ...)")
    p.add_argument("-o", "--ziel", type=Path,
                   help="Zielordner (Standard: <Eingabeordner>_bereinigt)")
    standard = Einstellungen()

    a = p.add_argument_group("Ausgabe")
    a.add_argument("--pdf", choices=PDF_MODI, default="ordner",
                   help="ordner: ein PDF pro Ordner (Standard) · datei: ein PDF pro Foto · aus: kein PDF")
    a.add_argument("--nur-pdf", action="store_true", help="nur die PDFs behalten, keine einzelnen PNG-Bilder")
    a.add_argument("--modus", choices=MODI, default=standard.modus,
                   help="glatt: weiche Kanten, sieht am Bildschirm am besten aus (Standard) · "
                        "hart: nur reines Schwarz und Weiß, kleinste Dateien")
    a.add_argument("--format", choices=FORMATE, default=standard.format,
                   help="Papierformat. auto (Standard): A4, wenn das Blatt ungefähr A4 ist, sonst "
                        "Seitenverhältnis beibehalten · original: Größe nicht verändern")
    a.add_argument("--dpi", type=int, default=standard.dpi, help="Auflösung (Standard: %(default)s)")

    b = p.add_argument_group("Bereinigung")
    b.add_argument("--empfindlichkeit", type=float, default=standard.empfindlichkeit, metavar="0.1-0.95",
                   help="höher: auch sehr blasser Druck bleibt erhalten · niedriger: nur kräftig "
                        "Gedrucktes, mehr Schmutz verschwindet (Standard: %(default)s)")
    b.add_argument("--entrauschen", type=int, choices=(0, 1, 2, 3), default=standard.entrauschen,
                   help="Flecken entfernen: 0 = aus, 1 = normal (Standard), 2 = stark, 3 = sehr stark")
    b.add_argument("--bleistift-entfernen", action="store_true",
                   help="frei stehende Bleistift-Eintragungen entfernen (grau statt schwarz)")
    b.add_argument("--ohne-perspektive", action="store_true",
                   help="Blatt nicht im Foto suchen/entzerren (z. B. bei Flachbettscans)")
    b.add_argument("--ohne-begradigen", action="store_true", help="Schräglage nicht korrigieren")
    b.add_argument("--ohne-drehen", action="store_true",
                   help="quer oder kopfüber fotografierte Seiten nicht automatisch drehen")
    b.add_argument("--linien-original", action="store_true",
                   help="Notenlinien, Hälse und Taktstriche nicht gleichmäßig neu ziehen")
    b.add_argument("--rand-behalten", action="store_true",
                   help="dunkle Ränder/Schatten am Blattrand nicht entfernen")

    s = p.add_argument_group("Ablauf")
    s.add_argument("--nur-neue", action="store_true",
                   help="Dateien überspringen, für die es schon ein Ergebnis gibt")
    s.add_argument("--jobs", type=int, default=standard_jobs(),
                   help="Anzahl gleichzeitig bearbeiteter Bilder (Standard: %(default)s)")
    s.add_argument("--zwischenschritte", action="store_true",
                   help="Zwischenschritte zur Fehlersuche im Unterordner _zwischenschritte speichern")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def einstellungen_aus(args: argparse.Namespace) -> Einstellungen:
    return Einstellungen(
        perspektive=not args.ohne_perspektive,
        begradigen=not args.ohne_begradigen,
        drehen=not args.ohne_drehen,
        modus=args.modus,
        empfindlichkeit=args.empfindlichkeit,
        entrauschen=args.entrauschen,
        rand_saeubern=not args.rand_behalten,
        bleistift_entfernen=args.bleistift_entfernen,
        linien_reparieren=not args.linien_original,
        format=args.format,
        dpi=args.dpi,
    )


def beschreibung(r: Rueckmeldung) -> str:
    if r.fehler:
        return f"FEHLER – {r.fehler}"
    if r.uebersprungen:
        return "übersprungen (schon vorhanden)"
    teile = []
    for info in r.infos:
        hinweise = ["Blatt entzerrt" if info.get("blatt_gefunden") else "ohne Entzerrung"]
        if info.get("drehung"):
            hinweise.append("lag kopfüber" if info["drehung"] == 180 else "lag quer")
        if info.get("winkel"):
            hinweise.append(f"{info['winkel']:+.1f}° gedreht")
        if info.get("selbstkontrolle"):
            hinweise.append(f"Selbstkontrolle: {info['selbstkontrolle']} Stelle(n) wie im Original belassen")
        if info.get("linienabstand") is None:
            hinweise.append("keine Notenlinien erkannt – bitte prüfen")
        teile.append(", ".join(hinweise))
    return "; ".join(teile) if len(teile) == 1 else f"{len(teile)} Seiten: " + " | ".join(teile)


def main(argv: list[str] | None = None) -> int:
    args = parser_erstellen().parse_args(argv)
    try:
        einstellungen = einstellungen_aus(args)
        einstellungen.pruefen()
    except ValueError as fehler:
        print(f"Fehler: {fehler}", file=sys.stderr)
        return 2

    start = time.time()

    def fortschritt(fertig: int, gesamt: int, r: Rueckmeldung) -> None:
        print(f"[{fertig:>{len(str(gesamt))}}/{gesamt}] {r.quelle.name}: {beschreibung(r)}", flush=True)

    try:
        z = stapel_verarbeiten(args.eingaben, args.ziel, einstellungen, pdf=args.pdf, nur_pdf=args.nur_pdf,
                               nur_neue=args.nur_neue, jobs=max(1, args.jobs),
                               zwischenschritte=args.zwischenschritte, fortschritt=fortschritt)
    except (ValueError, FileNotFoundError) as fehler:
        print(f"Fehler: {fehler}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        return 130

    if z.dateien == 0:
        print("Keine passenden Dateien gefunden (JPG, PNG, TIFF, HEIC, PDF, ...).", file=sys.stderr)
        return 1
    dauer = time.time() - start
    print(f"\nFertig in {dauer:.0f} s: {z.seiten} Seite(n) aus {z.dateien} Datei(en) → {z.ziel}")
    if z.uebersprungen:
        print(f"  {z.uebersprungen} Datei(en) übersprungen, weil schon vorhanden")
    if z.pdfs:
        print(f"  {len(z.pdfs)} PDF(s) erstellt")
    if z.fehler:
        print(f"  {len(z.fehler)} Datei(en) mit Fehlern:", file=sys.stderr)
        for r in z.fehler:
            print(f"    {r.quelle}: {r.fehler}", file=sys.stderr)
        return 1
    return 0
