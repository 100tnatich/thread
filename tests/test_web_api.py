"""Tests de l'API REST: authentification, cours, skip, ingestion, ordre.

Tout tourne en MOCK_LLM=1: aucun appel réseau.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flashcards.db import open_db
from flashcards.web import auth

TODAY = date(2026, 1, 5)
MDP = "motdepasse-test"


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    db = tmp_path / "web.db"
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("FLASHCARDS_DB", str(db))
    monkeypatch.setenv("FLASHCARDS_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.setenv("SESSION_SECRET", "secret-de-test")
    monkeypatch.setenv("FLASHCARDS_ENV", "development")

    conn = open_db(db)
    auth.creer_compte(conn, "nate", MDP)
    conn.close()

    auth.limiteur.echecs.clear()

    from flashcards.web import app as module_app

    # Les limiteurs sont des compteurs de processus: on les remet à zéro
    # entre les tests, sinon le test N+1 hérite de la cadence du test N.
    module_app.limiteur_llm.echecs.clear()

    from flashcards.web.app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_client(client):
    r = client.post("/api/login", json={"identifiant": "nate", "mot_de_passe": MDP})
    assert r.status_code == 200
    return client


def _attendre_job(client, job_id: str) -> dict:
    """Les BackgroundTasks de TestClient s'exécutent avant le retour de la requête."""
    r = client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200
    return r.json()


# --- Authentification ------------------------------------------------------

ROUTES_PROTEGEES = [
    ("get", "/api/me"),
    ("get", "/api/cours"),
    ("get", "/api/today"),
    ("get", "/api/session"),
    ("get", "/api/stats"),
    ("get", "/api/tests"),
    ("get", "/api/cartes"),
    ("get", "/api/reglages"),
    ("get", "/api/export"),
]


@pytest.mark.parametrize("methode,route", ROUTES_PROTEGEES)
def test_acces_refuse_sans_session(client, methode, route):
    reponse = getattr(client, methode)(route)
    assert reponse.status_code == 401


def test_ecriture_refusee_sans_session(client):
    assert client.post("/api/cours", json={"nom": "x"}).status_code == 401
    assert client.post("/api/skip", json={"card_id": "a", "session_id": "s"}).status_code == 401


def test_login_puis_acces(client):
    assert client.get("/api/me").status_code == 401
    r = client.post("/api/login", json={"identifiant": "nate", "mot_de_passe": MDP})
    assert r.status_code == 200
    assert client.get("/api/me").json()["identifiant"] == "nate"


def test_login_mauvais_mot_de_passe(client):
    r = client.post("/api/login", json={"identifiant": "nate", "mot_de_passe": "faux"})
    assert r.status_code == 401
    assert client.get("/api/me").status_code == 401


def test_logout(auth_client):
    assert auth_client.get("/api/me").status_code == 200
    auth_client.post("/api/logout")
    assert auth_client.get("/api/me").status_code == 401


def test_cookie_est_httponly(client):
    r = client.post("/api/login", json={"identifiant": "nate", "mot_de_passe": MDP})
    entete = r.headers["set-cookie"].lower()
    assert "httponly" in entete
    assert "samesite=lax" in entete


def test_limitation_force_brute(client):
    for _ in range(auth.MAX_TENTATIVES):
        client.post("/api/login", json={"identifiant": "nate", "mot_de_passe": "faux"})
    # Même le bon mot de passe est refusé pendant le blocage.
    r = client.post("/api/login", json={"identifiant": "nate", "mot_de_passe": MDP})
    assert r.status_code == 429


def test_changer_mot_de_passe(auth_client):
    r = auth_client.post("/api/mot-de-passe", json={"ancien": MDP, "nouveau": "nouveau-mdp-1"})
    assert r.status_code == 200
    auth_client.post("/api/logout")
    assert auth_client.post(
        "/api/login", json={"identifiant": "nate", "mot_de_passe": "nouveau-mdp-1"}
    ).status_code == 200


def test_changer_mot_de_passe_mauvais_ancien(auth_client):
    r = auth_client.post("/api/mot-de-passe", json={"ancien": "faux", "nouveau": "nouveau-mdp-1"})
    assert r.status_code == 400


def test_mot_de_passe_trop_court_refuse(auth_client):
    r = auth_client.post("/api/mot-de-passe", json={"ancien": MDP, "nouveau": "court"})
    assert r.status_code == 400


# --- Cours -----------------------------------------------------------------

def test_crud_cours(auth_client):
    assert auth_client.get("/api/cours").json() == []

    r = auth_client.post("/api/cours", json={"nom": "maths", "couleur": "#5CC8FF"})
    assert r.status_code == 201
    assert r.json()["couleur"] == "#5CC8FF"

    auth_client.patch("/api/cours/maths", json={"couleur": "#4ADE9F"})
    assert auth_client.get("/api/cours").json()[0]["couleur"] == "#4ADE9F"

    auth_client.request("DELETE", "/api/cours/maths", json={})
    assert auth_client.get("/api/cours").json() == []


def test_cours_doublon(auth_client):
    auth_client.post("/api/cours", json={"nom": "maths"})
    assert auth_client.post("/api/cours", json={"nom": "maths"}).status_code == 400


def test_renommer_cours_propage(auth_client):
    auth_client.post("/api/cours", json={"nom": "physique"})
    auth_client.post("/api/tests", json={
        "cours": "physique", "date": str(TODAY + timedelta(days=7)),
        "intitule": "TE",
    })
    auth_client.patch("/api/cours/physique", json={"nouveau_nom": "physique-2"})

    assert [c["nom"] for c in auth_client.get("/api/cours").json()] == ["physique-2"]
    assert auth_client.get("/api/tests").json()[0]["cours"] == ["physique-2"]


def test_supprimer_cours_avec_reaffectation(auth_client):
    job = _ingest_texte(auth_client, "physique", "Une notion importante a retenir ici.")
    assert job["statut"] == "termine"
    _generer(auth_client, "physique", job["resultat"])

    auth_client.post("/api/cours", json={"nom": "sciences"})
    r = auth_client.request(
        "DELETE", "/api/cours/physique", json={"reaffecter_vers": "sciences"}
    )
    assert r.status_code == 200
    assert r.json()["questions_concernees"] > 0
    assert all(c["cours"] == "sciences" for c in auth_client.get("/api/cartes").json())


# --- Ingestion: les trois voies en mode simulé -----------------------------

def _ingest_texte(client, cours: str, texte: str) -> dict:
    client.post("/api/cours", json={"nom": cours})
    r = client.post("/api/ingest/texte", json={
        "cours": cours, "texte": texte,
    })
    assert r.status_code == 200
    return _attendre_job(client, r.json()["job_id"])


def _generer(client, cours: str, extraction: dict) -> dict:
    r = client.post("/api/ingest/generer", json={
        "cours": cours,
        "source": extraction["source"], "concepts": extraction["concepts"],
    })
    assert r.status_code == 200
    return _attendre_job(client, r.json()["job_id"])


def test_voie_texte(auth_client):
    job = _ingest_texte(
        auth_client, "physique",
        "La tension egale la resistance multipliee par le courant.",
    )
    assert job["statut"] == "termine"
    assert job["resultat"]["concepts"], "des concepts doivent être extraits"

    gen = _generer(auth_client, "physique", job["resultat"])
    assert gen["statut"] == "termine"
    assert gen["resultat"]["questions_creees"] > 0
    assert gen["resultat"]["concepts_sans_question"] == []
    assert len(auth_client.get("/api/cartes").json()) == gen["resultat"]["questions_creees"]


def test_voie_texte_vide_avertit_sans_inventer(auth_client):
    job = _ingest_texte(auth_client, "physique", "   ")
    assert job["statut"] == "termine"
    assert job["resultat"]["concepts"] == []
    assert job["resultat"]["avertissements"], "un avertissement doit être remonté"


def test_voie_pdf(auth_client, tmp_path):
    auth_client.post("/api/cours", json={"nom": "physique"})
    pdf = Path("samples/physique-loi-ohm.pdf")
    with open(pdf, "rb") as f:
        r = auth_client.post(
            "/api/ingest/fichiers",
            data={"cours": "physique", "voie": "pdf"},
            files={"fichiers": ("cours.pdf", f, "application/pdf")},
        )
    assert r.status_code == 200
    job = _attendre_job(auth_client, r.json()["job_id"])
    assert job["statut"] == "termine"
    assert job["resultat"]["concepts"]


def test_voie_image(auth_client, tmp_path):
    auth_client.post("/api/cours", json={"nom": "maths"})
    image = tmp_path / "photo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    with open(image, "rb") as f:
        r = auth_client.post(
            "/api/ingest/fichiers",
            data={"cours": "maths", "voie": "image"},
            files={"fichiers": ("photo.png", f, "image/png")},
        )
    assert r.status_code == 200
    job = _attendre_job(auth_client, r.json()["job_id"])
    assert job["statut"] == "termine"
    # Le mock signale qu'aucune image n'a réellement été lue: le garde-fou
    # "ne jamais inventer en silence" doit remonter jusqu'à l'interface.
    assert job["resultat"]["avertissements"]


def test_mauvais_format_de_fichier_refuse(auth_client, tmp_path):
    auth_client.post("/api/cours", json={"nom": "maths"})
    fichier = tmp_path / "note.txt"
    fichier.write_text("bonjour")
    with open(fichier, "rb") as f:
        r = auth_client.post(
            "/api/ingest/fichiers",
            data={"cours": "maths", "voie": "image"},
            files={"fichiers": ("note.txt", f, "text/plain")},
        )
    assert r.status_code == 400


def test_generation_sans_concept_refusee(auth_client):
    r = auth_client.post("/api/ingest/generer", json={
        "cours": "physique", "source": "s", "concepts": [],
    })
    assert r.status_code == 400


def test_generation_uniquement_sur_concepts_valides(auth_client):
    """Si je décoche des concepts, seuls ceux gardés produisent des questions."""
    job = _ingest_texte(
        auth_client, "physique",
        "Premiere notion importante. Deuxieme notion differente. Troisieme element encore.",
    )
    concepts = job["resultat"]["concepts"]
    assert len(concepts) >= 2

    garde = concepts[:1]
    gen = _generer(auth_client, "physique", {**job["resultat"], "concepts": garde})

    # Le nombre exact dépend de l'importance du concept (un « essentiel » en
    # reçoit 2 à 3). L'invariant est que RIEN ne sort des concepts validés.
    assert gen["resultat"]["questions_creees"] >= 1
    gardes_ids = {c["id"] for c in garde}
    produits = {c["concept_id"] for c in auth_client.get("/api/cartes").json()}
    assert produits == gardes_ids


# --- Session: skip et ordre ------------------------------------------------

def _preparer_questions(client, cours="physique", texte=None) -> int:
    texte = texte or "Notion alpha importante. Notion beta distincte. Notion gamma encore."
    job = _ingest_texte(client, cours, texte)
    gen = _generer(client, cours, job["resultat"])
    return gen["resultat"]["questions_creees"]


def test_session_renvoie_les_questions(auth_client):
    nb = _preparer_questions(auth_client)
    r = auth_client.get("/api/session", params={"jour": str(TODAY)})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == nb
    assert data["session_id"]
    assert all("payload" in q and "type" in q for q in data["questions"])


def test_skip_remet_en_fin_de_file_sans_toucher_fsrs(auth_client):
    _preparer_questions(auth_client)
    session = auth_client.get("/api/session", params={"jour": str(TODAY), "ordre": "par_cours"}).json()
    sid = session["session_id"]
    premier = session["questions"][0]["card_id"]

    r = auth_client.post("/api/skip", json={
        "card_id": premier, "session_id": sid, "raison": "feuille", "jour": str(TODAY),
    })
    assert r.status_code == 200
    assert r.json() == {"nb_skips": 1, "reviendra": True}

    apres = auth_client.get("/api/session", params={
        "jour": str(TODAY), "ordre": "par_cours", "session_id": sid,
    }).json()
    assert apres["questions"][-1]["card_id"] == premier
    # Aucune révision enregistrée: un skip n'est pas une réponse.
    carte = next(c for c in auth_client.get("/api/cartes").json() if c["id"] == premier)
    assert carte["nb_revisions"] == 0
    assert carte["stabilite"] is None


def test_skip_ne_boucle_pas_indefiniment(auth_client):
    _preparer_questions(auth_client)
    session = auth_client.get("/api/session", params={"jour": str(TODAY)}).json()
    sid = session["session_id"]
    cible = session["questions"][0]["card_id"]

    dernier = None
    for _ in range(5):
        dernier = auth_client.post("/api/skip", json={
            "card_id": cible, "session_id": sid, "jour": str(TODAY),
        }).json()
    assert dernier["reviendra"] is False

    apres = auth_client.get("/api/session", params={
        "jour": str(TODAY), "session_id": sid,
    }).json()
    assert cible not in [q["card_id"] for q in apres["questions"]]


def test_reponse_met_a_jour_fsrs(auth_client):
    _preparer_questions(auth_client)
    session = auth_client.get("/api/session", params={"jour": str(TODAY)}).json()
    cible = session["questions"][0]["card_id"]

    r = auth_client.post("/api/reponse", json={
        "card_id": cible, "rating": 3, "correct": True, "source": "auto", "jour": str(TODAY),
    })
    assert r.status_code == 200

    carte = next(c for c in auth_client.get("/api/cartes").json() if c["id"] == cible)
    assert carte["nb_revisions"] == 1
    assert carte["stabilite"] is not None


def test_ordre_par_cours_regroupe(auth_client):
    _preparer_questions(auth_client, cours="aaa")
    _preparer_questions(auth_client, cours="zzz")

    data = auth_client.get("/api/session", params={
        "jour": str(TODAY), "ordre": "par_cours",
    }).json()
    cours_vus = [q["cours"] for q in data["questions"]]
    assert cours_vus == sorted(cours_vus)


def test_ordre_melange_garde_le_meme_contenu(auth_client):
    _preparer_questions(auth_client, cours="aaa")
    _preparer_questions(auth_client, cours="zzz")

    trie = auth_client.get("/api/session", params={
        "jour": str(TODAY), "ordre": "par_cours"}).json()
    melange = auth_client.get("/api/session", params={
        "jour": str(TODAY), "ordre": "melange"}).json()

    assert {q["card_id"] for q in trie["questions"]} == {
        q["card_id"] for q in melange["questions"]
    }


def test_ordre_invalide_refuse(auth_client):
    _preparer_questions(auth_client)
    r = auth_client.get("/api/session", params={"jour": str(TODAY), "ordre": "nimporte"})
    assert r.status_code == 400


def test_filtrer_la_session_par_cours(auth_client):
    _preparer_questions(auth_client, cours="maths")
    _preparer_questions(auth_client, cours="physique")

    data = auth_client.get("/api/session", params={
        "jour": str(TODAY), "cours": "maths",
    }).json()
    assert {q["cours"] for q in data["questions"]} == {"maths"}


# --- Today / stats / bibliothèque ------------------------------------------

def test_today_repartit_par_cours(auth_client):
    _preparer_questions(auth_client, cours="maths")
    data = auth_client.get("/api/today", params={"jour": str(TODAY)}).json()
    assert data["total"] > 0
    assert data["par_cours"][0]["cours"] == "maths"
    assert data["nb_nouvelles"] == data["total"]


def test_today_affiche_le_prochain_test(auth_client):
    _preparer_questions(auth_client, cours="maths")
    auth_client.post("/api/tests", json={
        "cours": "maths", "date": str(TODAY + timedelta(days=9)),
        "intitule": "TE maths",
    })
    data = auth_client.get("/api/today", params={"jour": str(TODAY)}).json()
    assert data["prochain_test"]["intitule"] == "TE maths"
    assert data["jours_avant_test"] == 9


def test_stats_charge_et_tests(auth_client):
    _preparer_questions(auth_client, cours="maths")
    auth_client.post("/api/tests", json={
        "cours": "maths", "date": str(TODAY + timedelta(days=10)),
        "intitule": "TE",
    })
    data = auth_client.get("/api/stats", params={"jour": str(TODAY)}).json()
    assert len(data["charge_14j"]) == 14
    assert len(data["tests"]) == 1
    assert data["tests"][0]["nb_cartes"] > 0


def test_deplacer_un_test_replanifie(auth_client):
    _preparer_questions(auth_client, cours="maths")
    test_id = auth_client.post("/api/tests", json={
        "cours": "maths", "date": str(TODAY + timedelta(days=40)),
        "intitule": "TE",
    }).json()["id"]

    r = auth_client.patch(f"/api/tests/{test_id}", json={"date": str(TODAY + timedelta(days=3))})
    assert r.status_code == 200
    assert auth_client.get("/api/tests").json()[0]["date"] == str(TODAY + timedelta(days=3))


def test_question_mauvaise_exclue_des_revisions(auth_client):
    _preparer_questions(auth_client)
    avant = auth_client.get("/api/today", params={"jour": str(TODAY)}).json()["total"]
    cible = auth_client.get("/api/cartes").json()[0]["id"]

    auth_client.patch(f"/api/cartes/{cible}", json={
        "desactivee": True, "note_qualite": "énoncé faux",
    })

    apres = auth_client.get("/api/today", params={"jour": str(TODAY)}).json()["total"]
    assert apres == avant - 1


def test_modifier_une_question_valide_le_schema(auth_client):
    _preparer_questions(auth_client)
    carte = auth_client.get("/api/cartes").json()[0]
    mauvais = {**carte["payload"], "type": "type_invente"}
    r = auth_client.patch(f"/api/cartes/{carte['id']}", json={"payload": mauvais})
    assert r.status_code == 400


def test_recherche_dans_la_bibliotheque(auth_client):
    _preparer_questions(auth_client, cours="maths", texte="Notion alphaunique ici.")
    trouve = auth_client.get("/api/cartes", params={"recherche": "alphaunique"}).json()
    assert trouve
    assert auth_client.get("/api/cartes", params={"recherche": "zzzintrouvable"}).json() == []


# --- Réglages / export -----------------------------------------------------

def test_reglages_surchargent_la_config(auth_client):
    assert auth_client.get("/api/reglages").json()["plafond_quotidien"] == 40
    auth_client.patch("/api/reglages", json={"plafond_quotidien": 7})
    assert auth_client.get("/api/reglages").json()["plafond_quotidien"] == 7

    _preparer_questions(auth_client)
    data = auth_client.get("/api/session", params={"jour": str(TODAY)}).json()
    assert data["total"] <= 7


def test_reglage_hors_bornes_refuse(auth_client):
    assert auth_client.patch("/api/reglages", json={"plafond_quotidien": 0}).status_code == 422
    assert auth_client.patch("/api/reglages", json={"retention_normale": 1.5}).status_code == 422


def test_mock_llm_signale_dans_les_reglages(auth_client):
    assert auth_client.get("/api/reglages").json()["mock_llm"] is True


def test_export_complet(auth_client):
    _preparer_questions(auth_client)
    data = auth_client.get("/api/export").json()
    assert set(data) >= {"cours", "cartes", "tests", "revisions"}
    assert data["cartes"]


# --- Protection du crédit API (scénarios catastrophe côté web) -------------

def test_route_llm_refusee_sans_session(client):
    """Une route qui peut dépenser ne doit jamais être atteignable sans session."""
    for route, corps in [
        ("/api/ingest/texte", {"cours": "x", "texte": "z" * 50}),
        ("/api/ingest/generer", {"cours": "x", "source": "s",
                                 "concepts": [{"id": "c1", "titre": "t"}]}),
        ("/api/ingest/estimer", {"cours": "x", "texte": "z"}),
    ]:
        assert client.post(route, json=corps).status_code == 401, route
    assert client.post("/api/ingest/fichiers").status_code == 401
    assert client.get("/api/budget").status_code == 401


def test_double_soumission_ne_cree_qu_un_job(auth_client, monkeypatch):
    """Double clic / deux onglets: un seul job, donc une seule dépense.

    On fige la tâche de fond pour que le premier job reste « en cours »,
    comme pendant une vraie extraction.
    """
    from flashcards.web import app as module_app

    monkeypatch.setattr(module_app, "_tache_extraction", lambda *a, **k: None)
    auth_client.post("/api/cours", json={"nom": "physique"})

    corps = {"cours": "physique", "texte": "Une notion identique."}
    premier = auth_client.post("/api/ingest/texte", json=corps)
    second = auth_client.post("/api/ingest/texte", json=corps)

    assert premier.status_code == 200
    assert second.status_code == 200
    assert premier.json()["job_id"] == second.json()["job_id"], (
        "deux jobs créés pour la même demande: le contenu serait payé deux fois"
    )


def test_deux_jobs_differents_en_meme_temps_refuses(auth_client, monkeypatch):
    """Un seul travail à la fois, garanti par un index unique en base."""
    from flashcards.web import app as module_app

    monkeypatch.setattr(module_app, "_tache_extraction", lambda *a, **k: None)
    auth_client.post("/api/cours", json={"nom": "physique"})

    premier = auth_client.post("/api/ingest/texte", json={
        "cours": "physique", "texte": "Première notion."})
    second = auth_client.post("/api/ingest/texte", json={
        "cours": "physique", "texte": "Deuxième notion, différente."})

    assert premier.status_code == 200
    assert second.status_code == 409
    assert "déjà en cours" in second.json()["detail"]


def test_redemarrage_ne_relance_aucun_job(auth_client, monkeypatch, tmp_path):
    """Un job coupé devient « interrompu », jamais « à relancer »."""
    from flashcards.web import app as module_app
    from flashcards.db import open_db

    monkeypatch.setattr(module_app, "_tache_extraction", lambda *a, **k: None)
    auth_client.post("/api/cours", json={"nom": "physique"})
    job_id = auth_client.post("/api/ingest/texte", json={
        "cours": "physique", "texte": "Notion en cours."
    }).json()["job_id"]
    assert auth_client.get(f"/api/jobs/{job_id}").json()["statut"] == "en_cours"

    # Redémarrage: on rejoue le hook de démarrage.
    with TestClient(module_app.app):
        pass

    etat = auth_client.get(f"/api/jobs/{job_id}").json()
    assert etat["statut"] == "interrompu"
    assert "redémarrage" in (etat["message"] or "")


def test_texte_trop_long_rejete_sans_job(auth_client):
    auth_client.post("/api/cours", json={"nom": "physique"})
    r = auth_client.post("/api/ingest/texte", json={
        "cours": "physique", "texte": "x" * 60_000,
    })
    assert r.status_code == 400
    assert "trop long" in r.json()["detail"].lower()


def test_fichier_trop_lourd_rejete(auth_client):
    auth_client.post("/api/cours", json={"nom": "physique"})
    enorme = b"%PDF-" + b"0" * (21 * 1024 * 1024)
    r = auth_client.post(
        "/api/ingest/fichiers",
        data={"cours": "physique", "voie": "pdf"},
        files={"fichiers": ("gros.pdf", enorme, "application/pdf")},
    )
    assert r.status_code == 413


def test_fichier_au_mauvais_contenu_rejete(auth_client):
    """L'extension ne prouve rien: le contenu réel est vérifié."""
    auth_client.post("/api/cours", json={"nom": "physique"})
    r = auth_client.post(
        "/api/ingest/fichiers",
        data={"cours": "physique", "voie": "image"},
        files={"fichiers": ("piege.png", b"#!/bin/sh\nrm -rf /", "image/png")},
    )
    assert r.status_code == 400
    assert "ne correspond pas" in r.json()["detail"]


def test_trop_d_images_rejete(auth_client):
    auth_client.post("/api/cours", json={"nom": "physique"})
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    fichiers = [("fichiers", (f"i{i}.png", png, "image/png")) for i in range(15)]
    r = auth_client.post(
        "/api/ingest/fichiers",
        data={"cours": "physique", "voie": "image"},
        files=fichiers,
    )
    assert r.status_code == 400
    assert "Trop d'images" in r.json()["detail"]


def test_plafond_atteint_bloque_l_ingestion(auth_client, monkeypatch):
    """Plafond journalier atteint: la tâche échoue avec un message clair."""
    from flashcards import budget
    from flashcards.web import app as module_app

    monkeypatch.setattr(module_app.llm, "mock_actif", lambda: False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-faux")
    monkeypatch.setenv("FLASHCARDS_COUT_MAX_JOUR", "0.0")

    auth_client.post("/api/cours", json={"nom": "physique"})
    job_id = auth_client.post("/api/ingest/texte", json={
        "cours": "physique", "texte": "Une notion à extraire."
    }).json()["job_id"]

    etat = auth_client.get(f"/api/jobs/{job_id}").json()
    assert etat["statut"] == "echoue"
    assert "plafond" in (etat["message"] or "").lower()


def test_route_budget_expose_la_consommation(auth_client):
    budget_json = auth_client.get("/api/budget").json()
    assert budget_json["cout_jour_usd"] == 0.0
    assert budget_json["plafond_jour_usd"] > 0
    assert budget_json["llm_desactive"] is False


def test_estimation_sans_appel(auth_client):
    r = auth_client.post("/api/ingest/estimer", json={
        "cours": "physique", "texte": "Du texte à estimer." * 10,
    })
    assert r.status_code == 200
    assert r.json()["cout_estime_usd"] > 0
    # Rien n'a été dépensé.
    assert auth_client.get("/api/budget").json()["appels_jour"] == 0
