"""Rôles (admin / standard / étendu) et dépôt de fichiers par cours.

Tout tourne en MOCK_LLM=1 sur une base jetable: aucun appel réseau.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flashcards import depot
from flashcards.db import open_db
from flashcards.web import auth

MDP = "motdepasse-test"
PDF = b"%PDF-1.4\n%contenu de test\n"


@pytest.fixture
def serveur(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("FLASHCARDS_DB", str(tmp_path / "web.db"))
    monkeypatch.setenv("FLASHCARDS_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.setenv("FLASHCARDS_DEPOT", str(tmp_path / "depot"))
    monkeypatch.setenv("SESSION_SECRET", "secret-de-test")
    monkeypatch.setenv("FLASHCARDS_ENV", "development")
    conn = open_db(tmp_path / "web.db")
    auth.creer_compte(conn, "nate", MDP, "admin")
    auth.creer_compte(conn, "eleve", MDP, "standard")
    auth.creer_compte(conn, "ecole", MDP, "etendu")
    conn.close()
    auth.limiteur.echecs.clear()

    from flashcards.web.app import app

    with TestClient(app) as c:
        yield c


def _client_connecte(serveur: TestClient, identifiant: str) -> TestClient:
    """Un client indépendant (cookies propres) connecté sous `identifiant`."""
    from flashcards.web.app import app

    c = TestClient(app)
    r = c.post("/api/login", json={"identifiant": identifiant, "mot_de_passe": MDP})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def admin(serveur):
    return _client_connecte(serveur, "nate")


@pytest.fixture
def standard(serveur):
    return _client_connecte(serveur, "eleve")


@pytest.fixture
def etendu(serveur):
    return _client_connecte(serveur, "ecole")


def _deposer(c: TestClient, cours="physique", nom="cours.pdf", contenu=PDF):
    return c.post("/api/depot", data={"cours": cours}, files={"fichier": (nom, contenu)})


# --- Identité et rôles -------------------------------------------------------

def test_me_renvoie_le_role(admin, standard, etendu):
    assert admin.get("/api/me").json() == {"identifiant": "nate", "role": "admin"}
    assert standard.get("/api/me").json()["role"] == "standard"
    assert etendu.get("/api/me").json()["role"] == "etendu"


def test_login_renvoie_le_role(serveur):
    r = serveur.post("/api/login", json={"identifiant": "ecole", "mot_de_passe": MDP})
    assert r.json() == {"identifiant": "ecole", "role": "etendu"}


def test_identifiant_inconnu_et_mauvais_mdp_sont_indiscernables(serveur):
    a = serveur.post("/api/login", json={"identifiant": "fantome", "mot_de_passe": MDP})
    b = serveur.post("/api/login", json={"identifiant": "nate", "mot_de_passe": "faux-faux-faux"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_un_role_inconnu_est_refuse(tmp_path):
    conn = open_db(tmp_path / "r.db")
    with pytest.raises(RuntimeError, match="Rôle inconnu"):
        auth.creer_compte(conn, "x", MDP, "superuser")
    conn.close()


def test_identifiant_invalide_refuse(tmp_path):
    conn = open_db(tmp_path / "r.db")
    for mauvais in ("", "a b", "../x", "é", "x" * 41):
        with pytest.raises(RuntimeError):
            auth.creer_compte(conn, mauvais, MDP, "standard")
    conn.close()


# --- Gestion des comptes (admin seul) ----------------------------------------

def test_les_comptes_sont_reserves_a_l_admin(standard, etendu):
    for c in (standard, etendu):
        assert c.get("/api/comptes").status_code == 403
        r = c.post("/api/comptes", json={"identifiant": "x", "mot_de_passe": MDP, "role": "admin"})
        assert r.status_code == 403
        assert c.patch("/api/comptes/eleve", json={"role": "admin"}).status_code == 403
        assert c.delete("/api/comptes/eleve").status_code == 403


def test_comptes_exigent_une_session(serveur):
    assert serveur.get("/api/comptes").status_code == 401


def test_admin_cree_modifie_supprime_un_compte(admin, serveur):
    r = admin.post("/api/comptes", json={"identifiant": "ami", "mot_de_passe": MDP, "role": "standard"})
    assert r.status_code == 201 and r.json()["role"] == "standard"
    assert admin.post(
        "/api/comptes", json={"identifiant": "ami", "mot_de_passe": MDP, "role": "standard"}
    ).status_code == 400  # déjà pris, jamais écrasé

    assert admin.patch("/api/comptes/ami", json={"role": "etendu"}).status_code == 200
    roles = {c["identifiant"]: c["role"] for c in admin.get("/api/comptes").json()}
    assert roles["ami"] == "etendu"

    assert admin.delete("/api/comptes/ami").status_code == 200
    assert "ami" not in {c["identifiant"] for c in admin.get("/api/comptes").json()}


def test_un_compte_ne_donne_jamais_son_hash(admin):
    corps = admin.get("/api/comptes").text
    assert "hash" not in corps and "argon2" not in corps


def test_le_dernier_admin_ne_peut_etre_ni_supprime_ni_retrograde(admin):
    assert admin.delete("/api/comptes/nate").status_code == 400  # soi-même
    r = admin.patch("/api/comptes/nate", json={"role": "standard"})
    assert r.status_code == 400 and "dernier administrateur" in r.json()["detail"]


def test_un_admin_peut_en_retrograder_un_autre_s_il_en_reste_un(admin):
    admin.post("/api/comptes", json={"identifiant": "bis", "mot_de_passe": MDP, "role": "admin"})
    assert admin.patch("/api/comptes/bis", json={"role": "standard"}).status_code == 200


def test_la_retrogradation_prend_effet_immediatement(admin, serveur):
    ami = None
    admin.post("/api/comptes", json={"identifiant": "ami", "mot_de_passe": MDP, "role": "etendu"})
    ami = _client_connecte(serveur, "ami")
    assert ami.get("/api/depot").status_code == 200
    admin.patch("/api/comptes/ami", json={"role": "standard"})
    assert ami.get("/api/depot").status_code == 403  # même cookie, rôle relu en base


def test_la_suppression_ferme_la_session(admin, serveur):
    admin.post("/api/comptes", json={"identifiant": "ami", "mot_de_passe": MDP, "role": "standard"})
    ami = _client_connecte(serveur, "ami")
    assert ami.get("/api/me").status_code == 200
    admin.delete("/api/comptes/ami")
    assert ami.get("/api/me").status_code == 401


def test_reinitialiser_le_mdp_invalide_les_sessions_du_compte(admin, serveur):
    ami = _client_connecte(serveur, "eleve")
    admin.patch("/api/comptes/eleve", json={"mot_de_passe": "nouveau-mot-de-passe"})
    assert ami.get("/api/me").status_code == 401
    r = serveur.post("/api/login", json={"identifiant": "eleve", "mot_de_passe": "nouveau-mot-de-passe"})
    assert r.status_code == 200


def test_changer_son_mdp_ne_deconnecte_pas_les_autres_comptes(standard, etendu):
    r = standard.post(
        "/api/mot-de-passe", json={"ancien": MDP, "nouveau": "nouveau-mot-de-passe"}
    )
    assert r.status_code == 200
    assert standard.get("/api/me").status_code == 200  # cookie renouvelé
    assert etendu.get("/api/me").status_code == 200


def test_mdp_trop_court_refuse_a_la_creation(admin):
    r = admin.post("/api/comptes", json={"identifiant": "x", "mot_de_passe": "court", "role": "standard"})
    assert r.status_code == 400


# --- Réglages système réservés à l'admin -------------------------------------

def test_arret_d_urgence_et_modele_reserves_a_l_admin(standard, admin):
    assert standard.post("/api/budget/arret", json={"coupe": True}).status_code == 403
    assert standard.patch("/api/reglages", json={"modele": "claude-sonnet-5"}).status_code == 403
    # Les réglages de planification restent ouverts: l'appli « comme avant ».
    assert standard.patch("/api/reglages", json={"plafond_quotidien": 30}).status_code == 200
    assert admin.post("/api/budget/arret", json={"coupe": False}).status_code == 200


def test_l_appli_de_revision_reste_ouverte_au_standard(standard):
    for chemin in ("/api/today", "/api/cours", "/api/tests", "/api/reglages", "/api/stats"):
        assert standard.get(chemin).status_code == 200, chemin


# --- Dépôt -------------------------------------------------------------------

def test_le_depot_est_reserve_aux_comptes_etendus(standard, etendu, admin):
    assert standard.get("/api/depot").status_code == 403
    assert _deposer(standard).status_code == 403
    assert admin.get("/api/depot").status_code == 200
    assert etendu.get("/api/depot").status_code == 200


def test_creer_un_cours_puis_deposer_un_fichier(etendu, tmp_path):
    assert etendu.post("/api/cours", json={"nom": "physique"}).status_code == 201
    r = _deposer(etendu)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["cours"] == "physique" and d["nom"] == "cours.pdf"
    assert d["depose_par"] == "ecole" and d["traite_le"] is None and d["taille"] == len(PDF)
    fichiers = list((tmp_path / "depot").rglob("*.pdf"))
    assert len(fichiers) == 1 and fichiers[0].read_bytes() == PDF
    assert etendu.get("/api/depot").json()[0]["id"] == d["id"]


def test_deposer_dans_un_cours_inconnu_est_refuse(etendu):
    r = _deposer(etendu, cours="n-existe-pas")
    assert r.status_code == 400 and "Cours inconnu" in r.json()["detail"]


@pytest.mark.parametrize(
    "nom, contenu",
    [
        ("virus.exe", b"MZ\x90\x00"),
        ("faux.pdf", b"ceci n'est pas un pdf"),
        ("faux.png", b"%PDF-1.4"),
        ("vide.pdf", b""),
        ("sans-extension", PDF),
    ],
)
def test_formats_et_contenus_invalides_refuses(etendu, nom, contenu, tmp_path):
    etendu.post("/api/cours", json={"nom": "physique"})
    assert _deposer(etendu, nom=nom, contenu=contenu).status_code == 400
    assert not list((tmp_path / "depot").rglob("*.*")) if (tmp_path / "depot").exists() else True


def test_fichier_trop_lourd_refuse(etendu, monkeypatch):
    etendu.post("/api/cours", json={"nom": "physique"})
    monkeypatch.setattr(depot, "MAX_OCTETS_FICHIER", 1000)
    assert _deposer(etendu, contenu=PDF + b"x" * 2000).status_code == 413


def test_le_nom_envoye_ne_peut_pas_sortir_du_dossier(etendu, tmp_path):
    etendu.post("/api/cours", json={"nom": "physique"})
    r = _deposer(etendu, nom="../../../etc/evil.pdf")
    assert r.status_code == 201
    ecrits = [p for p in tmp_path.rglob("*evil*")]
    assert len(ecrits) == 1
    assert (tmp_path / "depot") in ecrits[0].parents


def test_un_cours_au_nom_etrange_reste_dans_le_depot(etendu, tmp_path):
    nom = "../../dehors"
    assert etendu.post("/api/cours", json={"nom": nom}).status_code in (201, 400)
    _deposer(etendu, cours=nom)
    assert not (tmp_path.parent / "dehors").exists()
    for p in tmp_path.rglob("*.pdf"):
        assert (tmp_path / "depot") in p.parents


def test_telechargement_en_piece_jointe_et_suppression(etendu, standard, tmp_path):
    etendu.post("/api/cours", json={"nom": "physique"})
    d = _deposer(etendu).json()
    r = etendu.get(f"/api/depot/{d['id']}/fichier")
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert standard.get(f"/api/depot/{d['id']}/fichier").status_code == 403

    assert etendu.delete(f"/api/depot/{d['id']}").status_code == 200
    assert not list((tmp_path / "depot").rglob("*.pdf"))
    assert etendu.get(f"/api/depot/{d['id']}/fichier").status_code == 404
    assert etendu.delete(f"/api/depot/{d['id']}").status_code == 404


def test_quota_du_depot(etendu, monkeypatch):
    etendu.post("/api/cours", json={"nom": "physique"})
    monkeypatch.setattr(depot, "MAX_FICHIERS", 1)
    assert _deposer(etendu).status_code == 201
    r = _deposer(etendu, nom="deux.pdf")
    assert r.status_code == 400 and "plein" in r.json()["detail"]


# --- Côté maison: récupérer et marquer (CLI / module) ------------------------

def test_recuperer_copie_dans_inbox_sans_ecraser_et_marquer_traite(etendu, tmp_path, monkeypatch):
    etendu.post("/api/cours", json={"nom": "physique"})
    a = _deposer(etendu, nom="Loi d'Ohm.pdf").json()
    b = _deposer(etendu, nom="Loi d'Ohm.pdf").json()

    conn = open_db(tmp_path / "web.db")
    inbox = tmp_path / "inbox"
    p1 = depot.recuperer(conn, a["id"], inbox)
    p2 = depot.recuperer(conn, b["id"], inbox)
    assert p1 != p2 and p1.read_bytes() == p2.read_bytes() == PDF
    assert " " not in p1.name and "'" not in p1.name

    assert [d.id for d in depot.lister(conn, a_traiter=True)] == [a["id"], b["id"]]
    depot.marquer_traite(conn, a["id"])
    assert [d.id for d in depot.lister(conn, a_traiter=True)] == [b["id"]]
    conn.close()
    assert etendu.get("/api/depot").json()[0]["traite_le"] is not None
