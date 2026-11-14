"""Prüfungen der Notenerkennung am Bild (bereinigte Seite):
  - Mehrtaktpausen: dicker Balken auf der Mittellinie, Zahl mittig darüber (auch „1“).
  - Pausenform: Höhe eines Pausenzeichens (Viertel- oder Achtelpause).
  - Faulenzer (Taktwiederholungszeichen), auch über einen Taktstrich (zwei Takte).
  - Wiederholungszeichen (dicker Taktstrich mit zwei Punkten) und Volta-Klammern mit ihrer Zahl.
  - Texte: Titel und Stimme im Kopf der Seite, Vortrags- und Tempoangaben, eingerahmte Studierzeichen.
  - Gegenprobe Notenkopf: Gibt es an der Stelle einer Note wirklich einen Notenkopf?
"""
import re, sys
import cv2, numpy as np
import xml.etree.ElementTree as ET
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))
from notenscanner.verarbeitung import notenlinien_masse
from notenscanner.linien import notensysteme_finden

_ocr = None


OCR_FEHLER = ""


def _ocr_holen():
    global _ocr
    if _ocr is None:
        import logging
        from rapidocr import RapidOCR
        _ocr = RapidOCR()
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
    return _ocr


def _ocr_lesen(bild):
    """Text in einem kleinen Bildausschnitt lesen (rapidocr). Fehlt die Texterkennung oder schlägt
    sie fehl, kommt "" zurück – die Erkennung der Noten läuft dann ohne diese Zugabe weiter."""
    global OCR_FEHLER
    if OCR_FEHLER:
        return ""
    try:
        _ocr_holen()
        h, w = bild.shape
        f = 72 / max(h, 1)
        gross = cv2.resize(bild, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        gross = cv2.copyMakeBorder(gross, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
        erg = _ocr(cv2.cvtColor(gross, cv2.COLOR_GRAY2BGR))
        return " ".join(getattr(erg, "txts", None) or [])
    except Exception as fehler:
        OCR_FEHLER = f"{type(fehler).__name__}: {fehler}"
        return ""


class Seite:
    def __init__(self, bild):
        self.bild = bild
        self.maske = (bild < 128).astype(np.uint8)
        dicke, luecke = notenlinien_masse(self.maske)[:2]
        self.ab = dicke + luecke
        self.systeme = notensysteme_finden(self.maske, self.ab, dicke)

    def system_bei(self, x, y):
        """Nummer des Systems, dessen Mittellinie bei x am nächsten an y liegt."""
        return min(range(len(self.systeme)), key=lambda k: abs(float(self.systeme[k][2]["verlauf"](x)) - y))

    def linien_bei(self, x, y):
        """Notenlinien (y oben..unten) des Systems, dessen Mittellinie bei x am nächsten an y liegt."""
        bestes = self.systeme[self.system_bei(x, y)]
        return [float(li["verlauf"](x)) for li in bestes]

    def ohne_linien(self, a, b, xa, xb, ys):
        """Ausschnitt als Maske, Notenlinien entfernt; Pixel, die zu einem Zeichen gehören (senkrechter
        Lauf länger als die Liniendicke), bleiben erhalten."""
        m = self.maske[a:b, xa:xb].copy()
        dicke = max(2, int(round(self.ab * 0.12)) + 1)
        for y in ys:
            r0, r1 = int(round(y - dicke)) - a, int(round(y + dicke)) - a + 1
            if r1 <= 0 or r0 >= m.shape[0]:
                continue
            r0, r1 = max(0, r0), min(m.shape[0], r1)
            for x in range(m.shape[1]):
                spalte = self.maske[a:b, xa + x]
                for yy in range(r0, r1):
                    if not spalte[yy]:
                        continue
                    o = yy
                    while o > 0 and spalte[o - 1]:
                        o -= 1
                    u = yy
                    while u + 1 < len(spalte) and spalte[u + 1]:
                        u += 1
                    if u - o + 1 <= dicke + 1:
                        m[o:u + 1, x] = 0
        return m

    def zahl_ueber(self, x0, x1, ys, oben=True, y0=None, y1=None):
        """Zahl über (bzw. unter) dem Zeichen im Bereich x0..x1 (z. B. dem Balken einer
        Mehrtaktpause, senkrecht y0..y1), waagrecht mittig darüber. Ohne y0/y1: über bzw. unter dem
        Notensystem. Rückgabe: (Zahl oder None, Ziffern gefunden)."""
        ab = self.ab
        if oben:
            a, b = ys[0] - 3.8 * ab, (y0 - 0.1 * ab) if y0 is not None else ys[1] - 0.15 * ab
        else:
            a, b = (y1 + 0.1 * ab) if y1 is not None else ys[3] + 0.15 * ab, ys[4] + 3.8 * ab
        a, b = int(max(0, a)), int(min(self.bild.shape[0], b))
        xa, xb = int(max(0, x0 - 1.2 * ab)), int(min(self.bild.shape[1], x1 + 1.2 * ab))
        if b <= a or xb <= xa:
            return None, False
        m = self.ohne_linien(a, b, xa, xb, ys)
        lang = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((1, int(2.2 * ab)), np.uint8))
        m = m & ~(lang > 0)                            # lange waagrechte Striche (Klammern, Rahmen)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        mitte = (x0 + x1) / 2 - xa
        teile = [i for i in range(1, n) if 0.8 * ab <= stats[i, 3] <= 3.2 * ab and stats[i, 4] >= 0.3 * ab * ab
                 and stats[i, 2] <= 2.5 * ab and stats[i, 4] < 0.8 * stats[i, 2] * stats[i, 3]]
        if not teile:
            return None, False
        haupt = min(teile, key=lambda i: abs(stats[i, 0] + stats[i, 2] / 2 - mitte))
        gruppe = [i for i in teile if abs(stats[i, 1] - stats[haupt, 1]) < 0.6 * ab
                  and abs(stats[i, 0] + stats[i, 2] / 2 - (stats[haupt, 0] + stats[haupt, 2] / 2)) < 2.2 * ab]
        gx0 = min(stats[i, 0] for i in gruppe); gx1 = max(stats[i, 0] + stats[i, 2] for i in gruppe)
        gy0 = min(stats[i, 1] for i in gruppe); gy1 = max(stats[i, 1] + stats[i, 3] for i in gruppe)
        if abs((gx0 + gx1) / 2 - mitte) > max(1.5 * ab, (x1 - x0) / 2):
            return None, False                         # nicht mittig über dem Balken
        ziffern = np.where(np.isin(lab, gruppe), 0, 255).astype(np.uint8)[gy0:gy1, gx0:gx1]
        self.letzte_ziffern = ziffern
        text = _ocr_lesen(ziffern)
        text = text.replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1").replace("|", "1")
        z = re.sub(r"[^0-9]", "", text)
        if z and len(z) <= 3 and 1 <= int(z) <= 200:
            return int(z), True
        return None, True

    def zahl_bei(self, x, ys, oben, breite=3.2):
        """Zahl über bzw. unter dem System bei x (ältere Schnittstelle)."""
        return self.zahl_ueber(x - breite * self.ab / 2, x + breite * self.ab / 2, ys, oben)[0]


def mehrtaktbalken(seite, x, ys):
    """Balken einer Mehrtaktpause nahe x: dicker, waagrechter, gefüllter Balken auf der Mittellinie
    (moderne Schreibweise). Rückgabe (x0, x1) oder None."""
    ab = seite.ab
    a, b = int(max(0, ys[1] - 0.6 * ab)), int(min(seite.bild.shape[0], ys[3] + 0.6 * ab))
    xa, xb = int(max(0, x - 6 * ab)), int(min(seite.bild.shape[1], x + 8 * ab))
    if b <= a or xb <= xa:
        return None
    m = seite.maske[a:b, xa:xb]
    kern = np.ones((max(2, int(round(0.35 * ab))), max(3, int(round(1.5 * ab)))), np.uint8)
    dick = cv2.morphologyEx(m, cv2.MORPH_OPEN, kern)   # nur dicke, breite Flächen bleiben
    n, lab, stats, _ = cv2.connectedComponentsWithStats(dick, 8)
    kandidaten = []
    for i in range(1, n):
        bx, by, bw, bh, fl = stats[i]
        mitte_y = a + by + bh / 2
        if (bw >= 2.0 * ab and bh <= 1.3 * ab and ys[1] - 0.3 * ab <= mitte_y <= ys[3] + 0.3 * ab
                and fl >= 0.75 * bw * bh):
            kandidaten.append((abs(xa + bx + bw / 2 - x), xa + bx, xa + bx + bw, a + by, a + by + bh))
    if not kandidaten:
        return None
    return min(kandidaten)[1:]


def kirchenpause(seite, x, ys):
    """Alte Schreibweise: gefüllter Block über einen (2 Takte) oder zwei Zwischenräume (4 Takte),
    mehrere Blöcke nebeneinander für größere Zahlen. Rückgabe (x0, x1) aller Blöcke nahe x oder None."""
    ab = seite.ab
    a, b = int(max(0, ys[0] - 0.3 * ab)), int(min(seite.bild.shape[0], ys[4] + 0.3 * ab))
    xa, xb = int(max(0, x - 3 * ab)), int(min(seite.bild.shape[1], x + 3 * ab))
    if b <= a or xb <= xa:
        return None
    m = seite.maske[a:b, xa:xb]
    k = max(2, int(round(0.45 * ab)))
    dick = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(dick, 8)
    bloecke = []
    for i in range(1, n):
        bx, by, bw, bh, fl = stats[i]
        if 0.8 * ab <= bh <= 2.3 * ab and 0.4 * ab <= bw <= 1.6 * ab and fl >= 0.85 * bw * bh:
            bloecke.append((xa + bx, xa + bx + bw, a + by, a + by + bh))
    if not bloecke:
        return None
    naechster = min(bloecke, key=lambda q: abs((q[0] + q[1]) / 2 - x))
    gruppe = [q for q in bloecke if abs((q[0] + q[1]) / 2 - (naechster[0] + naechster[1]) / 2) <= 3 * ab]
    return (min(q[0] for q in gruppe), max(q[1] for q in gruppe),
            min(q[2] for q in gruppe), max(q[3] for q in gruppe))


def mehrtaktpause_bei(seite, x, y):
    """Mehrtaktpause an der Stelle einer gelesenen Pause? Erkennt den modernen Balken (Zahl meist
    darüber, auch „1“) und die alten Blöcke (Zahl meist darunter). Ohne solches Zeichen zählt nur
    eine Zahl ab 2 unter dem System (über dem System könnte es eine Volten-Nummer sein).
    Rückgabe: None = keine Mehrtaktpause; sonst (Anzahl oder None wenn unlesbar, x0, x1)."""
    ab = seite.ab
    ys = seite.linien_bei(x, y)
    balken = mehrtaktbalken(seite, x, ys)
    if balken is not None:
        x0, x1, y0, y1 = balken
        n, gefunden = seite.zahl_ueber(x0, x1, ys, True, y0, y1)
        if n is None and not gefunden:
            n, gefunden = seite.zahl_ueber(x0, x1, ys, False, y0, y1)
        return n, x0, x1
    block = kirchenpause(seite, x, ys)
    if block is not None:
        x0, x1, y0, y1 = block
        n, gefunden = seite.zahl_ueber(x0, x1, ys, False, y0, y1)
        if n is None and not gefunden:
            n, gefunden = seite.zahl_ueber(x0, x1, ys, True, y0, y1)
    else:
        x0, x1 = x - 0.8 * ab, x + 0.8 * ab
        n, gefunden = seite.zahl_ueber(x0, x1, ys, False)
    if n is not None and n >= 2:
        return n, x0, x1
    return None


def faulenzer_suchen(seite):
    """Faulenzer (Taktwiederholungszeichen) auf der ganzen Seite: dicker, nach rechts ansteigender
    Schrägstrich (oder zwei) mit einem Punkt links oben und einem rechts unten, mitten im System.
    Gesucht wird von den Punktpaaren aus – das klappt bei steilen und flachen, dicken und dünnen
    Zeichen (geprüft mit fünf Notenschriften und echten Vereinsnoten). Steht das Zeichen auf einem
    Taktstrich, ist es ein Zweitakt-Faulenzer.
    Rückgabe: Liste von {x, y, system, taktstrich}."""
    if not seite.systeme:
        return []
    import math
    ab = seite.ab
    ohne = Kopfpruefer(seite).ohne
    # lange senkrechte Striche (Taktstriche, Hälse) getrennt halten: Ein Taktstrich durch das Zeichen
    # (Zweitakt-Faulenzer) würde die Schrägstriche sonst zu einem systemhohen Gebilde verbinden
    senkrecht = cv2.morphologyEx(ohne, cv2.MORPH_OPEN, np.ones((max(3, int(round(2.8 * ab))), 1), np.uint8))
    ohne = ohne & (1 - senkrecht)
    n, lab, st, cent = cv2.connectedComponentsWithStats(ohne, 8)
    punkte = []
    for i in range(1, n):
        x, y, w, h, fl = st[i]
        if not (0.05 * ab * ab <= fl <= 0.75 * ab * ab and 0.18 * ab <= w <= 1.0 * ab and 0.18 * ab <= h <= 1.0 * ab):
            continue
        if fl < 0.5 * w * h or not (0.45 <= w / h <= 2.2):
            continue
        px, py = cent[i]
        linien = seite.linien_bei(px, py)
        if not (linien[0] - 0.4 * ab <= py <= linien[4] + 0.4 * ab):
            continue
        punkte.append((float(px), float(py), i, seite.system_bei(px, py)))
    punkte.sort()
    funde = []
    for a, (lx, ly, li, ls) in enumerate(punkte):
        for rx, ry, ri, rs in punkte[a + 1:]:
            if rx - lx > 3.2 * ab:
                break
            if rs != ls or not (0.6 * ab <= rx - lx <= 3.2 * ab and 0.25 * ab <= ry - ly <= 1.9 * ab):
                continue
            treffer = set()                            # Zeichen, die die Verbindung der Punkte kreuzen
            for t in np.linspace(0.15, 0.85, 25):
                qx, qy = int(round(lx + t * (rx - lx))), int(round(ly + t * (ry - ly)))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if 0 <= qy + dy < lab.shape[0] and 0 <= qx + dx < lab.shape[1]:
                            k = lab[qy + dy, qx + dx]
                            if k and k not in (li, ri):
                                treffer.add(int(k))
            teile = list(treffer)
            if not teile:
                continue
            mx_, my_ = int(round((lx + rx) / 2)), int(round((ly + ry) / 2))
            fenster = senkrecht[max(0, my_ - int(ab)):my_ + int(ab), max(0, mx_ - int(0.7 * ab)):mx_ + int(0.7 * ab) + 1]
            taktstrich = bool(fenster.any())             # Taktstrich mitten durch: Zweitakt-Faulenzer
            x0 = min(st[k, 0] for k in teile); x1 = max(st[k, 0] + st[k, 2] for k in teile)
            y0 = min(st[k, 1] for k in teile); y1 = max(st[k, 1] + st[k, 3] for k in teile)
            if (y1 - y0) > 3.5 * ab or (x1 - x0) > 3.5 * ab:
                continue
            # jeder Strich für sich: lang gestreckt, nach rechts ansteigend, gefüllt (kein hohler Notenkopf)
            striche_gut, alle_x, alle_y = True, [], []
            for k in teile:
                ys_, xs_ = np.nonzero(lab[st[k, 1]:st[k, 1] + st[k, 3], st[k, 0]:st[k, 0] + st[k, 2]] == k)
                xs_, ys_ = xs_ + st[k, 0], ys_ + st[k, 1]
                alle_x.append(xs_); alle_y.append(ys_)
                if len(xs_) < 0.05 * ab * ab:
                    continue                           # Krümel (z. B. Rest einer Notenlinie)
                cx_, cy_ = xs_.mean(), ys_.mean()
                ew, ev = np.linalg.eigh(np.cov(np.vstack([xs_ - cx_, ys_ - cy_])))
                streck = math.sqrt(ew[1] / max(ew[0], 1e-6))
                winkel = math.degrees(math.atan2(-ev[1, 1], ev[0, 1])) % 180
                huelle = cv2.convexHull(np.column_stack([xs_, ys_]).astype(np.int32))
                voll = len(xs_) / max(1.0, cv2.contourArea(huelle))
                dicke = len(xs_) / max(1.0, math.hypot(xs_.max() - xs_.min(), ys_.max() - ys_.min())) / ab
                if not (15 <= winkel <= 85 and streck >= 2.2 and voll >= 0.6 and dicke >= 0.12):
                    striche_gut = False
            xs_, ys_ = np.concatenate(alle_x), np.concatenate(alle_y)
            hoehe, breite = (ys_.max() - ys_.min() + 1) / ab, (xs_.max() - xs_.min() + 1) / ab
            cx, cy = xs_.mean(), ys_.mean()
            mx, my = (lx + rx) / 2, (ly + ry) / 2
            linien = seite.linien_bei(mx, my)
            if (striche_gut and 1.35 <= hoehe <= 3.2 and breite <= (2.8 if len(teile) == 1 else 3.4)
                    and abs(cx - mx) <= 0.7 * ab and abs(cy - my) <= 0.8 * ab and abs(my - linien[2]) <= 1.2 * ab):
                funde.append(dict(x=float(mx), y=float(my), system=ls, taktstrich=taktstrich))
    ergebnis = []                                      # mehrere Punktpaare am selben Zeichen zusammenfassen
    for f in sorted(funde, key=lambda f: (f["system"], f["x"])):
        if ergebnis and ergebnis[-1]["system"] == f["system"] and abs(ergebnis[-1]["x"] - f["x"]) < 1.5 * ab:
            ergebnis[-1]["taktstrich"] = ergebnis[-1]["taktstrich"] or f["taktstrich"]
            continue
        ergebnis.append(f)
    for f in ergebnis:                                 # Taktstriche links und rechts (Ausdehnung des Takts)
        f["von"], f["bis"] = _taktgrenzen(seite, senkrecht, f["x"], f["y"], ab)
    return ergebnis


def _taktgrenzen(seite, senkrecht, x, y, ab):
    """x der nächsten Taktstriche links und rechts von x (Striche über das ganze System); ein
    Taktstrich genau unter dem Zeichen (Zweitakt-Faulenzer) zählt nicht. Ohne Fund: ±4 Linienabstände."""
    linien = seite.linien_bei(x, y)
    a, b = int(linien[0] + 0.3 * ab), int(linien[4] - 0.3 * ab)
    xa, xb = int(max(0, x - 16 * ab)), int(min(senkrecht.shape[1], x + 16 * ab))
    if b <= a or xb <= xa:
        return x - 4 * ab, x + 4 * ab
    spalten = np.nonzero(senkrecht[a:b, xa:xb].mean(axis=0) >= 0.9)[0] + xa
    links = [c for c in spalten if c < x - 0.8 * ab]
    rechts = [c for c in spalten if c > x + 0.8 * ab]
    return (float(max(links)) if links else x - 4 * ab), (float(min(rechts)) if rechts else x + 4 * ab)


# ----------------------------------------------------------------------------------------------
# Texte: Titel und Stimme im Kopf, Vortrags- und Tempoangaben, Studierzeichen
# ----------------------------------------------------------------------------------------------
INSTRUMENT_WORTE = ("trompete", "flügelhorn", "fluegelhorn", "flugelhorn", "tenorhorn", "bariton", "posaune", "tuba",
                    "horn", "klarinette", "flöte", "floete", "sax", "piston", "kornett", "cornet", "bass", "oboe",
                    "fagott", "euphonium", "schlagzeug", "trommel", "direktion", "trumpet", "trombone", "clarinet",
                    "flute", "bugel", "stimme")
_ERLAUBT = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ0-9 .,!?:;'’\"()\-–/&+=♭#]+$")


_NICHT_MUSIK = re.compile(r"(©|\bcopyright\b|\brechte\b|\brights\b|verlag|\bedition\b|artikel|\bdruck\b|\bnr\b|www\.|"
                          r"\bmusik\s*:|\bmusic\s*:|\btext\s*:|\bworte\s*:|\bwords\s*:|bearb|\barr\b|\barr\.|arrang|"
                          r"\bkomp|\bcompos|\bby\b|\bmelodie\b)", re.IGNORECASE)
_VON_NAME = re.compile(r"\bvon\s+[A-ZÄÖÜ]")           # „von Willy Trapp“


def texte_lesen(seite):
    """Alle Texte der Seite in einem Durchgang (rapidocr): Liste {x0, y0, x1, y1, text, sicher}.
    Fehlt die Texterkennung, kommt eine leere Liste zurück (die Notenerkennung läuft ohne sie weiter)."""
    global OCR_FEHLER
    if getattr(seite, "_texte", None) is not None:
        return seite._texte
    seite._texte = []
    if OCR_FEHLER:
        return seite._texte
    try:
        erg = _ocr_holen()(cv2.cvtColor(seite.bild, cv2.COLOR_GRAY2BGR))
        boxen, worte, werte = getattr(erg, "boxes", None), getattr(erg, "txts", None), getattr(erg, "scores", None)
        if boxen is None or worte is None:
            return seite._texte
        for box, wort, wert in zip(boxen, worte, werte if werte is not None else [1.0] * len(worte)):
            box = np.asarray(box, dtype=float)
            seite._texte.append(dict(x0=float(box[:, 0].min()), y0=float(box[:, 1].min()), x1=float(box[:, 0].max()),
                                     y1=float(box[:, 1].max()), text=str(wort).strip(), sicher=float(wert)))
    except Exception as fehler:
        OCR_FEHLER = f"{type(fehler).__name__}: {fehler}"
    return seite._texte


def _buchstaben(text):
    return sum(1 for z in text if z.isalpha())


def _oberkante(seite):
    """y der obersten Notenlinie der ersten Zeile (bei Seitenmitte)."""
    mitte = seite.bild.shape[1] / 2
    return min(float(system[0]["verlauf"](mitte)) for system in seite.systeme)


def kopf_lesen(seite, texte=None):
    """Titel und Stimme aus dem Kopf der Seite: der größte Text über den Noten ist der Titel, ein Text
    mit einem Instrumentennamen die Stimme. Rückgabe {titel, stimme, instrument} (leer, wenn unklar);
    instrument ist die Stimmung für das Transponieren (b, es, f, c, c-bass, b-tief …)."""
    texte = texte_lesen(seite) if texte is None else texte
    if not seite.systeme:
        return {}
    ab, oben = seite.ab, _oberkante(seite)
    kopf = [t for t in texte if t["y1"] < oben - 1.0 * ab and _buchstaben(t["text"]) >= 3 and t["sicher"] >= 0.8
            and _ERLAUBT.match(t["text"])]
    if not kopf:
        return {}
    stimme = next((t for t in sorted(kopf, key=lambda t: (t["y0"], t["x0"]))
                   if any(w in t["text"].lower() for w in INSTRUMENT_WORTE) and len(t["text"]) <= 32
                   and not re.search(r"[+=!?]|//", t["text"])), None)
    rest = [t for t in kopf if t is not stimme and ":" not in t["text"]]
    titel = max(rest, key=lambda t: t["y1"] - t["y0"]) if rest else None
    # Der Titel ist deutlich größer als gewöhnlicher Text der Seite
    hoehen = sorted(t["y1"] - t["y0"] for t in texte if _buchstaben(t["text"]) >= 3)
    if titel is not None and hoehen and (titel["y1"] - titel["y0"]) < 1.25 * hoehen[len(hoehen) // 2]:
        titel = None
    aus = {}
    if titel is not None:
        name = re.sub(r"^\d+\.\s*", "", titel["text"]).strip(" .:-–")
        if name.isupper():
            name = " ".join(w.capitalize() if len(w) > 3 or i == 0 else w.lower() for i, w in enumerate(name.split()))
        if _buchstaben(name) >= 3 and not re.search(r"[+=]|//", name):
            aus["titel"] = name
    if stimme is not None:
        aus["stimme"] = stimme["text"].strip(" /,;:-–")
        aus["instrument"] = instrument_aus_text(aus["stimme"])
    return aus


def instrument_aus_text(text):
    """Stimmung eines Instruments aus der Stimmenbezeichnung („1. Trompete in B“, „Altsaxophon Es“)."""
    t = " " + text.lower().replace("♭", "b") + " "
    stimmung = None
    if re.search(r"\b(in\s+)?(bb|b)\b(?!\w)", t.replace("1. b", "")) or " in b" in t or t.rstrip().endswith(" b"):
        stimmung = "b"
    if re.search(r"\b(in\s+)?(es|eb)\b", t):
        stimmung = "es"
    if re.search(r"\bin\s+f\b", t) or t.rstrip().endswith(" f"):
        stimmung = "f"
    if re.search(r"\bin\s+c\b", t) or t.rstrip().endswith(" c"):
        stimmung = "c"
    tief = any(w in t for w in ("tenorhorn", "bariton", "euphonium", "tenorsax"))
    bass = any(w in t for w in ("tuba", "bass"))
    if "baritonsax" in t:
        return "es-tief"
    if "altsax" in t or "es-horn" in t or "althorn" in t:
        return "es"
    if any(w in t for w in ("posaune", "trombone", "fagott")):
        return "b-tief" if stimmung == "b" else "c-bass"
    if bass:
        return "b-bass" if stimmung == "b" else ("es-tief" if stimmung == "es" else "c-bass")
    if tief:
        return "c-bass" if stimmung == "c" else "b-tief"
    if stimmung:
        return stimmung
    if any(w in t for w in ("flöte", "floete", "flute", "oboe")):
        return "c"
    if "horn" in t and "flügel" not in t and "fluegel" not in t and "flugel" not in t:
        return "f"
    return "b"                                           # Trompete, Flügelhorn, Klarinette …


def angaben_lesen(seite, texte=None):
    """Vortrags- und Tempoangaben über und unter den Notenzeilen („Trio“, „dolce“, „D.C. al Fine“):
    Liste {system, x, oben, text, fett, abstand (zur Zeile, in Linienabständen)}. Nicht dabei: der Kopf
    der Seite, die Fußzeile, Zahlen (Taktnummern, Pausenzahlen, Volten) und einzelne Buchstaben (die liest
    die Texterkennung auch aus Notenköpfen heraus)."""
    texte = texte_lesen(seite) if texte is None else texte
    if not seite.systeme:
        return []
    ab = seite.ab
    breite = seite.bild.shape[1]
    folge = sorted(range(len(seite.systeme)), key=lambda k: float(seite.systeme[k][0]["verlauf"](breite / 2)))
    luecken = [float(seite.systeme[b][0]["verlauf"](breite / 2)) - float(seite.systeme[a][4]["verlauf"](breite / 2))
               for a, b in zip(folge, folge[1:])]
    normal = sorted(luecken)[len(luecken) // 2] if luecken else 0.0
    # über diesen Zeilen steht ein Kopf (Titel, Stimme, Komponist): die erste und jede nach einer großen Lücke
    kopfzeilen = {folge[0]} | {b for (a, b), l in zip(zip(folge, folge[1:]), luecken) if l > 1.5 * normal and l > 9 * ab}
    kopf = kopf_lesen(seite, texte)
    aus = []
    for t in texte:
        text = re.sub(r"\s+", " ", t["text"]).strip()
        if _buchstaben(text) < 3 or t["sicher"] < 0.85 or not _ERLAUBT.match(text.replace("≈", "=")):
            continue
        if text in (kopf.get("stimme"), ) or (kopf.get("titel") and kopf["titel"].lower() in text.lower() and t["y1"] < _oberkante(seite)):
            continue
        if _NICHT_MUSIK.search(text) or _VON_NAME.search(text) or len(text) > 60:
            continue                                    # Urheber-, Verlags- und Rechtevermerke
        cx, cy = (t["x0"] + t["x1"]) / 2, (t["y0"] + t["y1"]) / 2
        # nächstes System: Abstand zur obersten bzw. untersten Linie
        beste, drueber = None, None
        for k, system in enumerate(seite.systeme):
            o, u = float(system[0]["verlauf"](cx)), float(system[4]["verlauf"](cx))
            if o - 0.3 * ab <= cy <= u + 0.3 * ab:
                beste = drueber = None                  # mitten im System: kein Text
                break
            abstand, ist_oben = (o - t["y1"], True) if cy < o else (t["y0"] - u, False)
            if beste is None or abstand < beste[0]:
                beste = (abstand, k, ist_oben)
            if ist_oben and (drueber is None or abstand < drueber[0]):
                drueber = (abstand, k, True)
        if beste is None:
            continue
        # Tempo und Charakter („Trio“, „Scherzo“) stehen über ihrer Zeile – auch wenn die Zeile darüber näher ist
        if text[:1].isupper() and drueber is not None and drueber[0] <= 7.5 * ab:
            beste = drueber
        abstand, k, ist_oben = beste
        if abstand > (7.5 if ist_oben else 6.0) * ab:
            continue
        if not ist_oben and k == folge[-1] and any(
                0 <= u["y0"] - t["y1"] < 3.5 * ab and u["x1"] - u["x0"] > 15 * ab and _buchstaben(u["text"]) >= 12 for u in texte):
            continue                                    # unter der letzten Zeile beginnt die Fußzeile: eine
                                                        # Überschrift („Anmerkung“) mit Fließtext direkt darunter
        if ist_oben and k in kopfzeilen:
            # Über einer Kopfzeile steht links das Tempo. Weiter rechts gehört fast alles zum Kopf (Untertitel,
            # Komponist) – außer was dicht über den Linien steht und Notentext-Größe hat („piu mosso“, „Solo“).
            # Ein Name am rechten Rand („Vorname Nachname“) bleibt auch dann draußen.
            dicht = -0.3 * ab <= abstand <= 2.6 * ab and t["y1"] - t["y0"] <= 4.5 * ab
            name = t["x1"] > 0.88 * breite and len(re.findall(r"\b[A-ZÄÖÜ][a-zäöüß]+", text)) >= 2
            if any(w_ in text.lower() for w_ in INSTRUMENT_WORTE) or (t["x0"] > 0.35 * breite and (not dicht or name)):
                continue
        m = re.match(r"^(.*?[A-Za-zäöü.])\s*[≈=~]\s*(\d{2,3})\s*$", text)
        if m:                                           # Tempoangabe: das Notensymbol liest die Texterkennung nicht
            text = f"{m.group(1).strip()} ({m.group(2)})"
        text = text.rstrip(" .") + ("." if text.rstrip().endswith(".") and not text.rstrip().endswith(" .") else "")
        text = re.sub(r"^D\.?\s*([CS])\.?\s*a[l1I]\b\.?", lambda m_: f"D.{m_.group(1)}. al", text)   # „D.S.a1“
        fett = ist_oben and text[:1].isupper()
        aus.append(dict(system=k, x=t["x0"], oben=ist_oben, text=text, fett=fett, abstand=abstand / ab))
    return aus


def studierzeichen_suchen(seite):
    """Eingerahmte Buchstaben oder Zahlen (Kasten oder Kreis) über den Notenzeilen: Liste {system, x, text}.
    Der Rahmen ist eine geschlossene Linie mit einem Zeichen darin; gelesen wird nur das Innere."""
    ab = seite.ab
    h, w = seite.maske.shape
    konturen, baum = cv2.findContours(seite.maske, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if baum is None:
        return []
    aus = []
    for i, kontur in enumerate(konturen):
        if baum[0][i][3] < 0:                           # nur Löcher (Inneres eines Rahmens)
            continue
        x, y, bw, bh = cv2.boundingRect(kontur)
        if not (1.2 * ab <= bw <= 4.2 * ab and 1.2 * ab <= bh <= 4.2 * ab and 0.6 <= bw / bh <= 1.7):
            continue
        if cv2.contourArea(kontur) < 0.6 * bw * bh:
            continue
        cx, cy = x + bw / 2, y + bh / 2
        k = seite.system_bei(cx, cy + 3 * ab)
        oben = float(seite.systeme[k][0]["verlauf"](cx))
        mitte_x = w / 2
        erste = min(range(len(seite.systeme)), key=lambda j: float(seite.systeme[j][0]["verlauf"](mitte_x)))
        if not (0.2 * ab <= oben - (y + bh) <= (4.5 if k == erste else 7.0) * ab):
            continue                                    # (über der ersten Zeile beginnt bald der Kopf der Seite)
        # Der Rahmen steht frei über der Zeile. Zwei Hälse mit Balken und Notenlinie schließen auch ein Feld
        # ein, ein hohler Notenkopf ebenfalls – die hängen aber an Notenlinien oder Hälsen. Berührt ein echter
        # Rahmen den Taktstrich darunter, zählt er nur, wenn er groß ist und nicht an der Zeile darüber hängt.
        _, ey, _, eh = cv2.boundingRect(konturen[baum[0][i][3]])
        frei = eh <= bh + 2.2 * ab and ey + eh <= oben - 0.1 * ab
        if not frei:
            drueber = [float(sy[4]["verlauf"](cx)) for sy in seite.systeme if float(sy[4]["verlauf"](cx)) < y + 0.5 * ab]
            haengt = bool(drueber) and y - max(drueber) < 1.0 * ab
            if haengt or min(bw, bh) < 1.7 * ab or cv2.contourArea(kontur) < 0.7 * bw * bh:
                continue
        innen = seite.maske[y + 2:y + bh - 2, x + 2:x + bw - 2]
        loch = np.zeros((bh, bw), np.uint8)
        cv2.drawContours(loch, [kontur - np.array([[x, y]])], -1, 1, -1)
        tinte = innen & loch[2:bh - 2, 2:bw - 2]
        ys, xs = np.nonzero(tinte)
        if len(ys) < 0.03 * bw * bh or ys.max() - ys.min() < 0.6 * ab:
            continue                                    # leeres Loch (Buchstabe O, Notenkopf)
        ausschnitt = np.where(loch[2:bh - 2, 2:bw - 2] > 0, seite.bild[y + 2:y + bh - 2, x + 2:x + bw - 2], 255).astype(np.uint8)
        text = re.sub(r"[\s.,:;'’\"()\[\]|_-]", "", _ocr_lesen(ausschnitt))
        if re.fullmatch(r"[A-Z]{1,2}|\d{1,3}|[A-Z]\d", text) and (frei or text.isalpha()):
            aus.append(dict(system=k, x=float(cx), text=text))
    return aus


# ----------------------------------------------------------------------------------------------
# Wiederholungszeichen und Volta-Klammern
# ----------------------------------------------------------------------------------------------
def taktstriche(seite, k, mit_anteil=False):
    """Senkrechte Striche über das ganze System k: Liste (x0, x1) (Spaltenbereiche); mit_anteil: dazu
    der Tintenanteil jeder Spalte über die Systemhöhe."""
    ab = seite.ab
    h, w = seite.maske.shape
    system = seite.systeme[k]
    xs = np.arange(w)
    oben = np.asarray(system[0]["verlauf"](xs), dtype=float)
    unten = np.asarray(system[4]["verlauf"](xs), dtype=float)
    spalten = np.cumsum(seite.maske, axis=0, dtype=np.int32)          # Tinte je Spalte bis Zeile y
    a = np.clip(np.round(oben + 0.15 * ab).astype(int), 1, h - 1)
    b = np.clip(np.round(unten - 0.15 * ab).astype(int), 1, h - 1)
    gueltig = b > a
    anteil = np.zeros(w)
    anteil[gueltig] = (spalten[b[gueltig], xs[gueltig]] - spalten[a[gueltig] - 1, xs[gueltig]]) / (b[gueltig] - a[gueltig] + 1)
    voll = anteil >= 0.93
    striche, x = [], 0
    while x < w:
        if voll[x]:
            x1 = x
            while x1 + 1 < w and voll[x1 + 1]:
                x1 += 1
            striche.append((x, x1))
            x = x1 + 1
        else:
            x += 1
    return (striche, anteil) if mit_anteil else striche


def _punkte_neben(seite, punkte, anteil, k, kante, richtung):
    """Wie _punkte_fenster, mit zwei Annahmen über den ausgefransten Rand des Strichs (ein mit dem Strich
    verschmolzener Punkte-Klecks füllt selbst über die Hälfte der Systemhöhe)."""
    return any(_punkte_fenster(seite, punkte, anteil, k, kante, richtung, schwelle) for schwelle in (0.5, 0.75))


def _punkte_fenster(seite, punkte, anteil, k, kante, richtung, schwelle):
    """Stehen direkt neben dem Strich (kante = äußerste Spalte der Strichgruppe, richtung +1 rechts,
    -1 links) die zwei Punkte eines Wiederholungszeichens? Alte Drucke setzen sie auf die Linien, neue
    in die Zwischenräume, manche lassen sie zu einem Klecks verschmelzen – deshalb zählt die Form:
    zwei gleich große Punkte übereinander (oder ein schmaler, in der Mitte symmetrischer Klecks) um
    die Mittellinie, und in ihrer Spalte sonst nichts."""
    ab = seite.ab
    h, w = punkte.shape
    # Rand des Strichs: Spalten, die noch überwiegend Tinte über die Systemhöhe haben, gehören dazu
    x = int(kante)
    while 0 < x + richtung < w - 1 and anteil[x + richtung] >= schwelle and abs(x + richtung - kante) < 0.5 * ab:
        x += richtung
    x0, x1 = (x + 1, x + 1 + int(1.35 * ab)) if richtung > 0 else (x - int(1.35 * ab), x)
    x0, x1 = max(0, x0), min(w, x1)
    if x1 - x0 < 3:
        return False
    m = float(seite.systeme[k][2]["verlauf"]((x0 + x1) / 2))
    a0, a1 = int(max(0, m - 1.9 * ab)), int(min(h, m + 1.9 * ab))
    teil = np.ascontiguousarray(punkte[a0:a1, x0:x1])
    # Spalten direkt am Strich, die auch oben und unten im System Tinte haben, gehören noch zum Strich
    r1, r2 = int(round(m - 1.4 * ab)) - a0, int(round(m + 1.4 * ab)) - a0
    spalten = range(teil.shape[1]) if richtung > 0 else range(teil.shape[1] - 1, -1, -1)
    for c in spalten:
        aussen = np.concatenate([teil[:max(0, r1), c], teil[max(0, r2):, c]])
        if len(aussen) and aussen.mean() >= 0.5:
            teil[:, c] = 0
        else:
            break
    n, lab, st, mitte = cv2.connectedComponentsWithStats(teil, 8)
    mindest = 0.05 * ab * ab

    def spalte_frei(i_liste, flaeche):
        """In den Spalten der Punkte steht sonst (fast) nichts."""
        c0 = min(st[i, 0] for i in i_liste)
        c1 = max(st[i, 0] + st[i, 2] for i in i_liste)
        fremd = (teil[:, max(0, c0 - 1):c1 + 1] > 0) & ~np.isin(lab[:, max(0, c0 - 1):c1 + 1], i_liste)
        return fremd.sum() <= 0.3 * flaeche

    # (ein Punkt im Zwischenraum, der die Linie berührt, behält beim Entfernen der Linie einen Stummel:
    #  deshalb bis einen Linienabstand hoch)
    punktartig = [i for i in range(1, n) if 0.2 * ab <= st[i, 2] <= 0.85 * ab and 0.2 * ab <= st[i, 3] <= 1.0 * ab
                  and st[i, 4] >= mindest and st[i, 4] >= 0.45 * st[i, 2] * st[i, 3]]
    for o in punktartig:
        for u in punktartig:
            dy = mitte[u][1] - mitte[o][1]
            if not (0.7 * ab <= dy <= 1.5 * ab and abs(mitte[u][0] - mitte[o][0]) <= 0.35 * ab):
                continue
            if abs(a0 + (mitte[o][1] + mitte[u][1]) / 2 - m) > 0.75 * ab:
                continue
            if max(st[o, 4], st[u, 4]) > 3.0 * min(st[o, 4], st[u, 4]):
                continue
            if spalte_frei([o, u], min(st[o, 4], st[u, 4])):
                return True
    # zu einem Klecks verschmolzene Punkte
    for i in range(1, n):
        bx, by, bw, bh, fl = (int(v) for v in st[i])
        if not (1.5 * ab <= bh <= 2.5 * ab and 0.25 * ab <= bw <= 0.9 * ab and fl >= 2 * mindest):
            continue
        if abs(a0 + by + bh / 2 - m) > 0.3 * ab or a0 + by < m - 1.35 * ab or a0 + by + bh > m + 1.35 * ab:
            continue
        # dicht am Strich und ganz im Fenster (eine Achtelpause vor dem Strich steht weiter weg)
        nah = bx if richtung > 0 else teil.shape[1] - (bx + bw)
        fern = teil.shape[1] - (bx + bw) if richtung > 0 else bx
        if nah > 0.7 * ab or fern < 1:
            continue
        stueck = lab[by:by + bh, bx:bx + bw] == i
        oben, unten = stueck[:bh // 2], stueck[bh - bh // 2:]
        fo, fu = int(oben.sum()), int(unten.sum())
        if min(fo, fu) == 0 or max(fo, fu) > 1.8 * min(fo, fu):
            continue
        xo = np.nonzero(oben.any(axis=0))[0]; xu = np.nonzero(unten.any(axis=0))[0]
        if abs(xo.mean() - xu.mean()) > 0.25 * ab or max(len(xo), len(xu)) > 1.6 * min(len(xo), len(xu)):
            continue
        if spalte_frei([i], fl):
            return True
    return False


def wiederholungen_suchen(seite, ohne=None):
    """Wiederholungszeichen: dicker Taktstrich (meist mit dünnem daneben) und zwei Punkte übereinander um
    die Mittellinie. Punkte rechts davon = Anfang, links davon = Ende (beides = Ende und Anfang).
    Rückgabe: Liste {system, x, x0, x1, art: "anfang"|"ende"}."""
    ab = seite.ab
    if ohne is None:
        ohne = Kopfpruefer(seite).ohne
    # auch blasse Punkte (alter Druck): graue Pixel zählen mit, außer direkt an einer Notenlinie
    h, w = seite.maske.shape
    zone = np.zeros((h, w), bool)
    xs = np.arange(w)
    rand = max(2, int(round(0.12 * ab)) + 2)
    for system in seite.systeme:
        for linie in system:
            ys = np.floor(np.asarray(linie["verlauf"](xs), dtype=float)).astype(int)
            for d in range(-rand, rand + 1):
                zone[np.clip(ys + d, 0, h - 1), xs] = True
    punkte = ((ohne > 0) | ((seite.bild < 185) & (seite.maske == 0) & ~zone)).astype(np.uint8)
    funde = []
    for k in range(len(seite.systeme)):
        striche, anteil = taktstriche(seite, k, mit_anteil=True)
        gruppen = []
        for a, b in striche:
            if gruppen and a - gruppen[-1][-1][1] <= 1.3 * ab:
                gruppen[-1].append((a, b))
            else:
                gruppen.append([(a, b)])
        for g in gruppen:
            if not any(b - a + 1 >= 0.28 * ab for a, b in g):
                continue                                               # kein dicker Strich dabei
            gx0, gx1 = g[0][0], g[-1][1]
            if gx1 - gx0 > 2.6 * ab:
                continue
            for art, kante, richtung in (("ende", gx0, -1), ("anfang", gx1, 1)):
                if _punkte_neben(seite, punkte, anteil, k, kante, richtung):
                    funde.append(dict(system=k, x=(gx0 + gx1) / 2, x0=float(gx0), x1=float(gx1), art=art))
    return funde


def _haken(m, x, y, ab):
    """Länge (in Linienabständen) eines senkrechten Strichs, der bei (x, y) von einer Klammerlinie nach
    unten geht; die Linie darf dort etwas höher oder tiefer liegen, der Haken leicht abgesetzt sein."""
    h, w = m.shape
    a0, a1 = int(max(0, y - 0.5 * ab)), int(min(h, y + 2.8 * ab))
    b0, b1 = int(max(0, x - 0.6 * ab)), int(min(w, x + 0.6 * ab))
    best = 0
    for c in range(b0, b1):
        sp = m[a0:a1, c]
        lauf = laengster = 0
        for v in sp:
            lauf = lauf + 1 if v else 0
            laengster = max(laengster, lauf)
        best = max(best, laengster)
    return best / ab


def volten_suchen(seite):
    """Volta-Klammern: lange dünne waagrechte Linie über dem System, an einem Ende ein Haken nach unten,
    ein Ende über einem Taktstrich (oder am Zeilenanfang). Rückgabe: Liste {system, x0, x1, y, nummer,
    haken_links, haken_rechts}."""
    ab = seite.ab
    h, w = seite.maske.shape
    m = seite.maske
    lang = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((1, int(2.5 * ab)), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(lang, 8)
    striche_je = {}
    funde = []
    for i in range(1, n):
        x, y, bw, bh, fl = (int(v) for v in st[i])
        if bw < 4.0 * ab or bh > 1.5 * ab:                           # (Höhe: eine lange Linie liegt leicht schräg)
            continue
        cx, cy = x + bw / 2, y + bh / 2
        k = seite.system_bei(cx, cy + 3 * ab)          # das System darunter
        ys0 = float(seite.systeme[k][0]["verlauf"](cx))
        if not (1.4 * ab <= ys0 - cy <= 7.5 * ab):
            continue
        if bw > 70 * ab:
            continue                                                   # Notenlinie, Trennstrich
        spalten = lab[y:y + bh, x:x + bw] == i
        dicke = float(np.median(spalten.sum(axis=0)))                 # echte Strichdicke (Linie darf schräg liegen)
        if dicke > 0.42 * ab:
            continue                                                   # Balken
        hl, hr = _haken(m, x, cy, ab), _haken(m, x + bw - 1, cy, ab)
        if max(hl, hr) < 0.8:
            continue
        # Balken einer Notengruppe: auch zwischen den Enden hängen Hälse an der Linie
        # (die Zahl der Volte am linken Ende darf die Linie berühren – erst dahinter prüfen)
        innen0, innen1 = int(x + min(3.6 * ab, 0.4 * bw)), int(x + bw - 1.0 * ab)
        hals, vorher = False, False
        for c in range(innen0, innen1):
            sp = np.nonzero(spalten[:, c - x])[0]
            a0 = int(y + sp.max() + 1) if len(sp) else int(cy)
            jetzt = bool(m[a0:a0 + int(0.7 * ab), c].all())
            hals = hals or (jetzt and vorher)                          # mindestens zwei Spalten breit
            vorher = jetzt
        if hals:
            continue
        # Rahmen (Tempoangabe, Studierzeichen): unten geschlossen
        bis = int(min(cy + 3.6 * ab, ys0 - 0.5 * ab))               # nicht bis in die Notenlinien hinein
        unten = lang[int(cy + 0.8 * ab):bis, int(x + 0.3 * ab):int(x + bw - 0.3 * ab)]
        if unten.size and (unten.any(axis=0).mean() >= 0.7):
            continue
        if k not in striche_je:
            striche_je[k] = taktstriche(seite, k)
        striche = striche_je[k]
        anfang_system = min((a for a, b in striche), default=x) if striche else x
        nah_l = min((abs((a + b) / 2 - x) for a, b in striche), default=1e9) / ab
        nah_r = min((abs((a + b) / 2 - (x + bw)) for a, b in striche), default=1e9) / ab
        zeilenanfang = x < anfang_system + 1.0 * ab or x < 0.12 * w
        if not (nah_l <= 1.0 or nah_r <= 1.0 or zeilenanfang):
            continue
        funde.append(dict(system=k, x0=float(x), x1=float(x + bw), y=float(cy), haken_links=round(hl, 2),
                          haken_rechts=round(hr, 2), nah_l=round(nah_l, 2), nah_r=round(nah_r, 2), laenge=round(bw / ab, 1)))
    for f in funde:
        f["nummer"] = _volta_nummer(seite, f)
    # ohne lesbare Zahl nur, wenn die Klammer lang ist und einen kräftigen Haken hat
    funde = [f for f in funde if f["nummer"] is not None
             or (f["laenge"] >= 8 and max(f["haken_links"], f["haken_rechts"]) >= 1.5 and min(f["nah_l"], f["nah_r"]) <= 0.6)]
    return sorted(funde, key=lambda f: (f["system"], f["x0"]))


def _volta_nummer(seite, f):
    """Zahl unter dem linken Ende der Klammer („1.“, „2.“) per Texterkennung; None, wenn unlesbar."""
    ab = seite.ab
    h, w = seite.bild.shape
    a0, a1 = int(f["y"] + 0.35 * ab), int(min(h, f["y"] + 2.9 * ab))
    b0, b1 = int(max(0, f["x0"] + 0.3 * ab)), int(min(w, f["x0"] + 3.6 * ab))
    if a1 <= a0 or b1 <= b0:
        return None
    teil = seite.bild[a0:a1, b0:b1]
    text = _ocr_lesen(teil)
    text = text.replace("l", "1").replace("I", "1").replace("|", "1").replace("i", "1").replace("Z", "2").replace("z", "2")
    z = re.findall(r"[1-4]", text)
    return int(z[0]) if z else None


class Zeichenfinder:
    """Kleine Zeichen über und unter Notenköpfen (Artikulationen) finden und nach ihrer Form bestimmen:
    st Punkt (Staccato), sz Keil (Staccatissimo), te Strich (Tenuto), ak Akzent (>), ma Marcato (^/v),
    fe Fermate. Notenlinien und lange senkrechte Striche (Hälse, Taktstriche) sind vorher entfernt.
    Zusammengewachsene Zeichen (Strich + Akzent „≥“, Strich + Punkt) werden zerlegt."""

    KURZ = ("st", "sz", "te")

    def __init__(self, seite, kopf=None):
        """kopf: wahlweise kopf(x, y, suche) – Anteil erkannter Notenkopf-Pixel (Segmentierungsnetz) in
        einem kopfgroßen Fenster; damit werden Notenköpfe nicht mit Vorzeichen verwechselt."""
        self.seite = seite
        self.kopf = kopf
        ab = self.ab = seite.ab
        self.kp = Kopfpruefer(seite)
        ohne = self.kp.ohne
        senkrecht = cv2.morphologyEx(ohne, cv2.MORPH_OPEN, np.ones((max(3, int(round(1.6 * ab))), 1), np.uint8))
        self.maske = ohne & (1 - senkrecht)
        self.senk = senkrecht
        self.alles = ohne                               # zum Prüfen, ob ein Zeichen frei steht
        self.n, self.lab, self.st, self.mitte = cv2.connectedComponentsWithStats(self.maske, 8)
        self._formen = {}
        self._mitte_ohne_rest = {}

    def kopf_x(self, x, y):
        """Mitte des Notenkopfs nahe x – homrs Lage liegt manchmal gut einen Linienabstand daneben:
        das nächstgelegene Maximum im Umkreis von 1,9 Linienabständen, bevorzugt in der Notenkopf-Maske
        (sonst im Füllgrad, ab 0,45)."""
        ab = self.ab
        d = np.arange(-1.9, 1.901, 0.1)
        if self.kopf is not None:
            f, schwelle = np.array([self.kopf(x + t * ab, y, 0.0) for t in d]), 0.3
        else:
            f, schwelle = np.array([self.kp.fuellung(x + t * ab, y, suchbreite=0) for t in d]), 0.45
        g = np.convolve(np.pad(f, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
        spitzen = [i for i in range(len(g)) if g[i] >= schwelle and (i == 0 or g[i] >= g[i - 1])
                   and (i == len(g) - 1 or g[i] >= g[i + 1])]
        if not spitzen:
            return x
        i = min(spitzen, key=lambda j: abs(d[j]))
        j0 = j1 = i                                    # Plateau: dessen Mitte (höchstens ±0,4 ab)
        while j0 > 0 and g[j0 - 1] >= g[i] - 0.02 and i - j0 < 4:
            j0 -= 1
        while j1 < len(g) - 1 and g[j1 + 1] >= g[i] - 0.02 and j1 - i < 4:
            j1 += 1
        return float(x + (d[j0] + d[j1]) / 2 * ab)

    def frei(self, k, ausser=()):
        """Steht die Komponente frei (etwas Weiß ringsum)? Bruchstücke von Köpfen, Balken und Hälsen nicht.
        Komponenten in `ausser` (z. B. das zweite Zeichen von „≥“) stören nicht."""
        ab = self.ab
        x, y, w, h, fl = (int(v) for v in self.st[k])
        r = max(2, int(round(0.2 * ab)))
        a0, a1 = max(0, y - r), min(self.alles.shape[0], y + h + r)
        b0, b1 = max(0, x - r), min(self.alles.shape[1], x + w + r)
        lab = self.lab[a0:a1, b0:b1]
        fenster = self.alles[a0:a1, b0:b1].astype(bool) & (lab != k)
        if ausser:
            fenster &= ~np.isin(lab, list(ausser))
        return fenster.sum() <= max(2, 0.03 * (a1 - a0) * (b1 - b0))

    def _form_teil(self, teil):
        """Form eines Zeichens aus seiner Maske (ohne Fermate): Kürzel oder None."""
        ab = self.ab
        h, w = teil.shape
        fl = int(teil.sum())
        bw, bh = w / ab, h / ab
        if fl < 0.04 * ab * ab or bw > 2.6 or bh > 1.6:
            return None
        fuell = fl / max(1, w * h)
        breiten = teil.sum(axis=1)
        viertel = max(1, h // 4)
        oben_b, unten_b = breiten[:viertel].mean(), breiten[-viertel:].mean()
        spitz = max(oben_b, unten_b) / max(1.0, min(oben_b, unten_b))     # Keil: ein Ende schmal
        if 0.26 <= bw <= 0.7 and 0.24 <= bh <= 0.7 and 0.6 <= bw / max(bh, 1e-6) <= 1.7 and fuell >= 0.5:
            return "st"
        if 0.26 <= bw <= 0.7 and 0.24 <= bh <= 0.95 and bw / max(bh, 1e-6) >= 0.45 and fuell >= 0.55 and spitz < 1.4:
            return "st"                                   # längliches, aber symmetrisches Oval (Foto)
        if 0.45 <= bh <= 1.1 and bw <= 0.5 and bh / max(bw, 1e-6) >= 1.6 and fuell >= 0.4:
            # Keil: verjüngt sich zu einem Ende (ein Halsrest ist überall gleich breit)
            return "sz" if spitz >= 1.6 else None
        if 0.6 <= bw <= 2.1 and 0.14 <= bh <= 0.5 and (bw / max(bh, 1e-6) >= 3.0 and fuell >= 0.55
                                                        or bw / max(bh, 1e-6) >= 2.3 and fuell >= 0.65
                                                        or bw / max(bh, 1e-6) >= 2.0 and fuell >= 0.72):
            return "te"
        if 0.6 <= bw <= 1.9 and 0.3 <= bh <= 1.3 and fuell <= 0.65 and self._spitze(teil):
            return "ak"
        if 0.45 <= bw <= 1.3 and 0.45 <= bh <= 1.4 and fuell <= 0.65 and 0.6 <= bw / bh <= 1.6:
            if self._spitze(teil.T) or self._spitze(teil.T[::-1]):
                return "ma"
        return None

    @staticmethod
    def _spitze(teil):
        """Winkel ">": zwei gerade Arme von links oben und links unten treffen sich rechts in halber Höhe;
        links in der Mitte offen. (Bogenstücke haben die Spitze oben oder unten, nicht in der Mitte.)
        Für Marcato "^"/"v" wird die gedrehte Maske geprüft."""
        h, w = teil.shape
        if h < 4 or w < 4:
            return False
        belegt = teil.any(axis=1)
        rechts = np.where(belegt, w - 1 - np.argmax(teil[:, ::-1], axis=1), -1)
        links = np.where(belegt, np.argmax(teil, axis=1), w)
        spitze = np.nonzero(rechts >= 0.85 * (w - 1))[0]
        if not len(spitze) or not 0.3 <= spitze.mean() / (h - 1) <= 0.7:
            return False
        q = max(1, h // 4)
        oben, unten = [r for r in range(q) if belegt[r]], [r for r in range(h - q, h) if belegt[r]]
        if not oben or not unten:
            return False
        if min(links[oben]) > 0.35 * w or min(links[unten]) > 0.45 * w:
            return False
        if max(rechts[oben]) > 0.75 * w or max(rechts[unten]) > 0.75 * w:
            return False                                 # die Arme enden links deutlich vor der Spitze
        mitte = [r for r in range(int(0.4 * h), int(0.6 * h) + 1) if belegt[r]]
        return bool(mitte) and min(links[mitte]) >= 0.3 * w

    def form(self, k):
        """Form einer Komponente: Kürzel oder None."""
        ab = self.ab
        x, y, w, h, fl = (int(v) for v in self.st[k])
        art = self._form_teil(self.lab[y:y + h, x:x + w] == k)
        if art is None and 1.0 * ab <= w <= 2.6 * ab and 0.4 * ab <= h <= 1.4 * ab and fl / max(1, w * h) <= 0.6:
            # Fermate: Bogen, darunter (oder darüber) ein Punkt in der Mitte
            cx = x + w / 2
            for j in range(1, self.n):
                if j == k:
                    continue
                px, py = self.mitte[j]
                if (abs(px - cx) <= 0.35 * ab and y - 0.2 * ab <= py <= y + h + 0.2 * ab
                        and self.st[j, 4] <= 0.6 * ab * ab):
                    return "fe"
        return art

    def formen(self, k):
        """Kürzel einer Komponente als Liste von oben nach unten. Ist sie keine einzelne Form, wird ein
        Strich am oberen oder unteren Rand abgetrennt und der Rest bestimmt („≥“ = Akzent über Strich)."""
        if k in self._formen:
            return self._formen[k]
        art = self.form(k)
        ergebnis = [art] if art else []
        ab = self.ab
        x, y, w, h, fl = (int(v) for v in self.st[k])
        if not art and 1.0 * ab <= w <= 2.2 * ab and 0.5 * ab <= h <= 1.8 * ab:
            teil = self.lab[y:y + h, x:x + w] == k
            breite = teil.sum(axis=1)
            for oben in (True, False):
                reihen = list(range(h) if oben else range(h - 1, -1, -1))
                vorlauf = 0                                   # Randpixel und schräge Kante überspringen
                while vorlauf < 3 and breite[reihen[vorlauf]] < 0.75 * w:
                    vorlauf += 1
                n = 0
                for r in reihen[vorlauf:]:
                    if breite[r] < 0.75 * w:
                        break
                    n += 1
                if not (max(2, 0.1 * ab) <= n <= 0.4 * ab):
                    continue
                schnitt = vorlauf + n + 1
                rest = teil[schnitt:] if oben else teil[:h - schnitt]
                ys, xs = np.nonzero(rest)
                if len(ys) == 0:
                    continue
                if xs.max() - xs.min() + 1 <= 0.35 * w and len(ys) <= 0.3 * fl and 0.14 * ab <= n <= 0.5 * ab:
                    ergebnis = ["te"]                         # Strich mit kleinem Anhängsel (Foto)
                    break
                if ys.max() - ys.min() + 1 < 0.25 * ab:
                    continue
                art2 = self._form_teil(rest[ys.min():ys.max() + 1, xs.min():xs.max() + 1])
                if art2 in ("ak", "ma", "st"):
                    ergebnis = ["te", art2] if oben else [art2, "te"]
                    break
        if not ergebnis or ergebnis in (["st"], ["sz"]):
            # Punkt an einer Notenlinie: Linienrest abschneiden – für die Form und für die Lage
            # (mit dem Stummel sieht ein runder Punkt wie ein Keil aus)
            rest = self._linienrest_weg(k)
            if rest is not None and self._form_teil(rest[0]) == "st":   # nur Punkte (Bogenenden würden zu Strichen)
                ergebnis = ["st"]
                self._mitte_ohne_rest[k] = rest[1]
        self._formen[k] = ergebnis
        return ergebnis

    def mitte_von(self, k):
        """Mitte einer Komponente (ohne angehängten Linienrest, falls abgeschnitten)."""
        return self._mitte_ohne_rest.get(k, tuple(self.mitte[k]))

    def _linienrest_weg(self, k):
        """Berührt ein Zeichen eine Notenlinie, bleibt beim Entfernen der Linie ein Stummel daran hängen
        (die Linie wird nur dort entfernt, wo nichts anderes sie kreuzt) – ein runder Punkt sieht dann wie
        ein Keil aus. Zeilen in der Linienzone an den Enden abschneiden: (Maske, Mitte) des Rests."""
        ab = self.ab
        x, y, w, h, fl = (int(v) for v in self.st[k])
        if h > 1.25 * ab or w > 0.8 * ab:                     # Punkt mit Stummel (oben und unten), nichts Größeres
            return None
        cx, cy = self.mitte[k]
        linien = self.seite.linien_bei(cx, cy)
        dicke = max(2, int(round(ab * 0.12)) + 1)            # wie die Linienzone im Kopfprüfer
        in_zone = [any(-dicke <= y + r - int(np.floor(lin)) <= dicke + 1 for lin in linien) for r in range(h)]
        oben, unten = 0, h
        while oben < h and in_zone[oben]:
            oben += 1
        while unten > oben and in_zone[unten - 1]:
            unten -= 1
        if (oben == 0 and unten == h) or unten - oben < 3 or (h - (unten - oben)) > 0.55 * h:
            return None
        rest = self.lab[y + oben:y + unten, x:x + w] == k
        ys, xs = np.nonzero(rest)
        if not len(ys):
            return None
        return (rest[ys.min():ys.max() + 1, xs.min():xs.max() + 1],
                (float(x + xs.mean()), float(y + oben + ys.mean())))

    def hals(self, x, y_oben, y_unten):
        """Halsrichtung am Notenkopf: "hoch" (Hals rechts nach oben), "runter" (links nach unten) oder None."""
        ab = self.ab
        h, w = self.senk.shape

        def menge(x0, x1, y0, y1):
            x0, x1, y0, y1 = int(max(0, x0)), int(min(w, x1)), int(max(0, y0)), int(min(h, y1))
            return int(self.senk[y0:y1, x0:x1].sum()) if x1 > x0 and y1 > y0 else 0
        hoch = menge(x - 0.2 * ab, x + 1.2 * ab, y_oben - 3.2 * ab, y_oben - 0.3 * ab)
        runter = menge(x - 1.2 * ab, x + 0.2 * ab, y_unten + 0.3 * ab, y_unten + 3.2 * ab)
        if hoch >= ab and hoch > 1.5 * runter:
            return "hoch"
        if runter >= ab and runter > 1.5 * hoch:
            return "runter"
        return None

    def _seitlich_frei(self, k, ausser=()):
        """Links und rechts (je 0,5 Linienabstände) auf gleicher Höhe nichts, was mindestens halb so groß
        ist: kein Nachbar in einer Punktreihe, kein Text („rit.“). Kleine Druckflecken stören nicht, der
        eigene Notenkopf (ausser) auch nicht."""
        ab = self.ab
        x, y, w, h, fl = (int(v) for v in self.st[k])
        h_, w_ = self.lab.shape
        a0, a1 = int(max(0, y - 0.3 * ab)), int(min(h_, y + h + 0.3 * ab))
        b0, b1 = int(max(0, x - 0.5 * ab)), int(min(w_, x + w + 0.5 * ab))
        andere = set(np.unique(self.lab[a0:a1, b0:b1])) - {0, k} - set(ausser)
        return not any(self.st[j, 4] >= 0.5 * fl for j in andere)

    def _fermatenpunkt(self, k, richtung):
        """Liegt direkt jenseits des Punkts (vom Kopf weg) ein kurzer Bogen, mittig darüber? Dann ist es
        der Punkt einer Fermate."""
        ab = self.ab
        x, y, w, h, fl = (int(v) for v in self.st[k])
        cx = self.mitte[k][0]
        h_, w_ = self.lab.shape
        a0, a1 = (y - 0.7 * ab, y) if richtung < 0 else (y + h, y + h + 0.7 * ab)
        a0, a1 = int(max(0, a0)), int(min(h_, a1))
        b0, b1 = int(max(0, cx - 0.4 * ab)), int(min(w_, cx + 0.4 * ab))
        if a1 <= a0 or b1 <= b0:
            return False
        for j in set(np.unique(self.lab[a0:a1, b0:b1])) - {0, k}:
            jx, jy, jw, jh, jf = (int(v) for v in self.st[j])
            if 1.0 * ab <= jw <= 2.8 * ab and jh <= 1.6 * ab and abs(self.mitte[j][0] - cx) <= 0.4 * ab:
                return True
        return False

    def _in_klammer(self, k):
        """Steht die Komponente zwischen zwei langen waagrechten Teilen auf gleicher Höhe – wie die Zahl
        in einer Triolenklammer?"""
        ab = self.ab
        x, y, w, h, fl = (int(v) for v in self.st[k])
        cy = self.mitte[k][1]
        h_, w_ = self.lab.shape
        a0, a1 = int(max(0, cy - 0.6 * ab)), int(min(h_, cy + 0.6 * ab))
        seiten = []
        for b0, b1 in ((x - 1.2 * ab, x), (x + w, x + w + 1.2 * ab)):
            b0, b1 = int(max(0, b0)), int(min(w_, b1))
            ks = set(np.unique(self.lab[a0:a1, b0:b1])) - {0, k} if b1 > b0 else set()
            seiten.append(any(self.st[j, 2] >= 1.5 * ab and self.st[j, 3] <= 1.0 * ab
                              and abs(self.mitte[j][1] - cy) <= 0.6 * ab for j in ks))
        return all(seiten)

    def _spalte_frei(self, xs, y0, y1, ausser):
        """Zwischen Kopf und Zeichen nichts außer Notenlinien und Hälsen (ausser: Zeichen-Komponenten)."""
        ab = self.ab
        h, w = self.lab.shape
        a0, a1 = int(max(0, min(y0, y1))), int(min(h, max(y0, y1)))
        b0, b1 = int(max(0, xs - 0.35 * ab)), int(min(w, xs + 0.35 * ab))
        if a1 <= a0 or b1 <= b0:
            return True
        lab = self.lab[a0:a1, b0:b1]
        tinte = (lab > 0) & ~np.isin(lab, list(ausser))
        return tinte.sum() <= max(3, 0.02 * (a1 - a0) * (b1 - b0))

    def zeichen_bei(self, x, y_oben, y_unten, kopf_suchen=True):
        """Artikulationszeichen am Notenkopf (bzw. Akkord von y_oben bis y_unten) bei x: Liste von
        (Kürzel, Abstand, Komponente). Punkt, Keil und Strich stehen auf der Kopfseite gegenüber dem
        Hals, genau über bzw. unter dem Kopf, im System in einem Zwischenraum (nicht auf einer Linie);
        außerhalb des Systems auch weiter weg, wenn dazwischen nichts steht. Akzent, Marcato und Fermate
        auch auf der Halsseite und weiter weg. Nicht gewertet: Bruchstücke des Kopfes (zu nah) und
        Hilfslinien zwischen einem Kopf außerhalb des Systems und dem System."""
        ab = self.ab
        h, w = self.lab.shape
        xo = self.kopf_x(x, y_oben) if kopf_suchen else x
        xu = (self.kopf_x(x, y_unten) if y_unten != y_oben else xo) if kopf_suchen else x
        hals = self.hals((xo + xu) / 2, y_oben, y_unten)
        linien = self.seite.linien_bei(x, (y_oben + y_unten) / 2)
        funde = []
        for seite_ in ("oben", "unten"):
            xs, yk, r = (xo, y_oben, -1) if seite_ == "oben" else (xu, y_unten, 1)
            a, b = sorted((yk + r * 0.55 * ab, yk + r * 4.4 * ab))
            a, b = int(max(0, a)), int(min(h, b))
            x0, x1 = int(max(0, xs - 1.0 * ab)), int(min(w, xs + 1.0 * ab))
            if b <= a or x1 <= x0:
                continue
            kopfseite = hals is None or (hals == "hoch" and seite_ == "unten") or (hals == "runter" and seite_ == "oben")
            kandidaten = []
            for k in set(np.unique(self.lab[a:b, x0:x1])) - {0}:
                k = int(k)
                cx, cy = self.mitte[k]
                if not (a - 0.5 * ab <= cy <= b + 0.5 * ab and abs(cx - xs) <= 1.0 * ab):
                    continue
                arten = self.formen(k)
                cx, cy = self.mitte_von(k)
                if arten and a <= cy <= b and abs(cx - xs) <= 0.75 * ab:
                    kandidaten.append((abs(cy - yk) / ab, k, arten))
            zeichen = {k for _, k, _ in kandidaten}
            kopf_ks = {int(self.lab[int(min(h - 1, max(0, yk + t * ab))), int(min(w - 1, max(0, xs)))])
                       for t in (-0.3, 0.0, 0.3)} - {0}          # der Kopf selbst stört nicht
            gefunden = set()
            for abstand, k, arten in sorted(kandidaten):
                sx, sy, sw, sh, fl = (int(v) for v in self.st[k])
                cx, cy = self.mitte_von(k)
                nah = (yk - (sy + sh)) / ab if seite_ == "oben" else (sy - yk) / ab
                if nah < 0.6:                                        # Teil des Kopfes
                    continue
                if (seite_ == "unten" and yk < linien[0] - 0.75 * ab and cy < linien[0]) or \
                        (seite_ == "oben" and yk > linien[4] + 0.75 * ab and cy > linien[4]):
                    continue                                         # Hilfslinien
                # außerhalb des Systems (bis 1,6 ab jenseits der Randlinie) bei einem Kopf im System
                aussen = (linien[0] - 1.6 * ab <= cy < linien[0] - 0.3 * ab or linien[4] + 0.3 * ab < cy <= linien[4] + 1.6 * ab)
                im_system = linien[0] - 0.6 * ab <= yk <= linien[4] + 0.6 * ab
                kante = sy + sh if seite_ == "oben" else sy
                frei_weit = aussen and im_system and self._spalte_frei(xs, yk + r * 0.75 * ab, kante, zeichen | kopf_ks)
                for art in arten:
                    if art in self.KURZ:
                        if not kopfseite or not self.frei(k, (zeichen - {k}) | kopf_ks):
                            continue                                 # (der eigene Kopf darf direkt daneben liegen)
                        if not (abstand <= 2.6 or (frei_weit and abstand <= 4.2 and self._seitlich_frei(k, kopf_ks))):
                            continue
                        if len(arten) == 1:
                            if min(abs(cy - l) for l in linien) < 0.25 * ab:
                                continue                             # auf einer Linie: Rest von Linie/Bogen
                            if art == "st" and (not self._seitlich_frei(k, kopf_ks) or self._fermatenpunkt(k, r)):
                                continue                             # Punktreihe, Text, Fermatenpunkt
                        if art == "st" and abs(cx - xs) > 0.5 * ab:
                            continue
                    elif not (abstand <= 3.6 or (frei_weit and abstand <= 4.4)) or self._in_klammer(k):
                        continue
                    gruppe = "kurz" if art in self.KURZ else "lang"
                    if art != "fe" and gruppe in gefunden:
                        continue                                 # je Seite ein Punkt/Strich, ein Akzent
                    if art != "fe":
                        gefunden.add(gruppe)
                    funde.append((art, abstand, k))
        return funde


def pausen_form(seite, x, y):
    """Höhe und Oberkante (in Linienabständen; Oberkante gemessen ab der obersten Notenlinie) des
    Zeichens an der Stelle (x, y), ohne Notenlinien. Viertelpausen sind etwa 3 Linienabstände hoch,
    Achtelpausen etwa 2."""
    ab = seite.ab
    ys = seite.linien_bei(x, y)
    a, e = int(max(0, ys[0] - 2 * ab)), int(min(seite.bild.shape[0], ys[4] + 2 * ab))
    xa, xb = int(max(0, x - 1.5 * ab)), int(min(seite.bild.shape[1], x + 1.5 * ab))
    if e <= a or xb <= xa:
        return None
    m = seite.ohne_linien(a, e, xa, xb, ys)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    beste = None
    for i in range(1, n):
        if st[i, 4] < 0.2 * ab * ab:
            continue
        cx, cy = xa + st[i, 0] + st[i, 2] / 2, a + st[i, 1] + st[i, 3] / 2
        d = abs(cx - x) + 0.3 * abs(cy - y)
        if beste is None or d < beste[0]:
            beste = (d, st[i, 3] / ab, (a + st[i, 1] - ys[0]) / ab)
    return None if beste is None else beste[1:]


def _imgpos(note):
    for kind in note:
        if kind.tag is ET.Comment and "imgpos" in (kind.text or ""):
            return tuple(int(v) for v in re.findall(r"\d+", kind.text))
    return None


def mehrtaktpausen(wurzel, seite, protokoll=print):
    """Für fertige MusicXML-Dateien (Aufruf von Hand): Takt mit nur einer Pause, an deren Stelle
    ein Mehrtaktbalken mit Zahl steht -> Mehrtaktpause mit dieser Zahl. Im Notenleser selbst
    geschieht das schon beim Lesen (notenleser.mehrtaktpausen_bild)."""
    for teil in wurzel.iter("part"):
        for takt in list(teil.findall("measure")):
            noten = takt.findall("note")
            if len(noten) != 1 or noten[0].find("rest") is None:
                continue
            pos = _imgpos(noten[0])
            if pos is None:
                continue
            erg = mehrtaktpause_bei(seite, *pos)
            if getattr(seite, "debug", False):
                protokoll(f"  [debug] Takt {takt.get('number')}: Pause bei {pos}, Mehrtaktpause {erg}")
            if not erg or not erg[0] or erg[0] < 2:
                continue
            n = erg[0]
            mr = takt.find("attributes/measure-style/multiple-rest")
            if mr is not None:
                if mr.text != str(n):
                    protokoll(f"Mehrtaktpause: {n} statt {mr.text} Takte (Takt {takt.get('number')})")
                    mr.text = str(n)
                continue
            attr = takt.find("attributes")
            if attr is None:
                attr = ET.Element("attributes")
                takt.insert(0, attr)
            stil = ET.SubElement(attr, "measure-style")
            ET.SubElement(stil, "multiple-rest").text = str(n)
            noten[0].find("rest").set("measure", "yes")
            protokoll(f"Mehrtaktpause erkannt: {n} Takte (Takt {takt.get('number')})")
    return wurzel


if __name__ == "__main__":
    bild = cv2.imread(sys.argv[1], 0)
    seite = Seite(bild)
    seite.debug = True
    baum = ET.parse(sys.argv[2], parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    mehrtaktpausen(baum.getroot(), seite)
    if len(sys.argv) > 3:
        baum.write(sys.argv[3], encoding="utf-8", xml_declaration=True)


# ----------------------------------------------------------------------------------------------
# Gegenprobe: Gibt es an der Stelle einer gelesenen Note wirklich einen Notenkopf?
# ----------------------------------------------------------------------------------------------
STUFEN = "CDEFGAB"
UNTERSTE_LINIE = {"clef_G2": ("E", 4), "clef_F4": ("G", 2), "clef_C3": ("F", 3), "clef_C4": ("D", 3),
                  "clef_G1": ("G", 4), "clef_F3": ("B", 2), "clef_C1": ("C", 4), "clef_C2": ("A", 3),
                  "clef_C5": ("B", 2), "clef_F5": ("D", 2)}


def _stufe(step, octave):
    return int(octave) * 7 + STUFEN.index(step)


class Kopfpruefer:
    """Füllgrad (Anteil schwarzer Pixel ohne Notenlinien) in einem kopfgroßen Fenster."""

    def __init__(self, seite):
        self.seite = seite
        m = seite.maske
        h, w = m.shape
        ab = seite.ab
        dicke = max(2, int(round(ab * 0.12)) + 1)
        # Pixel, die zu einem senkrechten Lauf länger als eine Notenlinie gehören
        lang = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((dicke + 2, 1), np.uint8))
        zone = np.zeros_like(m)
        xs = np.arange(w)
        for system in seite.systeme:
            for linie in system:
                ys = np.floor(np.asarray(linie["verlauf"](xs), dtype=float)).astype(int)
                for d in range(-dicke, dicke + 2):         # lückenlos (Rundung auf .5 ließ sonst Zeilen aus)
                    yy = np.clip(ys + d, 0, h - 1)
                    zone[yy, xs] = 1
        self.ohne = np.where(zone > 0, m & lang, m).astype(np.uint8)
        self.integral = cv2.integral(self.ohne)

    def fuellung(self, x, y, suchbreite=1.2):
        ab = self.seite.ab
        hw, hh = 0.6 * ab, 0.38 * ab
        h, w = self.ohne.shape
        y0, y1 = int(max(0, y - hh)), int(min(h, y + hh))
        if y1 <= y0:
            return 0.0
        beste = 0.0
        for xm in np.arange(x - suchbreite * ab, x + suchbreite * ab + 1, max(1.0, ab / 8)):
            x0, x1 = int(max(0, xm - hw)), int(min(w, xm + hw))
            if x1 <= x0:
                continue
            I = self.integral
            summe = I[y1, x1] - I[y0, x1] - I[y1, x0] + I[y0, x0]
            beste = max(beste, summe / ((x1 - x0) * (y1 - y0)))
        return float(beste)

    def soll_y(self, x, y_hinweis, pitch, schluessel):
        """Erwartete y-Lage einer Tonhöhe im System, das y_hinweis am nächsten liegt."""
        ys = self.seite.linien_bei(x, y_hinweis)
        ab = (ys[4] - ys[0]) / 4
        unten = UNTERSTE_LINIE.get(schluessel, ("E", 4))
        k = _stufe(pitch[0], pitch[1:]) - _stufe(*unten)
        return ys[4] - k * ab / 2
