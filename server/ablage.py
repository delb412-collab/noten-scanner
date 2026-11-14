"""Vereins-Bibliothek: Stücke (Metadaten, MusicXML, Seitenbilder) dauerhaft ablegen.

LokaleAblage: Ordner auf der Platte (zum Testen, oder auf einem eigenen Rechner).
HFAblage: privates Dataset-Repository bei Hugging Face (für den kostenlosen Space, dessen Platte
bei jedem Neustart leer ist). Beide haben dieselbe Schnittstelle.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

ERLAUBTE_FELDER = ("titel", "stimme", "instrument", "komponist", "notiz", "seiten", "unsicher", "korrigiert")


def _meta_saeubern(meta: dict) -> dict:
    sauber = {k: meta[k] for k in ERLAUBTE_FELDER if k in meta}
    for k in ("titel", "stimme", "instrument", "komponist", "notiz"):
        if k in sauber:
            sauber[k] = str(sauber[k])[:200]
    return sauber


class Ablage:
    def liste(self) -> list[dict]: ...
    def laden(self, sid: str) -> tuple[dict, str]: ...
    def seite(self, sid: str, k: int) -> bytes: ...
    def speichern(self, meta: dict, musicxml: str, seiten: list[bytes] | None, sid: str | None = None,
                  takte: list | None = None) -> str: ...
    def loeschen(self, sid: str) -> None: ...
    def takte(self, sid: str) -> list: ...

    @staticmethod
    def _neue_id() -> str:
        return time.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:8]

    @staticmethod
    def _gueltig(sid: str) -> bool:
        return bool(sid) and all(c.isalnum() or c == "-" for c in sid) and len(sid) < 64


class LokaleAblage(Ablage):
    def __init__(self, ordner: str):
        self.ordner = ordner
        os.makedirs(os.path.join(ordner, "stuecke"), exist_ok=True)
        self.sperre = threading.Lock()

    def _index_pfad(self):
        return os.path.join(self.ordner, "index.json")

    def _index(self) -> dict:
        try:
            with open(self._index_pfad(), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def _index_schreiben(self, index: dict):
        tmp = self._index_pfad() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self._index_pfad())

    def liste(self):
        return sorted(self._index().values(), key=lambda m: m.get("geaendert", ""), reverse=True)

    def laden(self, sid):
        if not self._gueltig(sid) or sid not in self._index():
            raise KeyError(sid)
        with open(os.path.join(self.ordner, "stuecke", sid, "stueck.musicxml"), encoding="utf-8") as f:
            return self._index()[sid], f.read()

    def seite(self, sid, k):
        if not self._gueltig(sid):
            raise KeyError(sid)
        pfad = os.path.join(self.ordner, "stuecke", sid, f"seite_{int(k)}.png")
        if not os.path.exists(pfad):
            raise KeyError(sid)
        with open(pfad, "rb") as f:
            return f.read()

    def takte(self, sid):
        if not self._gueltig(sid):
            raise KeyError(sid)
        try:
            with open(os.path.join(self.ordner, "stuecke", sid, "takte.json"), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return []

    def speichern(self, meta, musicxml, seiten, sid=None, takte=None):
        with self.sperre:
            index = self._index()
            if sid is not None and (not self._gueltig(sid) or sid not in index):
                raise KeyError(sid)
            sid = sid or self._neue_id()
            ordner = os.path.join(self.ordner, "stuecke", sid)
            os.makedirs(ordner, exist_ok=True)
            with open(os.path.join(ordner, "stueck.musicxml"), "w", encoding="utf-8") as f:
                f.write(musicxml)
            if takte is not None:
                with open(os.path.join(ordner, "takte.json"), "w", encoding="utf-8") as f:
                    json.dump(takte, f, ensure_ascii=False)
            alt = index.get(sid, {})
            if seiten is not None:
                for name in os.listdir(ordner):
                    if name.startswith("seite_"):
                        os.remove(os.path.join(ordner, name))
                for k, daten in enumerate(seiten):
                    with open(os.path.join(ordner, f"seite_{k}.png"), "wb") as f:
                        f.write(daten)
            eintrag = {**alt, **_meta_saeubern(meta), "id": sid,
                       "erstellt": alt.get("erstellt", time.strftime("%Y-%m-%d %H:%M")),
                       "geaendert": time.strftime("%Y-%m-%d %H:%M")}
            if seiten is not None:
                eintrag["bilder"] = len(seiten)
            index[sid] = eintrag
            self._index_schreiben(index)
            return sid

    def loeschen(self, sid):
        with self.sperre:
            index = self._index()
            if not self._gueltig(sid) or sid not in index:
                raise KeyError(sid)
            ordner = os.path.join(self.ordner, "stuecke", sid)
            for name in os.listdir(ordner):
                os.remove(os.path.join(ordner, name))
            os.rmdir(ordner)
            del index[sid]
            self._index_schreiben(index)


class HFAblage(Ablage):
    """Bibliothek in einem privaten Hugging-Face-Dataset. Das Inhaltsverzeichnis wird beim Start
    einmal geladen und danach im Speicher gehalten (nur dieser Server schreibt)."""

    def __init__(self, repo_id: str, token: str, cache: str = "/tmp/bibliothek"):
        from huggingface_hub import HfApi
        self.api = HfApi(token=token)
        self.repo = repo_id
        self.token = token
        self.cache = cache
        self.sperre = threading.Lock()
        os.makedirs(cache, exist_ok=True)
        self.api.create_repo(repo_id, repo_type="dataset", private=True, exist_ok=True)
        self.index = self._index_laden()

    def _datei(self, pfad: str) -> str:
        from huggingface_hub import hf_hub_download
        return hf_hub_download(self.repo, pfad, repo_type="dataset", token=self.token, cache_dir=self.cache)

    def _index_laden(self) -> dict:
        """Inhaltsverzeichnis laden. Nur wenn es noch keines gibt (neue Bibliothek), ist es leer –
        bei jedem anderen Fehler (Netz, Hugging Face nicht erreichbar) wird abgebrochen. Sonst würde
        das nächste Speichern ein fast leeres Verzeichnis schreiben und alle Stücke "vergessen"."""
        from huggingface_hub.errors import RemoteEntryNotFoundError
        try:
            pfad = self._datei("index.json")
        except RemoteEntryNotFoundError:               # nur "Datei gibt es (noch) nicht", nicht "offline"
            return {}
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)

    def liste(self):
        return sorted(self.index.values(), key=lambda m: m.get("geaendert", ""), reverse=True)

    def _vorhanden(self, pfad: str) -> str:
        """Datei aus der Bibliothek; fehlt sie dort, KeyError (-> 404 statt Serverfehler)."""
        from huggingface_hub.errors import RemoteEntryNotFoundError
        try:
            return self._datei(pfad)
        except RemoteEntryNotFoundError:
            raise KeyError(pfad)

    def laden(self, sid):
        if not self._gueltig(sid) or sid not in self.index:
            raise KeyError(sid)
        with open(self._vorhanden(f"stuecke/{sid}/stueck.musicxml"), encoding="utf-8") as f:
            return self.index[sid], f.read()

    def seite(self, sid, k):
        if not self._gueltig(sid) or sid not in self.index or int(k) < 0:
            raise KeyError(sid)
        with open(self._vorhanden(f"stuecke/{sid}/seite_{int(k)}.png"), "rb") as f:
            return f.read()

    def takte(self, sid):
        if not self._gueltig(sid) or sid not in self.index:
            raise KeyError(sid)
        try:
            with open(self._vorhanden(f"stuecke/{sid}/takte.json"), encoding="utf-8") as f:
                return json.load(f)
        except KeyError:                               # ältere Stücke ohne Taktbewertung
            return []

    def speichern(self, meta, musicxml, seiten, sid=None, takte=None):
        from huggingface_hub import CommitOperationAdd, CommitOperationDelete
        with self.sperre:
            if sid is not None and (not self._gueltig(sid) or sid not in self.index):
                raise KeyError(sid)
            sid = sid or self._neue_id()
            alt = self.index.get(sid, {})
            eintrag = {**alt, **_meta_saeubern(meta), "id": sid,
                       "erstellt": alt.get("erstellt", time.strftime("%Y-%m-%d %H:%M")),
                       "geaendert": time.strftime("%Y-%m-%d %H:%M")}
            ops = [CommitOperationAdd(f"stuecke/{sid}/stueck.musicxml", musicxml.encode("utf-8"))]
            if takte is not None:
                ops.append(CommitOperationAdd(f"stuecke/{sid}/takte.json", json.dumps(takte, ensure_ascii=False).encode("utf-8")))
            if seiten is not None:
                for k in range(len(seiten), int(alt.get("bilder", 0))):
                    ops.append(CommitOperationDelete(f"stuecke/{sid}/seite_{k}.png"))
                for k, daten in enumerate(seiten):
                    ops.append(CommitOperationAdd(f"stuecke/{sid}/seite_{k}.png", daten))
                eintrag["bilder"] = len(seiten)
            neuer_index = {**self.index, sid: eintrag}
            ops.append(CommitOperationAdd("index.json", json.dumps(neuer_index, ensure_ascii=False, indent=1).encode("utf-8")))
            self.api.create_commit(self.repo, ops, commit_message=f"Stück {eintrag.get('titel', sid)} gespeichert",
                                   repo_type="dataset")
            self.index = neuer_index
            return sid

    def loeschen(self, sid):
        from huggingface_hub import CommitOperationAdd, CommitOperationDelete
        with self.sperre:
            if not self._gueltig(sid) or sid not in self.index:
                raise KeyError(sid)
            neuer_index = {k: v for k, v in self.index.items() if k != sid}
            ops = [CommitOperationDelete(f"stuecke/{sid}/"),
                   CommitOperationAdd("index.json", json.dumps(neuer_index, ensure_ascii=False, indent=1).encode("utf-8"))]
            self.api.create_commit(self.repo, ops, commit_message=f"Stück {sid} gelöscht", repo_type="dataset")
            self.index = neuer_index


def ablage_aus_umgebung() -> Ablage:
    repo = os.environ.get("BIBLIOTHEK_REPO")
    token = os.environ.get("HF_TOKEN")
    if repo and token:
        return HFAblage(repo, token)
    return LokaleAblage(os.environ.get("BIBLIOTHEK_ORDNER", os.path.join(os.path.dirname(__file__), "bibliothek")))
