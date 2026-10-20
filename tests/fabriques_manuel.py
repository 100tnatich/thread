"""Fabriques de fichiers concepts/questions pour les tests du workflow manuel."""
from __future__ import annotations

import json
from pathlib import Path

from flashcards import manuel


def concept(cid: str, titre: str = "Un concept") -> dict:
    return {
        "id": cid, "titre": titre, "categorie": "definition",
        "resume": "Une phrase fidèle au document.", "source": "page 1",
        "importance": "essentiel", "adapte_calcul": False,
    }


def fichier_concepts(*ids: str, validated: bool = True) -> dict:
    return {
        "cours": "physique", "source": "cours.pdf",
        "concepts": [concept(i, f"Concept {i}") for i in ids],
        "avertissements": [], "validated": validated,
    }


def q_exact(cid: str = "c1", enonce: str = "Unité de la résistance ?", **sur) -> dict:
    return {
        "concept_id": cid, "type": "exact", "enonce": enonce,
        "reponse_exacte": "ohm", "source_ref": "page 2", "confiance": "haute", **sur,
    }


def q_qcm(cid: str = "c1") -> dict:
    return {
        "concept_id": cid, "type": "qcm", "enonce": "Quelle est la loi d'Ohm ?",
        "propositions": ["U = R·I", "U = R/I", "U = I/R", "U = R+I"],
        "bonnes_reponses": [0], "source_ref": "page 2", "confiance": "haute",
    }


def q_cloze(cid: str = "c1") -> dict:
    return {
        "concept_id": cid, "type": "cloze", "texte": "U = {{c1::R·I}}",
        "valeurs_attendues": {"c1": "R·I"}, "source_ref": "page 2", "confiance": "haute",
    }


def q_probleme(cid: str = "c1", confiance: str = "moyenne") -> dict:
    return {
        "concept_id": cid, "type": "probleme", "enonce": "R = 10 Ω, I = 2 A. Calcule U.",
        "solution_etapes": ["U = R·I", "U = 10 × 2"], "reponse_finale": "20 V",
        "source_ref": "exercice 1", "confiance": confiance,
    }


def q_libre(cid: str = "c1") -> dict:
    return {
        "concept_id": cid, "type": "libre_llm", "enonce": "Énonce la loi d'Ohm.",
        "reponse_attendue": "U = R·I", "source_ref": "page 2", "confiance": "haute",
    }


def q_redaction(cid: str = "c1") -> dict:
    return {
        "concept_id": cid, "type": "redaction", "consigne": "Rédige une introduction.",
        "contraintes": "100 mots", "criteres": ["Amorce", "Thème", "Problématique", "Plan"],
        "exemple_reference": "Un texte modèle complet.", "source_ref": "page 3",
        "confiance": "haute",
    }


def fichier_questions(questions: list[dict], **entete) -> dict:
    return {
        "source": "cours.pdf", "cours": "physique",
        "questions": questions, "avertissements": [], **entete,
    }


def analyser(obj: dict, **kw) -> manuel.Analyse:
    return manuel.analyser(json.dumps(obj), nom="x_questions.json", **kw)


def ecrire(chemin: Path, obj: dict) -> Path:
    chemin.write_text(json.dumps(obj, ensure_ascii=False, indent=2))
    return chemin


def json_texte(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False)
