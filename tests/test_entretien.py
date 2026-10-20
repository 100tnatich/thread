"""Mode entretien: entrée/sortie, quota hebdomadaire (plancher), plafond global, rotation.

Repères: TODAY = lundi 5 janvier 2026. La semaine d'entretien va du lundi 5 au
dimanche 11. Une carte « stable » (stabilité 200, revue il y a 40 jours) n'est
due dans aucune semaine de ces tests: seul le quota peut la faire apparaître.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from fsrs import Rating

from flashcards import api
from flashcards.heuristic import Exam
from flashcards.planning import bornes_semaine, themes_en_entretien

from .conftest import NOW, TODAY, make_card

LUNDI = TODAY  # 2026-01-05
VIEUX = datetime(2025, 11, 26, tzinfo=timezone.utc)  # il y a 40 jours


def stable(conn, cid: str, cours: str, *, poids: float | None = 0.5, qtype: str = "exact",
           revue: datetime = VIEUX, stabilite: float = 200.0, categorie: str | None = None):
    """Carte apprise, nulle part due: ne remonte que via le quota d'entretien."""
    return make_card(conn, cid, cours=cours, qtype=qtype, stability=stabilite, difficulty=5.0,
                     last_review=revue, state=2, poids=poids, categorie=categorie)


def jour_moment(jour: date) -> datetime:
    return datetime.combine(jour, datetime.min.time(), tzinfo=timezone.utc)


def repondre_a_tout(conn, config, jour: date, **kw):
    queue = api.get_today_queue(conn, jour, config, **kw)
    for item in queue:
        api.record_answer(conn, item.card.id, Rating.Good, reviewed_at=jour_moment(jour),
                          config=config, correct=True)
    return queue


def exam(cours: set[str], jours: int, n: int = 1) -> Exam:
    return Exam(id=n, cours=frozenset(cours), date=TODAY + timedelta(days=jours),
                intitule="TE")


# --- Entrée et sortie du mode entretien --------------------------------------

def test_semaine_lundi_dimanche():
    assert bornes_semaine(date(2026, 1, 5)) == (date(2026, 1, 5), date(2026, 1, 11))
    assert bornes_semaine(date(2026, 1, 11)) == (date(2026, 1, 5), date(2026, 1, 11))
    assert bornes_semaine(date(2026, 1, 7)) == (date(2026, 1, 5), date(2026, 1, 11))


def test_sans_aucun_test_un_theme_est_en_entretien():
    assert themes_en_entretien({"anglais"}, [], TODAY) == {"anglais"}


def test_tous_les_tests_passes_donnent_l_entretien():
    passes = [exam({"anglais"}, -3, 1), exam({"anglais"}, -30, 2)]
    assert themes_en_entretien({"anglais"}, passes, TODAY) == {"anglais"}


def test_un_test_a_venir_garde_le_mode_test_y_compris_le_jour_meme():
    assert themes_en_entretien({"anglais"}, [exam({"anglais"}, 0)], TODAY) == frozenset()
    assert themes_en_entretien({"anglais"}, [exam({"anglais"}, 12)], TODAY) == frozenset()


def test_un_test_futur_ajoute_fait_sortir_de_l_entretien_puis_on_y_revient(conn, config):
    stable(conn, "c1", "anglais")
    assert api.plan_du_jour(conn, TODAY, config).themes_entretien == {"anglais"}

    exam_id = api.add_test(conn, cours="anglais", jour=TODAY + timedelta(days=20), intitule="TE")
    en_test = api.plan_du_jour(conn, TODAY, config)
    assert en_test.themes_entretien == frozenset()
    assert en_test.quotas == {}

    # Le test passé, le thème retombe en entretien.
    apres = TODAY + timedelta(days=21)
    assert api.plan_du_jour(conn, apres, config).themes_entretien == {"anglais"}
    api.remove_test(conn, exam_id)
    assert api.plan_du_jour(conn, TODAY, config).themes_entretien == {"anglais"}


def test_un_theme_archive_n_est_jamais_en_entretien(conn, config):
    stable(conn, "c1", "ancien")
    api.archive_cours(conn, "ancien")
    plan = api.plan_du_jour(conn, TODAY, config)
    assert plan.themes_entretien == frozenset() and plan.items == []


def test_une_carte_neuve_n_est_pas_une_carte_d_entretien(conn, config):
    """Une carte jamais révisée est un apprentissage: circuit normal, pas de plafond d'entretien."""
    petit = replace(config, entretien_plafond_jour=0.5)
    for i in range(5):
        make_card(conn, f"n{i}", cours="anglais")  # neuves, thème sans test
    queue = api.get_today_queue(conn, TODAY, petit)
    assert len(queue) == 5
    assert not any(i.entretien for i in queue)


# --- Quota hebdomadaire ------------------------------------------------------

def test_quota_hebdomadaire_tenu_et_etale_sur_la_semaine(conn, config):
    """3 points par semaine et par thème, répartis plutôt que posés le même jour."""
    for i in range(20):
        stable(conn, f"c{i:02}", "anglais")  # 20 cartes de 0,5 pt

    par_jour = []
    for k in range(7):
        jour = LUNDI + timedelta(days=k)
        queue = repondre_a_tout(conn, config, jour)
        assert all(i.entretien for i in queue)
        par_jour.append(sum(i.points for i in queue))

    total = sum(par_jour)
    assert total >= config.entretien_quota_hebdo, f"quota non tenu: {par_jour}"
    assert total <= config.entretien_quota_hebdo + 0.5 + 1e-9, "on ne doit pas dépasser d'une carte"
    assert max(par_jour) <= 1.0, f"tout est tombé le même jour: {par_jour}"
    assert sum(1 for n in par_jour if n > 0) >= 4, f"mal étalé: {par_jour}"


def test_le_plancher_ne_rajoute_rien_si_fsrs_a_deja_planifie_assez(conn, config):
    """8 cartes de 0,5 pt dues mercredi (4 pts >= quota 3): lundi, aucun complément."""
    veille = datetime(2026, 1, 4, tzinfo=timezone.utc)
    for i in range(8):
        stable(conn, f"d{i}", "anglais", revue=veille, stabilite=3.0)  # due le mercredi 7
    stable(conn, "loin", "anglais")  # candidate à un complément, si on en voulait

    plan = api.plan_du_jour(conn, LUNDI, config)
    assert plan.complements == []
    assert plan.items == []
    assert plan.quotas["anglais"].planifie_fsrs == pytest.approx(4.0)

    # Mercredi, FSRS les met bien à l'échéance: elles passent normalement.
    mercredi = api.get_today_queue(conn, LUNDI + timedelta(days=2), config)
    assert mercredi and all(not i.complement for i in mercredi)


def test_le_quota_est_un_plancher_pas_un_plafond(conn, config):
    """FSRS a planifié bien plus que 3 pts: tout ce qui est dû passe quand même."""
    veille = datetime(2026, 1, 4, tzinfo=timezone.utc)
    for i in range(30):
        stable(conn, f"d{i:02}", "anglais", revue=veille - timedelta(days=60), stabilite=3.0)
    # 30 cartes dues depuis longtemps (15 pts): dans la limite du plafond d'entretien.
    queue = api.get_today_queue(conn, LUNDI, config)
    assert sum(i.points for i in queue) == pytest.approx(config.entretien_plafond_jour)
    assert all(not i.complement for i in queue)


def test_quota_nul_desactive_les_complements(conn, config):
    zero = replace(config, entretien_quota_hebdo=0.0)
    stable(conn, "c1", "anglais")
    assert api.get_today_queue(conn, LUNDI, zero) == []


def test_complement_prefere_les_cartes_legeres_meme_moins_oubliees(conn, config):
    """Poids <= entretien_poids_max d'abord, même si une carte lourde est plus oubliée."""
    stable(conn, "lourde", "anglais", qtype="redaction", poids=4.0, stabilite=600.0,
           revue=VIEUX - timedelta(days=200))  # la plus oubliée, mais pas due
    stable(conn, "legere", "anglais", poids=0.5)
    plan = api.plan_du_jour(conn, LUNDI, config)
    assert [i.card.id for i in plan.complements] == ["legere"]


def test_repli_sur_les_cartes_lourdes_si_rien_d_autre(conn, config):
    stable(conn, "r1", "francais", qtype="redaction", poids=4.0)
    stable(conn, "r2", "francais", qtype="redaction", poids=4.0)
    plan = api.plan_du_jour(conn, LUNDI, config)
    assert len(plan.complements) == 1
    assert plan.complements[0].card.type == "redaction"


def test_complement_choisit_la_plus_faible_retention(conn, config):
    stable(conn, "oublie", "anglais", revue=VIEUX - timedelta(days=100))
    stable(conn, "frais", "anglais", revue=VIEUX + timedelta(days=30))
    plan = api.plan_du_jour(conn, LUNDI, config)
    assert plan.complements[0].card.id == "oublie"


def test_les_cartes_deja_revues_cette_semaine_ne_sont_pas_rechoisies(conn, config):
    stable(conn, "a", "anglais")
    stable(conn, "b", "anglais")
    # Revue mardi: dans la semaine, déjà comptée dans le quota fait.
    api.record_answer(conn, "a", Rating.Good, reviewed_at=jour_moment(LUNDI + timedelta(days=1)),
                      config=config, correct=True)
    plan = api.plan_du_jour(conn, LUNDI + timedelta(days=2), config)
    assert "a" not in [i.card.id for i in plan.items]


def test_rouvrir_la_file_apres_la_session_ne_fait_pas_reapparaitre_de_complements(conn, config):
    for i in range(20):
        stable(conn, f"c{i:02}", "anglais")
    premiere = repondre_a_tout(conn, config, LUNDI)
    assert premiere, "il y a bien un complément ce jour-là"
    assert api.get_today_queue(conn, LUNDI, config) == []


def test_le_dernier_jour_de_la_semaine_tout_le_manque_est_du(conn, config):
    """Dimanche: il reste 1 jour, donc toute la part manquante est due ce jour-là."""
    for i in range(20):
        stable(conn, f"c{i:02}", "anglais")
    dimanche = LUNDI + timedelta(days=6)
    plan = api.plan_du_jour(conn, dimanche, config)
    assert sum(i.points for i in plan.complements) >= config.entretien_quota_hebdo
    assert plan.quotas["anglais"].allocation_jour == pytest.approx(3.0)


# --- Priorité: l'entretien passe après le mode test, sauf le plancher --------

def test_les_cartes_d_entretien_dues_passent_apres_le_mode_test(conn, config):
    sans_plancher = replace(config, entretien_quota_hebdo=0.0, plafond_quotidien=3.0)
    vieux = datetime(2025, 11, 1, tzinfo=timezone.utc)
    for i in range(3):
        make_card(conn, f"ent{i}", cours="ancien", stability=1.0, difficulty=5.0,
                  last_review=vieux, state=2)  # dues, thème en entretien
        make_card(conn, f"test{i}", cours="actuel", stability=1.0, difficulty=5.0,
                  last_review=vieux, state=2)  # dues, thème en mode test
    api.add_test(conn, cours="actuel", jour=TODAY + timedelta(days=30), intitule="TE")

    ids = [i.card.id for i in api.get_today_queue(conn, LUNDI, sans_plancher)]
    assert sorted(ids) == ["test0", "test1", "test2"], "le budget est pris par le mode test"


def test_le_plancher_reserve_sa_part_avant_le_mode_test(conn, config):
    """Même une journée saturée de tests laisse passer la part du jour du plancher."""
    serre = replace(config, plafond_quotidien=3.0)
    vieux = datetime(2025, 11, 1, tzinfo=timezone.utc)
    for i in range(6):
        make_card(conn, f"test{i}", cours="actuel", stability=1.0, difficulty=5.0,
                  last_review=vieux, state=2)  # 6 pts dus pour 3 de budget
    api.add_test(conn, cours="actuel", jour=TODAY + timedelta(days=30), intitule="TE")
    stable(conn, "ent", "ancien")

    queue = api.get_today_queue(conn, LUNDI, serre)
    assert queue[0].card.id == "ent" and queue[0].plancher and queue[0].complement
    assert sum(i.points for i in queue) <= 3.0 + 1e-9
    assert len(queue) == 3, "la réserve (0,5) + 2 cartes de test (2 pts) tiennent dans 3"


def test_le_plafond_quotidien_total_n_est_jamais_depasse(conn, config):
    serre = replace(config, plafond_quotidien=4.0, entretien_quota_hebdo=20.0,
                    entretien_plafond_jour=8.0)
    for i in range(30):
        stable(conn, f"e{i:02}", "ancien", poids=1.0)
    for i in range(10):
        make_card(conn, f"n{i}", cours="actuel", poids=1.0)
    for k in range(7):
        jour = LUNDI + timedelta(days=k)
        queue = repondre_a_tout(conn, serre, jour)
        assert sum(i.points for i in queue) <= 4.0 + 1e-9


# --- Plafond global d'entretien et rotation ----------------------------------

def test_plafond_global_d_entretien_par_jour(conn, config):
    exigeant = replace(config, entretien_quota_hebdo=14.0, entretien_plafond_jour=4.0)
    for t in range(6):
        for i in range(20):
            stable(conn, f"t{t}c{i:02}", f"theme{t}", poids=1.0)
    for k in range(7):
        jour = LUNDI + timedelta(days=k)
        queue = repondre_a_tout(conn, exigeant, jour)
        assert sum(i.points for i in queue if i.entretien) <= 4.0 + 1e-9, f"jour {k}"
        assert len(queue) == 4, "6 thèmes veulent 2 pts/jour chacun: le plafond (4) limite"


def test_le_plafond_d_entretien_est_debite_des_revisions_deja_faites_aujourd_hui(conn, config):
    serre = replace(config, entretien_quota_hebdo=14.0, entretien_plafond_jour=2.0)
    for t in range(3):
        for i in range(10):
            stable(conn, f"t{t}c{i}", f"theme{t}", poids=1.0)
    assert len(repondre_a_tout(conn, serre, LUNDI)) == 2
    assert api.get_today_queue(conn, LUNDI, serre) == [], "le plafond du jour est consommé"


def test_rotation_le_theme_revu_le_plus_anciennement_passe_en_premier(conn, config):
    """Plafond d'une seule carte par jour: chaque thème passe à tour de rôle."""
    une_carte = replace(config, entretien_quota_hebdo=14.0, entretien_plafond_jour=1.0)
    anciennetes = {"a-vieux": 60, "b-moyen": 50, "c-recent": 45}
    for theme, jours in anciennetes.items():
        for i in range(10):
            stable(conn, f"{theme}-{i}", theme, poids=1.0, revue=NOW - timedelta(days=jours + i))

    ordre = []
    for k in range(3):
        queue = repondre_a_tout(conn, une_carte, LUNDI + timedelta(days=k))
        assert len(queue) == 1
        ordre.append(queue[0].card.cours)

    assert ordre == ["a-vieux", "b-moyen", "c-recent"]


def test_aucune_carte_n_est_supprimee_ni_perdue_par_l_entretien(conn, config):
    for i in range(12):
        stable(conn, f"c{i:02}", "anglais")
    avant = conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]
    for k in range(14):
        repondre_a_tout(conn, config, LUNDI + timedelta(days=k))
    apres = conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]
    assert avant == apres == 12


def test_une_reponse_en_entretien_met_a_jour_fsrs_comme_toute_revision(conn, config):
    stable(conn, "c1", "anglais")
    avant = conn.execute("SELECT fsrs_stability, last_review FROM cards WHERE id='c1'").fetchone()
    item = api.get_today_queue(conn, LUNDI, config)[0]
    assert item.card.id == "c1" and item.complement

    api.record_answer(conn, "c1", Rating.Good, reviewed_at=jour_moment(LUNDI), config=config,
                      correct=True)

    apres = conn.execute("SELECT fsrs_stability, last_review FROM cards WHERE id='c1'").fetchone()
    assert apres["last_review"] == jour_moment(LUNDI).isoformat() != avant["last_review"]
    assert apres["fsrs_stability"] != avant["fsrs_stability"]
    assert conn.execute("SELECT COUNT(*) AS n FROM reviews WHERE card_id='c1'").fetchone()["n"] == 1


# --- Visibilité (today / stats) ----------------------------------------------

def test_la_part_d_entretien_est_exposee(conn, config):
    for i in range(20):
        stable(conn, f"e{i:02}", "ancien")
    for i in range(4):
        make_card(conn, f"n{i}", cours="actuel", poids=1.0)
    api.add_test(conn, cours="actuel", jour=TODAY + timedelta(days=20), intitule="TE")

    plan = api.plan_du_jour(conn, LUNDI, config)
    assert plan.points_entretien == pytest.approx(0.5)
    assert plan.nb_entretien == 1
    assert plan.part_entretien == pytest.approx(0.5 / 4.5)

    s = api.get_stats(conn, LUNDI, config)
    assert s.themes_entretien == ["ancien"]
    assert s.part_entretien == pytest.approx(0.5 / 4.5)
    assert s.charge_14j[0].points_entretien == pytest.approx(0.5)
    assert sum(c.points_entretien for c in s.charge_14j) >= 3.0, "le quota se retrouve dans la prévision"


def test_alerte_quand_les_quotas_depassent_la_capacite(config):
    serre = replace(config, entretien_quota_hebdo=3.0, entretien_plafond_jour=1.0)
    assert api.alertes_entretien({"a", "b", "c"}, serre)  # 9 pts demandés, 7 de capacité
    assert not api.alertes_entretien({"a", "b"}, serre)  # 6 <= 7
