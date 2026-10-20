"""Poids des questions: le plafond quotidien et la dette se comptent en points."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fsrs import Rating

from flashcards import api
from flashcards.config import load_scheduler_config
from flashcards.poids import poids_carte, poids_plafonne

from .conftest import NOW, TODAY, make_card


@pytest.fixture
def defaut():
    """La config réelle (config.toml): les poids par défaut y sont définis."""
    return load_scheduler_config()


# --- Résolution du poids ------------------------------------------------------

def test_poids_par_defaut_du_fichier_de_config(defaut):
    assert defaut.plafond_quotidien == 40
    attendu = {"exact": 0.5, "cloze": 0.5, "qcm": 0.5, "libre_llm": 1.0,
               "probleme": 2.0, "redaction": 4.0}
    for type_, poids in attendu.items():
        assert poids_carte(type_, None, None, defaut) == poids
    assert poids_carte("exact", "vocabulaire", None, defaut) == 0.1


def test_priorite_explicite_puis_categorie_puis_type(defaut):
    # 1. explicite: prime sur tout.
    assert poids_carte("probleme", "vocabulaire", 1.5, defaut) == 1.5
    # 2. catégorie: prime sur le type.
    assert poids_carte("probleme", "vocabulaire", None, defaut) == 0.1
    # 3. type, quand la catégorie n'a pas de poids propre.
    assert poids_carte("probleme", "formule", None, defaut) == 2.0
    assert poids_carte("probleme", None, None, defaut) == 2.0


def test_un_poids_explicite_nul_ne_retombe_pas_sur_le_type(defaut):
    """`poids=0` est une valeur explicite, pas « absent » (None)."""
    assert poids_carte("redaction", None, 0.0, defaut) == 0.0


def test_type_sans_poids_est_une_erreur(defaut):
    with pytest.raises(RuntimeError, match="Aucun poids"):
        poids_carte("hologramme", None, None, defaut)


def test_poids_depuis_la_base(conn, config):
    make_card(conn, "a", qtype="probleme", poids=None)
    make_card(conn, "b", qtype="probleme", poids=None, categorie="vocabulaire")
    make_card(conn, "c", qtype="probleme", poids=3.5, categorie="vocabulaire")
    points = {c.id: c.points(config) for c in api.load_cards(conn)}
    assert points == {"a": 2.0, "b": 0.1, "c": 3.5}


def test_poids_modifiable_dans_la_config(config):
    lourd = replace(config, poids_type={**config.poids_type, "qcm": 3.0})
    assert poids_carte("qcm", None, None, lourd) == 3.0


# --- Plafond en points -------------------------------------------------------

def test_le_plafond_se_compte_en_points(conn, config):
    petit = replace(config, plafond_quotidien=10)
    for i in range(10):
        make_card(conn, f"r{i}", qtype="redaction", poids=None)  # 4 pts chacune
    queue = api.get_today_queue(conn, TODAY, petit)
    assert len(queue) == 2, "2 rédactions = 8 points; une 3e ferait 12 > 10"
    assert sum(i.points for i in queue) <= 10


def test_beaucoup_de_cartes_legeres_pour_le_meme_budget(conn, config):
    petit = replace(config, plafond_quotidien=10)
    for i in range(300):
        make_card(conn, f"v{i:03}", qtype="exact", categorie="vocabulaire", poids=None)  # 0,1
    queue = api.get_today_queue(conn, TODAY, petit)
    assert len(queue) == 100


def test_une_carte_qui_ne_tient_pas_cede_la_place_a_une_plus_legere(conn, config):
    """Glouton par priorité: on saute ce qui ne tient pas plutôt que de s'arrêter."""
    petit = replace(config, plafond_quotidien=5)
    vieux = datetime(2025, 11, 1, tzinfo=timezone.utc)
    make_card(conn, "gros1", qtype="redaction", poids=4.0, stability=1.0, difficulty=5.0,
              last_review=vieux, state=2)
    make_card(conn, "gros2", qtype="redaction", poids=4.0, stability=1.0, difficulty=5.0,
              last_review=vieux + timedelta(days=1), state=2)
    make_card(conn, "petit", qtype="exact", poids=0.5, stability=1.0, difficulty=5.0,
              last_review=vieux + timedelta(days=2), state=2)

    ids = [i.card.id for i in api.get_today_queue(conn, TODAY, petit)]
    assert ids == ["gros1", "petit"], "gros2 (4 pts) ne tient plus, le petit passe"


def test_une_carte_ne_depasse_jamais_le_plafond_et_reste_planifiable(conn, config):
    """Garde-fou: une carte plus lourde que le plafond compte pour le plafond entier."""
    serre = replace(config, plafond_quotidien=3)
    # Ids choisis pour que la lourde passe en premier (cartes neuves: tri par id).
    make_card(conn, "a-enorme", qtype="redaction", poids=10.0)
    make_card(conn, "b-autre", qtype="exact", poids=0.5)

    queue = api.get_today_queue(conn, TODAY, serre)
    assert [i.card.id for i in queue] == ["a-enorme"], "elle prend la journée à elle seule"
    assert queue[0].points == 3
    assert sum(i.points for i in queue) <= serre.plafond_quotidien

    # Et elle passe vraiment: après réponse, le reste de la file est libre.
    api.record_answer(conn, "a-enorme", Rating.Good, reviewed_at=NOW, config=serre, correct=True)
    suivants = [i.card.id for i in api.get_today_queue(conn, TODAY, serre)]
    assert suivants == ["b-autre"]


def test_une_carte_lourde_finit_par_passer_meme_derriere_des_legeres(conn, config):
    """Une carte sautée par le glouton ne doit pas être oubliée: elle passe dès
    qu'elle est en tête de file (toujours possible: elle ne dépasse jamais le plafond)."""
    serre = replace(config, plafond_quotidien=3)
    make_card(conn, "z-lourde", qtype="redaction", poids=4.0)
    for i in range(5):
        make_card(conn, f"a-leg{i}", qtype="exact", poids=1.0)

    jour = TODAY
    vue = False
    for _ in range(4):
        moment = datetime.combine(jour, datetime.min.time(), tzinfo=timezone.utc)
        for item in api.get_today_queue(conn, jour, serre):
            vue |= item.card.id == "z-lourde"
            api.record_answer(conn, item.card.id, Rating.Easy, reviewed_at=moment, config=serre,
                              correct=True)
        jour += timedelta(days=1)
    assert vue


def test_poids_plafonne():
    assert poids_plafonne(4.0, 3.0) == 3.0
    assert poids_plafonne(0.5, 3.0) == 0.5
    assert poids_plafonne(4.0, 0) == 4.0  # pas de plafond configuré: inchangé


def test_la_dette_se_lisse_en_points(conn, config):
    """Le surplus reste dû: il repasse les jours suivants, sans rien perdre."""
    petit = replace(config, plafond_quotidien=10)
    for i in range(30):
        make_card(conn, f"c{i:02}", qtype="probleme", poids=None)  # 2 pts: 5 cartes/jour

    jour = TODAY
    par_jour = []
    for _ in range(8):
        moment = datetime.combine(jour, datetime.min.time(), tzinfo=timezone.utc)
        queue = api.get_today_queue(conn, jour, petit)
        par_jour.append(sum(i.points for i in queue))
        for item in queue:
            api.record_answer(conn, item.card.id, Rating.Easy, reviewed_at=moment, config=petit,
                              correct=True)
        jour += timedelta(days=1)

    assert max(par_jour) <= 10
    assert par_jour[0] == 10
    jamais = conn.execute("SELECT COUNT(*) AS n FROM cards WHERE last_review IS NULL").fetchone()["n"]
    assert jamais == 0, "toutes les cartes finissent par passer"


def test_priorite_aux_tests_proches_reste_vraie_en_points(conn, config):
    petit = replace(config, plafond_quotidien=2)
    stable = datetime(2026, 1, 1, tzinfo=timezone.utc)
    make_card(conn, "loin", cours="optique", stability=1.0, difficulty=5.0,
              last_review=stable, state=2)
    make_card(conn, "proche", cours="physique", stability=1.0, difficulty=5.0,
              last_review=stable, state=2)
    api.add_test(conn, cours="optique", jour=TODAY + timedelta(days=28), intitule="loin")
    api.add_test(conn, cours="physique", jour=TODAY + timedelta(days=7), intitule="proche")
    assert [i.card.id for i in api.get_today_queue(conn, TODAY, petit)][:1] == ["proche"]


# --- Stats ------------------------------------------------------------------

def test_stats_en_points_et_en_cartes(conn, config):
    for i in range(4):
        make_card(conn, f"p{i}", qtype="probleme", poids=None)       # 2 pts
    for i in range(10):
        make_card(conn, f"v{i}", qtype="exact", categorie="vocabulaire", poids=None)  # 0,1

    s = api.get_stats(conn, TODAY, config)

    assert s.nb_dues == 14
    assert s.points_dus == pytest.approx(4 * 2 + 10 * 0.1)
    assert s.points_jour == pytest.approx(9.0)
    assert s.cartes_jour == 14
    premier = s.charge_14j[0]
    assert (premier.cartes, premier.points) == (14, pytest.approx(9.0))
    assert len(s.charge_14j) == 14


def test_get_stats_respecte_le_plafond_en_points(conn, config):
    serre = replace(config, plafond_quotidien=6)
    for i in range(10):
        make_card(conn, f"p{i}", qtype="probleme", poids=None)
    s = api.get_stats(conn, TODAY, serre)
    assert all(c.points <= 6 + 1e-9 for c in s.charge_14j)
    assert s.points_dus == 20.0 and s.points_jour == 6.0
