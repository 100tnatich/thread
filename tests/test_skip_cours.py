"""Skip, ordre de session, CRUD des cours, désactivation d'une question."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fsrs import Rating

from flashcards import api

from .conftest import NOW, TODAY, make_card


# --- Skip ------------------------------------------------------------------

def test_skip_ne_touche_pas_fsrs(conn, config):
    """Un skip n'est pas une réponse: aucun effet sur l'état de la carte."""
    make_card(conn, "c1")
    avant = conn.execute(
        "SELECT fsrs_state, fsrs_stability, fsrs_difficulty, last_review FROM cards WHERE id='c1'"
    ).fetchone()

    api.skip_card(conn, "sess1", "c1", jour=TODAY, moment=NOW, raison="feuille")

    apres = conn.execute(
        "SELECT fsrs_state, fsrs_stability, fsrs_difficulty, last_review FROM cards WHERE id='c1'"
    ).fetchone()
    assert dict(avant) == dict(apres)
    assert conn.execute("SELECT COUNT(*) AS n FROM reviews").fetchone()["n"] == 0


def test_skip_remet_la_question_en_fin_de_file(conn, config):
    for i in range(3):
        make_card(conn, f"c{i}")

    avant = [i.card.id for i in api.get_today_queue(conn, TODAY, config, ordre="par_cours")]
    api.skip_card(conn, "sess1", avant[0], jour=TODAY, moment=NOW)

    apres = [
        i.card.id
        for i in api.get_today_queue(
            conn, TODAY, config, ordre="par_cours", session_id="sess1"
        )
    ]
    assert apres[-1] == avant[0], "la question passée doit repasser en fin de file"
    assert set(apres) == set(avant), "aucune question ne doit disparaître"


def test_skip_limite_pour_eviter_une_boucle_infinie(conn, config):
    make_card(conn, "c1")
    make_card(conn, "c2")

    for _ in range(api.MAX_SKIPS_PAR_SESSION):
        api.skip_card(conn, "sess1", "c1", jour=TODAY, moment=NOW)

    ids = [
        i.card.id
        for i in api.get_today_queue(conn, TODAY, config, session_id="sess1", ordre="par_cours")
    ]
    assert "c1" not in ids, "au-delà de la limite, la question ne reboucle plus"
    assert "c2" in ids


def test_skip_reste_du_le_lendemain(conn, config):
    """Une question passée et jamais traitée reste simplement due."""
    make_card(conn, "c1")
    api.skip_card(conn, "sess1", "c1", jour=TODAY, moment=NOW)

    demain = TODAY + timedelta(days=1)
    ids = [i.card.id for i in api.get_today_queue(conn, demain, config)]
    assert "c1" in ids


def test_skip_compte_les_passages(conn, config):
    make_card(conn, "c1")
    assert api.skip_card(conn, "s", "c1", jour=TODAY, moment=NOW) == 1
    assert api.skip_card(conn, "s", "c1", jour=TODAY, moment=NOW) == 2


def test_skip_carte_inconnue(conn, config):
    with pytest.raises(RuntimeError, match="inconnue"):
        api.skip_card(conn, "s", "fantome", jour=TODAY, moment=NOW)


# --- Ordre de session ------------------------------------------------------

def test_ordre_par_cours_regroupe(conn, config):
    make_card(conn, "a1", cours="maths")
    make_card(conn, "b1", cours="physique")
    make_card(conn, "a2", cours="maths")
    make_card(conn, "b2", cours="physique")

    queue = api.get_today_queue(conn, TODAY, config, ordre=api.ORDRE_PAR_COURS)
    cours_vus = [i.card.cours for i in queue]
    # Chaque cours doit former un bloc contigu.
    assert cours_vus == sorted(cours_vus)


def test_ordre_melange_change_l_ordre_mais_pas_le_contenu(conn, config):
    for i in range(20):
        make_card(conn, f"c{i:02}")

    trie = api.get_today_queue(conn, TODAY, config, ordre=api.ORDRE_PAR_COURS)
    melange = api.get_today_queue(conn, TODAY, config, ordre=api.ORDRE_MELANGE, graine=1)

    assert {i.card.id for i in trie} == {i.card.id for i in melange}
    assert [i.card.id for i in trie] != [i.card.id for i in melange]


def test_ordre_melange_est_reproductible_a_graine_egale(conn, config):
    for i in range(10):
        make_card(conn, f"c{i:02}")
    a = api.get_today_queue(conn, TODAY, config, ordre=api.ORDRE_MELANGE, graine=7)
    b = api.get_today_queue(conn, TODAY, config, ordre=api.ORDRE_MELANGE, graine=7)
    assert [i.card.id for i in a] == [i.card.id for i in b]


def test_ordre_inconnu_leve_une_erreur(conn, config):
    make_card(conn, "c1")
    with pytest.raises(RuntimeError, match="Ordre"):
        api.get_today_queue(conn, TODAY, config, ordre="n_importe_quoi")


def test_ordre_ne_change_pas_la_selection(conn, config):
    """L'ordre est cosmétique: il ne doit pas modifier quelles cartes sont dues."""
    from dataclasses import replace

    petit = replace(config, plafond_quotidien=5)
    for i in range(20):
        make_card(conn, f"c{i:02}")

    a = api.get_today_queue(conn, TODAY, petit, ordre=api.ORDRE_PAR_COURS)
    b = api.get_today_queue(conn, TODAY, petit, ordre=api.ORDRE_MELANGE, graine=3)
    assert len(a) == len(b) == 5


# --- Cours -----------------------------------------------------------------

def test_crud_cours(conn, config):
    api.add_cours(conn, "chimie", "#4ADE9F")
    noms = [c.nom for c in api.list_cours(conn)]
    assert "chimie" in noms

    api.update_cours(conn, "chimie", couleur="#FF6B7A")
    chimie = next(c for c in api.list_cours(conn) if c.nom == "chimie")
    assert chimie.couleur == "#FF6B7A"

    api.delete_cours(conn, "chimie")
    assert "chimie" not in [c.nom for c in api.list_cours(conn)]


def test_cours_doublon_refuse(conn, config):
    api.add_cours(conn, "chimie")
    with pytest.raises(RuntimeError, match="existe déjà"):
        api.add_cours(conn, "chimie")


def test_cours_nom_vide_refuse(conn, config):
    with pytest.raises(RuntimeError, match="vide"):
        api.add_cours(conn, "   ")


def test_renommer_un_cours_propage_aux_cartes_et_tests(conn, config):
    make_card(conn, "c1", cours="physique")
    api.add_test(
        conn, cours="physique", jour=TODAY + timedelta(days=5),
        intitule="TE",
    )

    api.update_cours(conn, "physique", nouveau_nom="physique-appliquee")

    assert api.load_cards(conn)[0].cours == "physique-appliquee"
    assert api.load_exams(conn)[0].cours == frozenset({"physique-appliquee"})
    assert "physique" not in [c.nom for c in api.list_cours(conn)]


def test_supprimer_un_cours_avec_reaffectation(conn, config):
    make_card(conn, "c1", cours="physique")
    api.add_cours(conn, "sciences")

    nb = api.delete_cours(conn, "physique", reaffecter_vers="sciences")

    assert nb == 1
    assert api.load_cards(conn)[0].cours == "sciences"
    assert "physique" not in [c.nom for c in api.list_cours(conn)]


def test_supprimer_un_cours_sans_reaffectation_supprime_ses_cartes(conn, config):
    make_card(conn, "c1", cours="physique")
    nb = api.delete_cours(conn, "physique")
    assert nb == 1
    assert api.load_cards(conn) == []


def test_reaffectation_vers_cours_inconnu_refusee(conn, config):
    make_card(conn, "c1", cours="physique")
    with pytest.raises(RuntimeError, match="inconnu"):
        api.delete_cours(conn, "physique", reaffecter_vers="inexistant")


# --- Question désactivée ---------------------------------------------------

def test_question_mauvaise_sort_des_revisions(conn, config):
    make_card(conn, "c1")
    make_card(conn, "c2")
    assert len(api.get_today_queue(conn, TODAY, config)) == 2

    api.set_question_desactivee(conn, "c1", True, note="énoncé faux")

    ids = [i.card.id for i in api.get_today_queue(conn, TODAY, config)]
    assert ids == ["c2"]


def test_question_reactivee_revient(conn, config):
    make_card(conn, "c1")
    api.set_question_desactivee(conn, "c1", True)
    assert api.get_today_queue(conn, TODAY, config) == []

    api.set_question_desactivee(conn, "c1", False)
    assert len(api.get_today_queue(conn, TODAY, config)) == 1


def test_filtrer_la_file_par_cours(conn, config):
    make_card(conn, "m1", cours="maths")
    make_card(conn, "p1", cours="physique")

    queue = api.get_today_queue(conn, TODAY, config, cours="maths")
    assert [i.card.id for i in queue] == ["m1"]
