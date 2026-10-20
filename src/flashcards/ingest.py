"""Orchestration du flux en deux temps: extraction puis génération.

La règle du prototype 1 tient toujours: les questions ne sont générées qu'à
partir de la liste de concepts **validée par l'élève**. La matière source est
rejointe à l'étape B uniquement pour que le LLM reprenne les valeurs et la
notation exactes du document — jamais pour y puiser des concepts non validés.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import TypeAdapter

from . import budget, llm
from .db import ensure_cours
from .models import (
    Concept,
    CoverageEntry,
    CoverageReport,
    FailedQuestion,
    Question,
    Source,
)
from .profiles import Profil

_QUESTION_ADAPTER = TypeAdapter(Question)

# Les concepts sont envoyés par lots (voir `llm.lots_de`): chaque lot reste
# une génération complète et autonome.
TAILLE_LOT = llm.TAILLE_LOT

# Refus qui ne se corrigent pas en passant au lot suivant: on arrête tout.
ARRETS_BUDGET = (
    budget.BudgetDepasse, budget.LLMDesactive, budget.DisjoncteurOuvert,
    budget.EntreeTropGrande,
)


@dataclass(frozen=True)
class ExtractionResult:
    concepts: list[Concept]
    avertissements: list[str]
    source: str
    profil: Profil


@dataclass
class GenerationSummary:
    questions: list = field(default_factory=list)
    questions_creees: int = 0
    echecs: list[FailedQuestion] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)
    couverture: CoverageReport | None = None
    profil: Profil | None = None


def _avertissements_profil(profil: Profil) -> list[str]:
    return [profil.avertissement] if profil.avertissement else []


def extraire(
    entree: llm.Entree,
    *,
    conn: sqlite3.Connection,
    modele: str | None = None,
    forcer: bool = False,
) -> ExtractionResult:
    """Étape A. N'écrit aucune carte: la liste doit d'abord être validée.

    `conn` sert au registre de consommation et au cache anti-doublon.
    """
    brut, profil = llm.extraire_concepts(
        entree, conn=conn, modele=modele, forcer=forcer
    )

    return ExtractionResult(
        concepts=brut.concepts,
        avertissements=_avertissements_profil(profil) + brut.avertissements,
        source=entree.nom_source(),
        profil=profil,
    )


def produire(
    entree: llm.Entree,
    concepts: list[Concept],
    *,
    conn: sqlite3.Connection,
    source: str | None = None,
    modele: str | None = None,
    forcer: bool = False,
    progression=None,
) -> GenerationSummary:
    """Étape B, sans persistance: génère les questions des concepts VALIDÉS.

    Les concepts sont envoyés par lots, et chaque question renvoyée est
    revérifiée contre la liste validée avant d'être retenue.
    """
    if not concepts:
        raise RuntimeError("Aucun concept validé: rien à générer.")

    source = source or entree.nom_source()
    valides = {c.id for c in concepts}
    par_concept: dict[str, int] = {c.id: 0 for c in concepts}
    avertissements: list[str] = []
    questions: list = []
    profil_utilise: Profil | None = None

    # Toute la génération est chiffrée et contrôlée AVANT le premier appel.
    llm.verifier_budget_generation(
        entree, concepts, conn=conn, modele=modele, forcer=forcer
    )

    raisons_echec: dict[str, str] = {}
    lots = llm.lots_de(concepts)
    for index, lot in enumerate(lots, 1):
        try:
            reponse, profil = llm.generer_questions(
                entree, lot, conn=conn, modele=modele, forcer=forcer
            )
        except ARRETS_BUDGET as exc:
            if index == 1:
                raise
            # Les lots déjà payés sont gardés; le reste est marqué échoué.
            # Aucun nouvel appel: on sort de la boucle.
            avertissements.append(f"Génération arrêtée au lot {index}/{len(lots)}: {exc}")
            for restant in lots[index - 1:]:
                for c in restant:
                    raisons_echec[c.id] = f"Non généré: {exc}"
            break
        except Exception as exc:
            if len(lots) == 1:
                raise
            # Pas de nouvelle tentative: le lot est marqué échoué et on
            # continue. Trois échecs d'affilée ouvrent le disjoncteur, qui
            # arrête la boucle au lot suivant.
            raison = str(exc) if isinstance(exc, RuntimeError) else "erreur de l'API"
            avertissements.append(f"Lot {index}/{len(lots)} échoué: {raison}")
            for c in lot:
                raisons_echec[c.id] = f"Échec de génération: {raison}"
            if progression:
                progression(index, len(lots))
            continue
        profil_utilise = profil
        avertissements.extend(reponse.avertissements)

        for generee in reponse.questions:
            # Garde-fou «aucune question hors des concepts validés»: le
            # concept_id vient du LLM, donc on le vérifie au lieu de le croire.
            if generee.concept_id not in valides:
                avertissements.append(
                    f"Question écartée: concept_id « {generee.concept_id} » absent "
                    f"de la liste validée."
                )
                continue

            questions.append(_QUESTION_ADAPTER.validate_python({
                **generee.model_dump(),
                "id": str(uuid4()),
                "cours": entree.cours,
                "source": Source(fichier=source, page=None).model_dump(),
            }))
            par_concept[generee.concept_id] += 1

        if progression:
            progression(index, len(lots))

    sans_question = [c for c in concepts if par_concept[c.id] == 0]
    prefixe = _avertissements_profil(profil_utilise) if profil_utilise else []

    return GenerationSummary(
        questions=questions,
        questions_creees=len(questions),
        echecs=[
            FailedQuestion(
                concept_id=c.id,
                concept_titre=c.titre,
                raison=raisons_echec.get(
                    c.id,
                    "Aucune question générée pour ce concept (voir les avertissements).",
                ),
            )
            for c in sans_question
        ],
        avertissements=prefixe + avertissements,
        couverture=CoverageReport(
            source=source,
            total_concepts=len(concepts),
            concepts_sans_question=[c.id for c in sans_question],
            details=[
                CoverageEntry(concept_id=c.id, titre=c.titre, nb_questions=par_concept[c.id])
                for c in concepts
            ],
        ),
        profil=profil_utilise,
    )


def generer(
    conn: sqlite3.Connection,
    entree: llm.Entree,
    concepts: list[Concept],
    *,
    source: str | None = None,
    modele: str | None = None,
    forcer: bool = False,
    now: datetime | None = None,
    progression=None,
) -> GenerationSummary:
    """Étape B + persistance SQLite (voie utilisée par l'interface web)."""
    resume = produire(
        entree, concepts, conn=conn, source=source, modele=modele,
        forcer=forcer, progression=progression,
    )

    now = now or datetime.now(timezone.utc)
    ensure_cours(conn, entree.cours)
    categories = {c.id: c.categorie for c in concepts}
    for question in resume.questions:
        conn.execute(
            "INSERT INTO cards (id, concept_id, cours, type, payload,"
            " source_fichier, source_page, imported_at, poids, categorie)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                question.id, question.concept_id, question.cours,
                question.type, question.model_dump_json(),
                question.source.fichier, question.source.page, now.isoformat(),
                question.poids, categories.get(question.concept_id),
            ),
        )
    conn.commit()
    return resume
