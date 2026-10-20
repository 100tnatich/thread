"""Étape B du CLI: génération des questions depuis la liste VALIDÉE.

La liste de concepts validée reste la seule source des concepts; la matière
est rejointe uniquement pour que le LLM reprenne les valeurs et la notation
exactes du document.
"""
from __future__ import annotations

from pathlib import Path

from . import ingest, llm
from .config import load_scheduler_config
from .db import open_db
from .extract import concepts_path_for
from .models import ConceptList, GenerationResult


def questions_path_for(pdf_path: Path) -> Path:
    return pdf_path.with_name(pdf_path.stem + ".questions.json")


def coverage_path_for(pdf_path: Path) -> Path:
    return pdf_path.with_name(pdf_path.stem + ".coverage.json")


def load_validated_concepts(pdf_path: Path) -> ConceptList:
    concepts_path = concepts_path_for(pdf_path)
    if not concepts_path.exists():
        raise FileNotFoundError(
            f"Fichier de concepts introuvable: {concepts_path}. Lance d'abord `extract`."
        )
    concept_list = ConceptList.model_validate_json(concepts_path.read_text())
    if not concept_list.validated:
        raise RuntimeError(
            f"{concepts_path} n'est pas validé. Relis la liste, corrige-la si "
            "besoin, puis passe `validated` à `true` avant de lancer `generate`."
        )
    if not concept_list.concepts:
        raise RuntimeError(f"{concepts_path} ne contient aucun concept.")
    return concept_list


def _entree_generation(pdf_path: Path, cours: str | None) -> tuple[llm.Entree, ConceptList]:
    concept_list = load_validated_concepts(pdf_path)
    voie = "pdf" if pdf_path.suffix.lower() == ".pdf" else "image"
    return llm.Entree(
        voie=voie,
        cours=cours or concept_list.cours,
        fichiers=(pdf_path,),
    ), concept_list


def estimer_generate(pdf_path: Path, cours: str | None = None) -> llm.Estimation:
    """Coût estimé sans aucun appel (`--dry-run`)."""
    entree, concept_list = _entree_generation(pdf_path, cours)
    return llm.estimer(
        entree, operation="generation", concepts=concept_list.concepts
    )


def run_generate(
    pdf_path: Path, cours: str | None = None, forcer: bool = False
) -> tuple[Path, Path, list[str]]:
    """Écrit `<pdf>.questions.json` + `<pdf>.coverage.json`.

    `cours` surcharge le cours enregistré dans la liste de concepts — c'est ce
    qui choisit le profil appliqué.
    """
    entree, concept_list = _entree_generation(pdf_path, cours)
    cours_final = entree.cours

    conn = open_db(load_scheduler_config().db_path)
    try:
        resume = ingest.produire(
            entree, concept_list.concepts, conn=conn,
            source=pdf_path.name, forcer=forcer,
        )
    finally:
        conn.close()

    result = GenerationResult(
        source=pdf_path.name,
        cours=cours_final,
        questions=resume.questions,
        echecs=resume.echecs,
        avertissements=resume.avertissements,
    )
    questions_path = questions_path_for(pdf_path)
    questions_path.write_text(result.model_dump_json(indent=2, by_alias=True) + "\n")

    coverage_path = coverage_path_for(pdf_path)
    coverage_path.write_text(resume.couverture.model_dump_json(indent=2) + "\n")

    return questions_path, coverage_path, resume.avertissements
