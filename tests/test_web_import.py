"""Mode « Importer des questions déjà générées »: aperçu puis import, sans LLM.

Les fichiers viennent du workflow manuel (Claude Code). La route est protégée
par la session, bornée en taille, valide avec les modèles stricts et n'écrit
rien tant qu'on ne confirme pas.
"""
from __future__ import annotations

import json

import pytest

from .fabriques_manuel import (
    fichier_concepts, fichier_questions, json_texte, q_cloze, q_exact, q_probleme, q_qcm,
)
# Fixtures de l'API (client, auth_client): partagées avec test_web_api.
from .test_web_api import auth_client, client  # noqa: F401

pytestmark = pytest.mark.usefixtures("pas_de_llm")

APERCU = "/api/import/apercu"
CONFIRMER = "/api/import/confirmer"


def _fichiers(questions: dict | str, concepts: dict | str | None = None) -> dict:
    def contenu(valeur):
        return valeur if isinstance(valeur, (str, bytes)) else json_texte(valeur)

    fichiers = {"fichier": ("ch1_questions.json", contenu(questions), "application/json")}
    if concepts is not None:
        fichiers["concepts"] = ("ch1_concepts.json", contenu(concepts), "application/json")
    return fichiers


def _poster(client, route, questions, *, concepts=None, cours="physique"):
    return client.post(
        route, files=_fichiers(questions, concepts),
        data={"cours": cours},
    )


def _cartes(client) -> list[dict]:
    return client.get("/api/cartes").json()


def _fichier_valide() -> dict:
    return fichier_questions([q_exact("c1"), q_qcm("c2"), q_cloze("c3")])


# --- Protection -------------------------------------------------------------

@pytest.mark.parametrize("route", [APERCU, CONFIRMER])
def test_import_refuse_sans_session(client, route):  # noqa: F811
    r = _poster(client, route, _fichier_valide())
    assert r.status_code == 401


# --- Aperçu -----------------------------------------------------------------

def test_apercu_fichier_valide_n_ecrit_rien(auth_client):  # noqa: F811
    r = _poster(auth_client, APERCU, _fichier_valide())

    assert r.status_code == 200, r.text
    corps = r.json()
    assert corps["importable"] is True
    assert (corps["nouvelles"], corps["deja_en_base"], corps["doublons_fichier"]) == (3, 0, 0)
    rapport = corps["rapport"]
    assert rapport["valide"] and rapport["par_type"] == {"exact": 1, "qcm": 1, "cloze": 1}
    assert [a["type"] for a in rapport["apercu"]] == ["exact", "qcm", "cloze"]
    assert _cartes(auth_client) == [], "l'aperçu ne doit rien importer"
    assert auth_client.get("/api/cours").json() == [], "ni créer de cours"


def test_apercu_fichier_invalide_donne_les_erreurs_par_question(auth_client):  # noqa: F811
    mauvais = q_qcm("c2")
    del mauvais["propositions"]
    r = _poster(auth_client, APERCU, fichier_questions([q_exact("c1"), mauvais]))

    assert r.status_code == 200
    corps = r.json()
    assert corps["importable"] is False
    erreur = corps["rapport"]["erreurs"][0]
    assert (erreur["position"], erreur["reference"]) == (2, "c2")
    assert "champ manquant: propositions" in erreur["messages"]


def test_apercu_json_casse(auth_client):  # noqa: F811
    r = _poster(auth_client, APERCU, "{ ceci n'est pas du json")
    assert r.status_code == 200
    corps = r.json()
    assert corps["importable"] is False
    assert "JSON invalide" in corps["rapport"]["erreurs_fichier"][0]


def test_apercu_avec_concepts_donne_la_couverture(auth_client):  # noqa: F811
    questions = fichier_questions([q_exact("c1"), q_probleme("c2", confiance="basse")])
    r = _poster(auth_client, APERCU, questions, concepts=fichier_concepts("c1", "c2", "c3"))

    rapport = r.json()["rapport"]
    assert r.json()["importable"] is True
    assert [c["concept_id"] for c in rapport["concepts_sans_question"]] == ["c3"]
    assert [c["concept_id"] for c in rapport["confiance_basse"]] == ["c2"]


def test_apercu_concept_hors_liste_bloque(auth_client):  # noqa: F811
    r = _poster(
        auth_client, APERCU, fichier_questions([q_exact("c99")]),
        concepts=fichier_concepts("c1"),
    )
    assert r.json()["importable"] is False


def test_une_liste_de_concepts_n_est_pas_un_fichier_de_questions(auth_client):  # noqa: F811
    r = _poster(auth_client, APERCU, fichier_concepts("c1"))
    assert r.status_code == 400
    assert "liste de concepts" in r.json()["detail"]


def test_cours_obligatoire(auth_client):  # noqa: F811
    r = _poster(auth_client, APERCU, _fichier_valide(), cours="   ")
    assert r.status_code == 400


def test_fichier_trop_lourd_refuse(auth_client):  # noqa: F811
    enorme = json.dumps({"questions": [], "bourrage": "x" * (2 * 1024 * 1024 + 10)})
    for route in (APERCU, CONFIRMER):
        r = _poster(auth_client, route, enorme)
        assert r.status_code == 413, route


def test_trop_de_questions_refuse(auth_client):  # noqa: F811
    from flashcards import manuel

    questions = [q_exact("c1", f"Question {i} ?") for i in range(manuel.MAX_QUESTIONS + 1)]
    r = _poster(auth_client, APERCU, fichier_questions(questions))
    assert r.status_code == 200
    assert r.json()["importable"] is False
    assert "trop de questions" in r.json()["rapport"]["erreurs_fichier"][0]


# --- Import -----------------------------------------------------------------

def test_import_fichier_valide(auth_client):  # noqa: F811
    r = _poster(auth_client, CONFIRMER, _fichier_valide())

    assert r.status_code == 200, r.text
    assert r.json() == {
        "importees": 3, "deja_presentes": 0, "doublons_contenu": 0, "cours": ["physique"],
    }
    cartes = _cartes(auth_client)
    assert {c["type"] for c in cartes} == {"exact", "qcm", "cloze"}
    assert {c["cours"] for c in cartes} == {"physique"}
    assert [c["nom"] for c in auth_client.get("/api/cours").json()] == ["physique"]


def test_import_fichier_invalide_n_importe_rien(auth_client):  # noqa: F811
    mauvais = q_qcm("c2")
    del mauvais["propositions"]
    r = _poster(auth_client, CONFIRMER, fichier_questions([q_exact("c1"), mauvais]))

    assert r.status_code == 422
    assert "rien n'a été importé" in r.json()["detail"]
    assert _cartes(auth_client) == []


def test_reimport_idempotent(auth_client):  # noqa: F811
    _poster(auth_client, CONFIRMER, _fichier_valide())
    r = _poster(auth_client, CONFIRMER, _fichier_valide())

    assert r.json()["importees"] == 0
    assert r.json()["deja_presentes"] == 3
    assert len(_cartes(auth_client)) == 3


def test_doublon_de_contenu_detecte_meme_avec_un_autre_id(auth_client):  # noqa: F811
    existante = {
        **q_exact("c1", "Unité de R ?"),
        "id": "carte-existante", "cours": "physique",
        "source": {"fichier": "ancien.pdf", "page": 1},
    }
    premier = {"source": "ancien.pdf", "cours": "physique",
               "questions": [existante]}
    assert _poster(auth_client, CONFIRMER, premier).json()["importees"] == 1

    # Même contenu (espaces différents), reçu dans un nouveau fichier brut.
    nouveau = fichier_questions([
        q_exact("c1", "Unité  de R ?"), q_exact("c1", "Une autre question ?"),
    ])
    apercu = _poster(auth_client, APERCU, nouveau).json()
    assert [a["statut"] for a in apercu["rapport"]["apercu"]] == ["deja_en_base", "nouvelle"]
    assert (apercu["nouvelles"], apercu["deja_en_base"]) == (1, 1)

    r = _poster(auth_client, CONFIRMER, nouveau).json()
    assert (r["importees"], r["doublons_contenu"]) == (1, 1)
    assert len(_cartes(auth_client)) == 2


def test_doublon_dans_le_fichier(auth_client):  # noqa: F811
    contenu = fichier_questions([q_exact("c1", "Même ?"), q_exact("c1", "Même ?")])
    apercu = _poster(auth_client, APERCU, contenu).json()
    assert apercu["doublons_fichier"] == 1 and apercu["nouvelles"] == 1

    assert _poster(auth_client, CONFIRMER, contenu).json()["importees"] == 1


def test_rattachement_au_cours_choisi(auth_client):  # noqa: F811
    r = _poster(auth_client, CONFIRMER, _fichier_valide(), cours="maths")

    assert r.json()["cours"] == ["maths"]
    cartes = _cartes(auth_client)
    assert {c["cours"] for c in cartes} == {"maths"}
    assert _cartes(auth_client)[0]["payload"]["cours"] == "maths"


def test_les_cartes_importees_entrent_dans_la_session(auth_client):  # noqa: F811
    from datetime import date

    _poster(auth_client, CONFIRMER, _fichier_valide())
    session = auth_client.get("/api/session", params={"jour": str(date(2026, 1, 5))}).json()
    assert session["total"] == 3


def test_import_n_appelle_aucun_llm_et_ne_cree_aucune_tache(auth_client):  # noqa: F811
    # `pas_de_llm` ferait échouer le test au moindre appel au wrapper.
    _poster(auth_client, CONFIRMER, _fichier_valide())
    from flashcards.db import open_db
    from flashcards.web import settings_store
    from pathlib import Path

    conn = open_db(Path(settings_store.db_path()))
    try:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        conn.close()
