---
title: Noten-Scanner Server
emoji: 🎺
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: agpl-3.0
---

# Noten-Scanner – Server

Notenerkennung, Notensatz (PDF) und Vereins-Bibliothek für die Noten-Scanner-App.
Wird automatisch aus dem GitHub-Repository des Vereins aktualisiert (Ordner `server/`,
`notenleser/`, `notenscanner/`).

Einstellungen im Space (Settings → Variables and secrets):

| Name | Art | Bedeutung |
|---|---|---|
| `VEREIN_KENNWORT` | Secret | Kennwort, das die App mitschickt |
| `HF_TOKEN` | Secret | Zugriffsschlüssel (Schreiben) für die private Bibliothek |
| `BIBLIOTHEK_REPO` | Variable | z. B. `name/noten-bibliothek` (privates Dataset, wird angelegt) |
| `ERLAUBTE_URSPRUENGE` | Variable | Adresse der App, z. B. `https://name.github.io` |

Die Notenerkennung baut auf [homr](https://github.com/liebharc/homr) auf (AGPL-3.0); dieser
Server steht deshalb ebenfalls unter der AGPL-3.0.
