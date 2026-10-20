"""Import des questions (JSON) vers la base SQLite.

Accepte les fichiers du prototype 1 (`generate`) comme ceux du workflow manuel
(Claude Code, voir `manuel.py`). Deux protections contre les doublons:
- par `id`: réimporter un fichier n'écrase jamais l'état FSRS d'une carte;
- par contenu: une question identique (même énoncé, mêmes réponses) à une
  carte déjà présente dans le même cours est ignorée, même sous un autre `id`.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import manuel
from .db import ensure_cours
from .models import GenerationResult


@dataclass(frozen=True)
class ImportReport:
    fichier: str
    importees: int
    deja_presentes: int
    cours: list[str]
    doublons_contenu: int = 0


def concepts_voisin(fichier: Path) -> Path | None:
    """`x_questions.json` -> `x_concepts.json` (et `x.questions.json` -> `x.concepts.json`)."""
    for ancien, nouveau in (("_questions.json", "_concepts.json"),
                            (".questions.json", ".concepts.json")):
        if fichier.name.endswith(ancien):
            candidat = fichier.with_name(fichier.name[: -len(ancien)] + nouveau)
            return candidat if candidat.exists() else None
    return None


def categories_de(concepts) -> dict[str, str]:
    """concept_id -> catégorie, depuis une liste de concepts (ou rien)."""
    return {c.id: c.categorie for c in concepts.concepts} if concepts else {}


def empreintes_en_base(conn: sqlite3.Connection, cours: str) -> set[str]:
    """Empreintes de contenu des cartes déjà présentes dans ce cours."""
    empreintes = set()
    for row in conn.execute("SELECT payload FROM cards WHERE cours = ?", (cours,)):
        try:
            empreintes.add(manuel.empreinte_contenu(json.loads(row["payload"])))
        except (json.JSONDecodeError, TypeError):
            continue  # une carte au payload illisible ne doit pas bloquer l'import
    return empreintes


def marquer_deja_en_base(conn: sqlite3.Connection, analyse: manuel.Analyse) -> None:
    """Complète l'aperçu d'un rapport: quelles questions existent déjà en base.

    Ne modifie pas la base. Sert à l'aperçu avant import (interface web).
    """
    if analyse.resultat is None:
        return
    connues = empreintes_en_base(conn, analyse.resultat.cours)
    ids = {row["id"] for row in conn.execute("SELECT id FROM cards")}
    # Le résultat n'existe que si toutes les questions sont valides: l'aperçu
    # et la liste de questions ont alors la même longueur et le même ordre.
    for apercu, question in zip(analyse.rapport.apercu, analyse.resultat.questions):
        if apercu.statut == "doublon_fichier":
            continue
        if question.id in ids or manuel.empreinte_contenu(question.model_dump()) in connues:
            apercu.statut = "deja_en_base"


def importer_resultat(
    conn: sqlite3.Connection,
    resultat: GenerationResult,
    *,
    fichier: str,
    now: datetime | None = None,
    categories: dict[str, str] | None = None,
) -> ImportReport:
    """Écrit les questions validées. Idempotent, ne touche jamais à l'état FSRS.

    `categories` (concept_id -> catégorie du concept) renseigne la catégorie
    des cartes quand on connaît la liste de concepts: c'est elle qui fait
    peser le vocabulaire 0,1 au lieu du poids de son type.
    """
    now = now or datetime.now(timezone.utc)
    importees = deja = doublons = 0
    cours: set[str] = set()
    connues: dict[str, set[str]] = {}

    for question in resultat.questions:
        cours.add(question.cours)
        ensure_cours(conn, question.cours)

        if conn.execute("SELECT 1 FROM cards WHERE id = ?", (question.id,)).fetchone():
            deja += 1
            continue

        if question.cours not in connues:
            connues[question.cours] = empreintes_en_base(conn, question.cours)
        empreinte = manuel.empreinte_contenu(question.model_dump())
        if empreinte in connues[question.cours]:
            doublons += 1
            continue

        conn.execute(
            "INSERT INTO cards ("
            " id, concept_id, cours, type, payload,"
            " source_fichier, source_page, imported_at, poids, categorie"
            ") VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                question.id,
                question.concept_id,
                question.cours,
                question.type,
                question.model_dump_json(),
                question.source.fichier,
                question.source.page,
                now.isoformat(),
                question.poids,
                (categories or {}).get(question.concept_id),
            ),
        )
        connues[question.cours].add(empreinte)
        importees += 1

    conn.commit()
    return ImportReport(
        fichier=fichier,
        importees=importees,
        deja_presentes=deja,
        cours=sorted(cours),
        doublons_contenu=doublons,
    )


def import_questions(
    conn: sqlite3.Connection,
    path: Path,
    *,
    now: datetime | None = None,
) -> ImportReport:
    """Importe un fichier de questions (`<nom>.questions.json` ou brut).

    Strict: une seule question invalide et rien n'est importé, avec le détail
    des erreurs — un import à moitié fait est plus dur à rattraper qu'un refus.
    """
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable: {path}")

    voisin = concepts_voisin(path)
    analyse = manuel.analyser(
        path.read_bytes(), nom=path.name,
        concepts_contenu=voisin.read_bytes() if voisin else None,
        concepts_nom=voisin.name if voisin else None,
    )
    if analyse.rapport.nature == "questions" and analyse.resultat is not None:
        return importer_resultat(
            conn, analyse.resultat, fichier=path.name, now=now,
            categories=categories_de(analyse.concepts),
        )

    raise RuntimeError(
        f"{path} n'est pas un fichier de questions valide:\n"
        + manuel.rapport_en_texte(analyse.rapport)
    )
