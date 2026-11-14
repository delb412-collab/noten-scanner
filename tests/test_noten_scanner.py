"""Automatische Tests. Start: python -m pytest tests"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notenscanner.cli import main as cli_main  # noqa: E402
from notenscanner.dateien import seiten_laden  # noqa: E402
from notenscanner.stapel import stapel_verarbeiten  # noqa: E402
from notenscanner.verarbeitung import Einstellungen, verarbeiten, zielgroesse  # noqa: E402
from tests import testdaten as td  # noqa: E402


@pytest.fixture(scope="module")
def blatt():
    return td.einfaches_notenblatt(np.random.default_rng(1))


@pytest.fixture(scope="module")
def handyfoto(blatt):
    foto, _ = td.handyfoto(blatt[0], np.random.default_rng(2), winkel=4.0)
    return foto


def test_handyfoto_wird_entzerrt_und_bereinigt(blatt, handyfoto):
    ergebnis = verarbeiten(handyfoto)
    assert ergebnis.infos["blatt_gefunden"]
    assert ergebnis.bild.shape == (3508, 2480)  # A4 mit 300 dpi
    werte = td.bewerten(ergebnis.bild, blatt[0])
    assert werte["f1"] > 0.97, werte
    # Hintergrund wirklich reinweiß, Noten wirklich schwarz
    assert np.mean(ergebnis.bild == 255) > 0.9
    assert np.mean(ergebnis.bild == 0) > 0.02


def test_scanner_app_ohne_blattrand_wird_gerade_gedreht(blatt):
    rng = np.random.default_rng(3)
    bild = td.scanner_app(blatt[0], rng, winkel=2.2)
    ergebnis = verarbeiten(bild)
    assert not ergebnis.infos["blatt_gefunden"]
    assert abs(ergebnis.infos["winkel"] + 2.2) < 0.15
    assert td.bewerten(ergebnis.bild, blatt[0])["f1"] > 0.97
    # Der dunkle Tischstreifen am linken Rand muss weg sein
    assert np.all(ergebnis.bild[:, :40] == 255)


def test_staccato_punkte_bleiben_flecken_verschwinden(blatt):
    seite, staccato = blatt
    rng = np.random.default_rng(4)
    verschmutzt = seite.copy()
    flecken = []
    while len(flecken) < 60:  # Flecken nur auf freiem Papier
        x, y = int(rng.integers(150, 2330)), int(rng.integers(3300, 3450))
        cv2.circle(verschmutzt, (x, y), int(rng.integers(1, 4)), 0, -1)
        flecken.append((x, y))
    ergebnis = verarbeiten(cv2.cvtColor(verschmutzt, cv2.COLOR_GRAY2BGR), Einstellungen(perspektive=False))
    schwarz = ergebnis.bild < 128
    assert all(schwarz[y - 2:y + 3, x - 2:x + 3].any() for x, y in staccato)
    assert not any(schwarz[y - 4:y + 5, x - 4:x + 5].any() for x, y in flecken)


@pytest.mark.parametrize("drehung", [cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE])
def test_quer_oder_kopfueber_fotografiert(blatt, handyfoto, drehung):
    ergebnis = verarbeiten(cv2.rotate(handyfoto, drehung))
    assert ergebnis.infos["drehung"] in (90, 180, 270)
    assert ergebnis.bild.shape == (3508, 2480)
    assert td.bewerten(ergebnis.bild, blatt[0])["f1"] > 0.97


def test_aufrechte_seite_wird_nicht_gedreht(handyfoto):
    assert verarbeiten(handyfoto).infos["drehung"] == 0


def test_unterbrochene_und_ungleich_dicke_notenlinien_werden_repariert(blatt):
    seite = blatt[0].copy()
    rng = np.random.default_rng(8)
    luecken = []
    for zeile in range(9):
        for linie in range(5):
            y = 420 + zeile * 330 + linie * 24
            for _ in range(6):
                x = int(rng.integers(300, 2200))
                # nur dort, wo wirklich nur die Notenlinie ist (keine Note in der Nähe)
                if (seite[y - 11:y + 12, x - 8:x + 9] < 128).sum() == (seite[y - 2:y + 3, x - 8:x + 9] < 128).sum():
                    seite[y - 2:y + 3, x - 4:x + 5] = 255
                    luecken.append((x, y))
            x = int(rng.integers(300, 2000))
            cv2.line(seite, (x, y), (x + 80, y), 0, 7)  # dicke Stelle wie bei schlechten Kopien
    bild = cv2.cvtColor(seite, cv2.COLOR_GRAY2BGR)
    ohne = verarbeiten(bild, Einstellungen(perspektive=False, linien_reparieren=False))
    mit = verarbeiten(bild, Einstellungen(perspektive=False))
    assert mit.infos["notensysteme"] == 9
    geschlossen = lambda b: np.mean([b[y - 1:y + 2, x].min() < 128 for x, y in luecken])  # noqa: E731
    assert len(luecken) > 50 and geschlossen(ohne.bild) < 0.2 and geschlossen(mit.bild) > 0.95
    # Noten bleiben vollständig erhalten
    assert td.bewerten(mit.bild, blatt[0])["trefferquote"] > 0.97


def _maske_mit_balken_auf_linien():
    """Drei Notensysteme mit Maßen wie bei einem echten Handyfoto (Linienabstand 16 px,
    Linien 4 px) und dünnen Balken (6 px) genau auf bzw. knapp an den Linien."""
    abstand = 16
    maske = np.zeros((420, 1400), np.uint8)
    balken = []
    for system in range(3):
        oben = 60 + system * 130
        for i in range(5):
            maske[oben + i * abstand - 2:oben + i * abstand + 2, 50:1350] = 1
        for j, (linie, versatz) in enumerate(((0, 0), (2, 0), (4, -4), (1, 3))):
            x = 150 + j * 300
            y = oben + linie * abstand - 3 + versatz
            maske[y:y + 6, x:x + 90] = 1
            balken.append((x, y))
        for x in range(120, 1350, 75):                   # ein paar Hälse wie bei echten Noten
            maske[oben - 10:oben + 4 * abstand + 2, x:x + 3] = 1
    return maske, balken


def test_balken_auf_notenlinien_bleiben_erhalten():
    """Dünne Balken, die genau auf oder an einer Notenlinie liegen, dürfen von der
    Linien-Reparatur nicht als "zu dicke Linie" weggeschnitten werden."""
    from notenscanner.linien import linien_reparieren
    maske, balken = _maske_mit_balken_auf_linien()
    reparatur = linien_reparieren(maske, 16, 4)
    assert reparatur.systeme == 3
    nachher = (maske > 0) & ~reparatur.weg
    erhalten = [nachher[y:y + 6, x + 2:x + 88].mean() for x, y in balken]
    assert min(erhalten) > 0.95, [round(e, 2) for e in erhalten]


def test_selbstkontrolle_rettet_balken(monkeypatch):
    """Selbst wenn die Reparatur Balken fälschlich entfernen würde (hier absichtlich
    herbeigeführt), stellt die Selbstkontrolle sie wieder her."""
    from notenscanner import linien
    maske, balken = _maske_mit_balken_auf_linien()
    monkeypatch.setattr(linien, "_aufliegende_zeichen", lambda m, *a, **k: np.zeros(m.shape, bool))
    reparatur = linien.linien_reparieren(maske, 16, 4)
    vorher = [((maske > 0) & ~reparatur.weg)[y:y + 6, x + 2:x + 88].mean() for x, y in balken]
    assert min(vorher) < 0.5          # ohne Kontrolle wären Balken weg
    assert linien.selbstkontrolle(maske, reparatur, 16) > 0
    nachher = [((maske > 0) & ~reparatur.weg)[y:y + 6, x + 2:x + 88].mean() for x, y in balken]
    assert min(nachher) > 0.95, [round(e, 2) for e in nachher]


def test_selbstkontrolle_verhindert_phantom_linien():
    """Eine (absichtlich hinzugefügte) Linie dort, wo im Original nichts ist, wird nicht gezeichnet."""
    from notenscanner import linien
    maske, _ = _maske_mit_balken_auf_linien()
    reparatur = linien.linien_reparieren(maske, 16, 4)
    xs = np.arange(200, 1200)
    reparatur.linien.append(linien.Linie(False, xs, np.full(len(xs), 60.0 - 16), 2.4, 4.0))
    assert linien.selbstkontrolle(maske, reparatur, 16) >= 1
    bild = reparatur.zeichnen(np.full(maske.shape, 255, np.uint8), glatt=False)
    assert (bild[40:49, 300:1100] < 128).mean() < 0.01


def test_schraeges_system_mit_balken_erfindet_keine_linien(blatt):
    """Obere Systeme steigen über die Breite um einen Linienabstand an (Papier nicht ganz
    flach), darüber liegen Balken im Abstand einer Linie – es darf kein verschobenes
    Phantom-System entstehen."""
    seite = blatt[0].copy()
    for zeile in range(4):
        oben = 420 + zeile * 330
        for x in (250, 700, 1150):  # Balken genau einen Linienabstand über der obersten Linie
            cv2.rectangle(seite, (x, oben - 24 - 2), (x + 160, oben - 24 + 2), 0, -1)
    h, w = seite.shape
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    anstieg = 24 * (xs / w) * np.clip(1 - ys / (h / 2), 0, 1)   # nur in der oberen Hälfte schräg
    seite = cv2.remap(seite, xs, ys + anstieg, cv2.INTER_LINEAR, borderValue=255)
    ergebnis = verarbeiten(cv2.cvtColor(seite, cv2.COLOR_GRAY2BGR),
                           Einstellungen(perspektive=False, begradigen=False))
    assert ergebnis.infos["notensysteme"] == 9
    vorlage = seite < 128
    abstand_zur_tinte = cv2.distanceTransform((~vorlage).astype(np.uint8), cv2.DIST_L2, 3)
    erfunden = (ergebnis.bild < 128) & (abstand_zur_tinte > 4)
    assert erfunden.sum() < 50, int(erfunden.sum())


def test_leere_seite_bleibt_weiss():
    rng = np.random.default_rng(5)
    leer = np.full((2000, 1400, 3), 230, np.float32) + rng.normal(0, 6, (2000, 1400, 3))
    ergebnis = verarbeiten(np.clip(leer, 0, 255).astype(np.uint8))
    assert np.mean(ergebnis.bild == 255) > 0.999


def test_bleistift_wird_entfernt(blatt):
    rng = np.random.default_rng(6)
    mit_bleistift = td.bleistift_eintragen(blatt[0], rng)
    bild = td.scanner_app(mit_bleistift, rng)
    ohne = verarbeiten(bild, Einstellungen(bleistift_entfernen=False))
    mit = verarbeiten(bild, Einstellungen(bleistift_entfernen=True))
    assert td.bewerten(mit.bild, blatt[0])["praezision"] > td.bewerten(ohne.bild, blatt[0])["praezision"]


def test_hart_ergibt_nur_schwarz_und_weiss(handyfoto):
    ergebnis = verarbeiten(handyfoto, Einstellungen(modus="hart"))
    assert set(np.unique(ergebnis.bild)) <= {0, 255}


def test_querformat_bleibt_querformat(blatt):
    quer = blatt[0][:1970]  # ungefähr Marschbuch-Seitenverhältnis
    foto, _ = td.handyfoto(quer, np.random.default_rng(7), winkel=-3.0)
    ergebnis = verarbeiten(foto)
    h, w = ergebnis.bild.shape
    assert h == 2480 and w <= 3508  # passt auf A4 quer
    assert abs(w / h - 2480 / 1970) < 0.05


@pytest.mark.parametrize("breite, hoehe, format, erwartet", [
    (2000, 2830, "auto", ((2480, 3508), (2480, 3508))),      # fast A4 -> exakt A4
    (2830, 2000, "auto", ((3508, 2480), (3508, 2480))),      # A4 quer
    (1000, 1000, "auto", ((2480, 2480), (2480, 2480))),      # quadratisch: Verhältnis bleibt
    (1000, 1000, "A4", ((2480, 2480), (2480, 3508))),        # auf A4-Seite gesetzt
    (1234, 2345, "original", ((1234, 2345), (1234, 2345))),
])
def test_zielgroesse(breite, hoehe, format, erwartet):
    assert zielgroesse(breite, hoehe, format, 300) == erwartet


def test_exif_drehung_wird_beachtet(tmp_path):
    bild = Image.new("RGB", (300, 200), "white")
    exif = Image.Exif()
    exif[0x0112] = 6  # Handy wurde hochkant gehalten
    bild.save(tmp_path / "foto.jpg", exif=exif)
    (seite,) = seiten_laden(tmp_path / "foto.jpg")
    assert seite.shape[:2] == (300, 200)


def test_stapel_mit_unterordnern_und_pdf(tmp_path, blatt, handyfoto):
    eingabe = tmp_path / "Noten"
    (eingabe / "Marsch").mkdir(parents=True)
    cv2.imwrite(str(eingabe / "Marsch" / "Seite 1.jpg"), handyfoto)
    cv2.imwrite(str(eingabe / "Marsch" / "Seite 1.png"), handyfoto)  # gleicher Name, andere Endung
    cv2.imwrite(str(eingabe / "Marsch" / "Seite 10.jpg"), handyfoto)
    (eingabe / "notizen.txt").write_text("wird ignoriert")
    z = stapel_verarbeiten([eingabe], jobs=2)
    ziel = tmp_path / "Noten_bereinigt"
    assert z.ziel == ziel and not z.fehler and z.seiten == 3
    assert {p.name for p in (ziel / "Marsch").iterdir()} == {"Seite 1_jpg.png", "Seite 1_png.png", "Seite 10.png"}
    assert (ziel / "Marsch.pdf").read_bytes().startswith(b"%PDF")
    # Zweiter Lauf mit "nur neue" überspringt alles
    z2 = stapel_verarbeiten([eingabe], nur_neue=True, jobs=1)
    assert z2.uebersprungen == 3


def test_nur_pdf_und_pdf_als_eingabe(tmp_path, handyfoto):
    pytest.importorskip("pypdfium2")
    cv2.imwrite(str(tmp_path / "a.jpg"), handyfoto)
    z = stapel_verarbeiten([tmp_path / "a.jpg"], tmp_path / "aus", Einstellungen(modus="hart"),
                           pdf="datei", nur_pdf=True)
    assert [p.name for p in (tmp_path / "aus").iterdir()] == ["a.pdf"]
    assert len(z.pdfs) == 1
    # Das erzeugte PDF lässt sich wieder einlesen und erneut bereinigen
    (seite,) = seiten_laden(tmp_path / "aus" / "a.pdf")
    assert abs(seite.shape[0] - 3508) <= 1 and abs(seite.shape[1] - 2480) <= 1


def test_kommandozeile(tmp_path, handyfoto, capsys):
    cv2.imwrite(str(tmp_path / "foto.jpg"), handyfoto)
    assert cli_main([str(tmp_path / "foto.jpg"), "-o", str(tmp_path / "aus"), "--pdf", "aus"]) == 0
    assert (tmp_path / "aus" / "foto.png").exists()
    assert "Blatt entzerrt" in capsys.readouterr().out


def test_kaputte_datei_stoppt_stapel_nicht(tmp_path, handyfoto):
    cv2.imwrite(str(tmp_path / "gut.jpg"), handyfoto)
    (tmp_path / "kaputt.jpg").write_bytes(b"kein Bild")
    z = stapel_verarbeiten([tmp_path], tmp_path / "aus", pdf="aus", jobs=1)
    assert z.seiten == 1 and len(z.fehler) == 1
