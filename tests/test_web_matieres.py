"""API web: matières et thèmes, tests multi-cours, points, entretien, réglages de poids."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flashcards.db import open_db
from flashcards.web import settings_store

from .conftest import TODAY, make_card
from .test_web_api import auth_client, client  # noqa: F401  (fixtures partagées)

JOUR = str(TODAY)
VIEUX = datetime(2025, 11, 1, tzinfo=timezone.utc)


@contextmanager
def base():
    conn = open_db(Path(settings_store.db_path()))
    try:
        yield conn
    finally:
        conn.close()


def carte_due(cid, cours, **kw):
    with base() as conn:
        make_card(conn, cid, cours=cours, stability=1.0, difficulty=5.0, last_review=VIEUX,
                  state=2, **kw)


def carte_stable(cid, cours, **kw):
    with base() as conn:
        make_card(conn, cid, cours=cours, stability=300.0, difficulty=5.0,
                  last_review=datetime(2025, 11, 26, tzinfo=timezone.utc), state=2, **kw)


# --- Protection ----------------------------------------------------------------

@pytest.mark.parametrize("methode,route", [
    ("get", "/api/matieres"), ("post", "/api/matieres"),
    ("patch", "/api/matieres/x"), ("delete", "/api/matieres/x"),
])
def test_routes_matieres_protegees(client, methode, route):  # noqa: F811
    assert getattr(client, methode)(route).status_code == 401


# --- Matières ----------------------------------------------------------------

def test_crud_matiere(auth_client):  # noqa: F811
    r = auth_client.post("/api/matieres", json={"nom": "Anglais", "couleur": "#5CC8FF"})
    assert r.status_code == 201 and r.json()["nb_cours"] == 0
    assert auth_client.post("/api/matieres", json={"nom": "Anglais"}).status_code == 400

    r = auth_client.patch("/api/matieres/Anglais", json={"profil": "Niveau: B1", "nouveau_nom": "English"})
    assert r.status_code == 200
    matiere = auth_client.get("/api/matieres").json()[0]
    assert (matiere["nom"], matiere["profil"]) == ("English", "Niveau: B1")

    assert auth_client.delete("/api/matieres/English").json() == {"cours_detaches": 0}
    assert auth_client.get("/api/matieres").json() == []
    assert auth_client.delete("/api/matieres/English").status_code == 400


def test_theme_dans_une_matiere(auth_client):  # noqa: F811
    auth_client.post("/api/matieres", json={"nom": "Anglais", "couleur": "#5CC8FF"})
    r = auth_client.post("/api/cours", json={"nom": "Anglais conditionnel", "matiere": "Anglais"})
    assert r.status_code == 201
    theme = r.json()
    assert theme["matiere"] == "Anglais" and theme["couleur"] == "#5CC8FF"
    assert theme["archive"] is False and theme["en_entretien"] is True  # aucun test à venir

    assert auth_client.post("/api/cours", json={"nom": "X", "matiere": "Fantôme"}).status_code == 400
    assert auth_client.post("/api/cours", json={"nom": "Libre"}).json()["matiere"] is None

    liste = auth_client.get("/api/matieres").json()[0]
    assert liste["nb_cours"] == 1 and liste["nb_cours_entretien"] == 1


def test_deplacer_detacher_et_profil_d_un_theme(auth_client):  # noqa: F811
    auth_client.post("/api/matieres", json={"nom": "A"})
    auth_client.post("/api/matieres", json={"nom": "B"})
    auth_client.post("/api/cours", json={"nom": "t", "matiere": "A"})

    auth_client.patch("/api/cours/t", json={"matiere": "B", "profil": "Niveau: A2"})
    t = auth_client.get("/api/cours").json()[0]
    assert (t["matiere"], t["profil"]) == ("B", "Niveau: A2")

    auth_client.patch("/api/cours/t", json={"couleur": "#111111"})
    assert auth_client.get("/api/cours").json()[0]["matiere"] == "B", "champ absent: inchangé"

    auth_client.patch("/api/cours/t", json={"matiere": None})
    assert auth_client.get("/api/cours").json()[0]["matiere"] is None, "null: détache"


def test_archiver_un_theme(auth_client):  # noqa: F811
    auth_client.post("/api/cours", json={"nom": "vieux"})
    carte_due("c1", "vieux")
    assert auth_client.get("/api/session", params={"jour": JOUR}).json()["total"] == 1

    assert auth_client.patch("/api/cours/vieux", json={"archive": True}).status_code == 200
    theme = auth_client.get("/api/cours").json()[0]
    assert theme["archive"] is True and theme["en_entretien"] is False and theme["nb_cartes"] == 1
    assert auth_client.get("/api/session", params={"jour": JOUR}).json()["total"] == 0
    assert len(auth_client.get("/api/cartes").json()) == 1, "les cartes restent dans la bibliothèque"

    auth_client.patch("/api/cours/vieux", json={"archive": False})
    assert auth_client.get("/api/session", params={"jour": JOUR}).json()["total"] == 1


# --- Tests liés à plusieurs thèmes -------------------------------------------

def test_test_lie_a_plusieurs_themes(auth_client):  # noqa: F811
    for nom in ("cond", "pp"):
        auth_client.post("/api/cours", json={"nom": nom})
    r = auth_client.post("/api/tests", json={
        "cours": ["cond", "pp"], "date": str(TODAY + timedelta(days=9)), "intitule": "TE",
    })
    assert r.status_code == 201, r.text
    assert r.json()["cours"] == ["cond", "pp"] 

    assert [t["intitule"] for t in auth_client.get("/api/tests").json()] == ["TE"]
    themes = {c["nom"]: c["en_entretien"] for c in auth_client.get("/api/cours", params={"jour": JOUR}).json()}
    assert themes == {"cond": False, "pp": False}, "un test futur les sort de l'entretien"


def test_ancien_format_cours_en_chaine_toujours_accepte(auth_client):  # noqa: F811
    auth_client.post("/api/cours", json={"nom": "physique"})
    r = auth_client.post("/api/tests", json={
        "cours": "physique", "date": str(TODAY + timedelta(days=7)), "intitule": "TE",
    })
    assert r.status_code == 201 and r.json()["cours"] == ["physique"]


def test_test_lie_a_toute_une_matiere(auth_client):  # noqa: F811
    auth_client.post("/api/matieres", json={"nom": "Anglais"})
    for nom in ("cond", "pp"):
        auth_client.post("/api/cours", json={"nom": nom, "matiere": "Anglais"})
    r = auth_client.post("/api/tests", json={
        "matieres": ["Anglais"], "date": str(TODAY + timedelta(days=90)), "intitule": "Fin d'année",
    })
    assert r.status_code == 201
    assert r.json()["matieres"] == ["Anglais"] and r.json()["cours"] == ["cond", "pp"]

    # Un thème ajouté ensuite à la matière est couvert aussi.
    auth_client.post("/api/cours", json={"nom": "vocab", "matiere": "Anglais"})
    assert auth_client.get("/api/tests").json()[0]["cours"] == ["cond", "pp", "vocab"]


def test_test_sans_lien_ou_avec_cours_archive_refuse(auth_client):  # noqa: F811
    r = auth_client.post("/api/tests", json={"date": str(TODAY), "intitule": "X"})
    assert r.status_code == 400
    auth_client.post("/api/cours", json={"nom": "vieux"})
    auth_client.patch("/api/cours/vieux", json={"archive": True})
    r = auth_client.post("/api/tests", json={"cours": ["vieux"], "date": str(TODAY), "intitule": "X"})
    assert r.status_code == 400 and "archivé" in r.json()["detail"]


def test_modifier_les_liens_et_la_date_d_un_test(auth_client):  # noqa: F811
    for nom in ("a", "b"):
        auth_client.post("/api/cours", json={"nom": nom})
    test_id = auth_client.post("/api/tests", json={
        "cours": ["a"], "date": str(TODAY + timedelta(days=9)), "intitule": "TE",
    }).json()["id"]

    auth_client.patch(f"/api/tests/{test_id}", json={"cours": ["a", "b"]})
    assert auth_client.get("/api/tests").json()[0]["cours"] == ["a", "b"]

    nouvelle = str(TODAY + timedelta(days=20))
    auth_client.patch(f"/api/tests/{test_id}", json={"date": nouvelle})
    t = auth_client.get("/api/tests").json()[0]
    assert t["date"] == nouvelle and t["cours"] == ["a", "b"], "la date seule ne touche pas aux liens"

    assert auth_client.patch("/api/tests/999", json={"date": nouvelle}).status_code == 404


# --- Points: aujourd'hui, session, stats --------------------------------------

def test_today_en_cartes_et_en_points(auth_client):  # noqa: F811
    auth_client.post("/api/cours", json={"nom": "calcul"})
    for i in range(4):
        carte_due(f"p{i}", "calcul", qtype="probleme", poids=None)
    r = auth_client.get("/api/today", params={"jour": JOUR}).json()
    assert r["total"] == 4 and r["points"] == 8.0 and r["points_dus"] == 8.0
    assert r["plafond"] == 40.0
    assert r["par_cours"][0]["points"] == 8.0 and r["par_cours"][0]["nb"] == 4


def test_today_expose_la_part_d_entretien(auth_client):  # noqa: F811
    auth_client.post("/api/cours", json={"nom": "ancien"})
    auth_client.post("/api/cours", json={"nom": "actuel"})
    auth_client.post("/api/tests", json={
        "cours": ["actuel"], "date": str(TODAY + timedelta(days=30)), "intitule": "TE",
    })
    carte_stable("e1", "ancien", poids=0.5)
    for i in range(3):
        carte_due(f"t{i}", "actuel", poids=1.0)

    r = auth_client.get("/api/today", params={"jour": JOUR}).json()
    assert r["themes_entretien"] == ["ancien"]
    assert r["nb_entretien"] == 1 and r["points_entretien"] == 0.5
    assert r["part_entretien"] == pytest.approx(0.5 / 3.5, abs=1e-3)
    entretien = {c["cours"]: c["entretien"] for c in r["par_cours"]}
    assert entretien == {"ancien": True, "actuel": False}


def test_session_porte_les_poids_et_le_plafond_en_points(auth_client):  # noqa: F811
    auth_client.post("/api/cours", json={"nom": "calcul"})
    carte_due("p1", "calcul", qtype="probleme", poids=None)
    carte_due("p2", "calcul", qtype="exact", poids=None)
    s = auth_client.get("/api/session", params={"jour": JOUR}).json()
    assert s["plafond"] == 40.0 and s["points"] == 2.5
    assert {q["card_id"]: q["poids"] for q in s["questions"]} == {"p1": 2.0, "p2": 0.5}
    assert all(q["entretien"] is False or q["entretien"] is True for q in s["questions"])


def test_stats_en_points_et_entretien(auth_client):  # noqa: F811
    auth_client.post("/api/cours", json={"nom": "ancien"})
    for i in range(5):
        carte_stable(f"e{i}", "ancien", poids=0.5)
    s = auth_client.get("/api/stats", params={"jour": JOUR}).json()
    assert s["themes_entretien"] == ["ancien"] and s["nb_themes"] == 1
    assert len(s["charge_14j"]) == 14
    jour = s["charge_14j"][0]
    assert {"nb", "points", "nb_entretien", "points_entretien"} <= set(jour)
    assert sum(j["points_entretien"] for j in s["charge_14j"]) >= 3.0, "le quota se voit dans la prévision"
    assert s["quotas"][0]["theme"] == "ancien" and s["quotas"][0]["quota"] == 3.0
    assert s["plafond"] == 40.0


# --- Réglages ------------------------------------------------------------------

def test_reglages_exposent_points_poids_et_entretien(auth_client):  # noqa: F811
    r = auth_client.get("/api/reglages").json()
    assert r["plafond_quotidien"] == 40
    assert r["poids_type"]["redaction"] == 4.0 and r["poids_categorie"]["vocabulaire"] == 0.1
    assert (r["entretien_quota_hebdo"], r["entretien_plafond_jour"], r["entretien_poids_max"]) == (3.0, 8.0, 2.0)


def test_modifier_plafond_poids_et_entretien(auth_client):  # noqa: F811
    r = auth_client.patch("/api/reglages", json={
        "plafond_quotidien": 25.5, "entretien_quota_hebdo": 5, "entretien_plafond_jour": 10,
        "entretien_poids_max": 1.5,
        "poids_type": {"qcm": 0.75}, "poids_categorie": {"vocabulaire": 0.2},
    })
    assert r.status_code == 200
    reg = auth_client.get("/api/reglages").json()
    assert reg["plafond_quotidien"] == 25.5 and reg["entretien_quota_hebdo"] == 5
    assert reg["entretien_plafond_jour"] == 10 and reg["entretien_poids_max"] == 1.5
    assert reg["poids_type"]["qcm"] == 0.75 and reg["poids_type"]["probleme"] == 2.0
    assert reg["poids_categorie"]["vocabulaire"] == 0.2


def test_reglage_de_poids_invalide_refuse(auth_client):  # noqa: F811
    assert auth_client.patch("/api/reglages", json={"poids_type": {"hologramme": 1}}).status_code == 400
    assert auth_client.patch("/api/reglages", json={"poids_type": {"qcm": 0}}).status_code == 400
    assert auth_client.patch("/api/reglages", json={"poids_categorie": {"inconnue": 1}}).status_code == 400
    assert auth_client.patch("/api/reglages", json={"plafond_quotidien": 0}).status_code == 422
    assert auth_client.patch("/api/reglages", json={"entretien_poids_max": -1}).status_code == 422


def test_les_poids_reglés_changent_la_file(auth_client):  # noqa: F811
    auth_client.post("/api/cours", json={"nom": "calcul"})
    # En mode test (sinon ces cartes dues seraient de l'entretien, limité à 8 pts/jour).
    auth_client.post("/api/tests", json={
        "cours": ["calcul"], "date": str(TODAY + timedelta(days=60)), "intitule": "TE",
    })
    for i in range(10):
        carte_due(f"p{i}", "calcul", qtype="probleme", poids=None)  # 2 pts: 20 points au total
    auth_client.patch("/api/reglages", json={"plafond_quotidien": 10})
    assert auth_client.get("/api/session", params={"jour": JOUR}).json()["total"] == 5
    auth_client.patch("/api/reglages", json={"poids_type": {"probleme": 1.0}})
    assert auth_client.get("/api/session", params={"jour": JOUR}).json()["total"] == 10


# --- Bibliothèque: poids des cartes ------------------------------------------

def test_cartes_exposent_poids_et_points_et_se_modifient(auth_client):  # noqa: F811
    auth_client.post("/api/cours", json={"nom": "calcul"})
    with base() as conn:
        make_card(conn, "p1", cours="calcul", qtype="probleme", poids=None, categorie="vocabulaire")
    carte = auth_client.get("/api/cartes").json()[0]
    assert (carte["poids"], carte["categorie"], carte["points"]) == (None, "vocabulaire", 0.1)

    auth_client.patch("/api/cartes/p1", json={"poids": 3.0})
    carte = auth_client.get("/api/cartes").json()[0]
    assert carte["poids"] == 3.0 and carte["points"] == 3.0 and carte["payload"]["poids"] == 3.0

    auth_client.patch("/api/cartes/p1", json={"poids": None})  # efface: retour à la catégorie
    carte = auth_client.get("/api/cartes").json()[0]
    assert carte["poids"] is None and carte["points"] == 0.1

    assert auth_client.patch("/api/cartes/p1", json={"poids": -2}).status_code == 422


def test_export_contient_les_nouvelles_tables(auth_client):  # noqa: F811
    auth_client.post("/api/matieres", json={"nom": "Anglais"})
    auth_client.post("/api/cours", json={"nom": "cond", "matiere": "Anglais"})
    auth_client.post("/api/tests", json={
        "cours": ["cond"], "matieres": ["Anglais"], "date": str(TODAY + timedelta(days=9)),
        "intitule": "TE",
    })
    export = auth_client.get("/api/export").json()
    assert export["matieres"][0]["nom"] == "Anglais"
    assert export["test_cours"][0]["cours"] == "cond" and export["test_matieres"]


# --- Import: poids explicite et catégorie du concept ---------------------------

def test_import_porte_le_poids_explicite_et_la_categorie(auth_client):  # noqa: F811
    import json

    from .fabriques_manuel import fichier_concepts, fichier_questions, q_exact

    concepts = fichier_concepts("c1", "c2")
    concepts["concepts"][0]["categorie"] = "vocabulaire"
    questions = fichier_questions([
        q_exact("c1", "Traduction de « chat » ?"),
        q_exact("c2", "Autre question ?", poids=1.5),
    ])
    r = auth_client.post(
        "/api/import/confirmer",
        files={"fichier": ("q.json", json.dumps(questions), "application/json"),
               "concepts": ("c.json", json.dumps(concepts), "application/json")},
        data={"cours": "physique"},
    )
    assert r.status_code == 200, r.text
    cartes = {c["concept_id"]: c for c in auth_client.get("/api/cartes").json()}
    assert cartes["c1"]["categorie"] == "vocabulaire" and cartes["c1"]["points"] == 0.1
    assert cartes["c2"]["poids"] == 1.5 and cartes["c2"]["points"] == 1.5
