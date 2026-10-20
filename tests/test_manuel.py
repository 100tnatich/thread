"""Workflow manuel: validation de fichiers produits par Claude Code, sans LLM.

`validate` lit et rapporte, rien d'autre: ces tests vérifient aussi qu'il ne
modifie aucun fichier et qu'aucun appel au wrapper LLM n'a lieu.
"""
from __future__ import annotations

import copy
import json

import pytest
from typer.testing import CliRunner

from flashcards import manuel
from flashcards.cli import app

from .conftest import question_dict
from .fabriques_manuel import (
    analyser, ecrire, fichier_concepts, fichier_questions, q_cloze, q_exact,
    q_libre, q_probleme, q_qcm, q_redaction,
)

pytestmark = pytest.mark.usefixtures("pas_de_llm")

runner = CliRunner()


# --- Fichier de questions valide -------------------------------------------

def test_fichier_brut_valide_avec_les_six_types():
    obj = fichier_questions([
        q_qcm(), q_exact(), q_cloze(), q_probleme(), q_libre(), q_redaction(),
    ])
    analyse = analyser(obj)

    assert analyse.rapport.valide, analyse.rapport.erreurs
    assert analyse.resultat is not None
    assert analyse.rapport.par_type == {
        "qcm": 1, "exact": 1, "cloze": 1, "probleme": 1, "libre_llm": 1, "redaction": 1,
    }
    # L'en-tête est reporté sur chaque question, et l'id est généré.
    for question in analyse.resultat.questions:
        assert question.cours == "physique"
        assert question.source.fichier == "cours.pdf"
        assert question.id and question.id != "provisoire"


def test_ids_derives_du_contenu_sont_stables():
    obj = fichier_questions([q_exact(), q_qcm()])
    ids1 = [q.id for q in analyser(obj).resultat.questions]
    ids2 = [q.id for q in analyser(copy.deepcopy(obj)).resultat.questions]
    assert ids1 == ids2
    assert len(set(ids1)) == 2


def test_fichier_complet_du_prototype_1_reste_valide():
    obj = {
        "source": "cours.pdf", "cours": "physique",
        "questions": [question_dict("q1"), question_dict("q2")],
        "echecs": [], "avertissements": [],
    }
    analyse = analyser(obj)
    assert analyse.rapport.valide
    assert [q.id for q in analyse.resultat.questions] == ["q1", "q2"]  # ids conservés


def test_cours_peut_etre_force():
    analyse = analyser(fichier_questions([q_exact()]), cours="maths")
    assert analyse.resultat.questions[0].cours == "maths"


def test_source_absente_utilise_le_nom_du_fichier_avec_avertissement():
    obj = fichier_questions([q_exact()])
    del obj["source"]
    analyse = analyser(obj)
    assert analyse.rapport.valide
    assert analyse.resultat.questions[0].source.fichier == "x_questions.json"
    assert any("source" in a for a in analyse.rapport.avertissements)


# --- Fichier de questions invalide -----------------------------------------

def test_erreurs_par_question_avec_position_et_champ_manquant():
    sans_propositions = q_qcm("c2")
    del sans_propositions["propositions"]
    obj = fichier_questions([q_exact("c1"), sans_propositions, q_exact("c3", "Autre ?")])

    analyse = analyser(obj)
    r = analyse.rapport

    assert not r.valide
    assert analyse.resultat is None
    assert [e.position for e in r.erreurs] == [2]
    assert r.erreurs[0].reference == "c2"
    assert "champ manquant: propositions" in r.erreurs[0].messages
    assert r.nb_erreurs == 1


def test_type_inconnu_signale():
    mauvais = q_exact()
    mauvais["type"] = "devinette"
    r = analyser(fichier_questions([mauvais])).rapport
    assert not r.valide
    assert "inconnu" in r.erreurs[0].messages[0]


def test_confiance_invalide_signalee():
    r = analyser(fichier_questions([q_exact(confiance="certaine")])).rapport
    assert not r.valide
    assert any("confiance" in m for m in r.erreurs[0].messages)


def test_qcm_bonne_reponse_hors_propositions_est_une_erreur():
    q = q_qcm()
    q["bonnes_reponses"] = [4]
    r = analyser(fichier_questions([q])).rapport
    assert not r.valide
    assert "hors des propositions" in r.erreurs[0].messages[0]


def test_cloze_trou_et_valeurs_incoherents():
    q = q_cloze()
    q["valeurs_attendues"] = {"c2": "R·I"}
    r = analyser(fichier_questions([q])).rapport
    assert not r.valide
    assert "valeurs_attendues" in r.erreurs[0].messages[0]

    sans_trou = q_cloze()
    sans_trou["texte"] = "U = R·I"
    assert not analyser(fichier_questions([sans_trou])).rapport.valide


def test_champ_vide_et_liste_vide():
    vide = q_exact(enonce="   ")
    r = analyser(fichier_questions([vide])).rapport
    assert "champ vide: enonce" in r.erreurs[0].messages

    sans_etapes = q_probleme()
    sans_etapes["solution_etapes"] = []
    r = analyser(fichier_questions([sans_etapes])).rapport
    assert "liste vide: solution_etapes" in r.erreurs[0].messages


def test_champ_inconnu_est_un_avertissement_pas_une_erreur():
    r = analyser(fichier_questions([q_exact(difficulte="dure")])).rapport
    assert r.valide
    assert any("difficulte" in a for a in r.avertissements)


def test_json_casse_et_mauvaise_forme():
    r = manuel.analyser("{ pas du json", nom="x.json").rapport
    assert not r.valide and "JSON invalide" in r.erreurs_fichier[0]

    r = analyser({"quelque": "chose"}).rapport
    assert not r.valide and r.nature == "inconnu"

    r = analyser(fichier_questions([])).rapport
    assert not r.valide and "aucune question" in r.erreurs_fichier[0]


def test_cours_manquant_partout_est_une_erreur_de_fichier():
    obj = fichier_questions([q_exact()])
    del obj["cours"]
    r = analyser(obj).rapport
    assert not r.valide
    assert any("cours" in e for e in r.erreurs_fichier)


def test_id_duplique_avec_contenu_different_est_une_erreur():
    a, b = question_dict("q1"), question_dict("q1")
    b["enonce"] = "Tout autre énoncé"
    r = analyser({
        "source": "c.pdf", "cours": "physique", "questions": [a, b],
    }).rapport
    assert not r.valide
    assert "déjà utilisé" in r.erreurs[0].messages[0]


# --- Doublons ---------------------------------------------------------------

def test_doublon_dans_le_fichier_signale():
    obj = fichier_questions([
        q_exact("c1", "Unité de R ?"),
        q_exact("c1", "  Unité   de R ?  "),  # mêmes mots, espaces différents
    ])
    analyse = analyser(obj)
    r = analyse.rapport

    assert r.valide
    assert [a.statut for a in r.apercu] == ["nouvelle", "doublon_fichier"]
    assert any("doublon" in a for a in r.avertissements)
    # Même contenu => même id: l'import n'en créera qu'une.
    ids = {q.id for q in analyse.resultat.questions}
    assert len(ids) == 1


def test_l_empreinte_ignore_l_identite_mais_pas_le_contenu():
    a = {"type": "exact", "enonce": "Q ?", "reponse_exacte": "A", "id": "1", "concept_id": "c1"}
    b = {**a, "id": "2", "concept_id": "c9", "confiance": "basse"}
    c = {**a, "reponse_exacte": "B"}
    assert manuel.empreinte_contenu(a) == manuel.empreinte_contenu(b)
    assert manuel.empreinte_contenu(a) != manuel.empreinte_contenu(c)


# --- Concepts: couverture, confiance basse ---------------------------------

def test_couverture_confiance_basse_et_concepts_sans_question():
    concepts = json.dumps(fichier_concepts("c1", "c2", "c3"))
    obj = fichier_questions([
        q_exact("c1"), q_qcm("c1"), q_probleme("c3", confiance="basse"),
    ])
    r = analyser(obj, concepts_contenu=concepts, concepts_nom="x_concepts.json").rapport

    assert r.valide
    assert {l.concept_id: l.nb_questions for l in r.couverture} == {"c1": 2, "c2": 0, "c3": 1}
    assert [l.concept_id for l in r.concepts_sans_question] == ["c2"]
    assert [q.concept_id for q in r.confiance_basse] == ["c3"]
    assert r.concepts_fichier == "x_concepts.json"


def test_concept_id_hors_de_la_liste_est_une_erreur():
    concepts = json.dumps(fichier_concepts("c1"))
    r = analyser(fichier_questions([q_exact("c99")]), concepts_contenu=concepts).rapport
    assert not r.valide
    assert "absent de la liste de concepts" in r.erreurs[0].messages[0]


def test_liste_de_concepts_non_validee_avertit():
    concepts = json.dumps(fichier_concepts("c1", validated=False))
    r = analyser(fichier_questions([q_exact("c1")]), concepts_contenu=concepts).rapport
    assert r.valide
    assert any("validated" in a for a in r.avertissements)


def test_fichier_de_concepts_invalide_signale():
    r = analyser(fichier_questions([q_exact()]), concepts_contenu="{ casse").rapport
    assert not r.valide
    assert any("concepts" in e for e in r.erreurs_fichier)


# --- Fichier de concepts ----------------------------------------------------

def test_validation_d_un_fichier_de_concepts():
    analyse = manuel.analyser(json.dumps(fichier_concepts("c1", "c2")), nom="x_concepts.json")
    r = analyse.rapport
    assert r.valide and r.nature == "concepts" and r.nb_elements == 2
    assert r.concepts_valides_par_nate is True
    assert analyse.concepts is not None


def test_concepts_non_valides_par_nate_est_visible():
    r = manuel.analyser(
        json.dumps(fichier_concepts("c1", validated=False)), nom="c.json"
    ).rapport
    assert r.valide and r.concepts_valides_par_nate is False
    assert "NON" in manuel.rapport_en_texte(r)


def test_concepts_ids_dupliques_et_champs_absents():
    obj = fichier_concepts("c1", "c1")
    del obj["concepts"][1]["importance"]
    r = manuel.analyser(json.dumps(obj), nom="c.json").rapport
    assert not r.valide
    assert "déjà utilisé" in r.erreurs[0].messages[0]
    assert any("importance" in a for a in r.avertissements)


def test_concept_sans_id_signale():
    obj = fichier_concepts("c1")
    del obj["concepts"][0]["id"]
    r = manuel.analyser(json.dumps(obj), nom="c.json").rapport
    assert not r.valide
    assert "champ manquant: id" in r.erreurs[0].messages


# --- Commande `validate` ----------------------------------------------------

def test_cli_validate_fichier_valide_et_ne_modifie_rien(tmp_path):
    q = ecrire(tmp_path / "ch1_questions.json", fichier_questions([q_exact("c1"), q_qcm("c2")]))
    c = ecrire(tmp_path / "ch1_concepts.json", fichier_concepts("c1", "c2", "c3"))
    avant = (q.read_bytes(), c.read_bytes(), sorted(p.name for p in tmp_path.iterdir()))

    res = runner.invoke(app, ["validate", str(q)])

    assert res.exit_code == 0, res.output
    assert "VALIDE" in res.output
    # Les concepts voisins sont repérés tout seuls: couverture affichée.
    assert "AUCUNE QUESTION" in res.output and "c3" in res.output
    assert avant == (q.read_bytes(), c.read_bytes(), sorted(p.name for p in tmp_path.iterdir()))


def test_cli_validate_fichier_invalide_sort_en_erreur(tmp_path):
    mauvais = q_qcm()
    del mauvais["propositions"]
    q = ecrire(tmp_path / "ch1_questions.json", fichier_questions([q_exact(), mauvais]))
    avant = q.read_bytes()

    res = runner.invoke(app, ["validate", str(q)])

    assert res.exit_code == 1
    assert "INVALIDE" in res.output
    assert "Question n°2" in res.output and "champ manquant: propositions" in res.output
    assert q.read_bytes() == avant


def test_cli_validate_concepts_explicites(tmp_path):
    q = ecrire(tmp_path / "quelconque.json", fichier_questions([q_exact("c1")]))
    c = ecrire(tmp_path / "autre.json", fichier_concepts("c1", "c2"))
    res = runner.invoke(app, ["validate", str(q), "--concepts", str(c)])
    assert res.exit_code == 0, res.output
    assert "Concepts sans question : 1 sur 2" in res.output


def test_cli_validate_fichier_absent(tmp_path):
    res = runner.invoke(app, ["validate", str(tmp_path / "absent.json")])
    assert res.exit_code != 0


# --- Commande `prompt` ------------------------------------------------------

def test_cli_prompt_extraction_rend_les_variables():
    res = runner.invoke(
        app, ["prompt", "extraction", "--cours", "physique"]
    )
    assert res.exit_code == 0, res.output
    assert "Thème: physique" in res.output
    assert "{cours}" not in res.output and "{profil_cours}" not in res.output
    # Le JSON de la section SORTIE survit au rendu.
    assert '{"concepts": [...]' in res.output


def test_cli_prompt_generation_exige_des_concepts_valides(tmp_path):
    args = ["prompt", "generation", "--cours", "physique"]
    assert runner.invoke(app, args).exit_code == 1

    c = ecrire(tmp_path / "c.json", fichier_concepts("c1", validated=False))
    assert runner.invoke(app, [*args, "--concepts", str(c)]).exit_code == 1

    c = ecrire(tmp_path / "c.json", fichier_concepts("c1", validated=True))
    res = runner.invoke(app, [*args, "--concepts", str(c)])
    assert res.exit_code == 0, res.output
    assert '"id": "c1"' in res.output and "{concepts_valides}" not in res.output
    assert "{{c1::valeur}}" in res.output  # la syntaxe cloze n'est pas touchée


def test_cli_prompt_cours_sans_profil_avertit():
    res = runner.invoke(
        app, ["prompt", "extraction", "--cours", "cours-sans-profil-xyz"]
    )
    assert res.exit_code == 0
    assert "Aucun profil" in res.output


def test_question_brute_sans_source_ref_ni_confiance_est_refusee():
    q = q_exact()
    del q["source_ref"], q["confiance"]
    r = analyser(fichier_questions([q])).rapport
    assert not r.valide
    assert "champ manquant: source_ref" in r.erreurs[0].messages
    assert "champ manquant: confiance" in r.erreurs[0].messages
