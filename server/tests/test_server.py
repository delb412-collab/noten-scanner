"""Tests für den Server: Kennwort, Bibliothek (lokal und Hugging-Face-Ablage mit Attrappe),
Taktbewertung, Seiten zusammenfügen, PDF. Die eigentliche Notenerkennung wird hier ersetzt
(sie wird im Ende-zu-Ende-Test mit echten Seiten geprüft)."""
from __future__ import annotations

import importlib
import os
import sys
import time

import pytest

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, ".."))

KLEINE_PARTITUR = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Test</part-name></score-part></part-list>
<part id="P1">
<measure number="1"><attributes><divisions>2</divisions><key><fifths>0</fifths></key><time><beats>2</beats><beat-type>4</beat-type></time>
<clef><sign>G</sign><line>2</line></clef></attributes>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>2</duration><type>quarter</type><voice>1</voice><!-- imgpos: 300, 400 --><!-- sicher: r=0.95; p=0.99 --></note>
</measure>
<measure number="2">
<note><pitch><step>D</step><octave>5</octave></pitch><duration>2</duration><type>quarter</type><voice>1</voice><!-- imgpos: 500, 400 --><!-- sicher: r=0.95; p=0.99 --></note>
<note><pitch><step>E</step><octave>5</octave></pitch><duration>2</duration><type>quarter</type><voice>1</voice><!-- imgpos: 560, 400 --><!-- sicher: r=0.30; p=0.99 --></note>
</measure>
<measure number="3">
<note><pitch><step>F</step><octave>5</octave></pitch><duration>2</duration><type>quarter</type><voice>1</voice><!-- imgpos: 700, 400 --><!-- sicher: r=0.95; p=0.99 --></note>
<note><pitch><step>G</step><octave>5</octave></pitch><duration>4</duration><type>half</type><voice>1</voice><!-- imgpos: 760, 400 --><!-- sicher: r=0.95; p=0.99 --></note>
</measure>
<measure number="4">
<note><pitch><step>A</step><octave>5</octave></pitch><duration>2</duration><type>quarter</type><voice>1</voice><!-- imgpos: 900, 400 --><!-- sicher: r=0.95; p=0.99; korrektur=Notenwert über die Taktprüfung geändert --></note>
<note><rest/><duration>2</duration><type>quarter</type><voice>1</voice><!-- imgpos: 960, 400 --></note>
</measure>
<measure number="5">
<note><pitch><step>B</step><octave>5</octave></pitch><duration>2</duration><type>quarter</type><voice>1</voice><!-- imgpos: 1100, 400 --></note>
</measure>
</part></score-partwise>"""

MASSE = [{"breite": 2480, "hoehe": 3508, "ab": 20.0, "systeme": [[360.0, 380.0, 400.0, 420.0, 440.0]]}]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VEREIN_KENNWORT", "geheim")
    monkeypatch.setenv("BIBLIOTHEK_ORDNER", str(tmp_path / "bibliothek"))
    monkeypatch.delenv("BIBLIOTHEK_REPO", raising=False)
    import erkennung

    def falsches_lesen(pfade, titel="", stimme="", melden=None):
        if melden:
            melden("Seite 1 von 1 wird gelesen …", 0.0)
        wurzel = erkennung.zusammenfuegen([KLEINE_PARTITUR] * len(pfade), titel, stimme)
        takte = erkennung.takte_bewerten(wurzel, MASSE * len(pfade))
        return {"musicxml": erkennung.aufraeumen(wurzel), "takte": takte,
                "seiten": [{"breite": 2480, "hoehe": 3508}] * len(pfade), "protokoll": []}

    monkeypatch.setattr(erkennung, "stueck_lesen", falsches_lesen)
    import app as app_modul
    app_modul = importlib.reload(app_modul)
    monkeypatch.setattr(app_modul.Auftraege, "_aufwaermen", lambda self: None)
    app_modul.AUFTRAEGE = app_modul.Auftraege()
    from fastapi.testclient import TestClient
    return TestClient(app_modul.app)


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
KOPF = {"X-Kennwort": "geheim"}


def _warten(client, aid):
    for _ in range(100):
        a = client.get(f"/api/auftrag/{aid}", headers=KOPF).json()
        if a["status"] in ("fertig", "fehler"):
            return a
        time.sleep(0.05)
    raise AssertionError("Auftrag wurde nicht fertig")


def test_kennwort_wird_verlangt(client):
    assert client.get("/api/status").json()["kennwort"] is True
    assert client.post("/api/anmelden").status_code == 401
    assert client.post("/api/anmelden", headers={"X-Kennwort": "falsch"}).status_code == 401
    assert client.post("/api/anmelden", headers=KOPF).status_code == 200
    assert client.get("/api/bibliothek").status_code == 401


def test_lesen_nur_bilder(client):
    r = client.post("/api/lesen", headers=KOPF, files=[("seiten", ("a.txt", b"hallo", "text/plain"))])
    assert r.status_code == 400


def test_auftrag_und_bibliothek(client):
    r = client.post("/api/lesen", headers=KOPF, files=[("seiten", ("s1.png", PNG, "image/png")),
                                                        ("seiten", ("s2.png", PNG, "image/png"))],
                    data={"titel": "Marsch", "stimme": "1. Trompete"})
    aid = r.json()["auftrag"]
    a = _warten(client, aid)
    assert a["status"] == "fertig"
    erg = a["ergebnis"]
    assert len(erg["takte"]) == 10 and {t["seite"] for t in erg["takte"]} == {0, 1}
    assert "new-page" in erg["musicxml"] and "Marsch" in erg["musicxml"]
    assert client.get(f"/api/auftrag/{aid}/seite/1", headers=KOPF).content == PNG

    neu = client.post("/api/bibliothek", headers=KOPF, json={
        "titel": "Marsch", "stimme": "1. Trompete", "instrument": "b", "musicxml": erg["musicxml"],
        "auftrag": aid, "unsicher": 2, "takte": erg["takte"]}).json()
    assert neu["bilder"] is True
    liste = client.get("/api/bibliothek", headers=KOPF).json()
    assert len(liste) == 1 and liste[0]["titel"] == "Marsch" and liste[0]["seiten"] == 2
    stueck = client.get(f"/api/bibliothek/{neu['id']}", headers=KOPF).json()
    assert stueck["musicxml"] == erg["musicxml"] and len(stueck["takte"]) == 10
    assert client.get(f"/api/bibliothek/{neu['id']}/seite/0", headers=KOPF).content == PNG
    # ändern ohne neue Bilder
    client.post(f"/api/bibliothek?sid={neu['id']}", headers=KOPF, json={"titel": "Marsch (korrigiert)", "musicxml": "<x/>"})
    assert client.get("/api/bibliothek", headers=KOPF).json()[0]["titel"] == "Marsch (korrigiert)"
    assert client.get(f"/api/bibliothek/{neu['id']}/seite/1", headers=KOPF).status_code == 200
    # Bilder nachreichen
    assert client.post(f"/api/bibliothek/{neu['id']}/seiten", headers=KOPF,
                       files=[("seiten", ("x.png", PNG, "image/png"))]).status_code == 200
    assert client.get(f"/api/bibliothek/{neu['id']}/seite/1", headers=KOPF).status_code == 404
    # ungültige IDs
    assert client.get("/api/bibliothek/../../etc", headers=KOPF).status_code == 404
    assert client.get("/api/bibliothek/gibtsnicht", headers=KOPF).status_code == 404
    assert client.delete(f"/api/bibliothek/{neu['id']}", headers=KOPF).status_code == 200
    assert client.get("/api/bibliothek", headers=KOPF).json() == []


def test_takte_bewerten():
    import erkennung
    wurzel = erkennung.zusammenfuegen([KLEINE_PARTITUR])
    takte = {t["nr"]: t for t in erkennung.takte_bewerten(wurzel, MASSE)}
    assert takte[1]["status"] == "ok"                      # erster Takt: Auftakt erlaubt
    assert takte[2]["status"] == "unsicher" and "Notenwert unsicher gelesen" in takte[2]["gruende"]
    assert takte[3]["status"] == "unsicher" and any("zu lang" in g for g in takte[3]["gruende"])
    assert takte[4]["status"] == "korrigiert"
    assert takte[5]["status"] == "ok"                      # letzter Takt darf kurz sein
    x0, y0, x1, y1 = takte[2]["box"]
    assert x0 < 500 < 560 < x1 and y0 < 360 and y1 > 440
    # Ausschnitt = ganzer Takt: von hinter der letzten Note des Vortakts (x=300) bis vor die erste des
    # Folgetakts (x=700) – auch eine am Taktrand übersehene Note wäre im Bild
    assert 300 < x0 <= 330 and 670 <= x1 < 700
    # am Zeilenanfang und -ende großzügig (Schlüssel, Wiederholungszeichen)
    assert takte[1]["box"][0] <= 300 - 2.5 * 20 and takte[5]["box"][2] >= 1100 + 2.5 * 20


def test_pdf(client):
    import erkennung
    xml = erkennung.aufraeumen(erkennung.zusammenfuegen([KLEINE_PARTITUR], "Test"))
    for intervall in ("", "P5", "-M2"):
        r = client.post("/api/pdf", headers=KOPF, json={"musicxml": xml, "transponieren": intervall, "dateiname": "Test"})
        assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert client.post("/api/pdf", headers=KOPF, json={"musicxml": xml, "transponieren": "rm -rf"}).status_code == 400


def test_hf_ablage_mit_attrappe(tmp_path, monkeypatch):
    """Die Hugging-Face-Ablage gegen eine Attrappe der huggingface_hub-Schnittstelle."""
    import huggingface_hub
    dateien: dict[str, bytes] = {}
    commits = []

    class Attrappe:
        def __init__(self, token=None):
            self.token = token

        def create_repo(self, repo_id, repo_type=None, private=None, exist_ok=False):
            assert repo_type == "dataset" and private is True

        def create_commit(self, repo_id, operations, commit_message="", repo_type=None):
            commits.append(commit_message)
            for op in operations:
                if isinstance(op, huggingface_hub.CommitOperationAdd):
                    dateien[op.path_in_repo] = op.path_or_fileobj
                else:
                    for k in [k for k in dateien if k == op.path_in_repo or k.startswith(op.path_in_repo)]:
                        del dateien[k]

    from huggingface_hub.errors import LocalEntryNotFoundError, RemoteEntryNotFoundError
    offline = {"an": False}

    def herunterladen(repo_id, pfad, repo_type=None, token=None, cache_dir=None):
        if offline["an"]:
            raise LocalEntryNotFoundError("keine Verbindung und nichts im Zwischenspeicher")
        if pfad not in dateien:
            fehler = RemoteEntryNotFoundError.__new__(RemoteEntryNotFoundError)
            fehler.args = (f"404 {pfad}",)
            raise fehler
        ziel = tmp_path / pfad.replace("/", "_")
        ziel.write_bytes(dateien[pfad])
        return str(ziel)

    monkeypatch.setattr(huggingface_hub, "HfApi", Attrappe)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", herunterladen)
    import ablage
    a = ablage.HFAblage("verein/noten-bibliothek", "tok", cache=str(tmp_path / "cache"))
    assert a.liste() == []
    sid = a.speichern({"titel": "Polka", "stimme": "Tuba"}, "<score/>", [PNG, PNG], takte=[{"nr": 1}])
    assert "index.json" in dateien and f"stuecke/{sid}/seite_1.png" in dateien
    assert a.laden(sid) == (a.index[sid], "<score/>") and a.takte(sid) == [{"nr": 1}]
    a.speichern({"titel": "Polka"}, "<score2/>", [PNG], sid)
    assert f"stuecke/{sid}/seite_1.png" not in dateien and a.seite(sid, 0) == PNG
    b = ablage.HFAblage("verein/noten-bibliothek", "tok", cache=str(tmp_path / "cache"))   # Neustart
    assert [s["titel"] for s in b.liste()] == ["Polka"]
    with pytest.raises(KeyError):
        b.seite(sid, 5)                                  # fehlende Seite -> 404, kein Serverfehler
    # Netzfehler beim Start: NICHT als leere Bibliothek behandeln (sonst überschreibt das nächste
    # Speichern das Verzeichnis und alle Stücke wären "vergessen")
    offline["an"] = True
    with pytest.raises(LocalEntryNotFoundError):
        ablage.HFAblage("verein/noten-bibliothek", "tok", cache=str(tmp_path / "cache"))
    offline["an"] = False
    b.loeschen(sid)
    assert b.liste() == [] and not any(k.startswith("stuecke/") for k in dateien)
    with pytest.raises(KeyError):
        b.laden("../x")


def test_im_internet_nie_ohne_kennwort(tmp_path, monkeypatch):
    """Auf Hugging Face (SPACE_ID gesetzt) ohne Vereinskennwort: keine Aufträge, keine Bibliothek."""
    monkeypatch.delenv("VEREIN_KENNWORT", raising=False)
    monkeypatch.setenv("SPACE_ID", "verein/noten-scanner")
    monkeypatch.setenv("BIBLIOTHEK_ORDNER", str(tmp_path / "bibliothek"))
    import app as app_modul
    app_modul = importlib.reload(app_modul)
    monkeypatch.setattr(app_modul.Auftraege, "_aufwaermen", lambda self: None)
    from fastapi.testclient import TestClient
    c = TestClient(app_modul.app)
    assert c.get("/api/status").status_code == 200
    assert c.get("/api/bibliothek").status_code == 503
    assert c.post("/api/lesen", files=[("seiten", ("s.png", PNG, "image/png"))]).status_code == 503
    monkeypatch.delenv("SPACE_ID")
    importlib.reload(app_modul)


def test_seitengroesse_bildtyp_und_dateiname(client, monkeypatch):
    import app as app_modul
    monkeypatch.setattr(app_modul, "MAX_BYTES", 100)
    gross = PNG + b"\x00" * 200
    r = client.post("/api/lesen", headers=KOPF, files=[("seiten", ("s.png", gross, "image/png"))])
    assert r.status_code == 413
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 40
    aid = client.post("/api/lesen", headers=KOPF, files=[("seiten", ("s.jpg", jpeg, "image/jpeg"))]).json()["auftrag"]
    _warten(client, aid)
    r = client.get(f"/api/auftrag/{aid}/seite/0", headers=KOPF)
    assert r.headers["content-type"] == "image/jpeg" and r.content == jpeg
    import erkennung
    xml = erkennung.aufraeumen(erkennung.zusammenfuegen([KLEINE_PARTITUR], "Test"))
    r = client.post("/api/pdf", headers=KOPF, json={"musicxml": xml, "dateiname": "Böhmischer Traum – Łódź"})
    assert r.status_code == 200
    kopf = r.headers["content-disposition"]
    assert "filename*=UTF-8''B%C3%B6hmischer" in kopf and kopf.isascii()


def test_auftakt_und_mehrtaktpause():
    """Auftakt = Takt 0 bleibt beim Zusammenfügen erhalten; Korrektur an einer Mehrtaktpause wird
    als „korrigiert“ gemeldet und hat einen Bildausschnitt."""
    import erkennung
    xml = KLEINE_PARTITUR.replace('<measure number="1">', '<measure number="0" implicit="yes">')
    xml = xml.replace('<measure number="5">', '<measure number="5"><attributes><measure-style><multiple-rest>3</multiple-rest>'
                      '<!-- imgpos: 1150, 400 --><!-- sicher: korrektur=Mehrtaktpause im Bild gelesen: 3 --></measure-style></attributes>'
                      ).replace('<note><pitch><step>B</step><octave>5</octave></pitch><duration>2</duration><type>quarter</type><voice>1</voice><!-- imgpos: 1100, 400 --></note>',
                                '<note><rest measure="yes"/><duration>4</duration><voice>1</voice></note>')
    wurzel = erkennung.zusammenfuegen([xml, xml])
    nummern = [t.get("number") for t in wurzel.iter("measure")]
    assert nummern[:3] == ["0", "1", "2"] and nummern[-1] == "9"
    assert [t.get("implicit") for t in wurzel.iter("measure")].count("yes") == 1
    takte = {t["nr"]: t for t in erkennung.takte_bewerten(wurzel, MASSE * 2)}
    assert takte[0]["status"] == "ok"
    assert takte[4]["status"] == "korrigiert" and takte[4]["box"] is not None
