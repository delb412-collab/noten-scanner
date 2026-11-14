"""Linien-Reparatur: Notenlinien, Notenhälse und Taktstriche gleichmäßig neu ziehen.

Bei alten Kopien und Fotos mit wenig Auflösung sind Linien oft unterbrochen, mal dünner,
mal dicker. Die Linien werden deshalb erkannt, ihr genauer Verlauf vermessen, die alten
Pixel dort entfernt, wo kein anderes Notenzeichen darüberliegt, und die Linie dann sauber
in einheitlicher Dicke neu gezeichnet.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

LINIE_ANTEIL = 0.15      # Dicke neuer Notenlinien in Linienabständen (höchstens so dick wie im Scan)
HALS_ANTEIL = 0.16       # Dicke neuer Hälse/Taktstriche
RAND_NEU = 2.0           # Randstreifen: neue Dicke + dieser Wert ...
RAND_ALT = 3.0           # ... mindestens aber (örtlich häufigste) alte Dicke + dieser Wert
DICKER_ALS_LINIE = 1.5   # so viel dicker als üblich = aufliegendes Zeichen (Balken, ♯ …)


@dataclass
class Linie:
    """Eine neu zu zeichnende Linie. Waagrecht: entlang = x-Spalten, quer = y-Mitte je Spalte.
    Senkrecht: entlang = y-Zeilen, quer = x-Mitte je Zeile."""
    senkrecht: bool
    entlang: np.ndarray
    quer: np.ndarray
    dicke: float
    alte_dicke: float | np.ndarray = 0.0   # typische Dicke der alten Linie im Scan (je Position)
    ende_anfang: int = 0      # an freien Enden auf dieser Länge breiterer Randstreifen
    ende_ende: int = 0        # (alte Drucke haben an Halsenden oft Tintenkleckse)


@dataclass
class Reparatur:
    weg: np.ndarray                              # alte Linienpixel, die entfernt werden (bool)
    linien: list[Linie] = field(default_factory=list)
    systeme: int = 0
    striche: int = 0
    ausgenommen: np.ndarray | None = None        # Bereiche, die wie im Original bleiben
    _deckung: np.ndarray | None = None           # zwischengespeichertes Raster der neuen Linien
    _deckung_fuer: tuple = ()

    def deckung(self, form: tuple[int, int]) -> np.ndarray:
        """Wie stark jedes Pixel von den neuen Linien bedeckt ist (0..1), zwischengespeichert."""
        schluessel = (tuple(form), len(self.linien), id(self.linien[-1]) if self.linien else None)
        if self._deckung is None or self._deckung_fuer != schluessel:
            self._deckung, self._deckung_fuer = _rastern(form, self.linien), schluessel
        return self._deckung

    def zeichnen(self, bild: np.ndarray, glatt: bool) -> np.ndarray:
        """Zeichnet die neuen Linien schwarz in ein Graustufenbild (Weiß = 255)."""
        deckung = self.deckung(bild.shape)
        self._deckung = None            # wird danach nicht mehr gebraucht (Speicher sparen)
        if self.ausgenommen is not None:
            deckung[self.ausgenommen] = 0
        if glatt:
            return np.minimum(bild, np.round(255 * (1 - deckung)).astype(np.uint8))
        bild = bild.copy()
        bild[deckung >= 0.5] = 0
        return bild


def _rastern(form: tuple[int, int], linien: list[Linie], streifen: bool = False) -> np.ndarray:
    """Wie viel jedes Pixel von den Linien bedeckt ist (0..1). Die Dicke wird exakt
    eingehalten, auch Bruchteile von Pixeln (cv2 zeichnet dicke Linien deutlich zu dick).
    streifen=True: der Randstreifen um die Linie statt der Linie selbst."""
    deckung = np.zeros(form, np.float32)
    for linie in linien:
        dicke = (np.maximum(linie.dicke + RAND_NEU, np.asarray(linie.alte_dicke, np.float64) + RAND_ALT)
                 if streifen else linie.dicke)
        halb = np.broadcast_to(np.asarray(dicke, np.float64) / 2, (len(linie.entlang),)).copy()
        if streifen:
            halb[:linie.ende_anfang] += 2
            halb[len(halb) - linie.ende_ende:] += 2 if linie.ende_ende else 0
        mitte = np.round(linie.quer).astype(int)
        grenze = form[1] if linie.senkrecht else form[0]
        r = int(np.ceil(halb.max() + 0.5))
        for d in range(-r, r + 1):
            pos = mitte + d
            wert = np.clip(halb + 0.5 - np.abs(pos - linie.quer), 0, 1).astype(np.float32)
            ok = (pos >= 0) & (pos < grenze) & (wert > 0)
            idx = (linie.entlang[ok], pos[ok]) if linie.senkrecht else (pos[ok], linie.entlang[ok])
            deckung[idx] = np.maximum(deckung[idx], wert[ok])
    return deckung


def linien_reparieren(maske: np.ndarray, abstand: float, dicke: float) -> Reparatur:
    """Findet Notensysteme und senkrechte Striche und bereitet ihre saubere Neuzeichnung vor.
    `abstand`: Abstand zweier Notenlinien, `dicke`: Dicke einer Notenlinie (Pixel)."""
    reparatur = Reparatur(np.zeros(maske.shape, bool))
    # Dicke wie im Notensatz üblich (Notenlinie und Hals ≈ 0,15 Linienabstände), aber
    # nie dicker als im Original – ausgefranste alte Linien wirken sonst zu fett
    linien_dicke = max(1.6, min(dicke, LINIE_ANTEIL * abstand))
    notenlinien = []  # (verlauf, x_von, x_bis, ungefähres y)
    for system in notensysteme_finden(maske, abstand, dicke):
        reparatur.systeme += 1
        for linie in system:
            for xa, xb in linie["abschnitte"]:
                xs = np.arange(xa, xb + 1)
                ys = _feinverlauf(maske, xs, linie["verlauf"](xs), dicke, abstand)
                alt = _typische_dicke(_lauflaengen(maske, xs, ys, dicke), dicke, abstand)
                reparatur.linien.append(Linie(False, xs, ys, linien_dicke, alt))
                notenlinien.append((linie["verlauf"], xa, xb, float(np.median(ys))))

    def einrasten(y: float, x: float) -> float:
        """Endet ein Strich auf einer (alten, dickeren) Notenlinie, genau auf der neuen enden
        lassen – sonst ragen Taktstriche ein Stück über die dünnere neue Linie hinaus."""
        for verlauf, xa, xb, y_ungefaehr in notenlinien:
            if xa - 2 <= x <= xb + 2 and abs(y_ungefaehr - y) < 2 * abstand:
                y_linie = float(verlauf(np.array([x]))[0])
                if abs(y_linie - y) <= dicke / 2 + 1.5:
                    return y_linie
        return y

    anzahl_waagrecht = len(reparatur.linien)
    ohne_linien = maske & (1 - cv2.morphologyEx(maske, cv2.MORPH_OPEN,
                                                np.ones((1, max(10, int(1.5 * abstand))), np.uint8)))
    for y0, y1, a, b, breite in senkrechte_striche_finden(maske, abstand):
        y0, y1 = einrasten(y0, a * y0 + b), einrasten(y1, a * y1 + b)
        ys = np.arange(int(round(y0)), int(round(y1)) + 1)
        laenge = int(0.5 * abstand)
        frei_oben = _freies_ende(ohne_linien, a * y0 + b, y0, abstand)
        frei_unten = _freies_ende(ohne_linien, a * y1 + b, y1, abstand)
        reparatur.linien.append(Linie(True, ys, a * ys + b, max(1.8, min(breite, HALS_ANTEIL * abstand)), breite,
                                      laenge if frei_oben else 0, laenge if frei_unten else 0))
        reparatur.striche += 1
    reparatur.weg = _fransen_finden(maske, reparatur.linien[:anzahl_waagrecht],
                                    reparatur.linien[anzahl_waagrecht:], abstand, dicke,
                                    reparatur.deckung(maske.shape) >= 0.5)
    return reparatur


def _feinverlauf(maske: np.ndarray, xs: np.ndarray, y_grob: np.ndarray, dicke: float,
                 abstand: float) -> np.ndarray:
    """Folgt der alten Linie Spalte für Spalte (alte Kopien haben oft leicht wellige Linien),
    geglättet über etwa einen Linienabstand. Wo die Linie fehlt oder von Noten verdeckt ist,
    wird zwischen den Nachbarstellen interpoliert."""
    h = maske.shape[0]
    r = int(dicke) + 3
    basis = np.round(y_grob).astype(int)
    zeilen = basis[:, None] + np.arange(-r, r + 1)[None, :]
    fenster = (maske[np.clip(zeilen, 0, h - 1), xs[:, None]] > 0) & (zeilen >= 0) & (zeilen < h)
    wechsel = np.diff(np.pad(fenster, ((0, 0), (1, 1))).astype(np.int8), axis=1)
    anfaenge, enden = np.argwhere(wechsel == 1), np.argwhere(wechsel == -1)
    spalte = anfaenge[:, 0]
    laenge = enden[:, 1] - anfaenge[:, 1]
    mitte = (anfaenge[:, 1] + enden[:, 1] - 1) / 2 - r + basis[spalte]
    # nur reine Linienstücke (kurze Läufe, nicht am Fensterrand abgeschnitten)
    gut = (laenge <= 2 * dicke + 1) & (anfaenge[:, 1] > 0) & (enden[:, 1] < 2 * r + 1)
    spalte, mitte = spalte[gut], mitte[gut]
    abweichung = np.full(len(xs), np.nan)
    if len(spalte):
        naechste = np.lexsort((np.abs(mitte - y_grob[spalte]), spalte))
        spalte, mitte = spalte[naechste], mitte[naechste]
        erste = np.unique(spalte, return_index=True)[1]
        abweichung[spalte[erste]] = mitte[erste] - y_grob[spalte[erste]]
    breite = max(5, int(abstand)) | 1
    glatt = _gleitender_mittelwert(abweichung, breite)
    # Ausreißer (z. B. ein Notenteil statt der Linie) verwerfen und neu glätten
    abweichung[np.abs(abweichung - glatt) > 1.5] = np.nan
    return y_grob + _gleitender_mittelwert(abweichung, breite)


def _gleitender_mittelwert(werte: np.ndarray, breite: int) -> np.ndarray:
    """Gleitender Mittelwert, der fehlende Werte (NaN) überspringt; Lücken werden aus der
    Umgebung aufgefüllt (bei sehr großen Lücken: 0 = keine Abweichung)."""
    gueltig = ~np.isnan(werte)
    kern = np.ones(breite) / breite
    summe = np.convolve(np.where(gueltig, werte, 0.0), kern, mode="same")
    anzahl = np.convolve(gueltig.astype(float), kern, mode="same")
    ergebnis = np.divide(summe, anzahl, out=np.zeros_like(summe), where=anzahl > 0.05)
    if gueltig.any() and (anzahl <= 0.05).any():
        x = np.arange(len(werte))
        ergebnis[anzahl <= 0.05] = np.interp(x[anzahl <= 0.05], x[anzahl > 0.05], ergebnis[anzahl > 0.05]) \
            if (anzahl > 0.05).any() else 0.0
    return ergebnis


def _baender(form: tuple[int, int], linien: list[Linie], streifen: bool = False) -> np.ndarray:
    return _rastern(form, linien, streifen) >= 0.5


def _lauflaengen(maske: np.ndarray, xs: np.ndarray, ys: np.ndarray, dicke: float) -> np.ndarray:
    """Länge des senkrechten schwarzen Laufs durch die Linienmitte, je Spalte (0 = Lücke).
    Läufe, die bis an den Rand des Suchfensters reichen, werden sehr lang gezählt."""
    h = maske.shape[0]
    r = int(3 * dicke) + 2
    zeilen = np.round(ys).astype(int)[:, None] + np.arange(-r, r + 1)[None, :]
    fenster = (maske[np.clip(zeilen, 0, h - 1), xs[:, None]] > 0) & (zeilen >= 0) & (zeilen < h)
    start = np.full(len(fenster), -1)
    for o in (0, -1, 1):
        frei = (start < 0) & fenster[:, r + o]
        start[frei] = r + o
    idx = np.arange(fenster.shape[1])[None, :]
    s = np.where(start >= 0, start, r)[:, None]
    oben = np.where(~fenster & (idx <= s), idx, -1).max(axis=1)
    unten = np.where(~fenster & (idx >= s), idx, fenster.shape[1]).min(axis=1)
    return np.where(start >= 0, unten - oben - 1, 0)


def _typische_dicke(laengen: np.ndarray, dicke: float, abstand: float) -> float:
    """Typische Dicke dieser alten Linie: die häufigste Strichdicke über ihre ganze Länge.
    So passt sich die Reparatur an Linien an, die im Foto insgesamt dicker sind (Schärfe,
    Licht – z. B. die oberste Zeile einer Seite). Aufliegende Balken bilden eine eigene,
    deutlich dickere Häufung; über die ganze Linie gesehen kommen sie seltener vor als die
    freie Linie. Zur Sicherheit höchstens 2 Pixel dicker als auf der Seite üblich."""
    rein = laengen[(laengen > 0) & (laengen <= 2 * dicke + 2)].astype(int)
    if len(rein) < 20:
        return float(dicke)
    return float(np.clip(np.argmax(np.bincount(rein)), 0.75 * dicke, dicke + 2))


def _aufliegende_zeichen(maske: np.ndarray, waagrechte: list[Linie], dicke: float, abstand: float) -> np.ndarray:
    """Wo liegt etwas auf einer Notenlinie (Balken, Querbalken eines ♯, Notenkopf …)?

    Ist die alte Linie über mindestens einen halben Linienabstand deutlich dicker als dort
    üblich, ist das keine Ausbeulung der Linie, sondern ein aufliegendes Zeichen – dort wird
    nichts entfernt. Kurze Verdickungen (typisch für alte Kopien) zählen dagegen als Fransen."""
    h, w = maske.shape
    schutz = np.zeros(maske.shape, bool)
    r = int(3 * dicke) + 2
    for linie in waagrechte:
        laengen = _lauflaengen(maske, linie.entlang, linie.quer, dicke)
        ueblich = np.broadcast_to(np.asarray(linie.alte_dicke, np.float64), laengen.shape)
        dick = laengen > ueblich + DICKER_ALS_LINIE
        # nur zusammenhängende dicke Stellen ab einem halben Linienabstand Länge
        wechsel = np.diff(np.concatenate([[0], dick.astype(np.int8), [0]]))
        zeilen = np.round(linie.quer).astype(int)[:, None] + np.arange(-r, r + 1)[None, :]
        for a, b in zip(np.flatnonzero(wechsel == 1), np.flatnonzero(wechsel == -1)):
            if b - a >= 0.5 * abstand:
                a, b = max(0, a - 2), min(len(dick), b + 2)
                spalten = np.repeat(linie.entlang[a:b], 2 * r + 1)
                schutz[np.clip(zeilen[a:b].ravel(), 0, h - 1), spalten] = True
    return schutz


def _fransen_finden(maske: np.ndarray, waagrechte, senkrechte, abstand: float, dicke: float,
                    neu: np.ndarray) -> np.ndarray:
    """Welche alten Pixel dürfen weg? Nur Fransen und Ausbeulungen der alten Linien.

    Um jede neue Linie liegt ein schmaler Randstreifen. Ein Pixel darin wird
    entfernt, wenn der schwarze Strich durch ihn – quer zur Linie gemessen – vollständig im
    Streifen bleibt. Reicht er darüber hinaus, gehört er zu einem Notenkopf, Bogen, Schlüssel
    usw. und bleibt. Direkt an größeren Zeichen bleiben außerdem 3 Pixel stehen, damit z. B.
    ein Bogen, der eine Linie flach kreuzt, nicht angeknabbert wird."""
    schwarz = maske > 0
    weg = np.zeros(maske.shape, bool)
    # Randstreifen: 1 Pixel je Seite über die neue bzw. die (örtliche) alte Linie hinaus – was
    # darüber hinaus reicht, gehört zu einem anderen Zeichen
    streifen = []
    for linien, senkrecht_messen in ((waagrechte, True), (senkrechte, False)):
        if linien:
            streifen.append(_baender(maske.shape, linien, streifen=True))
            weg |= streifen[-1] & _laeufe_im_streifen(schwarz, streifen[-1], senkrecht_messen)
    if len(streifen) == 2:
        # Kreuzungen von Notenlinie und Hals: dort bleibt ein Pixel nur, wenn er in einer
        # Richtung über beide Streifen hinausreicht (z. B. ein Notenkopf auf der Kreuzung)
        beide, vereint = streifen[0] & streifen[1], streifen[0] | streifen[1]
        weg |= (beide & _laeufe_im_streifen(schwarz, vereint, True)
                & _laeufe_im_streifen(schwarz, vereint, False))
    if waagrechte:
        weg &= ~_aufliegende_zeichen(maske, waagrechte, dicke, abstand)
    # Schutz direkt an größeren Zeichen
    zeichen = schwarz & ~weg & ~neu
    anzahl, etiketten, stats, _ = cv2.connectedComponentsWithStats(zeichen.astype(np.uint8), connectivity=8)
    gross = (stats[:, cv2.CC_STAT_WIDTH] >= 0.4 * abstand) | (stats[:, cv2.CC_STAT_HEIGHT] >= 0.4 * abstand)
    gross[0] = False
    geschuetzt = gross[etiketten].astype(np.uint8)
    for _ in range(3):
        geschuetzt = (cv2.dilate(geschuetzt, np.ones((3, 3), np.uint8)) > 0) & (weg | (geschuetzt > 0))
        geschuetzt = geschuetzt.astype(np.uint8)
    return weg & ~(geschuetzt > 0)


def _laeufe_im_streifen(schwarz: np.ndarray, streifen: np.ndarray, senkrecht: bool) -> np.ndarray:
    """Markiert schwarze Pixel, deren zusammenhängender Lauf (senkrecht bzw. waagrecht)
    komplett innerhalb des Streifens liegt."""
    a = np.ascontiguousarray(schwarz.T if senkrecht else schwarz)   # Läufe entlang der Zeilen
    b = np.ascontiguousarray(streifen.T if senkrecht else streifen)
    anfang = a.copy()
    anfang[:, 1:] &= ~a[:, :-1]
    lauf = np.cumsum(anfang.ravel(), dtype=np.int32).reshape(a.shape) * a
    ragt_hinaus = np.bincount(lauf[a & ~b], minlength=int(lauf.max()) + 1) > 0
    ragt_hinaus[0] = True
    drin = ~ragt_hinaus[lauf]
    return drin.T if senkrecht else drin


# ---------------------------------------------------------------- Notenlinien

def notensysteme_finden(maske: np.ndarray, abstand: float, dicke: float) -> list[list[dict]]:
    """Sucht Notensysteme (je fünf gleichmäßig verteilte, waagrechte Linien).

    Das Blatt wird in senkrechte Streifen geteilt; in jedem Streifen werden Fünfergruppen
    von Linien gesucht und von Streifen zu Streifen verfolgt. So folgt der Verlauf auch
    leicht gewölbtem Papier. Jede Linie bekommt eine Funktion verlauf(x) -> y und die
    x-Abschnitte, in denen sie tatsächlich vorhanden ist.
    """
    h, w = maske.shape
    waagrecht = cv2.morphologyEx(maske, cv2.MORPH_OPEN, np.ones((1, max(8, int(0.8 * abstand))), np.uint8))
    breite = max(24, int(3 * abstand))
    anzahl = w // breite
    if anzahl < 3:
        return []
    profil = waagrecht[:, :anzahl * breite].reshape(h, anzahl, breite).sum(axis=2).astype(np.float32)
    max_hoehe = 2 * dicke + 2

    # 1. In jedem Streifen die Lage aller dünnen waagrechten Linien bestimmen
    spitzen_je_streifen = []
    for s in range(anzahl):
        p = profil[:, s]
        wechsel = np.diff(np.concatenate([[0], (p > 0.3 * breite).astype(np.int8), [0]]))
        spitzen = []
        for a, b in zip(np.flatnonzero(wechsel == 1), np.flatnonzero(wechsel == -1)):
            if b - a <= max_hoehe:
                gewicht = p[a:b]
                spitzen.append(float((np.arange(a, b) * gewicht).sum() / gewicht.sum()))
        spitzen_je_streifen.append(np.array(spitzen))

    # Jede Linie über die Streifen hinweg verfolgen (auch schräg und über Lücken, in denen
    # Balken oder Notenköpfe sie verdecken). Echte Notenlinien laufen durch, Balken nicht.
    spuren = _linien_verfolgen(spitzen_je_streifen, abstand)

    # In jedem Streifen aus den durchlaufenden Spuren Fünfergruppen bilden. Gibt es mehr als
    # fünf gleichmäßig verteilte (z. B. ein Balken genau einen Abstand über dem System),
    # gewinnen die Spuren, die über die längste Strecke durchlaufen.
    gruppen = []
    for s in range(anzahl):
        da = [(float(np.interp(s, sp[:, 0], sp[:, 1])), len(sp)) for sp in spuren if sp[0, 0] <= s <= sp[-1, 0]]
        da.sort()
        ys = np.array([d[0] for d in da])
        laengen = np.array([d[1] for d in da])
        fenster = [i for i in range(len(ys) - 4)
                   if np.all(np.abs(np.diff(ys[i:i + 5]) - abstand) < 0.25 * abstand)]
        gefunden = []
        while fenster:
            bestes = max(fenster, key=lambda i: laengen[i:i + 5].min())
            gefunden.append(ys[bestes:bestes + 5])
            fenster = [i for i in fenster if abs(i - bestes) >= 5]
        gruppen.append(gefunden)

    # 2. Gruppen über die Streifen hinweg zu Systemen verketten
    ketten: list[dict] = []
    for s, gefunden in enumerate(gruppen):
        for g in gefunden:
            passend = [k for k in ketten if s - k["letzter"] <= 4
                       and abs(k["ys"][k["letzter"]][0] - g[0]) < 0.5 * abstand]
            if passend:
                kette = min(passend, key=lambda k: abs(k["ys"][k["letzter"]][0] - g[0]))
            else:
                kette = {"ys": {}}
                ketten.append(kette)
            kette["ys"][s] = g
            kette["letzter"] = s

    # Ketten desselben Systems (durch lange Lücken getrennt, z. B. wo dicht gedrängte Balken
    # die Linien verdecken) wieder zusammenführen – auch wenn das System leicht schräg verläuft:
    # zwei Ketten gehören zusammen, wenn sie auf derselben Geraden liegen
    zusammen = list(ketten)
    geaendert = True
    while geaendert:
        geaendert = False
        for i in range(len(zusammen)):
            for j in range(i + 1, len(zusammen)):
                if _gleiche_gerade(zusammen[i]["ys"], zusammen[j]["ys"], abstand):
                    for s, g in zusammen[j]["ys"].items():
                        zusammen[i]["ys"].setdefault(s, g)
                    del zusammen[j]
                    geaendert = True
                    break
            if geaendert:
                break
    # 3. Überlappende Kandidaten (z. B. um eine Linie verschoben, weil ein Balken oder eine
    # Hilfslinie mitgezählt wurde): nur den über die meisten Streifen bestätigten behalten
    zusammen.sort(key=lambda k: len(k["ys"]), reverse=True)
    ketten, belegt = [], []
    for kette in zusammen:
        ys = np.array(list(kette["ys"].values()))
        oben, unten = np.median(ys[:, 0]) - 0.5 * abstand, np.median(ys[:, 4]) + 0.5 * abstand
        if all(unten < o or oben > u for o, u in belegt):
            ketten.append(kette)
            belegt.append((oben, unten))

    systeme = []
    for kette in ketten:
        if len(kette["ys"]) < 3:
            continue
        streifen = np.array(sorted(kette["ys"]))
        Y = np.array([kette["ys"][s] for s in streifen])           # (Streifen x 5)
        mitten = streifen * breite + breite / 2.0
        # Gemeinsame Form aller fünf Linien (Wölbung) + Versatz jeder Linie
        versatz = np.median(Y, axis=0)
        form = np.median(Y - versatz, axis=1)
        gut = np.all(np.abs(Y - versatz - form[:, None]) <= max(1.5, dicke), axis=1)
        if gut.sum() < 3:
            continue
        streifen, Y, mitten, form = streifen[gut], Y[gut], mitten[gut], form[gut]
        versatz = np.median(Y - form[:, None], axis=0)
        form = _glaetten(np.median(Y - versatz, axis=1))
        verlauf_form = _stueckweise(mitten, form)
        linien = [{"verlauf": (lambda x, c=c, f=verlauf_form: c + f(x))} for c in versatz]
        # Ob die Linien wirklich da sind, wird über die ganze Breite geprüft (am Zeilenanfang
        # verdecken Schlüssel und Vorzeichen die Linien oft, dort gibt es keine Fünfergruppen)
        abschnitte = _vorhanden(waagrecht, linien, dicke, abstand, 0, w - 1)
        if not abschnitte:
            continue
        for linie in linien:
            linie["abschnitte"] = abschnitte
        systeme.append(linien)
    return systeme


def _linien_verfolgen(spitzen_je_streifen: list[np.ndarray], abstand: float) -> list[np.ndarray]:
    """Verbindet Linienstücke benachbarter Streifen zu Spuren (Streifen, y). Die Steigung jeder
    Spur wird laufend geschätzt, damit auch schräg verlaufende Linien sicher verfolgt werden.
    Lücken von bis zu 4 Streifen werden überbrückt."""
    spuren: list[list[tuple[int, float]]] = []
    aktiv: list[list[tuple[int, float]]] = []
    for s, ys in enumerate(spitzen_je_streifen):
        aktiv = [sp for sp in aktiv if s - sp[-1][0] <= 4]
        paare = []
        for ti, sp in enumerate(aktiv):
            if len(sp) >= 3:
                letzte = np.array(sp[-6:])
                vorhersage = np.polyval(np.polyfit(letzte[:, 0], letzte[:, 1], 1), s)
            else:
                vorhersage = sp[-1][1]
            toleranz = 0.2 * abstand + 0.5 * (s - sp[-1][0] - 1)
            for pi, y in enumerate(ys):
                if abs(y - vorhersage) <= toleranz:
                    paare.append((abs(y - vorhersage), ti, pi))
        paare.sort()
        spur_benutzt, punkt_benutzt = set(), set()
        for _, ti, pi in paare:
            if ti not in spur_benutzt and pi not in punkt_benutzt:
                aktiv[ti].append((s, float(ys[pi])))
                spur_benutzt.add(ti)
                punkt_benutzt.add(pi)
        for pi, y in enumerate(ys):
            if pi not in punkt_benutzt:
                neu = [(s, float(y))]
                spuren.append(neu)
                aktiv.append(neu)
    return [np.array(sp) for sp in spuren if len(sp) >= 3 and sp[-1][0] - sp[0][0] >= 4]


def _gleiche_gerade(a: dict, b: dict, abstand: float) -> bool:
    """Liegen zwei Ketten (Streifen -> Linienlagen) gemeinsam auf einer, evtl. schrägen,
    Geraden? (Die Steigung kurzer Teilstücke allein wäre zu ungenau.)"""
    s = np.array(sorted(a) + sorted(b), float)
    y = np.array([a[k][0] for k in sorted(a)] + [b[k][0] for k in sorted(b)])
    if np.ptp(s) == 0:   # beide nur im selben Streifen: keine Gerade bestimmbar
        return bool(np.abs(y - np.median(y)).max() < 0.15 * abstand)
    rest = np.abs(y - np.polyval(np.polyfit(s, y, 1), s))
    return float(np.median(rest)) < 0.15 * abstand and float(np.percentile(rest, 90)) < 0.3 * abstand


def _glaetten(werte: np.ndarray) -> np.ndarray:
    if len(werte) < 3:
        return werte
    gepolstert = np.concatenate([[werte[0]], werte, [werte[-1]]])
    return (gepolstert[:-2] + gepolstert[1:-1] + gepolstert[2:]) / 3


def _stueckweise(x_stuetz: np.ndarray, y_stuetz: np.ndarray):
    """Lineare Interpolation; außerhalb gerade weiter (Steigung der äußeren Stützstellen)."""
    n = min(3, len(x_stuetz))
    links = np.polyfit(x_stuetz[:n], y_stuetz[:n], 1) if n >= 2 else (0.0, y_stuetz[0])
    rechts = np.polyfit(x_stuetz[-n:], y_stuetz[-n:], 1) if n >= 2 else (0.0, y_stuetz[-1])

    def verlauf(x):
        x = np.asarray(x, np.float64)
        y = np.interp(x, x_stuetz, y_stuetz)
        y = np.where(x < x_stuetz[0], np.polyval(links, x) - np.polyval(links, x_stuetz[0]) + y_stuetz[0], y)
        return np.where(x > x_stuetz[-1], np.polyval(rechts, x) - np.polyval(rechts, x_stuetz[-1]) + y_stuetz[-1], y)
    return verlauf


def _vorhanden(waagrecht: np.ndarray, linien: list[dict], dicke: float, abstand: float,
               x_von: int, x_bis: int) -> list[tuple[int, int]]:
    """Wo genau beginnt und endet das System? Eine Spalte gehört dazu, wenn mindestens drei
    der fünf Linien dort zu sehen sind. Kleine Lücken (abgebrochene Linien) werden geschlossen."""
    h = waagrecht.shape[0]
    xs = np.arange(x_von, x_bis + 1)
    r = max(1, int(dicke // 2) + 1)
    sichtbar = np.zeros(len(xs), np.int32)
    for linie in linien:
        yc = np.round(linie["verlauf"](xs)).astype(int)
        treffer = np.zeros(len(xs), bool)
        for d in range(-r, r + 1):
            treffer |= waagrecht[np.clip(yc + d, 0, h - 1), xs] > 0
        sichtbar += treffer
    da = (sichtbar >= 3).astype(np.uint8)
    luecke = int(2.5 * abstand)
    da = cv2.morphologyEx(da[None, :], cv2.MORPH_CLOSE, np.ones((1, luecke), np.uint8))[0]
    wechsel = np.diff(np.concatenate([[0], da.astype(np.int8), [0]]))
    return [(int(xs[a]), int(xs[b - 1])) for a, b in zip(np.flatnonzero(wechsel == 1), np.flatnonzero(wechsel == -1))
            if b - a >= 3 * abstand]


# ---------------------------------------------------------------- Hälse und Taktstriche

def senkrechte_striche_finden(maske: np.ndarray, abstand: float) -> list[tuple[int, int, float, float, float]]:
    """Findet lange, dünne, gerade, (fast) senkrechte Striche: Notenhälse und Taktstriche.
    Kleine Unterbrechungen werden überbrückt. Gibt (y_oben, y_unten, a, b, breite) zurück,
    die Mittellinie ist x = a*y + b."""
    bruecke = max(3, int(0.3 * abstand))
    geschlossen = cv2.morphologyEx(maske, cv2.MORPH_CLOSE, np.ones((bruecke, 1), np.uint8))
    senkrecht = cv2.morphologyEx(geschlossen, cv2.MORPH_OPEN, np.ones((max(10, int(1.6 * abstand)), 1), np.uint8))
    anzahl, etiketten, stats, _ = cv2.connectedComponentsWithStats(senkrecht, connectivity=8)
    kandidaten = []
    for i in range(1, anzahl):
        x, y, bw, bh = stats[i, :4]
        if bh < 2.6 * abstand or bw > 0.5 * abstand:
            continue
        teil = etiketten[y:y + bh, x:x + bw] == i
        pro_zeile = teil.sum(axis=1)
        gueltig = pro_zeile > 0
        ys = np.arange(y, y + bh)[gueltig]
        mitte = (teil * np.arange(bw)).sum(axis=1)[gueltig] / pro_zeile[gueltig] + x
        a, b = np.polyfit(ys, mitte, 1)
        breite = float(np.median(pro_zeile[gueltig]))
        abweichung = np.abs(mitte - (a * ys + b))
        if (abs(a) > np.tan(np.radians(6)) or np.percentile(abweichung, 90) > max(1.0, 0.35 * breite)
                or abweichung.max() > max(2.0, 0.7 * breite)
                or np.percentile(pro_zeile[gueltig], 90) > 1.7 * breite + 1):
            continue  # schräg, gebogen oder ungleich breit (Notenschlüssel, Pausen) – nicht anfassen
        kandidaten.append((int(ys[0]), int(ys[-1]), float(a), float(b), breite))
    if not kandidaten:
        return []
    # Für die ♭-Erkennung Notenlinien ausblenden (die würden links und rechts gleich zählen)
    linien = cv2.morphologyEx(maske, cv2.MORPH_OPEN, np.ones((1, max(10, int(1.5 * abstand))), np.uint8))
    ohne_linien = maske & (1 - linien)
    kandidaten = [k for k in kandidaten if not _sieht_aus_wie_be(ohne_linien, k, abstand)]
    if not kandidaten:
        return []
    typisch = float(np.median([k[4] for k in kandidaten]))
    # Dicht nebeneinander stehende Striche (♯, ♮, doppelte Taktstriche) bleiben wie im Original
    k = np.array(kandidaten)
    mitte_x = k[:, 2] * (k[:, 0] + k[:, 1]) / 2 + k[:, 3]
    ueberlappung = np.minimum(k[:, 1][:, None], k[:, 1][None, :]) - np.maximum(k[:, 0][:, None], k[:, 0][None, :])
    kuerzer = np.minimum((k[:, 1] - k[:, 0])[:, None], (k[:, 1] - k[:, 0])[None, :])
    nah = ((np.abs(mitte_x[:, None] - mitte_x[None, :]) < max(3 * typisch + 2, 0.7 * abstand))
           & (ueberlappung > 0.5 * kuerzer))
    np.fill_diagonal(nah, False)
    einzeln = ~nah.any(axis=1)
    # Dicke Schlussstriche und fette Schrift auslassen; alle übrigen einheitlich dick
    return [(y0, y1, a, b, typisch) for (y0, y1, a, b, breite), frei in zip(kandidaten, einzeln)
            if frei and breite <= 1.8 * typisch]


def _sieht_aus_wie_be(maske: np.ndarray, strich, abstand: float) -> bool:
    """Ein ♭ hat unten rechts am Strich einen Bauch und links daneben nichts. Bei einem
    Notenhals sitzt der Kopf unten links (Hals nach oben) oder oben rechts (Hals nach unten).
    Solche Striche werden nicht neu gezeichnet, damit der Bauch nicht abgetrennt wird."""
    y0, y1, a, b, breite = strich
    h, w = maske.shape
    xm = int(round(a * y1 + b))
    oben, unten = max(0, int(y1 - 1.1 * abstand)), min(h, int(y1) + 1)
    rechts = maske[oben:unten, min(w, xm + int(breite // 2) + 2):min(w, xm + int(0.7 * abstand))]
    links = maske[oben:unten, max(0, xm - int(0.45 * abstand)):max(0, xm - int(breite // 2) - 1)]
    if rechts.size == 0:
        return False
    # Nur Spalten mit Tinte in mindestens einem Viertel der Zeilen zählen als "Bauch"
    anteil_rechts = float(np.mean(rechts.mean(axis=0) > 0.25)) if rechts.size else 0.0
    anteil_links = float(np.mean(links.mean(axis=0) > 0.25)) if links.size else 0.0
    return anteil_rechts > 0.3 and anteil_links < 0.15


def _freies_ende(ohne_linien: np.ndarray, x: float, y: float, abstand: float) -> bool:
    """Schließt an diesem Strichende nichts an (kein Notenkopf, kein Bauch eines ♭ …)?"""
    h, w = ohne_linien.shape
    oben, unten = max(0, int(y - 0.3 * abstand)), min(h, int(y + 0.3 * abstand) + 1)
    innen, aussen = int(0.35 * abstand), int(0.8 * abstand)
    links = ohne_linien[oben:unten, max(0, int(x) - aussen):max(0, int(x) - innen)]
    rechts = ohne_linien[oben:unten, min(w, int(x) + innen):min(w, int(x) + aussen)]
    return all(t.size == 0 or t.mean() < 0.05 for t in (links, rechts))


def selbstkontrolle(maske: np.ndarray, reparatur: Reparatur, abstand: float) -> int:
    """Prüft das Ergebnis der Linien-Reparatur gegen das Original und nimmt sie dort zurück,
    wo sie mehr verändert hätte als Linienfransen:

    - erfundene Linien: neu gezeichnete Striche länger als 4 Linienabstände an Stellen, an
      denen im Original keine Tinte ist (kurze Stücke sind gewollt: geschlossene Lücken)
    - verlorene Zeichen: entlang der Notenlinien entfernte Tinte, die mindestens 3 Pixel dick
      ist (z. B. ein Balken), oder Stellen, die über mindestens einen halben Linienabstand
      deutlich dicker als die Linie waren und trotzdem ausgedünnt wurden. Das Ausdünnen zu
      dicker Hälse und Taktstriche ist dagegen gewollt.

    In diesen Bereichen (plus einem Linienabstand Rand) bleibt alles wie im Original.
    Gibt die Anzahl der zurückgenommenen Stellen zurück."""
    alt = maske > 0
    neu = (alt & ~reparatur.weg) | (reparatur.deckung(maske.shape) >= 0.5)
    abstand_zur_tinte = cv2.distanceTransform((~alt).astype(np.uint8), cv2.DIST_L2, 3)
    erfunden = (neu & ~alt & (abstand_zur_tinte > 4)).astype(np.uint8)
    striche = [linie for linie in reparatur.linien if linie.senkrecht]
    strichbereich = cv2.dilate(_baender(maske.shape, striche, streifen=True).astype(np.uint8),
                               np.ones((5, 5), np.uint8)) > 0 if striche else np.zeros(maske.shape, bool)
    verloren = cv2.erode((alt & ~neu & ~strichbereich).astype(np.uint8), np.ones((3, 3), np.uint8))
    ausnehmen = np.zeros(maske.shape, bool)
    stellen = 0
    r = int(abstand)
    # zu viel entfernt: je Notenlinie die Strichdicke vorher/nachher vergleichen
    neu_u8 = neu.astype(np.uint8)
    for linie in reparatur.linien:
        if linie.senkrecht:
            continue
        typisch = float(np.max(linie.alte_dicke))
        vorher = _lauflaengen(maske, linie.entlang, linie.quer, typisch)
        nachher = _lauflaengen(neu_u8, linie.entlang, linie.quer, typisch)
        # Stellen, die im Original deutlich dicker als die Linie waren (aufliegendes Zeichen),
        # dürfen nicht ausgedünnt worden sein
        zu_viel = (vorher > typisch + DICKER_ALS_LINIE) & (vorher < 3 * typisch) & (nachher < vorher - 1.5)
        zu_viel &= ~strichbereich[np.clip(np.round(linie.quer).astype(int), 0, maske.shape[0] - 1), linie.entlang]
        wechsel = np.diff(np.concatenate([[0], zu_viel.astype(np.int8), [0]]))
        for a, b in zip(np.flatnonzero(wechsel == 1), np.flatnonzero(wechsel == -1)):
            if b - a >= 0.5 * abstand:
                xa, xb = int(linie.entlang[a]), int(linie.entlang[b - 1])
                ym = int(round(float(np.mean(linie.quer[a:b]))))
                ausnehmen[max(0, ym - r):ym + r, max(0, xa - r):xb + r] = True
                stellen += 1
    for teil, ist_verdaechtig in ((erfunden, lambda st: max(st[2], st[3]) > 4 * abstand),
                                  (verloren, lambda st: st[4] >= 12)):
        anzahl, _, stats, _ = cv2.connectedComponentsWithStats(teil, connectivity=8)
        for st in stats[1:]:
            if ist_verdaechtig(st):
                x, y, w, h = st[:4]
                ausnehmen[max(0, y - r):y + h + r, max(0, x - r):x + w + r] = True
                stellen += 1
    if stellen:
        reparatur.weg &= ~ausnehmen
        reparatur.ausgenommen = ausnehmen
    return stellen
