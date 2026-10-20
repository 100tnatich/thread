"""Pilotage du planning FSRS par les dates de test.

Heuristique retenue (et pourquoi) — deux mécanismes complémentaires:

1. **Rampe de rétention cible.** Hors examen proche, la rétention cible est
   `retention_normale` (0.90): c'est le régime FSRS classique, optimisé pour
   la mémorisation long terme. Dès qu'un examen couvrant la carte entre dans
   la fenêtre `jours_phase_finale`, la cible monte linéairement jusqu'à
   `retention_test` (0.97) le jour de l'examen. Une cible plus haute = des
   intervalles plus courts = plus de passages sur la carte juste avant le
   test. La rampe est progressive (et non un palier) pour éviter qu'un seul
   jour fasse basculer d'un coup toutes les cartes d'une cours dans la
   file, ce qui créerait un pic ingérable.

2. **Clamp dur avant l'examen, étalé sur la fenêtre finale.** La rampe seule
   ne suffit pas: une carte très stable peut avoir un intervalle naturel de
   60 jours et «sauter» par-dessus la date du test même avec une cible à
   0.97. On force donc l'échéance à tomber au plus tard juste avant
   l'examen. Mais un clamp à une date unique (`date_examen - buffer`)
   concentre TOUT un thème sur un seul jour: avec 150 cartes et un
   plafond de 40, 110 cartes ne seraient jamais revues avant le test — la
   garantie casse précisément sur les gros thèmes. Chaque carte reçoit
   donc une échéance limite propre, répartie de façon déterministe sur les
   `jours_phase_finale` jours précédant l'examen (hash stable de son id).
   150 cartes sur 10 jours = ~15/jour au lieu d'un pic de 150.

Après l'examen, celui-ci n'est plus «à venir»: la carte retombe
automatiquement sur `retention_normale`, sans traitement spécial.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from fsrs import Card, Rating

from .config import SchedulerConfig
from .fsrs_engine import apply_review, interval_days_for_retention, retrievability_at

# Échéance conventionnelle d'une carte jamais révisée: elle est due
# immédiatement (c'est le plafond quotidien qui lisse l'arrivée des
# nouvelles cartes, pas un délai artificiel).
NEVER_REVIEWED_DUE = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Exam:
    """Un test, lié à un ou plusieurs cours (thèmes).

    `cours` contient déjà les thèmes des matières liées (résolus au chargement).
    """

    id: int
    cours: frozenset[str]
    date: date
    intitule: str
    matieres: frozenset[str] = frozenset()

    def couvre(self, cours: str) -> bool:
        return cours in self.cours

    @property
    def cours_txt(self) -> str:
        return ", ".join(sorted(self.cours))


def exams_for_card(exams: list[Exam], cours: str) -> list[Exam]:
    return [e for e in exams if e.couvre(cours)]


def next_exam(exams: list[Exam], today: date) -> Exam | None:
    """Prochain examen à venir (aujourd'hui inclus) parmi `exams`."""
    futurs = sorted((e for e in exams if e.date >= today), key=lambda e: e.date)
    return futurs[0] if futurs else None


def _spread_offset(spread_key: str, fenetre: int) -> int:
    """Décalage déterministe (0..fenetre-1) propre à une carte.

    `hash()` de Python est salé par processus: on utilise un hash stable pour
    qu'une carte garde la même place dans la fenêtre d'un run à l'autre.
    """
    if fenetre <= 1:
        return 0
    digest = hashlib.md5(spread_key.encode()).digest()
    return int.from_bytes(digest[:4], "big") % fenetre


def deadline_avant_examen(
    exam: Exam, config: SchedulerConfig, spread_key: str | None
) -> date:
    """Date limite de révision d'une carte pour un examen donné."""
    base = exam.date - timedelta(days=config.buffer_jours_avant_test)
    if spread_key is None:
        return base
    return base - timedelta(days=_spread_offset(spread_key, config.jours_phase_finale))


def retention_target(exams: list[Exam], today: date, config: SchedulerConfig) -> float:
    """Rétention cible: rampe de `retention_normale` vers `retention_test`."""
    prochain = next_exam(exams, today)
    if prochain is None:
        return config.retention_normale

    jours_restants = (prochain.date - today).days
    if jours_restants >= config.jours_phase_finale:
        return config.retention_normale

    progression = 1 - (jours_restants / config.jours_phase_finale)
    return config.retention_normale + progression * (
        config.retention_test - config.retention_normale
    )


def effective_due(
    card: Card,
    exams: list[Exam],
    today: date,
    config: SchedulerConfig,
    spread_key: str | None = None,
) -> datetime:
    """Échéance réelle d'une carte, rampe et clamp d'examen appliqués.

    Jamais stockée en base: toujours recalculée, pour qu'ajouter, déplacer ou
    supprimer un examen replanifie immédiatement sans migration de données.
    """
    if card.last_review is None or card.stability is None:
        return NEVER_REVIEWED_DUE

    retention = retention_target(exams, today, config)
    intervalle = interval_days_for_retention(card.stability, retention)
    due = card.last_review + timedelta(days=intervalle)

    prochain = next_exam(exams, today)
    if prochain is not None:
        limite = datetime.combine(
            deadline_avant_examen(prochain, config, spread_key),
            datetime.min.time(),
            tzinfo=timezone.utc,
        )
        # Ne jamais ramener une carte avant le lendemain de sa dernière
        # révision: deux passages le même jour n'apportent rien.
        plancher = card.last_review + timedelta(days=1)
        due = max(min(due, limite), plancher)

    return due


def project_retention_at(
    card: Card,
    exams: list[Exam],
    target: date,
    today: date,
    config: SchedulerConfig,
    max_steps: int = 200,
    spread_key: str | None = None,
) -> float:
    """Rétention estimée à la date `target` si la carte est révisée à chaque
    échéance d'ici là, en supposant des réponses correctes (Rating.Good).

    Sert aux stats («où j'en serai le jour du test»), pas au planning.
    """
    target_dt = datetime.combine(target, datetime.min.time(), tzinfo=timezone.utc)
    now = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    courant = card

    for _ in range(max_steps):
        jour = max(now.date(), today)
        due = effective_due(courant, exams, jour, config, spread_key)
        if due > target_dt:
            break
        moment = max(due, now)
        if moment > target_dt:
            break
        retention = retention_target(exams, moment.date(), config)
        courant = apply_review(courant, Rating.Good, moment, retention)
        now = moment + timedelta(days=1)

    return retrievability_at(courant, target_dt)


def deja_couvert_pour(card: Card, exam: Exam | None, config: SchedulerConfig) -> bool:
    """La carte a-t-elle déjà été revue depuis l'ouverture de la phase finale?

    Sert à faire passer la COUVERTURE avant l'approfondissement: tant qu'une
    carte du programme n'a pas été vue au moins une fois dans la fenêtre
    pré-examen, elle passe devant celles qu'on a déjà repassées.
    """
    if exam is None or card.last_review is None:
        return False
    debut_fenetre = exam.date - timedelta(days=config.jours_phase_finale)
    return card.last_review.date() >= debut_fenetre


def priority_key(
    card_id: str,
    exams: list[Exam],
    due: datetime,
    today: date,
    deja_couvert: bool = False,
) -> tuple[int, int, int, str]:
    """Clé de tri de la file, par ordre d'importance décroissante:

    1. urgence de l'examen — un test dans une semaine passe avant un test
       dans un mois;
    2. couverture — une carte du programme jamais vue dans la phase finale
       passe avant une carte déjà repassée (sinon, sur un gros thème, on
       repasse 40 fois les mêmes et le reste n'est jamais vu);
    3. retard — à égalité, la carte due depuis le plus longtemps d'abord, ce
       qui évite qu'une carte reste indéfiniment au fond de la pile.
    """
    prochain = next_exam(exams, today)
    urgence = (prochain.date - today).days if prochain is not None else 10_000
    retard = (datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) - due).days
    return (urgence, int(deja_couvert), -retard, card_id)
