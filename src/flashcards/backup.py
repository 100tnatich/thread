"""Sauvegarde de la base SQLite.

Utilise l'API `Connection.backup()` de SQLite: la copie est cohérente même si
l'application écrit pendant la sauvegarde (contrairement à un simple `cp`).
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROTATION_JOURS = 14
INTERVALLE_SECONDES = 24 * 3600


def dossier_sauvegardes() -> Path:
    return Path(os.environ.get("FLASHCARDS_BACKUPS", "backups"))


def sauvegarder(db_path: Path, destination: Path | None = None) -> Path:
    """Copie cohérente de la base. Renvoie le chemin du fichier créé."""
    dossier = destination or dossier_sauvegardes()
    dossier.mkdir(parents=True, exist_ok=True)

    horodatage = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    cible = dossier / f"flashcards-{horodatage}.db"

    # La sauvegarde contient tous les cours et le hash du mot de passe:
    # lisible par le seul propriétaire, dès sa création.
    try:
        dossier.chmod(0o700)
    except OSError:
        pass
    os.close(os.open(cible, os.O_CREAT | os.O_WRONLY, 0o600))

    source = sqlite3.connect(db_path)
    copie = sqlite3.connect(cible)
    try:
        with copie:
            source.backup(copie)
    finally:
        source.close()
        copie.close()
    return cible


def purger(dossier: Path | None = None, jours: int = ROTATION_JOURS) -> list[Path]:
    """Supprime les sauvegardes plus vieilles que `jours`. Renvoie les supprimées."""
    dossier = dossier or dossier_sauvegardes()
    if not dossier.exists():
        return []

    limite = time.time() - jours * 86400
    supprimees = []
    for fichier in sorted(dossier.glob("flashcards-*.db")):
        if fichier.stat().st_mtime < limite:
            fichier.unlink()
            supprimees.append(fichier)
    return supprimees


def demarrer_planificateur(db_path: Path) -> threading.Thread:
    """Sauvegarde quotidienne en tâche de fond (thread daemon).

    Pas de cron ni de service séparé: pour une instance mono-utilisateur, un
    thread qui dort 24 h suffit et reste dans le conteneur de l'application.
    """

    def boucle() -> None:
        while True:
            try:
                sauvegarder(db_path)
                purger()
            except Exception as e:  # ne jamais tuer le thread de sauvegarde
                print(f"[backup] échec: {e}")
            time.sleep(INTERVALLE_SECONDES)

    fil = threading.Thread(target=boucle, daemon=True, name="sauvegarde")
    fil.start()
    return fil
