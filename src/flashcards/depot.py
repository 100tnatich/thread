"""Dépôt de fichiers par cours: on dépose (à l'école), on génère plus tard (à la maison).

Un compte étendu dépose des PDF, images, textes ou .docx depuis l'interface web.
Rien n'est envoyé à une IA: les fichiers attendent sur le disque. Plus tard,
`/generer-questions depot` (Claude Code, sans API) les récupère avec
`flashcards depot recuperer`, génère les questions, puis `flashcards depot traite`.

Les fichiers vivent sous `FLASHCARDS_DEPOT` (défaut `inbox/depot`), un dossier par
cours. Le nom envoyé par le client n'est JAMAIS un chemin: il sert à l'affichage,
le fichier est écrit sous un nom nettoyé préfixé par son identifiant.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .profiles import slug

SUFFIXES_ACCEPTES = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".txt", ".md", ".docx"}
)
# Signatures réelles: l'extension est déclarative et ne prouve rien.
SIGNATURES = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".docx": (b"PK\x03\x04",),
}
MAX_OCTETS_FICHIER = 20 * 1024 * 1024
# Plafond du dossier entier: un compte ne doit pas pouvoir remplir le disque.
MAX_OCTETS_TOTAL = 1024 * 1024 * 1024
MAX_FICHIERS = 500


class DepotRefuse(RuntimeError):
    """Fichier refusé (format, taille, quota…): le message est affichable tel quel."""


@dataclass(frozen=True)
class Depot:
    id: int
    cours: str
    nom_original: str
    chemin: str
    taille: int
    depose_par: str
    depose_le: str
    traite_le: str | None


def dossier_depot() -> Path:
    return Path(os.environ.get("FLASHCARDS_DEPOT") or "inbox/depot")


def _depuis_ligne(r: sqlite3.Row) -> Depot:
    return Depot(
        id=r["id"], cours=r["cours"], nom_original=r["nom_original"], chemin=r["chemin"],
        taille=r["taille"], depose_par=r["depose_par"], depose_le=r["depose_le"],
        traite_le=r["traite_le"],
    )


def nom_sur(nom: str) -> str:
    """Nom de fichier inoffensif: pas de dossier, que des caractères simples."""
    base = Path(nom or "fichier").name
    suffixe = Path(base).suffix.lower()
    racine = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(base).stem).strip("-._") or "fichier"
    return f"{racine[:80]}{suffixe}"


def verifier(nom: str, donnees: bytes) -> str:
    """Contrôle le format et la taille; renvoie le suffixe. Lève `DepotRefuse`."""
    suffixe = Path(nom or "").suffix.lower()
    if suffixe not in SUFFIXES_ACCEPTES:
        raise DepotRefuse(
            f"Format non accepté pour « {nom} »: PDF, image (png, jpg, webp, gif), "
            ".txt, .md ou .docx."
        )
    if not donnees:
        raise DepotRefuse(f"« {nom} » est vide.")
    if len(donnees) > MAX_OCTETS_FICHIER:
        raise DepotRefuse(
            f"« {nom} » est trop lourd: maximum {MAX_OCTETS_FICHIER // 1024 // 1024} Mo."
        )
    signatures = SIGNATURES.get(suffixe)
    if signatures and not any(donnees.startswith(s) for s in signatures):
        raise DepotRefuse(f"Le contenu de « {nom} » ne correspond pas à un fichier {suffixe}.")
    return suffixe


def enregistrer(
    conn: sqlite3.Connection, cours: str, nom: str, donnees: bytes, par: str,
    *, maintenant: datetime | None = None,
) -> Depot:
    """Écrit le fichier sur le disque et le note en base. Le cours doit exister."""
    if conn.execute("SELECT 1 FROM cours WHERE nom = ?", (cours,)).fetchone() is None:
        raise DepotRefuse(f"Cours inconnu: « {cours} ». Crée-le d'abord.")
    verifier(nom, donnees)

    nb, total = conn.execute("SELECT COUNT(*), COALESCE(SUM(taille), 0) FROM depots").fetchone()
    if nb >= MAX_FICHIERS or total + len(donnees) > MAX_OCTETS_TOTAL:
        raise DepotRefuse(
            "Le dépôt est plein. Supprime des fichiers déjà traités avant d'en ajouter."
        )

    maintenant = maintenant or datetime.now(timezone.utc)
    # Ligne d'abord (pour l'identifiant), fichier ensuite; la ligne est retirée si
    # l'écriture échoue, pour ne jamais référencer un fichier absent.
    cur = conn.execute(
        "INSERT INTO depots (cours, nom_original, chemin, taille, depose_par, depose_le)"
        " VALUES (?, ?, '', ?, ?, ?)",
        (cours, Path(nom).name[:200], len(donnees), par, maintenant.isoformat()),
    )
    depot_id = cur.lastrowid
    relatif = f"{slug(cours) or 'cours'}/{depot_id}-{nom_sur(nom)}"
    cible = dossier_depot() / relatif
    try:
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_bytes(donnees)
        try:
            cible.chmod(0o600)
        except OSError:
            pass
        conn.execute("UPDATE depots SET chemin = ? WHERE id = ?", (relatif, depot_id))
        conn.commit()
    except Exception:
        conn.rollback()
        cible.unlink(missing_ok=True)
        raise
    return lire(conn, depot_id)


def lire(conn: sqlite3.Connection, depot_id: int) -> Depot:
    r = conn.execute("SELECT * FROM depots WHERE id = ?", (depot_id,)).fetchone()
    if r is None:
        raise DepotRefuse(f"Dépôt inconnu: {depot_id}.")
    return _depuis_ligne(r)


def lister(
    conn: sqlite3.Connection, *, cours: str | None = None, a_traiter: bool = False
) -> list[Depot]:
    sql, args = "SELECT * FROM depots WHERE 1=1", []
    if cours:
        sql += " AND cours = ?"
        args.append(cours)
    if a_traiter:
        sql += " AND traite_le IS NULL"
    return [_depuis_ligne(r) for r in conn.execute(sql + " ORDER BY cours, id", args)]


def chemin_complet(depot: Depot) -> Path:
    """Chemin du fichier, en refusant tout ce qui sortirait du dossier de dépôt."""
    racine = dossier_depot().resolve()
    complet = (racine / depot.chemin).resolve()
    if racine not in complet.parents:
        raise DepotRefuse("Chemin de dépôt invalide.")
    return complet


def supprimer(conn: sqlite3.Connection, depot_id: int) -> None:
    depot = lire(conn, depot_id)
    chemin_complet(depot).unlink(missing_ok=True)
    conn.execute("DELETE FROM depots WHERE id = ?", (depot_id,))
    conn.commit()


def marquer_traite(
    conn: sqlite3.Connection, depot_id: int, *, maintenant: datetime | None = None
) -> None:
    lire(conn, depot_id)
    conn.execute(
        "UPDATE depots SET traite_le = ? WHERE id = ?",
        ((maintenant or datetime.now(timezone.utc)).isoformat(), depot_id),
    )
    conn.commit()


def recuperer(conn: sqlite3.Connection, depot_id: int, vers: Path) -> Path:
    """Copie le fichier dans `vers` (ex. `inbox/`) sous son nom d'origine nettoyé.

    N'écrase jamais un fichier existant: un nom pris reçoit l'identifiant en préfixe.
    """
    depot = lire(conn, depot_id)
    source = chemin_complet(depot)
    if not source.is_file():
        raise DepotRefuse(f"Le fichier du dépôt {depot_id} est introuvable sur le disque.")
    vers.mkdir(parents=True, exist_ok=True)
    cible = vers / nom_sur(depot.nom_original)
    if cible.exists():
        cible = vers / f"{depot.id}-{cible.name}"
    shutil.copyfile(source, cible)
    return cible
