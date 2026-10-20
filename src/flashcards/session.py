"""Présentation des questions dans le terminal et collecte des réponses.

Couche d'affichage uniquement: toute la décision (correction, note FSRS,
planning) vient de `grading`, `judge` et `api`. Un futur front-end remplace
ce module sans toucher au moteur.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from fsrs import Rating

from .grading import (
    MANUAL_RATINGS,
    corrige_cloze,
    corrige_exact,
    corrige_qcm,
    rating_depuis_correction,
    rating_depuis_criteres,
)
from .judge import JugeManuel

CLOZE_PATTERN = re.compile(r"\{\{(c\d+)::(.*?)\}\}")


class QuitSession(Exception):
    pass


@dataclass(frozen=True)
class Reponse:
    rating: Rating
    correct: bool | None
    source: str


def masque_cloze(texte: str) -> str:
    return CLOZE_PATTERN.sub(lambda m: f"[{m.group(1)}: ____]", texte)


def _input(prompt: str) -> str:
    valeur = input(prompt)
    if valeur.strip().lower() == "q":
        raise QuitSession()
    return valeur


def _demande_nuance() -> str | None:
    """Permet de préciser Hard/Easy sur une réponse juste (Good par défaut)."""
    raw = _input("Nuance [Entrée=normal / h=difficile / e=facile]: ").strip().lower()
    if raw == "h":
        return "hard"
    if raw == "e":
        return "easy"
    return None


def _demande_auto_evaluation(titre: str) -> Rating:
    while True:
        raw = _input(f"{titre} [1=Again / 2=Hard / 3=Good / 4=Easy]: ").strip()
        if raw in MANUAL_RATINGS:
            return MANUAL_RATINGS[raw]
        print("Entrée invalide, réessaie.")


def pose_question(question) -> Reponse:
    """Affiche une question selon son type et renvoie la note FSRS obtenue."""
    if question.type == "qcm":
        print(f"\n{question.enonce}")
        for i, proposition in enumerate(question.propositions):
            print(f"  {i}. {proposition}")
        saisie = _input("Ta réponse (indices, ex. 0,2): ")
        correct = corrige_qcm(saisie, question.bonnes_reponses)
        print("-> JUSTE" if correct else f"-> FAUX (attendu: {question.bonnes_reponses})")
        nuance = _demande_nuance() if correct else None
        return Reponse(rating_depuis_correction(correct, nuance), correct, "auto")

    if question.type == "exact":
        print(f"\n{question.enonce}")
        saisie = _input("Ta réponse: ")
        correct = corrige_exact(saisie, question.reponse_exacte)
        print("-> JUSTE" if correct else f"-> FAUX (attendu: {question.reponse_exacte})")
        nuance = _demande_nuance() if correct else None
        return Reponse(rating_depuis_correction(correct, nuance), correct, "auto")

    if question.type == "cloze":
        print(f"\n{masque_cloze(question.texte)}")
        reponses = {}
        for cle in question.valeurs_attendues:
            reponses[cle] = _input(f"  {cle} = ")
        correct = corrige_cloze(reponses, question.valeurs_attendues)
        print("-> JUSTE" if correct else f"-> FAUX (attendu: {question.valeurs_attendues})")
        nuance = _demande_nuance() if correct else None
        return Reponse(rating_depuis_correction(correct, nuance), correct, "auto")

    if question.type == "probleme":
        print(f"\n{question.enonce}")
        if question.necessite_dessin:
            print("(fais le schéma sur feuille)")
        _input("Résous sur feuille, puis Entrée pour voir la solution: ")
        print("\nSolution:")
        for i, etape in enumerate(question.solution_etapes, 1):
            print(f"  {i}. {etape}")
        print(f"Réponse finale: {question.reponse_finale}")
        if question.schema_svg:
            print("(un schéma SVG de référence est disponible dans la carte)")
        rating = _demande_auto_evaluation("Auto-évaluation")
        return Reponse(rating, None, "manuel")

    if question.type == "libre_llm":
        print(f"\n{question.enonce}")
        donnee = _input("Ta réponse: ")
        juge = JugeManuel(
            demander_note=lambda enonce, reponse_attendue, reponse_donnee: (
                print(f"\nRéponse attendue: {reponse_attendue}")
                or _demande_auto_evaluation("Auto-évaluation")
            )
        )
        verdict = juge.juge(
            enonce=question.enonce,
            reponse_attendue=question.reponse_attendue,
            reponse_donnee=donnee,
        )
        return Reponse(verdict.rating, None, verdict.source)

    if question.type == "redaction":
        print(f"\n{question.consigne}")
        print(f"Contraintes: {question.contraintes}")
        _input("Rédige sur feuille, puis Entrée pour voir l'exemple de référence: ")
        print("\nExemple de référence:")
        print(question.exemple_reference)

        print("\nCoche les critères que ta copie remplit:")
        coches = 0
        for critere in question.criteres:
            reponse = _input(f"  [{critere}] rempli ? [o/n]: ").strip().lower()
            coches += int(reponse.startswith("o"))
        print(f"-> {coches}/{len(question.criteres)} critères remplis")

        facile = False
        if coches == len(question.criteres):
            facile = _input("C'était facile ? [o/N]: ").strip().lower().startswith("o")
        rating = rating_depuis_criteres(coches, len(question.criteres), facile=facile)
        print(f"-> note: {rating.name}")
        return Reponse(rating, None, "manuel")

    raise RuntimeError(f"Type de question inconnu: {question.type}")
