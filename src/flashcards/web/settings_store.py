"""Réglages modifiables depuis l'interface, stockés en base.

`config.toml` reste la valeur par défaut; la table `settings` la surcharge.
Permet de changer le plafond ou la rétention depuis le téléphone sans toucher
à un fichier sur le serveur.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import replace

from ..config import SchedulerConfig, load_config, load_scheduler_config

CLES_NUMERIQUES = {
    "plafond_quotidien": float,  # en points
    "jours_phase_finale": int,
    "buffer_jours_avant_test": int,
    "retention_normale": float,
    "retention_test": float,
    "entretien_quota_hebdo": float,
    "entretien_plafond_jour": float,
    "entretien_poids_max": float,
}
CLES_TEXTE = {"modele", "ordre_session"}

# Les poids sont rangés à plat dans `settings`: `poids_type.qcm`, `poids_categorie.vocabulaire`.
PREFIXE_POIDS_TYPE = "poids_type."
PREFIXE_POIDS_CATEGORIE = "poids_categorie."

ORDRE_PAR_DEFAUT = "melange"


def lire_tout(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["cle"]: r["valeur"] for r in conn.execute("SELECT cle, valeur FROM settings")}


def ecrire(conn: sqlite3.Connection, cle: str, valeur) -> None:
    if (
        cle not in CLES_NUMERIQUES and cle not in CLES_TEXTE
        and not cle.startswith((PREFIXE_POIDS_TYPE, PREFIXE_POIDS_CATEGORIE))
    ):
        raise RuntimeError(f"Réglage inconnu: {cle}")
    conn.execute(
        "INSERT INTO settings (cle, valeur) VALUES (?,?)"
        " ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
        (cle, str(valeur)),
    )
    conn.commit()


def config_effective(conn: sqlite3.Connection) -> SchedulerConfig:
    """config.toml surchargé par les réglages enregistrés en base."""
    base = load_scheduler_config()
    stockes = lire_tout(conn)
    surcharges: dict = {}
    for cle, cast in CLES_NUMERIQUES.items():
        if cle in stockes:
            surcharges[cle] = cast(stockes[cle])
    poids_type = dict(base.poids_type)
    poids_categorie = dict(base.poids_categorie)
    for cle, valeur in stockes.items():
        if cle.startswith(PREFIXE_POIDS_TYPE):
            poids_type[cle[len(PREFIXE_POIDS_TYPE):]] = float(valeur)
        elif cle.startswith(PREFIXE_POIDS_CATEGORIE):
            poids_categorie[cle[len(PREFIXE_POIDS_CATEGORIE):]] = float(valeur)
    surcharges["poids_type"] = poids_type
    surcharges["poids_categorie"] = poids_categorie
    return replace(base, **surcharges)


def modele_effectif(conn: sqlite3.Connection) -> str:
    return lire_tout(conn).get("modele") or load_config().model_name


def ordre_effectif(conn: sqlite3.Connection) -> str:
    return lire_tout(conn).get("ordre_session") or ORDRE_PAR_DEFAUT


def db_path() -> "os.PathLike[str]":
    """Chemin de la base (FLASHCARDS_DB est géré dans `config.load_config`)."""
    return load_scheduler_config().db_path
