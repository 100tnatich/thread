"""Pont entre nos lignes SQLite et la bibliothèque `fsrs` (v6).

Toute transition d'état (stabilité, difficulté, état) passe par le vrai
`Scheduler.review_card` de la bibliothèque: l'algorithme FSRS n'est jamais
réimplémenté ici. Ce module ne fait que convertir nos lignes DB en
`fsrs.Card` et exposer deux projections (intervalle pour une rétention
cible, rétention à une date) nécessaires au pilotage par dates de test.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from fsrs import Card, Rating, Scheduler, State

# learning_steps/relearning_steps vides: on veut une seule session par jour,
# donc pas de re-présentation à 1 min / 10 min le jour même. Une carte passe
# directement en State.Review avec des intervalles en jours entiers.
# enable_fuzzing=False: la charge quotidienne reste prévisible et le clamp
# avant examen est exact (sinon le fuzz pourrait repousser une carte après la
# date de l'examen).
_LEARNING_STEPS: tuple[timedelta, ...] = ()
_RELEARNING_STEPS: tuple[timedelta, ...] = ()


@lru_cache(maxsize=32)
def scheduler_for(desired_retention: float) -> Scheduler:
    return Scheduler(
        desired_retention=desired_retention,
        learning_steps=_LEARNING_STEPS,
        relearning_steps=_RELEARNING_STEPS,
        enable_fuzzing=False,
    )


BASE_SCHEDULER = scheduler_for(0.90)

# Décroissance FSRS, lue depuis les paramètres de la bibliothèque pour suivre
# un éventuel changement de version / d'optimisation.
_DECAY = -BASE_SCHEDULER.parameters[20]
_FACTOR = 0.9 ** (1 / _DECAY) - 1


def card_from_row(row: sqlite3.Row) -> Card:
    """Construit un `fsrs.Card` depuis une ligne de la table `cards`.

    `due` est volontairement absent de la DB: on passe `last_review` comme
    `due` provisoire, car toutes nos décisions d'échéance sont recalculées
    par `heuristic.effective_due` à partir de (stability, last_review).
    """
    last_review = _parse_dt(row["last_review"])
    return Card(
        card_id=0,
        state=State(row["fsrs_state"]),
        step=row["fsrs_step"],
        stability=row["fsrs_stability"],
        difficulty=row["fsrs_difficulty"],
        due=last_review or datetime(1970, 1, 1, tzinfo=timezone.utc),
        last_review=last_review,
    )


def apply_review(card: Card, rating: Rating, reviewed_at: datetime, retention: float) -> Card:
    """Applique une révision via la bibliothèque FSRS et renvoie la carte à jour."""
    updated, _ = scheduler_for(retention).review_card(
        card=card, rating=rating, review_datetime=reviewed_at
    )
    return updated


def card_state_for_db(card: Card) -> dict[str, object]:
    return {
        "fsrs_state": int(card.state),
        "fsrs_step": card.step,
        "fsrs_stability": card.stability,
        "fsrs_difficulty": card.difficulty,
        "last_review": card.last_review.isoformat() if card.last_review else None,
    }


def interval_days_for_retention(stability: float, retention: float) -> int:
    """Intervalle en jours pour atteindre `retention` depuis `stability`.

    Reprend la formule d'intervalle publique de FSRS (identique à
    `Scheduler._next_interval`, inaccessible publiquement) parce qu'on a
    besoin de projeter une échéance SANS déclencher de révision. Les
    transitions réelles passent toujours par `apply_review`.
    """
    days = (stability / _FACTOR) * ((retention ** (1 / _DECAY)) - 1)
    return max(1, round(days))


def retrievability_at(card: Card, moment: datetime) -> float:
    """Probabilité estimée de bonne réponse à `moment` (0 si jamais révisée)."""
    return BASE_SCHEDULER.get_card_retrievability(card, current_datetime=moment)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
