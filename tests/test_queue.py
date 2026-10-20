"""File du jour: plafond, lissage de la dette, priorité aux tests proches."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from fsrs import Rating

from flashcards import api
from flashcards.heuristic import Exam

from .conftest import NOW, TODAY, make_card


def test_cartes_neuves_toutes_dues(conn, config):
    for i in range(5):
        make_card(conn, f"c{i}")
    items = api.due_items(api.load_cards(conn), [], TODAY, config)
    assert len(items) == 5


def test_plafond_quotidien_limite_la_file(conn, config):
    petit = replace(config, plafond_quotidien=10)
    for i in range(25):
        make_card(conn, f"c{i:02}")
    queue = api.get_today_queue(conn, TODAY, petit)
    assert len(queue) == 10


def test_le_surplus_n_est_pas_perdu(conn, config):
    """Les cartes au-delà du plafond restent dues le lendemain."""
    petit = replace(config, plafond_quotidien=10)
    for i in range(25):
        make_card(conn, f"c{i:02}")

    queue = api.get_today_queue(conn, TODAY, petit)
    for item in queue:
        api.record_answer(
            conn, item.card.id, Rating.Good, reviewed_at=NOW, config=petit, correct=True
        )

    restantes = api.due_items(api.load_cards(conn), [], TODAY, petit)
    assert len(restantes) == 15

    demain = TODAY + timedelta(days=1)
    total_demain = api.due_items(api.load_cards(conn), [], demain, petit)
    # Les 15 reportées sont toujours là (plus éventuellement des révisions dues).
    assert len(total_demain) >= 15


def test_priorite_au_test_le_plus_proche(conn, config):
    """Un test dans une semaine passe avant un test dans un mois."""
    stable = datetime(2026, 1, 1, tzinfo=timezone.utc)
    make_card(
        conn, "loin", cours="optique", stability=1.0, difficulty=5.0,
        last_review=stable, state=2,
    )
    make_card(
        conn, "proche", cours="physique", stability=1.0, difficulty=5.0,
        last_review=stable, state=2,
    )
    api.add_test(
        conn, cours="optique", jour=TODAY + timedelta(days=28), intitule="loin",
    )
    api.add_test(
        conn, cours="physique", jour=TODAY + timedelta(days=7), intitule="proche",
    )

    items = api.due_items(api.load_cards(conn), api.load_exams(conn), TODAY, config)
    assert [i.card.id for i in items] == ["proche", "loin"]


def test_a_urgence_egale_le_plus_en_retard_passe_devant(conn, config):
    vieux = datetime(2025, 12, 1, tzinfo=timezone.utc)
    recent = datetime(2026, 1, 1, tzinfo=timezone.utc)
    make_card(conn, "recent", stability=1.0, difficulty=5.0, last_review=recent, state=2)
    make_card(conn, "vieux", stability=1.0, difficulty=5.0, last_review=vieux, state=2)

    items = api.due_items(api.load_cards(conn), [], TODAY, config)
    assert [i.card.id for i in items] == ["vieux", "recent"]
    assert items[0].jours_retard > items[1].jours_retard


def test_dette_se_resorbe_avec_le_plafond(conn, config):
    """Sur plusieurs jours, un gros import finit par être absorbé."""
    petit = replace(config, plafond_quotidien=20)
    for i in range(60):
        make_card(conn, f"c{i:02}")

    jour = TODAY
    for _ in range(10):
        moment = datetime.combine(jour, datetime.min.time(), tzinfo=timezone.utc)
        for item in api.get_today_queue(conn, jour, petit):
            api.record_answer(
                conn, item.card.id, Rating.Easy, reviewed_at=moment, config=petit, correct=True
            )
        jour += timedelta(days=1)

    jamais_vues = conn.execute(
        "SELECT COUNT(*) AS n FROM cards WHERE last_review IS NULL"
    ).fetchone()["n"]
    assert jamais_vues == 0, "toutes les cartes doivent finir par passer"


def test_deplacer_un_test_replanifie(conn, config):
    """Déplacer un test change immédiatement les échéances, sans migration."""
    # Révisée longtemps avant: il existe donc des créneaux libres d'ici le test.
    stable = datetime(2025, 12, 1, tzinfo=timezone.utc)
    make_card(conn, "c1", stability=200.0, difficulty=5.0, last_review=stable, state=2)
    exam_id = api.add_test(
        conn, cours="physique", jour=TODAY + timedelta(days=60),
        intitule="TE",
    )

    cards = api.load_cards(conn)
    due_avant = api.due_items(cards, api.load_exams(conn), TODAY, config)
    assert not due_avant, "pas encore due: le test est loin"

    api.move_test(conn, exam_id, TODAY)
    due_apres = api.due_items(cards, api.load_exams(conn), TODAY, config)
    assert len(due_apres) == 1, "le test rapproché doit rendre la carte due"


def test_supprimer_un_test_fait_retomber_sur_fsrs(conn, config):
    stable = datetime(2026, 1, 5, tzinfo=timezone.utc)
    make_card(conn, "c1", stability=200.0, difficulty=5.0, last_review=stable, state=2)
    exam_id = api.add_test(
        conn, cours="physique", jour=TODAY + timedelta(days=2),
        intitule="TE",
    )
    cards = api.load_cards(conn)
    assert api.due_items(cards, api.load_exams(conn), TODAY + timedelta(days=1), config)

    api.remove_test(conn, exam_id)
    assert not api.due_items(cards, api.load_exams(conn), TODAY + timedelta(days=1), config)


def test_record_answer_ecrit_historique_et_etat(conn, config):
    make_card(conn, "c1")
    api.record_answer(
        conn, "c1", Rating.Good, reviewed_at=NOW, config=config, correct=True, source="auto"
    )

    row = conn.execute(
        "SELECT fsrs_stability, fsrs_difficulty, last_review, fsrs_state FROM cards WHERE id='c1'"
    ).fetchone()
    assert row["fsrs_stability"] is not None
    assert row["fsrs_difficulty"] is not None
    assert row["last_review"] == NOW.isoformat()

    log = conn.execute("SELECT rating, correct, source FROM reviews WHERE card_id='c1'").fetchone()
    assert log["rating"] == int(Rating.Good)
    assert log["correct"] == 1
    assert log["source"] == "auto"


def test_stats_calcule_charge_et_examens(conn, config):
    for i in range(5):
        make_card(conn, f"c{i}")
    api.add_test(
        conn, cours="physique", jour=TODAY + timedelta(days=10),
        intitule="TE",
    )

    stats = api.get_stats(conn, TODAY, config)
    assert stats.total_cartes == 5
    assert stats.nb_dues == 5
    assert len(stats.charge_14j) == 14
    assert len(stats.examens) == 1
    assert stats.examens[0].nb_cartes == 5
    assert stats.examens[0].nb_jamais_revisees == 5


def test_gros_theme_entierement_couvert_avant_le_test(conn, config):
    """Régression: un clamp à date unique concentrait tout un thème sur J-1.

    Avec 150 cartes stables et un plafond de 40, 110 cartes n'étaient jamais
    revues avant l'examen. L'étalement des échéances sur la phase finale plus
    la priorité à la couverture doivent ramener ce chiffre à zéro.
    """
    stable = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(150):
        make_card(
            conn, f"c{i:03}", cours="maths",
            stability=60.0, difficulty=5.0, last_review=stable, state=2,
        )
    jour_examen = TODAY + timedelta(days=14)
    api.add_test(
        conn, cours="maths", jour=jour_examen, intitule="gros TE"
    )

    jour = TODAY
    while jour < jour_examen:
        moment = datetime.combine(jour, datetime.min.time(), tzinfo=timezone.utc)
        for item in api.get_today_queue(conn, jour, config):
            api.record_answer(
                conn, item.card.id, Rating.Good, reviewed_at=moment, config=config, correct=True
            )
        jour += timedelta(days=1)

    moment_examen = datetime.combine(jour_examen, datetime.min.time(), tzinfo=timezone.utc)
    jamais = conn.execute(
        "SELECT COUNT(*) AS n FROM cards c WHERE NOT EXISTS ("
        " SELECT 1 FROM reviews r WHERE r.card_id = c.id AND r.reviewed_at < ?)",
        (moment_examen.isoformat(),),
    ).fetchone()["n"]
    assert jamais == 0, f"{jamais} carte(s) jamais revue(s) avant l'examen"


def test_echeances_etalees_sur_la_fenetre_finale(conn, config):
    """Les cartes d'un même examen ne doivent pas toutes tomber le même jour."""
    stable = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(60):
        make_card(
            conn, f"c{i:03}", cours="maths",
            stability=60.0, difficulty=5.0, last_review=stable, state=2,
        )
    api.add_test(
        conn, cours="maths", jour=TODAY + timedelta(days=14),
        intitule="TE",
    )

    cards = api.load_cards(conn)
    exams = api.load_exams(conn)
    echeances = {
        api.effective_due_for(row, exams, TODAY, config).date() for row in cards
    }
    assert len(echeances) > 1, "toutes les échéances tombent le même jour"
