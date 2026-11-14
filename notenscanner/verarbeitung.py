"""Bereinigung: macht aus einem (Handy-)Scan ein sauberes Notenblatt.

Ablauf für jedes Bild:
  1. Blatt im Foto finden und Perspektive entzerren
  2. auf einheitliche Größe bringen (Standard: A4 mit 300 dpi)
  3. Beleuchtung ausgleichen (Schatten, Farbstich, vergilbtes Papier -> gleichmäßig weiß)
  4. Quer oder kopfüber fotografierte Seiten erkennen und richtig herum drehen,
     Schräglage anhand der Notenlinien messen und gerade drehen
  5. Schwarz/Weiß-Trennung (Hintergrund reinweiß, Noten tiefschwarz)
  6. Aufräumen: dunkle Ränder, Finger/Schatten am Rand, Staub- und Kopierflecken,
     auf Wunsch Bleistift-Eintragungen
  7. Notenlinien, Notenhälse und Taktstriche gleichmäßig und durchgehend neu ziehen
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .linien import linien_reparieren, selbstkontrolle
from .seite import blatt_entzerren, blatt_finden, gemessene_groesse

PAPIERFORMATE_MM = {"A4": (210.0, 297.0), "A5": (148.0, 210.0), "Letter": (215.9, 279.4)}
MODI = ("glatt", "hart")
FORMATE = ("auto", "A4", "A5", "Letter", "original")


@dataclass
class Einstellungen:
    perspektive: bool = True        # Blatt im Foto suchen und entzerren
    begradigen: bool = True         # Schräglage korrigieren
    drehen: bool = True             # quer oder kopfüber fotografierte Seiten richtig herum drehen
    max_winkel: float = 10.0        # größte Schräglage (Grad), nach der gesucht wird
    modus: str = "glatt"            # "glatt": weiche Kanten, "hart": nur reines Schwarz und Weiß
    empfindlichkeit: float = 0.8    # 0..1 – größer: auch Blasses wird schwarz, kleiner: nur Kräftiges
    entrauschen: int = 1            # 0 = aus, 1 = normal, 2 = stark, 3 = sehr stark
    rand_saeubern: bool = True      # dunkle Ränder, Schatten und Finger am Rand entfernen
    bleistift_entfernen: bool = False
    linien_reparieren: bool = True  # Notenlinien, Hälse und Taktstriche gleichmäßig neu ziehen
    format: str = "auto"            # "auto", "A4", "A5", "Letter" oder "original"
    dpi: int = 300

    def pruefen(self) -> None:
        if self.modus not in MODI:
            raise ValueError(f"Unbekannter Modus '{self.modus}' (erlaubt: {', '.join(MODI)})")
        if self.format not in FORMATE:
            raise ValueError(f"Unbekanntes Format '{self.format}' (erlaubt: {', '.join(FORMATE)})")
        if not 0.1 <= self.empfindlichkeit <= 0.95:
            raise ValueError("Die Empfindlichkeit muss zwischen 0.1 und 0.95 liegen")
        if self.entrauschen not in (0, 1, 2, 3):
            raise ValueError("Entrauschen muss 0, 1, 2 oder 3 sein")
        if not 72 <= self.dpi <= 1200:
            raise ValueError("dpi muss zwischen 72 und 1200 liegen")


@dataclass
class Ergebnis:
    bild: np.ndarray                 # Graustufenbild, uint8 (im Modus "hart" nur 0 und 255)
    infos: dict = field(default_factory=dict)
    zwischenschritte: dict = field(default_factory=dict)


def verarbeiten(bild_bgr: np.ndarray, e: Einstellungen | None = None,
                zwischenschritte: bool = False) -> Ergebnis:
    """Bereinigt ein einzelnes Foto/Scan einer Notenseite."""
    e = e or Einstellungen()
    e.pruefen()
    infos: dict = {}
    schritte: dict = {}

    grau = cv2.cvtColor(bild_bgr, cv2.COLOR_BGR2GRAY) if bild_bgr.ndim == 3 else bild_bgr

    # 1.+2. Blatt finden, entzerren und auf Zielgröße bringen
    ecken = blatt_finden(bild_bgr) if e.perspektive and bild_bgr.ndim == 3 else None
    infos["blatt_gefunden"] = ecken is not None
    if ecken is not None:
        breite, hoehe = gemessene_groesse(ecken)
    else:
        hoehe, breite = grau.shape
    (zb, zh), (seite_b, seite_h) = zielgroesse(breite, hoehe, e.format, e.dpi)
    vergroesserung = zb / breite
    if zwischenschritte:
        schritte["1_foto"] = _ecken_zeichnen(bild_bgr, ecken)
    if ecken is not None:
        grau = blatt_entzerren(grau, ecken, zb, zh)
    elif (zb, zh) != (grau.shape[1], grau.shape[0]):
        verkleinern = zb < grau.shape[1]
        grau = cv2.resize(grau, (zb, zh), interpolation=cv2.INTER_AREA if verkleinern else cv2.INTER_CUBIC)

    # 3. Beleuchtung ausgleichen
    skala = max(zb, zh) / 3508  # 1.0 bei A4 mit 300 dpi
    norm = hintergrund_ausgleichen(grau)
    norm = filtern(norm, helligkeiten(norm)[2], vergroesserung, skala)
    papier, tinte, rauschen = helligkeiten(norm)
    blass = min(tinte + e.empfindlichkeit * (papier - tinte), papier - 5 * rauschen)
    fenster = 2 * max(2, int(round(2 * skala))) + 1
    if zwischenschritte:
        schritte["2_ausgeglichen"] = np.clip(norm, 0, 255).astype(np.uint8)

    # 4. Drehung um 90° (Notenlinien senkrecht) und Schräglage
    drehung = 0
    if e.drehen and ist_quer(schwarzweiss(norm, papier, blass, fenster)[0]):
        norm = np.ascontiguousarray(np.rot90(norm))
        zb, zh, seite_b, seite_h = zh, zb, seite_h, seite_b
        drehung = 90
    winkel = 0.0
    if e.begradigen:
        winkel = schraeglage_messen(schwarzweiss(norm, papier, blass, fenster)[0], e.max_winkel)
        if abs(winkel) >= 0.05:
            norm = drehen(norm, winkel, papier)
    infos["winkel"] = round(winkel, 2)

    # 5. Schwarz/Weiß-Trennung
    maske, grenze, lokal_dunkel = schwarzweiss(norm, papier, blass, fenster)
    masse = notenlinien_masse(maske)
    if masse is None:
        # Keine Notenlinien gefunden (z. B. Textseite): typischen Linienabstand annehmen
        abstand = 25.0 * skala
        infos["linienabstand"] = None
    else:
        abstand = masse[0] + masse[1]
        infos["linienabstand"] = round(abstand, 1)
        if e.drehen and steht_kopf(maske, abstand):
            norm, maske, grenze, lokal_dunkel = (np.ascontiguousarray(a[::-1, ::-1])
                                                 for a in (norm, maske, grenze, lokal_dunkel))
            drehung += 180
    infos["drehung"] = drehung
    if zwischenschritte:
        schritte["3_schwarzweiss_roh"] = np.where(maske > 0, 0, 255).astype(np.uint8)

    # 6. Aufräumen
    if e.bleistift_entfernen:
        maske = nur_mit_dunklem_kern(maske, norm, tinte + 0.25 * (papier - tinte), abstand)
    if e.rand_saeubern:
        maske = rand_saeubern(maske, abstand)
    infos["flecken_entfernt"] = 0
    if e.entrauschen:
        maske, infos["flecken_entfernt"] = flecken_entfernen(maske, abstand, e.entrauschen)

    # 7. Linien reparieren
    reparatur = None
    if e.linien_reparieren and masse is not None:
        reparatur = linien_reparieren(maske, abstand, masse[0])
        infos["selbstkontrolle"] = selbstkontrolle(maske, reparatur, abstand)
        maske = (maske > 0) & ~reparatur.weg
        maske = maske.astype(np.uint8)
        infos["notensysteme"] = reparatur.systeme
        infos["striche"] = reparatur.striche

    # Ausgabe
    if e.modus == "hart":
        ausgabe = np.where(maske > 0, 0, 255).astype(np.uint8)
    else:
        ausgabe = weiche_kanten(norm, maske, grenze, papier, lokal_dunkel)
        if reparatur is not None:
            # weiche (graue) Säume der entfernten alten Linien ebenfalls weiß machen
            rand = cv2.dilate(reparatur.weg.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            ausgabe[rand & (maske == 0)] = 255
    if reparatur is not None:
        ausgabe = reparatur.zeichnen(ausgabe, glatt=e.modus == "glatt")
        if e.modus == "glatt":   # auch die neuen Linien auf die 16 Graustufen runden
            ausgabe = (np.round(ausgabe / 17.0) * 17).astype(np.uint8)
    if (seite_b, seite_h) != (zb, zh):
        ausgabe = _auf_seite_setzen(ausgabe, seite_b, seite_h)
    return Ergebnis(ausgabe, infos, schritte)


def zielgroesse(breite: float, hoehe: float, format: str, dpi: int):
    """Berechnet die Größe des Notenbildes und der fertigen Seite in Pixeln.

    Gibt ((bild_breite, bild_hoehe), (seiten_breite, seiten_hoehe)) zurück.
    "auto": Blätter mit (ungefähr) A4-Seitenverhältnis werden exakt A4, alle anderen
            behalten ihr Seitenverhältnis und werden so skaliert, dass sie auf A4 passen.
    """
    if format == "original":
        groesse = (int(round(breite)), int(round(hoehe)))
        return groesse, groesse
    quer = breite > hoehe
    kurz_mm, lang_mm = PAPIERFORMATE_MM["A4" if format == "auto" else format]
    seite = (lang_mm, kurz_mm) if quer else (kurz_mm, lang_mm)
    seite_px = (int(round(seite[0] / 25.4 * dpi)), int(round(seite[1] / 25.4 * dpi)))
    verhaeltnis = max(breite, hoehe) / min(breite, hoehe)
    if abs(verhaeltnis / (lang_mm / kurz_mm) - 1) < 0.07:
        return seite_px, seite_px  # kleine Abweichungen kommen von der Perspektive
    skala = min(seite_px[0] / breite, seite_px[1] / hoehe)
    bild_px = (min(seite_px[0], int(round(breite * skala))), min(seite_px[1], int(round(hoehe * skala))))
    if format == "auto":
        return bild_px, bild_px
    return bild_px, seite_px


def hintergrund_ausgleichen(grau: np.ndarray) -> np.ndarray:
    """Schätzt die Papierhelligkeit an jeder Stelle (Schatten, Lichtverlauf, Vergilbung)
    und teilt sie heraus. Ergebnis: float32-Bild, in dem das Papier überall etwa 255 ist."""
    h, w = grau.shape
    f = 600 / max(h, w)
    klein = cv2.resize(grau, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    k = max(5, int(round(max(klein.shape) / 40)) | 1)
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    papier = cv2.morphologyEx(klein, cv2.MORPH_CLOSE, kern)  # Noten "zudecken"
    papier = cv2.medianBlur(papier, k)
    papier = cv2.GaussianBlur(papier, (0, 0), k / 3)
    papier = cv2.resize(papier, (w, h), interpolation=cv2.INTER_CUBIC).astype(np.float32)
    return grau.astype(np.float32) * 255.0 / np.maximum(papier, 1.0)


# Abgestimmt an simulierten Handyfotos (normal, verrauscht, per Messenger verkleinert)
GLAETTUNG_JE_RAUSCHEN = 0.05
GLAETTUNG_JE_VERGROESSERUNG = 0.25
SCHAERFE_SIGMA = 2.0
SCHAERFE_STAERKE = 1.2


def filtern(norm: np.ndarray, rauschen: float, vergroesserung: float, skala: float) -> np.ndarray:
    """Glättet Bildrauschen und JPEG-Klötzchen (stärker bei verrauschten oder stark
    vergrößerten Fotos, z. B. per Messenger verschickt) und gleicht danach die Unschärfe
    der Handykamera aus (Unscharf maskieren), damit dünne Linien schmal und dunkel bleiben."""
    sigma = min(2.5 * skala, max(GLAETTUNG_JE_VERGROESSERUNG * vergroesserung, GLAETTUNG_JE_RAUSCHEN * rauschen))
    if sigma >= 0.3:
        norm = cv2.GaussianBlur(norm, (0, 0), sigma)
    unscharf = cv2.GaussianBlur(norm, (0, 0), SCHAERFE_SIGMA * skala * max(1.0, vergroesserung))
    return norm + SCHAERFE_STAERKE * (norm - unscharf)


def helligkeiten(norm: np.ndarray) -> tuple[float, float, float]:
    """Typische Helligkeit von Papier und Druckfarbe im ausgeglichenen Bild sowie
    die Stärke des Bildrauschens auf dem Papier."""
    probe = norm[::3, ::3]
    papier = float(np.median(probe))
    tinte = float(np.percentile(probe, 0.5))
    # Auf (fast) leeren Seiten gibt es kaum Druckfarbe – dann nicht das Rauschen zur Tinte erklären
    tinte = min(tinte, 0.45 * papier)
    papierpixel = probe[probe > papier - 0.3 * (papier - tinte)]
    rauschen = 1.4826 * float(np.median(np.abs(papierpixel - papier)))
    return papier, tinte, max(rauschen, 1.0)


def schwarzweiss(norm: np.ndarray, papier: float, blass: float, fenster: int):
    """Trennt Noten vom Papier.

    Jeder Pixel wird mit der dunkelsten Stelle in seiner Umgebung verglichen und gilt als
    schwarz, wenn er näher an dieser als am Papier liegt. So werden dicke Noten nicht
    aufgebläht, aber dünne Notenlinien, die im Foto durch Unschärfe nur noch grau sind,
    bleiben trotzdem vollständig erhalten. Alles, dessen dunkelste Stelle heller als
    `blass` ist, gilt als Papier.

    Gibt (maske, grenze, lokal_dunkel) zurück; maske ist 1 für Noten, 0 für Papier.
    """
    lokal_dunkel = cv2.erode(norm, np.ones((fenster, fenster), np.uint8))
    grenze = (lokal_dunkel + papier) / 2
    maske = ((norm < grenze) & (lokal_dunkel < blass)).astype(np.uint8)
    # Direkt neben dicken Notenköpfen bekommen dünne Linien sonst winzige Lücken:
    # waagrecht/senkrecht schließen, aber nur dort, wo das Bild wirklich dunkel ist
    luecken = (cv2.morphologyEx(maske, cv2.MORPH_CLOSE, np.ones((1, fenster), np.uint8)) |
               cv2.morphologyEx(maske, cv2.MORPH_CLOSE, np.ones((fenster, 1), np.uint8)))
    maske |= luecken & (norm < blass)
    return maske, grenze, lokal_dunkel


def schraeglage_messen(maske: np.ndarray, max_winkel: float = 10.0) -> float:
    """Misst die Schräglage über die Notenlinien: Beim richtigen Winkel liegen die Linien
    exakt waagrecht und die Zeilensummen bilden besonders scharfe Spitzen."""
    h, w = maske.shape
    f = min(1.0, 900 / w)
    klein = cv2.resize(maske.astype(np.float32), None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    kh, kw = klein.shape
    mitte = (kw / 2, kh / 2)

    def schaerfe(winkel: float) -> float:
        M = cv2.getRotationMatrix2D(mitte, winkel, 1.0)
        gedreht = cv2.warpAffine(klein, M, (kw, kh), flags=cv2.INTER_LINEAR, borderValue=0)
        profil = gedreht.sum(axis=1)
        return float(np.sum(np.diff(profil) ** 2))

    grob = np.arange(-max_winkel, max_winkel + 1e-6, 0.5)
    bester = max(grob, key=schaerfe)
    fein = np.arange(max(-max_winkel, bester - 0.5), min(max_winkel, bester + 0.5) + 1e-6, 0.05)
    return float(max(fein, key=schaerfe))


def drehen(bild: np.ndarray, winkel: float, fuellwert: float) -> np.ndarray:
    h, w = bild.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), winkel, 1.0)
    return cv2.warpAffine(bild, M, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=fuellwert)


def notenlinien_masse(maske: np.ndarray) -> tuple[float, float, float] | None:
    """Ermittelt Notenlinien-Dicke und Lücke zwischen den Linien (in Pixeln) über die
    häufigsten senkrechten Schwarz- bzw. Weiß-Läufe, dazu ein Maß, wie deutlich die
    Notenlinien sind. None, wenn keine waagrechten Notenlinien erkennbar sind."""
    spalten = maske[:, ::5].T.astype(np.int8)
    if spalten.size == 0:
        return None
    h = spalten.shape[1]
    gepolstert = np.pad(spalten, ((0, 0), (1, 1)))
    d = np.diff(gepolstert, axis=1).ravel()
    anfaenge = np.flatnonzero(d == 1)
    enden = np.flatnonzero(d == -1)
    if len(anfaenge) < 100:
        return None
    schwarz = enden - anfaenge
    gleiche_spalte = (anfaenge[1:] // (h + 1)) == (enden[:-1] // (h + 1))
    weiss = (anfaenge[1:] - enden[:-1])[gleiche_spalte]

    dicke = int(np.argmax(np.bincount(schwarz)[1:])) + 1
    obergrenze = max(10, h // 15)
    weiss = weiss[(weiss > dicke) & (weiss < obergrenze)]
    if len(weiss) < 100:
        return None
    haeufigkeit = np.bincount(weiss)
    luecke = int(np.argmax(haeufigkeit))
    # Bei echten Notenzeilen ist die Lücke sehr deutlich die häufigste
    um_spitze = haeufigkeit[max(0, luecke - 1):luecke + 2].sum()
    if um_spitze < 0.15 * len(weiss) or luecke < 2 * dicke:
        return None
    return float(dicke), float(luecke), um_spitze / len(spalten)


def ist_quer(maske: np.ndarray) -> bool:
    """True, wenn die Notenlinien senkrecht verlaufen, das Foto also um 90° verdreht ist
    (passiert oft, wenn das Handy beim Fotografieren flach über dem Blatt liegt)."""
    senkrecht = notenlinien_masse(np.ascontiguousarray(maske.T))
    if senkrecht is None:
        return False
    waagrecht = notenlinien_masse(maske)
    return waagrecht is None or senkrecht[2] > 2 * waagrecht[2]


def steht_kopf(maske: np.ndarray, abstand: float) -> bool:
    """True, wenn die Seite auf dem Kopf steht. Am Anfang jeder Notenzeile stehen
    Notenschlüssel und Vorzeichen, am Ende meist nur ein Taktstrich – bei einer aufrechten
    Seite ist der Zeilenanfang deshalb deutlich "dichter" als das Zeilenende."""
    linien = cv2.morphologyEx(maske, cv2.MORPH_OPEN, np.ones((1, max(15, int(6 * abstand))), np.uint8))
    linienzeilen = linien.sum(axis=1) > 0.3 * maske.shape[1]
    band = cv2.dilate(linienzeilen.astype(np.uint8)[:, None],
                      np.ones((2 * int(1.5 * abstand) + 1, 1), np.uint8))[:, 0]
    wechsel = np.diff(np.concatenate([[0], band.astype(np.int8), [0]]))
    breite, rand = int(4 * abstand), int(2 * abstand)
    anfang = ende = zeilen = 0
    for y0, y1 in zip(np.flatnonzero(wechsel == 1), np.flatnonzero(wechsel == -1)):
        spalten = np.flatnonzero(linien[y0:y1].any(axis=0))
        if len(spalten) == 0 or spalten[-1] - spalten[0] < 10 * abstand:
            continue
        x0, x1 = spalten[0], spalten[-1]
        ya, yb = max(0, y0 - rand), y1 + rand
        noten = maske[ya:yb] & (1 - linien[ya:yb])  # Notenlinien selbst nicht mitzählen
        anfang += int(noten[:, x0:x0 + breite].sum())
        ende += int(noten[:, max(0, x1 - breite):x1 + 1].sum())
        zeilen += 1
    # Aufrecht gemessen: Anfang/Ende 1.4–2.5; kopfüber: 0.4–0.7
    return zeilen >= 2 and anfang < 0.75 * ende


def nur_mit_dunklem_kern(maske: np.ndarray, norm: np.ndarray, kernschwelle: float,
                         abstand: float) -> np.ndarray:
    """Behält nur zusammenhängende Teile, die irgendwo richtig dunkel sind.
    Gedruckte Noten sind tiefschwarz, Bleistift bleibt grau -> frei stehender Bleistift fällt weg.
    Notenlinien sind im Foto oft nur grau, deshalb zählen lange waagrechte Linien immer als Druck."""
    anzahl, etiketten = cv2.connectedComponents(maske, connectivity=8)
    linien = cv2.morphologyEx(maske, cv2.MORPH_OPEN, np.ones((1, max(10, int(1.4 * abstand))), np.uint8))
    dunkel = ((norm < kernschwelle) & (maske > 0)) | (linien > 0)
    hat_kern = np.bincount(etiketten[dunkel], minlength=anzahl) > 0
    hat_kern[0] = False
    return hat_kern[etiketten].astype(np.uint8)


def rand_saeubern(maske: np.ndarray, abstand: float) -> np.ndarray:
    """Entfernt alles, was am Blattrand nicht zu den Noten gehört: Tischkanten, Schatten,
    Finger, schwarze Kopiererränder."""
    maske = maske.copy()
    h, w = maske.shape
    rahmen = max(2, int(round(0.003 * max(h, w))))
    maske[:rahmen], maske[-rahmen:], maske[:, :rahmen], maske[:, -rahmen:] = 0, 0, 0, 0

    # a) Durchgehend dunkle Streifen direkt am Rand (z. B. Tischkante, Kopiererrand)
    band = max(rahmen + 1, int(0.04 * min(h, w)))
    for achse, anteil_grenze in ((0, 0.35), (1, 0.6)):  # Spalten links/rechts, Zeilen oben/unten
        dichte = maske.mean(axis=achse)
        for von_vorn in (True, False):
            folge = dichte[:band] if von_vorn else dichte[::-1][:band]
            bis = rahmen
            while bis < band and folge[bis] > anteil_grenze:
                bis += 1
            if bis > rahmen:
                bis += 2
                if achse == 0:
                    if von_vorn:
                        maske[:, :bis] = 0
                    else:
                        maske[:, -bis:] = 0
                else:
                    if von_vorn:
                        maske[:bis] = 0
                    else:
                        maske[-bis:] = 0

    # b) Große, flächig schwarze Gebiete, die den Rand berühren (Schatten, Finger, Ecken)
    groesse = max(9, int(1.6 * abstand) | 1)
    flaechig = cv2.morphologyEx(maske, cv2.MORPH_OPEN,
                                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (groesse, groesse)))
    if flaechig.any():
        anzahl, etiketten = cv2.connectedComponents(flaechig, connectivity=8)
        rand_etiketten = np.unique(np.concatenate([
            etiketten[rahmen], etiketten[-rahmen - 1], etiketten[:, rahmen], etiketten[:, -rahmen - 1]]))
        rand_etiketten = rand_etiketten[rand_etiketten > 0]
        if len(rand_etiketten):
            weg = np.isin(etiketten, rand_etiketten).astype(np.uint8)
            weg = cv2.dilate(weg, np.ones((5, 5), np.uint8))
            maske[weg > 0] = 0

    # c) Einzelteile, die an den Rand stoßen und nur im Randbereich liegen (Reste von
    #    Tischkante, Schattenkanten, Fingern) – gedruckte Noten reichen nie bis an die
    #    Papierkante, und ganze Notenzeilen sind viel breiter als dieser Bereich
    anzahl, etiketten, stats, _ = cv2.connectedComponentsWithStats(maske, connectivity=8)
    x, y, bw, bh = stats[:, 0], stats[:, 1], stats[:, 2], stats[:, 3]
    nah = rahmen + 2
    randbereich = int(0.1 * min(h, w))
    links = (x <= nah) & (x + bw <= randbereich)
    rechts = (x + bw >= w - nah) & (x >= w - randbereich)
    oben = (y <= nah) & (y + bh <= randbereich)
    unten = (y + bh >= h - nah) & (y >= h - randbereich)
    weg = links | rechts | oben | unten
    weg[0] = False
    if weg.any():
        maske[weg[etiketten]] = 0
    return maske


# Stufe: (winzig, klein, Abstand) jeweils in Notenlinienabständen
ENTRAUSCHEN_STUFEN = {1: (0.15, 0.42, 1.5), 2: (0.20, 0.55, 1.2), 3: (0.26, 0.70, 1.0)}


def flecken_entfernen(maske: np.ndarray, abstand: float, staerke: int) -> tuple[np.ndarray, int]:
    """Entfernt Staub, Kopierflecken und Bildrauschen.

    Winzige Punkte fallen immer weg. Etwas größere Punkte (so groß wie ein Staccato-Punkt)
    nur dann, wenn sie frei auf dem Papier liegen – echte Staccato-, Verlängerungs- und
    Wiederholungspunkte sitzen immer dicht an Noten oder Notenlinien. Alle Größen richten
    sich nach dem gemessenen Notenlinienabstand."""
    winzig, klein, entfernung = ENTRAUSCHEN_STUFEN[staerke]
    winzig_flaeche = max(3, (winzig * abstand) ** 2)
    klein_flaeche = (klein * abstand) ** 2
    anzahl, etiketten, stats, schwerpunkte = cv2.connectedComponentsWithStats(maske, connectivity=8)
    flaechen = stats[:, cv2.CC_STAT_AREA]
    behalten = flaechen >= winzig_flaeche
    gross = flaechen >= klein_flaeche
    gross[0] = False
    pruefen = np.flatnonzero(behalten & ~gross)
    pruefen = pruefen[pruefen > 0]
    if len(pruefen) and gross.any():
        # Abstand jedes Pixels zum nächsten "richtigen" Notenelement
        frei = np.where(gross[etiketten], 0, 255).astype(np.uint8)
        distanz = cv2.distanceTransform(frei, cv2.DIST_L2, 3)
        cx = np.clip(np.round(schwerpunkte[pruefen, 0]).astype(int), 0, maske.shape[1] - 1)
        cy = np.clip(np.round(schwerpunkte[pruefen, 1]).astype(int), 0, maske.shape[0] - 1)
        radius = np.sqrt(flaechen[pruefen] / np.pi)
        behalten[pruefen[distanz[cy, cx] - radius > entfernung * abstand]] = False
    elif len(pruefen):
        behalten[pruefen] = False  # Seite ohne größere Elemente: alles nur Flecken
    behalten[0] = False
    return behalten[etiketten].astype(np.uint8), int((~behalten[1:]).sum())


def weiche_kanten(norm: np.ndarray, maske: np.ndarray, grenze: np.ndarray,
                  papier: float, lokal_dunkel: np.ndarray) -> np.ndarray:
    """Reinweißer Hintergrund und tiefschwarze Noten, aber mit 1–2 Pixel weichem Übergang
    an den Kanten – sieht auf Bildschirm und Tablet glatter aus als reines Schwarz/Weiß."""
    breite = 0.4 * np.maximum(papier - lokal_dunkel, 1.0)
    werte = 0.5 + (norm - grenze) / breite
    # 16 Graustufen reichen für glatte Kanten und machen die Dateien deutlich kleiner
    ausgabe = (np.round(np.clip(werte, 0, 1) * 15) * 17).astype(np.uint8)
    ausgabe[(maske > 0) & (ausgabe > 102)] = 102  # alles, was zu den Noten gehört, ist dunkel
    umgebung = cv2.dilate(maske, np.ones((3, 3), np.uint8))
    ausgabe[umgebung == 0] = 255
    return ausgabe


def _auf_seite_setzen(bild: np.ndarray, breite: int, hoehe: int) -> np.ndarray:
    """Setzt das Bild mittig auf eine weiße Seite der gewünschten Größe."""
    h, w = bild.shape
    oben, links = (hoehe - h) // 2, (breite - w) // 2
    return cv2.copyMakeBorder(bild, oben, hoehe - h - oben, links, breite - w - links,
                              cv2.BORDER_CONSTANT, value=255)


def _ecken_zeichnen(bild_bgr: np.ndarray, ecken: np.ndarray | None) -> np.ndarray:
    vorschau = bild_bgr.copy() if bild_bgr.ndim == 3 else cv2.cvtColor(bild_bgr, cv2.COLOR_GRAY2BGR)
    if ecken is not None:
        dicke = max(3, vorschau.shape[1] // 300)
        cv2.polylines(vorschau, [ecken.astype(np.int32)], True, (0, 0, 255), dicke, cv2.LINE_AA)
    return vorschau
