"""Test de bout en bout: le simulateur doit produire un planning sain."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from flashcards.simulate import SimConfig, plot_charge, simulate

START = date(2026, 1, 5)


def test_simulation_bout_en_bout(config, tmp_path: Path):
    sim = SimConfig(jours=40, nb_cartes=120, graine=7, p_saut=0.1, saut_force_debut=15)
    result = simulate(tmp_path / "sim.db", config, sim, START)

    assert len(result.charge_par_jour) == 40
    assert result.total_revisions > 0
    # Le plafond quotidien doit être respecté, toujours.
    assert result.charge_max <= config.plafond_quotidien
    # Un élève dont la réussite suit la rétrivabilité FSRS doit tourner bien
    # au-dessus du hasard, sans être parfait.
    assert 0.5 < result.taux_reussite < 1.0


def test_aucune_carte_oubliee_avant_un_examen(config, tmp_path: Path):
    """Le clamp doit garantir qu'aucune carte au programme n'est jamais vue."""
    sim = SimConfig(jours=90, nb_cartes=200, graine=3, p_saut=0.1)
    result = simulate(tmp_path / "sim.db", config, sim, START)

    assert result.examens, "la simulation doit comporter des examens"
    for exam in result.examens:
        assert exam["nb_jamais_revisees_avant"] == 0, (
            f"{exam['intitule']}: {exam['nb_jamais_revisees_avant']} carte(s) jamais vue(s)"
        )
        assert exam["retention_moyenne"] > 0.8


def test_simulation_deterministe(config, tmp_path: Path):
    sim = SimConfig(jours=25, nb_cartes=80, graine=11)
    a = simulate(tmp_path / "a.db", config, sim, START)
    b = simulate(tmp_path / "b.db", config, sim, START)

    assert [n for _, n in a.charge_par_jour] == [n for _, n in b.charge_par_jour]
    assert a.total_revisions == b.total_revisions


def test_saut_de_3_jours_est_absorbe(config, tmp_path: Path):
    """Après 3 jours sautés la dette monte, puis redescend: rien n'est perdu."""
    sim = SimConfig(jours=60, nb_cartes=120, graine=5, p_saut=0.0, saut_force_debut=30)
    result = simulate(tmp_path / "sim.db", config, sim, START)

    effet = result.effet_saut_force
    assert effet, "l'effet du saut forcé doit être mesuré"
    assert effet["dette_apres_3j"] >= effet["dette_avant"]
    assert effet["jours_pour_resorber"] is not None, "la dette doit se résorber"
    assert len(result.jours_sautes) == 3


def test_plafond_respecte_meme_avec_dette(config, tmp_path: Path):
    serre = replace(config, plafond_quotidien=15)
    sim = SimConfig(jours=30, nb_cartes=200, graine=9, p_saut=0.0, saut_force_debut=None)
    result = simulate(tmp_path / "sim.db", serre, sim, START)

    assert result.charge_max <= 15
    assert result.jours_sautes == []


def test_graphique_est_produit(config, tmp_path: Path):
    sim = SimConfig(jours=20, nb_cartes=60, graine=1)
    result = simulate(tmp_path / "sim.db", config, sim, START)
    png = plot_charge(result, tmp_path / "charge.png")

    assert png.exists()
    assert png.stat().st_size > 1000


# --- Scénario « année »: points, entretien, examen de matière ----------------

@pytest.fixture(scope="module")
def annee120(tmp_path_factory):
    """Une seule simulation de 120 jours, partagée par les tests qui la lisent."""
    from flashcards.config import load_scheduler_config

    config = load_scheduler_config()
    chemin = tmp_path_factory.mktemp("annee") / "annee.db"
    sim = SimConfig(jours=120, graine=42, scenario="annee")
    return config, simulate(chemin, config, sim, START)


def _annee(config, tmp_path, jours=90, **kw):
    sim = SimConfig(jours=jours, graine=kw.pop("graine", 42), scenario="annee", **kw)
    return simulate(tmp_path / "annee.db", config, sim, START)


def test_scenario_annee_a_les_dimensions_demandees(config, tmp_path):
    r = _annee(config, tmp_path, jours=30).resume_scenario
    assert r["themes"] == 20 and r["matieres"] == 4
    assert r["vocabulaire"] == 300
    for type_ in ("probleme", "redaction"):
        assert r["par_type"][type_] > 0, "calcul et rédaction présents"


def test_scenario_annee_douze_themes_en_entretien_au_depart(config, tmp_path):
    result = _annee(config, tmp_path, jours=15)
    assert result.nb_themes_entretien_par_jour[0][1] == 12


def test_scenario_annee_plafond_et_garde_fous_respectes(annee120):
    config, result = annee120
    assert result.charge_max <= config.plafond_quotidien + 1e-9
    e = result.rapport_entretien
    assert e["jours_plafond_entretien_depasse"] == 0
    assert e["themes_jamais_revises"] == []
    assert result.rapport_charge["plus_lourde_carte"] <= config.plafond_quotidien
    # Aucune anomalie de plafond, quoi que dise le reste du rapport.
    assert not [a for a in result.anomalies if "> plafond" in a or "Plafond d'entretien" in a]


def test_scenario_annee_quota_d_entretien_tenu(annee120):
    """Hors semaines avec jour sauté, le plancher hebdomadaire est tenu presque partout."""
    e = annee120[1].rapport_entretien
    assert e["semaines_sans_saut"] > 50
    assert e["quota_tenu_sans_saut_pct"] >= 0.9
    assert 0.1 < e["part_moyenne"] < 0.5, "l'entretien existe sans écraser le reste"


def test_scenario_annee_la_rotation_est_equitable(annee120):
    e = annee120[1].rapport_entretien
    assert e["rotation_min"] > 0 and e["rotation_max"] <= 3 * e["rotation_min"]


def test_scenario_annee_examen_de_fin_d_annee_lie_a_toute_la_matiere(config, tmp_path):
    result = _annee(config, tmp_path, jours=110)
    final = next(x for x in result.examens if "fin d'année" in x["intitule"])
    assert final["nb_cartes"] == 288, "tous les thèmes d'Anglais, y compris les 200 de vocabulaire"
    assert final["nb_jamais_revisees_avant"] == 0
    assert final["retention_moyenne"] > 0.8


def test_scenario_annee_un_test_tardif_sort_un_theme_de_l_entretien(config, tmp_path):
    """« Info linux » reçoit un test au jour 40: de 12 thèmes en entretien, on passe à 13+ plus tard
    (autres thèmes qui finissent) mais jamais à moins pendant la fenêtre du test."""
    result = _annee(config, tmp_path, jours=70)
    avant = result.nb_themes_entretien_par_jour[39][1]
    pendant = result.nb_themes_entretien_par_jour[41][1]
    assert pendant == avant - 1


def test_scenario_annee_deterministe(config, tmp_path):
    a = _annee(config, tmp_path, jours=40)
    b = simulate(tmp_path / "b.db", config, SimConfig(jours=40, graine=42, scenario="annee"), START)
    assert [n for _, n in a.charge_par_jour] == [n for _, n in b.charge_par_jour]
    assert a.total_revisions == b.total_revisions


def test_scenario_annee_signale_une_capacite_d_entretien_insuffisante(annee120):
    """20 thèmes × 3 pts = 60 pts/semaine pour 56 de capacité: la simulation doit le dire."""
    result = annee120[1]
    assert any("Capacité d'entretien insuffisante" in a for a in result.anomalies)


def test_scenario_annee_capacite_suffisante_sans_alerte(config, tmp_path):
    large = replace(config, entretien_plafond_jour=12.0)
    result = _annee(large, tmp_path, jours=120)
    assert not any("Capacité d'entretien insuffisante" in a for a in result.anomalies)


def test_scenario_simple_inchange_et_en_points(config, tmp_path):
    sim = SimConfig(jours=30, nb_cartes=100, graine=2)
    result = simulate(tmp_path / "s.db", config, sim, START)
    assert len(result.cartes_par_jour) == len(result.charge_par_jour) == 30
    assert result.points_total > 0 and result.plafond == config.plafond_quotidien
    assert all(n <= config.plafond_quotidien + 1e-9 for _, n in result.charge_par_jour)
