"""Stapelverarbeitung: ganze Ordner voller Notenfotos auf einmal bereinigen."""
from __future__ import annotations

import multiprocessing
import os
import tempfile
import traceback
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2

from .dateien import ALLE_ENDUNGEN, natuerlich_sortiert, pdf_erstellen, seite_speichern, seiten_laden
from .verarbeitung import Einstellungen, verarbeiten

PDF_MODI = ("ordner", "datei", "aus")
ZWISCHENSCHRITTE_ORDNER = "_zwischenschritte"


@dataclass
class Auftrag:
    quelle: Path
    ziel: Path        # Zielpfad ohne Endung, z. B. <ziel>/Marsch/Trompete_1
    gruppe: Path      # relativer Ordner – Seiten einer Gruppe landen im selben PDF


@dataclass
class Rueckmeldung:
    """Ergebnis für eine Eingabedatei."""
    quelle: Path
    seiten: list[Path] = field(default_factory=list)
    infos: list[dict] = field(default_factory=list)
    fehler: str | None = None
    uebersprungen: bool = False


@dataclass
class Zusammenfassung:
    dateien: int = 0
    seiten: int = 0
    fehler: list[Rueckmeldung] = field(default_factory=list)
    uebersprungen: int = 0
    pdfs: list[Path] = field(default_factory=list)
    ziel: Path | None = None


def standard_ziel(eingaben: list[Path]) -> Path:
    erste = eingaben[0].resolve()
    if erste.is_dir():
        return erste.parent / f"{erste.name}_bereinigt"
    return erste.parent / "bereinigt"


def auftraege_sammeln(eingaben: list[Path], ziel: Path) -> list[Auftrag]:
    """Findet alle Bilder/PDFs und legt fest, wohin das Ergebnis geschrieben wird.
    Die Ordnerstruktur der Eingabe wird im Ziel nachgebaut."""
    ziel = ziel.resolve()
    mehrere = len(eingaben) > 1
    gefunden: list[tuple[Path, Path]] = []  # (Datei, relativer Pfad im Ziel)
    for eingabe in eingaben:
        eingabe = eingabe.resolve()
        if eingabe.is_dir():
            basis = eingabe.parent if mehrere else eingabe
            for datei in natuerlich_sortiert(eingabe.rglob("*")):
                if not datei.is_file() or datei.suffix.lower() not in ALLE_ENDUNGEN:
                    continue
                if datei.name.startswith(".") or _liegt_in(datei, ziel):
                    continue  # versteckte Dateien und eigene Ergebnisse nicht erneut verarbeiten
                gefunden.append((datei, datei.relative_to(basis)))
        elif eingabe.is_file():
            if eingabe.suffix.lower() not in ALLE_ENDUNGEN:
                raise ValueError(f"Dateityp wird nicht unterstützt: {eingabe.name}")
            gefunden.append((eingabe, Path(eingabe.name)))
        else:
            raise FileNotFoundError(f"Nicht gefunden: {eingabe}")

    # Gleichnamige Dateien (z. B. Seite1.jpg und Seite1.png) nicht gegenseitig überschreiben
    anzahl = defaultdict(int)
    for _, relativ in gefunden:
        anzahl[relativ.with_suffix("")] += 1
    auftraege = []
    for datei, relativ in gefunden:
        ohne_endung = relativ.with_suffix("")
        if anzahl[ohne_endung] > 1:
            ohne_endung = ohne_endung.with_name(f"{ohne_endung.name}_{relativ.suffix.lstrip('.').lower()}")
        auftraege.append(Auftrag(datei, ziel / ohne_endung, relativ.parent))
    return auftraege


def _liegt_in(pfad: Path, ordner: Path) -> bool:
    try:
        pfad.relative_to(ordner)
        return True
    except ValueError:
        return False


def _vorhandene_seiten(ziel: Path) -> list[Path]:
    einzeln = ziel.with_name(ziel.name + ".png")
    if einzeln.exists():
        return [einzeln]
    return natuerlich_sortiert(ziel.parent.glob(f"{ziel.name}_S[0-9][0-9]*.png"))


def datei_bearbeiten(auftrag: Auftrag, einstellungen: Einstellungen,
                     zwischenschritte_ordner: Path | None = None) -> Rueckmeldung:
    """Bereinigt eine Eingabedatei (bei PDFs alle Seiten) und speichert das Ergebnis als PNG."""
    rueckmeldung = Rueckmeldung(auftrag.quelle)
    try:
        bilder = seiten_laden(auftrag.quelle, einstellungen.dpi)
        if not bilder:
            raise ValueError("Datei enthält keine Seiten")
        for nummer, bild in enumerate(bilder, start=1):
            ergebnis = verarbeiten(bild, einstellungen, zwischenschritte=zwischenschritte_ordner is not None)
            name = auftrag.ziel.name if len(bilder) == 1 else f"{auftrag.ziel.name}_S{nummer:02d}"
            pfad = auftrag.ziel.with_name(name + ".png")
            seite_speichern(ergebnis.bild, pfad, einstellungen.dpi, hart=einstellungen.modus == "hart")
            rueckmeldung.seiten.append(pfad)
            rueckmeldung.infos.append(ergebnis.infos)
            if zwischenschritte_ordner is not None:
                zwischenschritte_ordner.mkdir(parents=True, exist_ok=True)
                for schritt, zwischenbild in ergebnis.zwischenschritte.items():
                    cv2.imwrite(str(zwischenschritte_ordner / f"{name}_{schritt}.jpg"), zwischenbild,
                                [cv2.IMWRITE_JPEG_QUALITY, 85])
    except Exception as fehler:  # eine kaputte Datei soll nicht den ganzen Stapel stoppen
        rueckmeldung.fehler = f"{type(fehler).__name__}: {fehler}"
        if os.environ.get("NOTENSCANNER_DEBUG"):
            rueckmeldung.fehler += "\n" + traceback.format_exc()
    return rueckmeldung


def _worker_start() -> None:
    cv2.setNumThreads(1)  # Parallelität kommt von mehreren Prozessen


def standard_jobs() -> int:
    return max(1, min(4, (os.cpu_count() or 2) - 1))


def stapel_verarbeiten(eingaben: list[Path], ziel: Path | None = None,
                       einstellungen: Einstellungen | None = None, pdf: str = "ordner",
                       nur_pdf: bool = False, nur_neue: bool = False, jobs: int | None = None,
                       zwischenschritte: bool = False,
                       fortschritt: Callable[[int, int, Rueckmeldung], None] | None = None,
                       abbrechen: Callable[[], bool] | None = None) -> Zusammenfassung:
    """Verarbeitet alle Dateien. `fortschritt(fertig, gesamt, rueckmeldung)` wird nach jeder
    Datei aufgerufen; gibt `abbrechen()` True zurück, wird nach den laufenden Dateien gestoppt."""
    einstellungen = einstellungen or Einstellungen()
    einstellungen.pruefen()
    if pdf not in PDF_MODI:
        raise ValueError(f"Unbekannter PDF-Modus '{pdf}' (erlaubt: {', '.join(PDF_MODI)})")
    if nur_pdf and pdf == "aus":
        raise ValueError("'nur PDF' geht nicht zusammen mit 'kein PDF'")
    ziel = (ziel or standard_ziel(eingaben)).resolve()
    jobs = jobs or standard_jobs()

    with tempfile.TemporaryDirectory(prefix="notenscanner_") as temp:
        # Bei "nur PDF" landen die Einzelseiten nur vorübergehend in einem Temp-Ordner
        seiten_ziel = Path(temp) if nur_pdf else ziel
        auftraege = auftraege_sammeln(eingaben, seiten_ziel)
        zusammenfassung = Zusammenfassung(dateien=len(auftraege), ziel=ziel)
        zwischen = ziel / ZWISCHENSCHRITTE_ORDNER if zwischenschritte else None

        ergebnisse: dict[Path, Rueckmeldung] = {}
        offen = []
        for auftrag in auftraege:
            vorhanden = _vorhandene_seiten(auftrag.ziel) if nur_neue and not nur_pdf else []
            if vorhanden:
                ergebnisse[auftrag.quelle] = Rueckmeldung(auftrag.quelle, vorhanden, uebersprungen=True)
            else:
                offen.append(auftrag)

        fertig = 0

        def melden(r: Rueckmeldung) -> None:
            nonlocal fertig
            fertig += 1
            ergebnisse[r.quelle] = r
            if fortschritt:
                fortschritt(fertig, len(auftraege), r)

        for r in list(ergebnisse.values()):
            melden(r)

        if jobs == 1 or len(offen) <= 1:
            for auftrag in offen:
                if abbrechen and abbrechen():
                    break
                melden(datei_bearbeiten(auftrag, einstellungen, zwischen))
        else:
            # "spawn" statt "fork": Fork aus einem Programm mit laufenden Threads (Fenster,
            # OpenCV) kann unter Linux hängen bleiben; Windows und macOS nutzen ohnehin spawn.
            kontext = multiprocessing.get_context("spawn")
            with ProcessPoolExecutor(max_workers=jobs, mp_context=kontext, initializer=_worker_start) as pool:
                laufend = {pool.submit(datei_bearbeiten, a, einstellungen, zwischen): a for a in offen}
                for zukunft in as_completed(laufend):
                    melden(zukunft.result())
                    if abbrechen and abbrechen():
                        for z in laufend:
                            z.cancel()
                        break

        for r in ergebnisse.values():
            if r.fehler:
                zusammenfassung.fehler.append(r)
            elif r.uebersprungen:
                zusammenfassung.uebersprungen += 1
            zusammenfassung.seiten += len(r.seiten)

        if pdf != "aus":
            zusammenfassung.pdfs = _pdfs_erstellen(auftraege, ergebnisse, pdf, ziel, seiten_ziel, eingaben)
    return zusammenfassung


def _pdfs_erstellen(auftraege: list[Auftrag], ergebnisse: dict[Path, Rueckmeldung], pdf: str,
                    ziel: Path, seiten_ziel: Path, eingaben: list[Path]) -> list[Path]:
    erstellt = []
    if pdf == "datei":
        for auftrag in auftraege:
            r = ergebnisse.get(auftrag.quelle)
            if r and r.seiten:
                pdf_pfad = ziel / auftrag.ziel.relative_to(seiten_ziel)
                pdf_pfad = pdf_pfad.with_name(pdf_pfad.name + ".pdf")
                pdf_erstellen(r.seiten, pdf_pfad)
                erstellt.append(pdf_pfad)
        return erstellt

    # Ein PDF pro Ordner, benannt nach dem Ordner
    gruppen: dict[Path, list[Path]] = defaultdict(list)
    for auftrag in auftraege:
        r = ergebnisse.get(auftrag.quelle)
        if r and r.seiten:
            gruppen[auftrag.gruppe].extend(r.seiten)
    for gruppe, seiten in gruppen.items():
        if gruppe == Path("."):
            erste = eingaben[0].resolve()
            if len(eingaben) == 1:
                name = erste.name if erste.is_dir() else erste.stem
            else:
                name = "Noten"
            pdf_pfad = ziel / f"{name}.pdf"
        else:
            pdf_pfad = ziel / gruppe.parent / f"{gruppe.name}.pdf"
        pdf_erstellen(seiten, pdf_pfad)
        erstellt.append(pdf_pfad)
    return erstellt
