from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fsrs import Card, State

from flashcards.fsrs_engine import interval_days_for_retention
from flashcards.heuristic import (
    NEVER_REVIEWED_DUE,
    Exam,
    effective_due,
    next_exam,
    project_retention_at,
    retention_target,
)

TODAY = date(2026, 1, 5)
LAST_REVIEW = datetime(2026, 1, 5, tzinfo=timezone.utc)


def carte_stable(stability: float = 60.0) -> Card:
    return Card(
        card_id=0,
        state=State.Review,
        stability=stability,
        difficulty=5.0,
        last_review=LAST_REVIEW,
    )


def examen(jours: int, cours: str = "physique") -> Exam:
    return Exam(
        id=1,
        cours=frozenset({cours}),
        date=TODAY + timedelta(days=jours),
        intitule="TE",
    )


# --- Rampe de rétention ----------------------------------------------------

def test_retention_normale_sans_examen(config):
    assert retention_target([], TODAY, config) == config.retention_normale


def test_retention_normale_si_examen_hors_phase_finale(config):
    assert retention_target([examen(30)], TODAY, config) == config.retention_normale
    # Pile à la limite de la fenêtre: encore le régime normal.
    assert retention_target([examen(10)], TODAY, config) == config.retention_normale


def test_retention_monte_quand_le_test_approche(config):
    r9 = retention_target([examen(9)], TODAY, config)
    r5 = retention_target([examen(5)], TODAY, config)
    r1 = retention_target([examen(1)], TODAY, config)
    r0 = retention_target([examen(0)], TODAY, config)

    assert config.retention_normale < r9 < r5 < r1 < r0
    assert r0 == config.retention_test


def test_retention_retombe_apres_le_test(config):
    passe = Exam(
        id=1,
        cours=frozenset({"physique"}),
        date=TODAY - timedelta(days=1),
        intitule="TE passé",
    )
    assert retention_target([passe], TODAY, config) == config.retention_normale


# --- Clamp avant examen ----------------------------------------------------

def test_sans_examen_l_echeance_suit_fsrs(config):
    card = carte_stable()
    attendu = LAST_REVIEW + timedelta(
        days=interval_days_for_retention(card.stability, config.retention_normale)
    )
    assert effective_due(card, [], TODAY, config) == attendu


def test_echeance_clampee_avant_le_test(config):
    """Une carte très stable ne doit pas 'sauter' par-dessus la date du test."""
    card = carte_stable(stability=200.0)
    naturelle = effective_due(card, [], TODAY, config)
    ex = examen(20)
    clampee = effective_due(card, [ex], TODAY, config)

    assert naturelle.date() > ex.date, "le test doit bien tomber avant l'échéance naturelle"
    assert clampee.date() == ex.date - timedelta(days=config.buffer_jours_avant_test)


def test_clamp_ne_raccourcit_pas_une_echeance_deja_plus_proche(config):
    card = carte_stable(stability=2.0)
    naturelle = effective_due(card, [], TODAY, config)
    clampee = effective_due(card, [examen(40)], TODAY, config)
    assert clampee == naturelle


def test_jamais_deux_revisions_le_meme_jour(config):
    """Le plancher last_review + 1j protège contre un clamp dans le passé."""
    card = carte_stable()
    due = effective_due(card, [examen(0)], TODAY, config)
    assert due >= LAST_REVIEW + timedelta(days=1)


def test_carte_jamais_revisee_est_due_immediatement(config):
    neuve = Card(card_id=0)
    assert effective_due(neuve, [], TODAY, config) == NEVER_REVIEWED_DUE


def test_examen_d_un_autre_theme_n_influence_pas(config):
    autre = examen(3, cours="optique")
    # `exams_for_card` filtre en amont; ici on vérifie la couverture.
    assert not autre.couvre("physique")
    assert autre.couvre("optique")


def test_next_exam_prend_le_plus_proche(config):
    proche, loin = examen(7), examen(30)
    assert next_exam([loin, proche], TODAY) is proche
    assert next_exam([], TODAY) is None


# --- Projection ------------------------------------------------------------

def test_projection_retention_elevee_le_jour_du_test(config):
    card = carte_stable(stability=200.0)
    ex = examen(20)
    retention = project_retention_at(card.__class__(
        card_id=0,
        state=State.Review,
        stability=200.0,
        difficulty=5.0,
        last_review=LAST_REVIEW,
    ), [ex], ex.date, TODAY, config)
    # Grâce au clamp, la carte est revue juste avant: rétention élevée.
    assert retention > 0.9


def test_projection_sur_carte_neuve_ne_plante_pas(config):
    neuve = Card(card_id=0)
    ex = examen(14)
    retention = project_retention_at(neuve, [ex], ex.date, TODAY, config)
    assert 0.0 <= retention <= 1.0
