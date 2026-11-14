"""Noten-Scanner Server: Notenerkennung, PDF-Satz und Vereins-Bibliothek.

Start lokal:   uvicorn app:app --port 7860
Umgebung:      VEREIN_KENNWORT (Pflicht im Internet), BIBLIOTHEK_REPO + HF_TOKEN (Bibliothek bei
               Hugging Face, sonst Ordner ./bibliothek), ERLAUBTE_URSPRUENGE (CORS, Standard "*")
"""
from __future__ import annotations

import hmac
import os
import queue
import urllib.parse
import shutil
import tempfile
import threading
import time
import traceback
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

import ablage as ablage_modul

VERSION = "1.0.0"
KENNWORT = os.environ.get("VEREIN_KENNWORT", "")
MAX_SEITEN = 12
MAX_BYTES = 20 * 1024 * 1024
AUFBEWAHRUNG = 6 * 3600                     # Aufträge (und ihre Seitenbilder) 6 Stunden behalten
HIER = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Noten-Scanner Server", version=VERSION)
app.add_middleware(CORSMiddleware,
                   allow_origins=[u.strip() for u in os.environ.get("ERLAUBTE_URSPRUENGE", "*").split(",")],
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["Content-Disposition"])


IM_INTERNET = bool(os.environ.get("SPACE_ID"))   # Hugging Face setzt SPACE_ID in jedem Space


def pruefen(request: Request):
    if not KENNWORT:
        if IM_INTERNET:                             # nie offen im Internet: lieber gar nicht arbeiten
            raise HTTPException(503, "Auf dem Server ist kein Vereinskennwort (VEREIN_KENNWORT) eingerichtet")
        return
    gegeben = request.headers.get("x-kennwort", "")
    if not hmac.compare_digest(gegeben.encode("utf-8"), KENNWORT.encode("utf-8")):
        raise HTTPException(401, "Falsches Vereinskennwort")


# ------------------------------------------------------------------------------------------------
# Aufträge: Die Erkennung läuft nacheinander in einem Hintergrund-Thread (sie braucht viel Speicher)
# ------------------------------------------------------------------------------------------------
class Auftraege:
    def __init__(self):
        self.liste: dict[str, dict] = {}
        self.schlange: queue.Queue = queue.Queue()
        self.bereit = threading.Event()
        self.startfehler = ""
        self.sperre = threading.Lock()
        threading.Thread(target=self._arbeiter, daemon=True).start()

    def _aufwaermen(self):
        """Modelle laden, damit der erste echte Auftrag nicht doppelt so lange dauert."""
        import erkennung
        probe = os.path.join(HIER, "aufwaermen.png")
        if os.path.exists(probe):
            erkennung.seite_lesen(probe)

    def _arbeiter(self):
        try:
            self._aufwaermen()
        except Exception as fehler:                       # Server bleibt erreichbar, meldet den Fehler
            self.startfehler = f"{type(fehler).__name__}: {fehler}"
            traceback.print_exc()
        self.bereit.set()
        while True:
            aid = self.schlange.get()
            a = self.liste.get(aid)
            if a is None:
                continue
            a["status"] = "laeuft"
            try:
                import erkennung

                def melden(text, anteil):
                    a["text"], a["fortschritt"] = text, round(anteil, 3)
                a["ergebnis"] = erkennung.stueck_lesen(a["seiten"], a["titel"], a["stimme"], melden)
                a["status"], a["text"], a["fortschritt"] = "fertig", "Fertig", 1.0
            except Exception as fehler:
                traceback.print_exc()
                a["status"], a["text"] = "fehler", f"Die Erkennung ist fehlgeschlagen: {fehler}"
            finally:
                a["fertig_um"] = time.time()
            self._aufraeumen()

    def _aufraeumen(self):
        jetzt = time.time()
        with self.sperre:
            for aid, a in list(self.liste.items()):
                if a.get("fertig_um") and jetzt - a["fertig_um"] > AUFBEWAHRUNG:
                    shutil.rmtree(a["ordner"], ignore_errors=True)
                    del self.liste[aid]

    def neu(self, seiten: list[bytes], titel: str, stimme: str) -> str:
        aid = uuid.uuid4().hex[:12]
        ordner = tempfile.mkdtemp(prefix=f"auftrag-{aid}-")
        pfade = []
        for k, daten in enumerate(seiten):
            pfad = os.path.join(ordner, f"seite_{k}.png")
            with open(pfad, "wb") as f:
                f.write(daten)
            pfade.append(pfad)
        with self.sperre:
            self.liste[aid] = {"id": aid, "status": "wartet", "text": "Wartet …", "fortschritt": 0.0,
                               "seiten": pfade, "ordner": ordner, "titel": titel, "stimme": stimme,
                               "erstellt": time.time()}
        self.schlange.put(aid)
        return aid

    def info(self, aid: str) -> dict:
        a = self.liste.get(aid)
        if a is None:
            raise KeyError(aid)
        antwort = {k: a[k] for k in ("id", "status", "text", "fortschritt")}
        if a["status"] == "wartet":
            with self.sperre:
                alle = list(self.liste.values())
            vor = sum(1 for b in alle if b["status"] in ("wartet", "laeuft") and b["erstellt"] < a["erstellt"])
            antwort["text"] = "Wartet …" if vor == 0 else f"Wartet ({vor} Auftrag/Aufträge vorher)"
            if not self.bereit.is_set():
                antwort["text"] = "Server startet, Erkennung wird geladen …"
        if a["status"] == "fertig":
            antwort["ergebnis"] = a["ergebnis"]
        return antwort


AUFTRAEGE = Auftraege()
ABLAGE = None
ABLAGE_SPERRE = threading.Lock()


def ablage():
    """Bibliothek beim ersten Gebrauch öffnen (genau einmal, auch bei gleichzeitigen Anfragen –
    zwei Exemplare mit eigenem Inhaltsverzeichnis würden sich gegenseitig überschreiben)."""
    global ABLAGE
    with ABLAGE_SPERRE:
        if ABLAGE is None:
            try:
                ABLAGE = ablage_modul.ablage_aus_umgebung()
            except Exception as fehler:
                raise HTTPException(503, f"Bibliothek nicht erreichbar: {fehler}")
        return ABLAGE


def _bildtyp(daten: bytes) -> str:
    return "image/jpeg" if daten[:3] == b"\xff\xd8\xff" else "image/png"


async def _seiten_lesen(seiten: list[UploadFile], nur_png: bool = False) -> list[bytes]:
    """Hochgeladene Seiten lesen; höchstens MAX_BYTES je Seite (nicht erst alles in den Speicher)."""
    if not seiten or len(seiten) > MAX_SEITEN:
        raise HTTPException(400, f"Bitte 1 bis {MAX_SEITEN} Seiten schicken")
    daten = []
    for s in seiten:
        inhalt = await s.read(MAX_BYTES + 1)
        if len(inhalt) > MAX_BYTES:
            raise HTTPException(413, "Eine Seite ist zu groß")
        png = inhalt[:8] == b"\x89PNG\r\n\x1a\n"
        if not (png or (not nur_png and inhalt[:3] == b"\xff\xd8\xff")):
            raise HTTPException(400, "Bitte Seiten als PNG schicken" if nur_png else "Bitte Seiten als PNG oder JPEG schicken")
        daten.append(inhalt)
    return daten


# ------------------------------------------------------------------------------------------------
# Schnittstelle
# ------------------------------------------------------------------------------------------------
@app.get("/api/status")
def status():
    return {"version": VERSION, "bereit": AUFTRAEGE.bereit.is_set(), "startfehler": AUFTRAEGE.startfehler,
            "kennwort": bool(KENNWORT)}


@app.post("/api/anmelden")
def anmelden(request: Request):
    pruefen(request)
    return {"ok": True}


@app.post("/api/lesen")
async def lesen(request: Request, seiten: list[UploadFile] = File(...), titel: str = Form(""), stimme: str = Form("")):
    pruefen(request)
    daten = await _seiten_lesen(seiten)
    aid = AUFTRAEGE.neu(daten, titel[:200], stimme[:200])
    return {"auftrag": aid}


@app.get("/api/auftrag/{aid}")
def auftrag(aid: str, request: Request):
    pruefen(request)
    try:
        return AUFTRAEGE.info(aid)
    except KeyError:
        raise HTTPException(404, "Auftrag nicht gefunden (abgelaufen oder Server neu gestartet)")


@app.get("/api/auftrag/{aid}/seite/{k}")
def auftrag_seite(aid: str, k: int, request: Request):
    pruefen(request)
    a = AUFTRAEGE.liste.get(aid)
    if a is None or not 0 <= k < len(a["seiten"]):
        raise HTTPException(404, "Seite nicht gefunden")
    try:
        with open(a["seiten"][k], "rb") as f:
            daten = f.read()
    except OSError:                                 # gerade abgelaufen und aufgeräumt
        raise HTTPException(404, "Seite nicht gefunden")
    return Response(daten, media_type=_bildtyp(daten))


class SatzAnfrage(BaseModel):
    musicxml: str
    transponieren: str = ""
    dateiname: str = "Noten"


@app.post("/api/pdf")
def pdf(anfrage: SatzAnfrage, request: Request):
    pruefen(request)
    import satz
    if anfrage.transponieren and not all(c in "+-PMmAd0123456789" for c in anfrage.transponieren):
        raise HTTPException(400, "Ungültiges Intervall")
    try:
        daten = satz.pdf_erzeugen(anfrage.musicxml, anfrage.transponieren)
    except ValueError as fehler:
        raise HTTPException(400, str(fehler))
    name = "".join(c for c in anfrage.dateiname if c.isalnum() or c in " -_.")[:80].strip() or "Noten"
    # Kopfzeilen dürfen nur Latin-1 enthalten: einfacher Name als Ersatz, echter Name nach RFC 5987
    einfach = "".join(c if c.isascii() else "_" for c in name)
    kopf = f'attachment; filename="{einfach}.pdf"; filename*=UTF-8\'\'{urllib.parse.quote(name + ".pdf")}'
    return Response(daten, media_type="application/pdf", headers={"Content-Disposition": kopf})


@app.get("/api/bibliothek")
def bibliothek(request: Request):
    pruefen(request)
    return ablage().liste()


class Stueck(BaseModel):
    titel: str
    stimme: str = ""
    instrument: str = ""
    komponist: str = ""
    notiz: str = ""
    musicxml: str
    auftrag: str = ""
    unsicher: int = 0
    korrigiert: int = 0
    takte: list = []


@app.post("/api/bibliothek")
def speichern(stueck: Stueck, request: Request, sid: str | None = None):
    pruefen(request)
    if len(stueck.musicxml) > 5_000_000:
        raise HTTPException(413, "Stück zu groß")
    seiten = None
    if stueck.auftrag:
        a = AUFTRAEGE.liste.get(stueck.auftrag)
        if a is not None:
            seiten = []
            for pfad in a["seiten"]:
                with open(pfad, "rb") as f:
                    seiten.append(f.read())
    meta = stueck.model_dump(exclude={"musicxml", "auftrag", "takte"})
    if seiten is not None:
        meta["seiten"] = len(seiten)
    try:
        neue_id = ablage().speichern(meta, stueck.musicxml, seiten, sid, takte=stueck.takte)
    except KeyError:
        raise HTTPException(404, "Stück nicht gefunden")
    return {"id": neue_id, "bilder": seiten is not None}


@app.post("/api/bibliothek/{sid}/seiten")
async def stueck_seiten(sid: str, request: Request, seiten: list[UploadFile] = File(...)):
    """Seitenbilder nachreichen (falls der Erkennungsauftrag auf dem Server schon abgelaufen war)."""
    pruefen(request)
    daten = await _seiten_lesen(seiten, nur_png=True)
    try:
        meta, musicxml = ablage().laden(sid)
        ablage().speichern({"seiten": len(daten)}, musicxml, daten, sid)
    except KeyError:
        raise HTTPException(404, "Stück nicht gefunden")
    return {"ok": True}


@app.get("/api/bibliothek/{sid}")
def stueck_laden(sid: str, request: Request):
    pruefen(request)
    try:
        meta, musicxml = ablage().laden(sid)
        takte = ablage().takte(sid)
    except KeyError:
        raise HTTPException(404, "Stück nicht gefunden")
    return {"meta": meta, "musicxml": musicxml, "takte": takte}


@app.get("/api/bibliothek/{sid}/seite/{k}")
def stueck_seite(sid: str, k: int, request: Request):
    pruefen(request)
    try:
        daten = ablage().seite(sid, k)
    except KeyError:
        raise HTTPException(404, "Seite nicht gefunden")
    return Response(daten, media_type=_bildtyp(daten))


@app.delete("/api/bibliothek/{sid}")
def stueck_loeschen(sid: str, request: Request):
    pruefen(request)
    try:
        ablage().loeschen(sid)
    except KeyError:
        raise HTTPException(404, "Stück nicht gefunden")
    return {"ok": True}


@app.get("/")
def start():
    return JSONResponse({"dienst": "Noten-Scanner Server", "version": VERSION,
                         "hinweis": "Die App findest du unter der GitHub-Pages-Adresse des Vereins."})
