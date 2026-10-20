"""Jugement des réponses libres (`libre_llm`) et des rédactions (`redaction`).

Deux juges derrière la même interface:
- `JugeManuel`: l'élève voit la réponse attendue et se note (aucun coût);
- `JugeLLM`: un modèle (via OpenRouter, voir `llm.py`) corrige la réponse.

Le juge LLM ne passe jamais par un autre chemin que `llm.py`: budget, plafonds
et interrupteur d'urgence s'appliquent. Il ne décide pas seul de la note finale
côté interface: l'élève voit le verdict et peut le corriger.
"""
from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass

from fsrs import Rating

from . import llm
from .grading import rating_depuis_criteres


@dataclass(frozen=True)
class Verdict:
    rating: Rating
    source: str
    commentaire: str | None = None
    # "juste" | "partiel" | "faux" pour une réponse libre.
    verdict: str | None = None
    # Un booléen par critère pour une rédaction.
    criteres: tuple[bool, ...] | None = None


class Juge(ABC):
    @abstractmethod
    def juge(self, *, enonce: str, reponse_attendue: str, reponse_donnee: str) -> Verdict:
        """Évalue une réponse libre et renvoie une note FSRS."""


class JugeManuel(Juge):
    """Auto-évaluation: l'utilisateur voit la réponse attendue et se note.

    `demander_note` est injecté pour que la logique reste testable sans
    terminal.
    """

    def __init__(self, demander_note):
        self._demander_note = demander_note

    def juge(self, *, enonce: str, reponse_attendue: str, reponse_donnee: str) -> Verdict:
        rating = self._demander_note(
            enonce=enonce,
            reponse_attendue=reponse_attendue,
            reponse_donnee=reponse_donnee,
        )
        return Verdict(rating=rating, source="manuel")


# Une réponse « juste » vaut Bien, « partielle » Difficile, « fausse » Raté.
# Facile reste un choix de l'élève: le juge ne peut pas savoir que c'était aisé.
_RATING_PAR_VERDICT = {"juste": Rating.Good, "partiel": Rating.Hard, "faux": Rating.Again}


class JugeLLM(Juge):
    """Juge qui interroge un modèle via `llm.py` (budget et arrêt d'urgence inclus)."""

    def __init__(self, conn: sqlite3.Connection, langue: str = "fr"):
        self._conn = conn
        self._langue = langue

    def juge(self, *, enonce: str, reponse_attendue: str, reponse_donnee: str) -> Verdict:
        sortie = llm.juger_libre(
            conn=self._conn, enonce=enonce, reponse_attendue=reponse_attendue,
            reponse_donnee=reponse_donnee, langue=self._langue,
        )
        return Verdict(
            rating=_RATING_PAR_VERDICT[sortie.verdict], source="juge",
            commentaire=sortie.commentaire, verdict=sortie.verdict,
        )

    def juge_redaction(
        self, *, consigne: str, contraintes: str, criteres: list[str],
        exemple_reference: str, texte: str,
    ) -> Verdict:
        sortie = llm.juger_redaction(
            conn=self._conn, consigne=consigne, contraintes=contraintes,
            criteres=criteres, exemple_reference=exemple_reference, texte=texte,
            langue=self._langue,
        )
        coches = sum(sortie.criteres)
        return Verdict(
            rating=rating_depuis_criteres(coches, len(criteres)), source="juge",
            commentaire=sortie.commentaire, criteres=tuple(sortie.criteres),
        )
