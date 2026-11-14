"""Tests für den Notentext nach der Erkennung: offene Bindebögen, Textangaben in Mehrtaktpausen
(Aufbereitung für den Notensatz) und der Titel-Vorschlag aus dem Kopf der Seite."""
from __future__ import annotations

import os
import sys
import types
import xml.etree.ElementTree as ET

import pytest

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, ".."))
sys.path.insert(0, os.path.join(HIER, "..", "..", "notenleser"))


def _note(ton="C", typ="quarter", marken=""):
    inhalt = "<rest/>" if ton == "P" else f"<pitch><step>{ton}</step><octave>5</octave></pitch>"
    notation = f"<notations>{marken}</notations>" if marken else ""
    return f"<note>{inhalt}<duration>4</duration><voice>1</voice><type>{typ}</type>{notation}</note>"


AUF, ZU = '<slur type="start" number="1"/>', '<slur type="stop" number="1"/>'


def _stueck(*takte):
    innen = "".join(f'<measure number="{i + 1}">{t}</measure>' for i, t in enumerate(takte))
    return ET.fromstring(f'<score-partwise><part-list><score-part id="P1"><part-name/></score-part></part-list>'
                         f'<part id="P1">{innen}</part></score-partwise>')


def _boegen(wurzel):
    """Je Takt die Folge der Bogenmarken, z. B. "C( D) E F"."""
    aus = []
    for takt in wurzel.iter("measure"):
        teile = []
        for n in takt.findall("note"):
            name = "P" if n.find("rest") is not None else n.findtext("pitch/step")
            teile.append(name + "".join(")" if b.get("type") == "stop" else "(" for b in n.findall("notations/slur")))
        aus.append(" ".join(teile))
    return aus


def test_offene_boegen_werden_geschlossen_oder_entfernt():
    pytest.importorskip("homr")
    import notenleser as nl
    nl.ZUSTAND["protokoll"] = []
    wurzel = _stueck(
        # Vorbild: Bogen von der zweiten Note über den Taktstrich zur ersten des nächsten Takts
        _note("C") + _note("D", marken=AUF) + _note("E") + _note("F"),
        _note("G", marken=ZU) + _note("A") + _note("A") + _note("A"),
        # gleicher Takt, das Ende fehlt (Zeilenwechsel): wird wie das Vorbild geschlossen
        _note("C") + _note("D", marken=AUF) + _note("E") + _note("F"),
        _note("G") + _note("A") + _note("A") + _note("A"),
        # kein Vorbild: bis zur nächsten Note
        _note("C", "half", marken=AUF) + _note("D", "half"),
        # Anfang vor einer Pause, das nächste Ende erst drei Takte weiter hinter einer Pause: kein Paar –
        # beide Marken fallen weg (sonst zöge der Notensatz einen Bogen über die ganze Zeile)
        _note("E", "half", marken=AUF) + _note("P", "half"),
        _note("C", "whole"),
        _note("C", "whole"),
        _note("P", "half") + _note("G", "half", marken=ZU),
        # Stückende: nichts mehr zum Schließen
        _note("C", "whole", marken=AUF),
    )
    nl.boegen_richten(wurzel)
    assert _boegen(wurzel) == ["C D( E F", "G) A A A", "C D( E F", "G) A A A", "C( D)", "E P", "C", "C", "P G", "C"]
    assert any("Bindebögen" in z for z in nl.ZUSTAND["protokoll"])
    # Ein zweiter Lauf ändert nichts mehr
    vorher = ET.tostring(wurzel)
    nl.boegen_richten(wurzel)
    assert ET.tostring(wurzel) == vorher


def test_haltebogen_ende_steht_vor_anfang():
    pytest.importorskip("homr")
    import notenleser as nl
    wurzel = _stueck(_note("C", "whole", '<tied type="start"/>'),
                     _note("C", "whole", '<tied type="start"/><tied type="stop"/>'),
                     _note("C", "whole", '<tied type="stop"/>'))
    nl.boegen_richten(wurzel)
    mitte = list(wurzel.iter("measure"))[1]
    assert [t.get("type") for t in mitte.iter("tied")] == ["stop", "start"]


PAUSE = '<note><rest measure="yes"/><duration>16</duration><voice>1</voice></note>'
ANGABEN = ('<attributes><divisions>4</divisions><key><fifths>0</fifths></key><time><beats>4</beats><beat-type>4</beat-type>'
           '</time><clef><sign>G</sign><line>2</line></clef></attributes>')


def _text(wort, art="words"):
    return f'<direction placement="above"><direction-type><{art}>{wort}</{art}></direction-type></direction>'


def test_texte_in_mehrtaktpausen_bleiben_sichtbar():
    import satz
    mehrtakt = '<attributes><measure-style><multiple-rest>3</multiple-rest></measure-style></attributes>'
    xml = ('<score-partwise version="4.0"><part-list><score-part id="P1"><part-name/></score-part></part-list><part id="P1">'
           f'<measure number="1">{ANGABEN}{mehrtakt}{_text("Lento")}{PAUSE}</measure>'
           f'<measure number="2">{_text("B", "rehearsal")}{PAUSE}</measure><measure number="3">{PAUSE}</measure>'
           f'<measure number="4">{_text("dolce")}{_note("C", "whole")}</measure></part></score-partwise>')
    neu = satz.fuer_verovio(xml)
    takte = list(ET.fromstring(neu).iter("measure"))
    folge = [k.tag for k in takte[0]]
    # beide Texte stehen jetzt im ersten Takt vor der Pausen-Angabe; der Text an der Note bleibt, wo er war
    assert folge == ["attributes", "direction", "direction", "attributes", "note"]
    assert takte[1].find("direction") is None and takte[3].find("direction") is not None
    assert satz.fuer_verovio(neu) == neu                         # nichts mehr zu tun
    verovio = pytest.importorskip("verovio")
    tk = verovio.toolkit()
    tk.setResourcePath(os.path.join(os.path.dirname(verovio.__file__), "data"))

    def bild(text):
        tk.loadData(text)
        return tk.renderToSVG(1)
    assert "Lento" not in bild(xml)                              # der Grund für die Aufbereitung
    svg = bild(neu)
    assert "Lento" in svg and "dolce" in svg and 'class="reh' in svg


def test_stimme_steht_unter_dem_titel():
    import satz
    xml = ('<score-partwise version="4.0"><work><work-title>Marsch</work-title></work><part-list><score-part id="P1">'
           '<part-name>1. Trompete in B</part-name></score-part></part-list><part id="P1">'
           f'<measure number="1">{ANGABEN}{_note("C", "whole")}</measure></part></score-partwise>')
    neu = satz.fuer_verovio(xml)
    wurzel = ET.fromstring(neu)
    assert [k.tag for k in wurzel.find("work")] == ["work-number", "work-title"]
    assert wurzel.findtext("work/work-number") == "1. Trompete in B"
    assert wurzel.find(".//part-name").get("print-object") == "no" and wurzel.findtext(".//part-name") == "1. Trompete in B"
    assert satz.fuer_verovio(neu) == neu                         # nichts mehr zu tun
    assert satz.fuer_verovio(xml.replace("1. Trompete in B", "")) == xml.replace("1. Trompete in B", "")   # ohne Stimme: unverändert
    verovio = pytest.importorskip("verovio")
    tk = verovio.toolkit()
    tk.setResourcePath(os.path.join(os.path.dirname(verovio.__file__), "data"))
    tk.setOptions({"header": "auto"})
    tk.loadData(neu)
    svg = tk.renderToSVG(1)
    assert "1. Trompete in B" in svg and "Marsch" in svg and 'class="label"' not in svg      # keine Zeilen-Beschriftung


def test_titel_vorschlag_vom_seitenkopf(monkeypatch, tmp_path):
    import erkennung
    seite = ('<score-partwise><part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list>'
             f'<part id="P1"><measure number="1">{ANGABEN}{_note("C", "whole")}</measure></part></score-partwise>')
    monkeypatch.setattr(erkennung, "seite_lesen", lambda pfad: (seite, []))
    monkeypatch.setattr(erkennung, "seiten_masse", lambda pfad: {"breite": 100, "hoehe": 100, "ab": 20.0, "systeme": []})
    kopf = {"titel": "Lummerland", "stimme": "1. Trompete Bb", "instrument": "b"}
    monkeypatch.setitem(sys.modules, "notenleser", types.SimpleNamespace(ZUSTAND={"kopf": kopf}))
    erg = erkennung.stueck_lesen(["a.png", "b.png"])
    assert erg["vorschlag"] == kopf
    wurzel = ET.fromstring(erg["musicxml"])
    assert wurzel.findtext("work/work-title") == "Lummerland" and wurzel.findtext(".//part-name") == "1. Trompete Bb"
    # Was der Nutzer eingetippt hat, geht vor; der Vorschlag wird trotzdem mitgeliefert
    erg = erkennung.stueck_lesen(["a.png"], titel="Mein Titel")
    assert ET.fromstring(erg["musicxml"]).findtext("work/work-title") == "Mein Titel"
    assert erg["vorschlag"]["titel"] == "Lummerland"
    # Ohne lesbaren Kopf: kein Vorschlag, kein Titel
    monkeypatch.setitem(sys.modules, "notenleser", types.SimpleNamespace(ZUSTAND={"kopf": {}}))
    erg = erkennung.stueck_lesen(["a.png"])
    assert erg["vorschlag"] == {} and ET.fromstring(erg["musicxml"]).find("work") is None


# ---------- Texterkennung: Kopf, Textangaben, Studierzeichen (gezeichnete Seite) ----------

AB = 20
ZEILEN = (700, 1100, 1500)                       # oberste Linie der drei Notenzeilen
STRICHE = (80, 540, 1000, 1460, 1920)


def _seite_mit_text():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    bild = np.full((2000, 2000), 255, np.uint8)
    for y0 in ZEILEN:
        for k in range(5):
            cv2.line(bild, (80, y0 + k * AB), (1920, y0 + k * AB), 0, 2)
        for x in STRICHE:
            cv2.line(bild, (x, y0), (x, y0 + 4 * AB), 0, 2)

    def text(wort, x, y, groesse=1.6, dick=2):
        cv2.putText(bild, wort, (x, y), cv2.FONT_HERSHEY_DUPLEX, groesse, 0, dick, cv2.LINE_AA)
    text("Testmarsch", 620, 330, 4.0, 5)             # Titel: der größte Text über den Noten
    text("1. Trompete in B", 90, 470)                # Stimme
    text("Hans Mustermann", 1450, 560, 1.4)          # Komponist: rechts im Kopf, kein Notentext
    text("Allegro", 100, 650)                        # Tempo links über der ersten Zeile
    text("dolce", 1050, 670, 1.5)                    # dicht über der ersten Zeile, rechts
    text("Trio", 560, 1060, 1.7, 3)
    text("D.C. al Fine", 1500, 1070, 1.5)
    text("rit.", 1050, 1640, 1.5)                    # unter der dritten Zeile
    for x, y, zeichen in ((1005, 1020, "A"), (85, 1420, "12")):      # Studierzeichen im Kasten
        cv2.rectangle(bild, (x, y), (x + (62 if len(zeichen) == 1 else 84), y + 62), 0, 3)
        cv2.putText(bild, zeichen, (x + 12, y + 50), cv2.FONT_HERSHEY_DUPLEX, 1.7, 0, 3, cv2.LINE_AA)
    return bild


def test_kopf_texte_und_studierzeichen_vom_bild():
    pytest.importorskip("homr")
    pytest.importorskip("rapidocr")
    import bildpruefung as bp
    import notenleser as nl
    seite = bp.Seite(_seite_mit_text())
    texte = bp.texte_lesen(seite)
    if bp.OCR_FEHLER:
        pytest.skip("Texterkennung nicht verfügbar: " + bp.OCR_FEHLER)
    assert bp.kopf_lesen(seite, texte) == {"titel": "Testmarsch", "stimme": "1. Trompete in B", "instrument": "b"}
    angaben = {a["text"]: a for a in bp.angaben_lesen(seite, texte)}
    assert set(angaben) == {"Allegro", "dolce", "Trio", "D.C. al Fine", "rit."}        # der Komponist ist nicht dabei
    assert angaben["Allegro"]["fett"] and not angaben["dolce"]["fett"] and not angaben["rit."]["oben"]
    assert sorted(z["text"] for z in bp.studierzeichen_suchen(seite)) == ["12", "A"]

    # Vier Takte je Zeile; der erste der dritten Zeile ist eine Mehrtaktpause (Takt 9–10), danach 11–13
    takte = []
    for z, y0 in enumerate(ZEILEN):
        for i in range(4):
            nr = 4 * z + i + 1
            x, y = (STRICHE[i] + STRICHE[i + 1]) // 2, y0 + 2 * AB
            kopf = '<print new-system="yes"/>' if i == 0 and z else ""
            if nr == 9:
                takte.append(f'<measure number="9">{kopf}<attributes><measure-style><multiple-rest>2</multiple-rest>'
                             f'<!-- imgpos: {x}, {y} --></measure-style></attributes>{PAUSE}</measure><measure number="10">{PAUSE}</measure>')
                continue
            takte.append(f'<measure number="{nr + (1 if nr > 9 else 0)}">{kopf}<note><pitch><step>C</step><octave>5</octave></pitch>'
                         f'<duration>16</duration><voice>1</voice><type>whole</type><!-- imgpos: {x}, {y} --></note></measure>')
    wurzel = ET.fromstring('<score-partwise><part-list><score-part id="P1"><part-name/></score-part></part-list><part id="P1">'
                           + "".join(takte) + "</part></score-partwise>", parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    nl.ZUSTAND.update(protokoll=[], seite=seite, kopf={})
    wurzel = nl.texte_einsetzen(wurzel)
    assert nl.ZUSTAND["kopf"]["titel"] == "Testmarsch"
    gefunden = {}
    for takt in wurzel.iter("measure"):
        for d in takt.findall("direction"):
            el = d.find("direction-type/words")
            art = "Text"
            if el is None:
                el, art = d.find("direction-type/rehearsal"), "Zeichen"
            gefunden[el.text] = (int(takt.get("number")), art, d.get("placement"))
    assert gefunden == {"Allegro": (1, "Text", "above"), "dolce": (3, "Text", "above"), "Trio": (6, "Text", "above"),
                        "A": (7, "Zeichen", "above"), "D.C. al Fine": (8, "Text", "above"),
                        "12": (9, "Zeichen", "above"), "rit.": (12, "Text", "below")}
    assert any("Texte am Bild gelesen" in z for z in nl.ZUSTAND["protokoll"])
    # Im Notensatz ist auch das Zeichen an der Mehrtaktpause zu sehen
    import satz
    verovio = pytest.importorskip("verovio")
    tk = verovio.toolkit()
    tk.setResourcePath(os.path.join(os.path.dirname(verovio.__file__), "data"))
    tk.loadData(satz.fuer_verovio(ET.tostring(wurzel, encoding="unicode").replace(
        '<measure number="1">', '<measure number="1">' + ANGABEN, 1)))
    svg = tk.renderToSVG(1)
    assert svg.count('class="reh') == 2 and "Trio" in svg
