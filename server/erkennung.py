"""Notenerkennung für den Server: Seiten lesen, Seiten zusammenfügen, Takte bewerten.

Die eigentliche Erkennung macht der Notenleser (homr + eigene Prüfungen, Ordner ../notenleser).
Jede Note trägt Kommentare mit ihrer Lage im Bild (imgpos) und der Sicherheit des Decoders.
Daraus wird pro Takt bestimmt: Seite, Bildausschnitt, Status (ok / korrigiert / unsicher) und Gründe.
"""
from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction

HIER = os.path.dirname(os.path.abspath(__file__))
for kandidat in (os.path.join(HIER, "..", "notenleser"), os.path.join(HIER, "notenleser")):
    if os.path.isdir(kandidat) and kandidat not in sys.path:
        sys.path.insert(0, kandidat)

# Schwellen für "unsicher" (geeicht an den 5 Testseiten, siehe pruefstand)
SCHWELLE_RHYTHMUS = 0.4
SCHWELLE_TONHOEHE = 0.7

NOTENWERTE = {"whole": Fraction(4), "half": Fraction(2), "quarter": Fraction(1), "eighth": Fraction(1, 2),
              "16th": Fraction(1, 4), "32nd": Fraction(1, 8), "64th": Fraction(1, 16)}
WERT_NAMEN = {Fraction(4): "eine Ganze", Fraction(3): "eine punktierte Halbe", Fraction(2): "eine Halbe",
              Fraction(3, 2): "eine punktierte Viertel", Fraction(1): "eine Viertel",
              Fraction(3, 4): "eine punktierte Achtel", Fraction(1, 2): "eine Achtel",
              Fraction(1, 4): "eine Sechzehntel", Fraction(1, 8): "eine Zweiunddreißigstel"}


def _parser():
    return ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))


def seite_lesen(pfad: str) -> tuple[str, list[str]]:
    """Liest eine bereinigte Notenseite. Rückgabe: MusicXML (Text) und Protokoll der Eingriffe."""
    import notenleser as nl
    stimmen = nl.erkennen(pfad)
    xml = nl.als_musicxml(stimmen)
    return xml, list(nl.ZUSTAND.get("protokoll", []))


def zusammenfuegen(xml_texte: list[str], titel: str = "", stimme: str = "") -> ET.Element:
    """Mehrere Seiten zu einem Stück: Takte der Folgeseiten werden an die erste Seite angehängt.
    Jeder Takt bekommt ein Hilfsattribut data-seite (0-basiert), das später wieder entfernt wird."""
    wurzel = None
    teil = None
    for k, text in enumerate(xml_texte):
        doc = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text, parser=_parser())
        teile = doc.findall("part")
        if not teile:
            continue
        if wurzel is None:
            wurzel, teil = doc, teile[0]
            for takt in teil.findall("measure"):
                takt.set("data-seite", str(k))
            continue
        for i, takt in enumerate(teile[0].findall("measure")):
            takt.set("data-seite", str(k))
            if i == 0:
                for alt in takt.findall("print"):
                    takt.remove(alt)
                takt.insert(0, ET.Element("print", {"new-page": "yes"}))
            teil.append(takt)
    if wurzel is None:
        raise ValueError("Auf keiner Seite wurden Noten gefunden")
    takte = teil.findall("measure")
    start = 0 if takte and takte[0].get("implicit") == "yes" else 1   # Auftakt = Takt 0
    for nr, takt in enumerate(takte, start):
        takt.set("number", str(nr))
        if nr > 0:
            takt.attrib.pop("implicit", None)
    # Titel und Stimme eintragen
    if titel:
        work = wurzel.find("work")
        if work is None:
            work = ET.Element("work")
            wurzel.insert(0, work)
        wt = work.find("work-title")
        if wt is None:
            wt = ET.SubElement(work, "work-title")
        wt.text = titel
    if stimme:
        for pn in wurzel.iter("part-name"):
            pn.text = stimme
        for name in wurzel.iter("instrument-name"):
            name.text = stimme
    return wurzel


def _kommentare(note):
    pos, sicher = None, {}
    for kind in note:
        if kind.tag is not ET.Comment:
            continue
        text = kind.text or ""
        if "imgpos" in text:
            zahlen = re.findall(r"-?\d+", text)
            if len(zahlen) >= 2:
                pos = (int(zahlen[0]), int(zahlen[1]))
        elif "sicher:" in text:
            for teil in text.split("sicher:", 1)[1].split(";"):
                if "=" in teil:
                    k, v = teil.split("=", 1)
                    sicher[k.strip()] = v.strip()
    return pos, sicher


def takte_bewerten(wurzel: ET.Element, seiten_masse: list[dict]) -> list[dict]:
    """Bewertung je Takt. seiten_masse: je Seite {"breite", "hoehe", "ab", "systeme": [[y0..y4 bei Mitte], ...]}.
    Rückgabe: Liste {nr, seite, box:[x0,y0,x1,y1] | None, status, gruende:[...]}."""
    ergebnis = []
    teil = wurzel.find("part")
    if teil is None:
        return ergebnis
    divisions, beats, beat_type = 1, 4, 4
    takte = teil.findall("measure")
    zeile = 0
    for idx, takt in enumerate(takte):
        if any(p.get("new-system") == "yes" or p.get("new-page") == "yes" for p in takt.findall("print")):
            zeile += 1
        for attr in takt.findall("attributes"):
            if attr.findtext("divisions"):
                divisions = int(attr.findtext("divisions"))
            t = attr.find("time")
            if t is not None:
                beats, beat_type = int(t.findtext("beats")), int(t.findtext("beat-type"))
        seite = int(takt.get("data-seite", "0"))
        mehrtakt = takt.find("attributes/measure-style/multiple-rest") is not None
        soll = Fraction(beats * 4, beat_type)                     # in Vierteln
        gruende, status = [], "ok"
        positionen = []
        stimmen: dict[str, Fraction] = {}
        nur_ganzpause = True
        stil = takt.find("attributes/measure-style")
        for note in ([stil] if stil is not None else []) + takt.findall("note"):
            pos, sicher = _kommentare(note)
            if note is stil:                           # Mehrtaktpause: nur Lage und Korrektur
                if pos:
                    positionen.append(pos)
                if "korrektur" in sicher:
                    gruende.append("Automatisch korrigiert: " + sicher["korrektur"])
                    status = "korrigiert"
                if "unsicher" in sicher:
                    gruende.append(sicher["unsicher"])
                    status = "unsicher"
                continue
            if pos:
                positionen.append(pos)
            if note.find("rest") is None or note.find("rest").get("measure") != "yes":
                nur_ganzpause = False
            if note.find("grace") is None and note.find("chord") is None:
                d = note.findtext("duration")
                if d:
                    v = note.findtext("voice") or "1"
                    stimmen[v] = stimmen.get(v, Fraction(0)) + Fraction(int(d), divisions)
            r = float(sicher.get("r", "1"))
            p = float(sicher.get("p", "1"))
            if "unsicher" in sicher:
                gruende.append(sicher["unsicher"])
                status = "unsicher"
            elif r < SCHWELLE_RHYTHMUS:
                gruende.append("Notenwert unsicher gelesen")
                status = "unsicher"
            elif p < SCHWELLE_TONHOEHE and note.find("rest") is None:
                gruende.append("Tonhöhe unsicher gelesen")
                status = "unsicher"
            if "korrektur" in sicher:
                gruende.append("Automatisch korrigiert: " + sicher["korrektur"])
                if status == "ok":
                    status = "korrigiert"
        # Taktlänge (Auftakt am Anfang und an Wiederholungen/Doppelstrichen ist erlaubt)
        if stimmen and not mehrtakt and not nur_ganzpause:
            neue_taktart = takt.find("attributes/time") is not None
            grenze = idx == 0 or idx == len(takte) - 1 or neue_taktart or any(
                b.find("repeat") is not None or (b.findtext("bar-style") or "") in ("light-light", "light-heavy", "heavy-light")
                for t in (takt, takte[idx - 1] if idx else takt) for b in t.findall("barline"))
            for v, ist in sorted(stimmen.items()):
                if ist > soll or (ist < soll and not grenze):
                    diff = abs(ist - soll)
                    was = WERT_NAMEN.get(diff, f"{diff} Viertel")
                    wer = "" if len(stimmen) == 1 else (" (obere Stimme)" if v == min(stimmen) else " (untere Stimme)")
                    gruende.append(f"Takt ist um {was} zu {'lang' if ist > soll else 'kurz'}{wer}")
                    status = "unsicher"
        lage = None
        if positionen and seite < len(seiten_masse):
            m = seiten_masse[seite]
            ab = m.get("ab", 20.0)
            xs = [x for x, y in positionen]
            ys = sorted(y for x, y in positionen)
            y_mitte = ys[len(ys) // 2]
            system = min(m.get("systeme") or [[y_mitte - 2 * ab, y_mitte - ab, y_mitte, y_mitte + ab, y_mitte + 2 * ab]],
                         key=lambda s: abs(s[2] - y_mitte))
            lage = {"x0": min(xs), "x1": max(xs), "system": tuple(system), "zeile": zeile}
        ergebnis.append({"nr": int(takt.get("number")), "seite": seite, "box": None, "status": status,
                         "gruende": list(dict.fromkeys(gruende)), "_lage": lage})
    _ausschnitte_setzen(ergebnis, seiten_masse)
    return ergebnis


def _ausschnitte_setzen(ergebnis: list[dict], seiten_masse: list[dict]) -> None:
    """Bildausschnitt je Takt: von hinter der letzten Note des Vortakts bis vor die erste Note des
    Folgetakts derselben Zeile – also der ganze Takt mit beiden Taktstrichen. (Nur um die erkannten
    Noten herum zu schneiden, ließe gerade das weg, was die Erkennung am Taktrand übersehen hat.)"""
    mit_lage = [e for e in ergebnis if e["_lage"]]
    for i, e in enumerate(mit_lage):
        lage = e["_lage"]
        m = seiten_masse[e["seite"]]
        ab = m.get("ab", 20.0)

        def nachbar(j):
            if not 0 <= j < len(mit_lage):
                return None
            n = mit_lage[j]
            gleich = n["seite"] == e["seite"] and n["_lage"]["zeile"] == lage["zeile"] and n["_lage"]["system"] == lage["system"]
            return n["_lage"] if gleich else None
        vor, nach = nachbar(i - 1), nachbar(i + 1)
        links = lage["x0"] - 3.0 * ab                 # Zeilenanfang: Schlüssel, Vorzeichen, Wiederholungszeichen
        if vor is not None and vor["x1"] < lage["x0"]:
            links = max(lage["x0"] - 10.0 * ab, min(lage["x0"] - 1.2 * ab, vor["x1"] + 0.7 * ab))
        rechts = lage["x1"] + 3.0 * ab
        if nach is not None and nach["x0"] > lage["x1"]:
            rechts = min(lage["x1"] + 10.0 * ab, max(lage["x1"] + 1.2 * ab, nach["x0"] - 0.5 * ab))
        e["box"] = [max(0, int(links)), max(0, int(lage["system"][0] - 3.5 * ab)),
                    min(m["breite"], int(rechts)), min(m["hoehe"], int(lage["system"][4] + 3.5 * ab))]
    for e in ergebnis:
        del e["_lage"]


def seiten_masse(pfad: str) -> dict:
    """Linienabstand und Lage der Notensysteme einer Seite (für die Bildausschnitte)."""
    import cv2
    import bildpruefung
    bild = cv2.imread(pfad, 0)
    h, w = bild.shape
    try:
        seite = bildpruefung.Seite(bild)
        systeme = []
        for linien in seite.systeme:
            systeme.append([float(li["verlauf"](w / 2)) for li in linien])
        systeme.sort()
        return {"breite": w, "hoehe": h, "ab": float(seite.ab), "systeme": systeme}
    except Exception:
        return {"breite": w, "hoehe": h, "ab": 20.0, "systeme": []}


def aufraeumen(wurzel: ET.Element) -> str:
    """Hilfsattribute entfernen, Kommentare behalten (die App nutzt imgpos nicht, stört aber nicht)."""
    for takt in wurzel.iter("measure"):
        takt.attrib.pop("data-seite", None)
    return ET.tostring(wurzel, encoding="unicode", xml_declaration=True)


def kopf_vorschlag() -> dict:
    """Titel, Stimme und Instrument, die der Notenleser im Kopf der zuletzt gelesenen Seite gefunden hat
    (leer, wenn dort nichts Eindeutiges stand)."""
    nl = sys.modules.get("notenleser")
    kopf = (getattr(nl, "ZUSTAND", None) or {}).get("kopf") or {}
    return {k: str(kopf[k])[:200] for k in ("titel", "stimme", "instrument") if kopf.get(k)}


def stueck_lesen(seiten_pfade: list[str], titel: str = "", stimme: str = "", melden=None) -> dict:
    """Alle Seiten lesen und zu einem Stück zusammenfügen. Ohne Titel oder Stimme vom Nutzer gilt, was
    im Kopf der ersten Seite steht.
    Rückgabe: {musicxml, takte, seiten:[{breite,hoehe}], protokoll, vorschlag:{titel, stimme, instrument}}"""
    texte, protokoll, masse, vorschlag = [], [], [], {}
    for k, pfad in enumerate(seiten_pfade):
        if melden:
            melden(f"Seite {k + 1} von {len(seiten_pfade)} wird gelesen …", k / max(1, len(seiten_pfade)))
        xml, prot = seite_lesen(pfad)
        if k == 0:
            vorschlag = kopf_vorschlag()
        texte.append(xml)
        protokoll += [f"Seite {k + 1}: {p}" for p in prot if "[debug]" not in p]
        masse.append(seiten_masse(pfad))
    if melden:
        melden("Takte werden geprüft …", 0.97)
    wurzel = zusammenfuegen(texte, titel or vorschlag.get("titel", ""), stimme or vorschlag.get("stimme", ""))
    takte = takte_bewerten(wurzel, masse)
    return {"musicxml": aufraeumen(wurzel), "takte": takte, "vorschlag": vorschlag,
            "seiten": [{"breite": m["breite"], "hoehe": m["hoehe"]} for m in masse], "protokoll": protokoll}
