"""Étape A du CLI: extraction du plan de concepts depuis un PDF de cours."""
from __future__ import annotations

from pathlib import Path

from . import ingest, llm
from .config import load_scheduler_config
from .db import open_db
from .models import ConceptList


def concepts_path_for(pdf_path: Path) -> Path:
    return pdf_path.with_name(pdf_path.stem + ".concepts.json")


def _entree(pdf_path: Path, cours: str) -> llm.Entree:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF introuvable: {pdf_path}")
    voie = "pdf" if pdf_path.suffix.lower() == ".pdf" else "image"
    return llm.Entree(voie=voie, cours=cours, fichiers=(pdf_path,))


def estimer_extract(pdf_path: Path, cours: str) -> llm.Estimation:
    """Coût estimé sans aucun appel (`--dry-run`)."""
    return llm.estimer(_entree(pdf_path, cours), operation="extraction")


def run_extract(
    pdf_path: Path, cours: str, forcer: bool = False
) -> tuple[Path, list[str]]:
    """Écrit `<pdf>.concepts.json` et renvoie les avertissements à afficher."""
    entree = _entree(pdf_path, cours)
    conn = open_db(load_scheduler_config().db_path)
    try:
        resultat = ingest.extraire(entree, conn=conn, forcer=forcer)
    finally:
        conn.close()

    concept_list = ConceptList(
        cours=cours,
        source=pdf_path.name,
        concepts=resultat.concepts,
        avertissements=resultat.avertissements,
        validated=False,
    )

    out_path = concepts_path_for(pdf_path)
    out_path.write_text(concept_list.model_dump_json(indent=2, by_alias=True) + "\n")
    return out_path, resultat.avertissements
