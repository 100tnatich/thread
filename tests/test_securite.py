"""Sécurité de l'interface web: sessions, contrôle d'accès, CSRF, entrées.

Complète `test_web_api.py` (qui couvre le fonctionnel). Tout est en mode
simulé; les tests qui exercent le chemin réel installent un faux SDK.
"""
from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from flashcards import backup, budget, profiles
from flashcards.db import open_db
from flashcards.web import auth

from .test_web_api import MDP, auth_client, client  # noqa: F401  (fixtures)

RACINE = Path(__file__).resolve().parents[1]


# --- Contrôle d'accès ------------------------------------------------------

def test_toute_route_api_exige_une_session(client):
    """Passe en revue TOUTES les routes déclarées, pas une liste écrite à la main."""
    from flashcards.web import app as module_app

    publiques = module_app.ROUTES_PUBLIQUES
    verifiees = 0
    for route in module_app.app.routes:
        chemin = getattr(route, "path", "")
        if not chemin.startswith("/api/") or chemin in publiques:
            continue
        url = chemin.replace("{nom}", "x").replace("{test_id}", "1") \
                    .replace("{card_id}", "x").replace("{job_id}", "x")
        for methode in route.methods - {"HEAD", "OPTIONS"}:
            r = client.request(methode, url)
            assert r.status_code == 401, f"{methode} {chemin} -> {r.status_code}"
            verifiees += 1
    assert verifiees >= 30


def test_route_oubliee_sans_dependance_reste_protegee(client):
    """Refus par défaut: une route ajoutée sans `User` n'est pas ouverte."""
    from flashcards.web import app as module_app

    module_app.app.add_api_route("/api/_oubliee", lambda: {"secret": 1}, methods=["GET"])
    try:
        assert client.get("/api/_oubliee").status_code == 401
    finally:
        module_app.app.router.routes.pop()


def _nb_uploads() -> int:
    # Le dossier est fixé au premier import du module (voir `app.UPLOADS`).
    from flashcards.web import app as module_app

    return len(list(module_app.UPLOADS.glob("*"))) if module_app.UPLOADS.exists() else 0


def test_envoi_de_fichier_anonyme_refuse_avant_lecture(client):
    avant = _nb_uploads()
    r = client.post(
        "/api/ingest/fichiers",
        data={"cours": "x", "voie": "pdf"},
        files={"fichiers": ("a.pdf", b"%PDF-" + b"0" * 1024, "application/pdf")},
    )
    assert r.status_code == 401
    assert _nb_uploads() == avant


def test_pas_de_documentation_ni_de_schema_exposes(client):
    for chemin in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(chemin).status_code == 404, chemin


def test_entetes_de_securite_sur_chaque_reponse(auth_client):
    for reponse in (auth_client.get("/api/me"), auth_client.get("/api/inexistante")):
        assert reponse.headers["x-content-type-options"] == "nosniff"
        assert reponse.headers["x-frame-options"] == "DENY"
        assert "default-src 'none'" in reponse.headers["content-security-policy"]
        assert reponse.headers["cache-control"] == "no-store"


# --- Sessions --------------------------------------------------------------

def _cookie(secret: str, contenu: dict) -> str:
    return URLSafeTimedSerializer(secret, salt="flashcards-session").dumps(contenu)


@pytest.mark.parametrize("secret", ["dev-secret-non-securise", "dev", ""])
def test_cookie_forge_avec_un_secret_devinable_refuse(client, secret):
    client.cookies.set(auth.COOKIE_NAME, _cookie(secret, {"u": "nate"}))
    assert client.get("/api/me").status_code == 401


def test_cookie_signe_mais_sans_version_refuse(client):
    """Ancien format de cookie (identifiant seul): plus accepté."""
    client.cookies.set(auth.COOKIE_NAME, _cookie("secret-de-test", {"u": "nate"}))
    assert client.get("/api/me").status_code == 401


def test_changer_le_mot_de_passe_deconnecte_les_autres_appareils(auth_client):
    from flashcards.web.app import app

    with TestClient(app) as autre:
        assert autre.post(
            "/api/login", json={"identifiant": "nate", "mot_de_passe": MDP}
        ).status_code == 200
        assert autre.get("/api/me").status_code == 200

        r = auth_client.post(
            "/api/mot-de-passe", json={"ancien": MDP, "nouveau": "nouveau-mot-de-passe"}
        )
        assert r.status_code == 200

        assert autre.get("/api/me").status_code == 401, "session volée toujours valide"
        assert auth_client.get("/api/me").status_code == 200, "l'appareil courant reste connecté"


def test_secret_obligatoire_et_long_hors_developpement(monkeypatch):
    monkeypatch.delenv("FLASHCARDS_ENV", raising=False)  # oubli = production
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        auth.secret_session()
    monkeypatch.setenv("SESSION_SECRET", "trop-court")
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        auth.secret_session()
    monkeypatch.setenv("SESSION_SECRET", "x" * 32)
    assert auth.secret_session() == "x" * 32
    assert auth.cookie_securise(), "sans FLASHCARDS_ENV, le cookie doit être Secure"


def test_secret_de_developpement_aleatoire(monkeypatch):
    monkeypatch.setenv("FLASHCARDS_ENV", "development")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setattr(auth, "_secret_ephemere", None)
    secret = auth.secret_session()
    assert len(secret) >= 32 and secret != "dev-secret-non-securise"
    assert auth.secret_session() == secret


def test_cookie_secure_en_production(client, monkeypatch):
    monkeypatch.setenv("FLASHCARDS_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    r = client.post("/api/login", json={"identifiant": "nate", "mot_de_passe": MDP})
    assert r.status_code == 200
    entete = r.headers["set-cookie"].lower()
    assert "secure" in entete and "httponly" in entete and "samesite=lax" in entete


# --- Force brute et entrées hostiles ---------------------------------------

def test_identifiant_non_ascii_ne_fait_pas_planter_le_login(client):
    r = client.post("/api/login", json={"identifiant": "naté", "mot_de_passe": "x"})
    assert r.status_code == 401


def test_mot_de_passe_geant_refuse_sans_hachage(client):
    r = client.post(
        "/api/login", json={"identifiant": "nate", "mot_de_passe": "x" * 100_000}
    )
    assert r.status_code == 422


def test_force_brute_repartie_bloquee_globalement(client):
    for _ in range(auth.MAX_TENTATIVES_GLOBAL):
        auth.limiteur.enregistrer_echec(auth.CLE_GLOBALE)
    r = client.post("/api/login", json={"identifiant": "nate", "mot_de_passe": MDP})
    assert r.status_code == 429


def test_connexion_echouee_journalisee_sans_le_mot_de_passe(client, caplog):
    with caplog.at_level("WARNING", logger="flashcards.securite"):
        client.post("/api/login", json={"identifiant": "nate", "mot_de_passe": "secret-tapé"})
    assert "connexion échouée" in caplog.text
    assert "secret-tapé" not in caplog.text


def test_ancien_mot_de_passe_non_devinable_a_volonte(auth_client):
    for _ in range(auth.MAX_TENTATIVES):
        auth_client.post(
            "/api/mot-de-passe", json={"ancien": "faux", "nouveau": "nouveau-mot-de-passe"}
        )
    r = auth_client.post(
        "/api/mot-de-passe", json={"ancien": MDP, "nouveau": "nouveau-mot-de-passe"}
    )
    assert r.status_code == 429


def test_mot_de_passe_de_moins_de_12_caracteres_refuse(tmp_path):
    conn = open_db(tmp_path / "u.db")
    with pytest.raises(RuntimeError, match="12"):
        auth.creer_compte(conn, "nate", "onze-caract")
    conn.close()


# --- CSRF ------------------------------------------------------------------

def test_ecriture_depuis_un_autre_site_refusee(client, monkeypatch):
    monkeypatch.setenv("FLASHCARDS_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    r = client.post("/api/logout", headers={"origin": "https://site-piege.example"})
    assert r.status_code == 403
    r = client.post("/api/logout", headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    # Même origine: accepté.
    assert client.post("/api/logout", headers={"origin": "http://testserver"}).status_code == 200


# --- Injection par le nom du cours -----------------------------------------

@pytest.mark.parametrize("nom", ["../../etc/passwd", "/etc/passwd", "..", "a/../../b"])
def test_nom_de_cours_ne_sort_pas_du_dossier_des_profils(nom):
    for chemin in profiles.chemins_candidats(nom):
        assert chemin.resolve().parent == profiles.PROFILES_DIR.resolve(), chemin
    assert profiles.charger(nom).chemin is None


# --- Contenu généré = donnée non fiable ------------------------------------

def test_aucune_injection_html_brute_dans_le_frontend():
    """Le SVG du modèle (ou d'un fichier importé) ne doit jamais être injecté."""
    coupables = [
        str(f.relative_to(RACINE))
        for f in (RACINE / "frontend" / "src").rglob("*.ts*")
        if "dangerouslySetInnerHTML" in f.read_text() or ".innerHTML" in f.read_text()
    ]
    assert not coupables, f"injection HTML brute: {coupables}"


def test_csp_de_la_page_sans_script_en_ligne():
    caddy = (RACINE / "web" / "Caddyfile").read_text()
    assert "Content-Security-Policy" in caddy
    assert "script-src 'self';" in caddy
    assert "object-src 'none'" in caddy and "frame-ancestors 'none'" in caddy
    assert "request_body" in caddy and "max_size" in caddy


# --- Routes LLM: double déclenchement, arrêt d'urgence, modèle --------------

def test_double_clic_ne_lance_qu_une_seule_tache(auth_client, monkeypatch):
    """Avant: un seul job en base, mais DEUX tâches lancées (donc deux appels)."""
    from flashcards.web import app as module_app

    lancements = []
    monkeypatch.setattr(
        module_app, "_tache_extraction", lambda *a, **k: lancements.append(a[0])
    )
    auth_client.post("/api/cours", json={"nom": "physique"})
    corps = {"cours": "physique", "texte": "Une notion identique."}
    ids = {auth_client.post("/api/ingest/texte", json=corps).json()["job_id"] for _ in range(3)}
    assert len(ids) == 1
    assert len(lancements) == 1, f"{len(lancements)} tâches lancées pour un seul job"


def test_double_clic_sur_un_fichier_ne_lance_qu_une_tache(auth_client, monkeypatch):
    from flashcards.web import app as module_app

    lancements = []
    monkeypatch.setattr(
        module_app, "_tache_extraction", lambda *a, **k: lancements.append(a[0])
    )
    auth_client.post("/api/cours", json={"nom": "physique"})
    png = b"\x89PNG\r\n\x1a\n" + os.urandom(64)
    avant = _nb_uploads()

    def envoyer():
        return auth_client.post(
            "/api/ingest/fichiers",
            data={"cours": "physique", "voie": "image"},
            files={"fichiers": ("photo.png", png, "image/png")},
        )

    assert envoyer().json()["job_id"] == envoyer().json()["job_id"]
    assert len(lancements) == 1
    # La copie du doublon n'est pas gardée sur le disque.
    assert _nb_uploads() == avant + 1


def test_double_clic_sur_generer_ne_lance_qu_une_tache(auth_client, monkeypatch):
    from flashcards.web import app as module_app

    lancements = []
    monkeypatch.setattr(
        module_app, "_tache_generation", lambda *a, **k: lancements.append(a[0])
    )
    corps = {"cours": "physique", "source": "s",
             "concepts": [{"id": "c1", "titre": "Loi d'Ohm"}]}
    for _ in range(2):
        assert auth_client.post("/api/ingest/generer", json=corps).status_code == 200
    assert len(lancements) == 1


def test_job_bloque_ne_condamne_pas_le_creneau(auth_client, monkeypatch):
    from flashcards.web import app as module_app

    monkeypatch.setattr(module_app, "_tache_extraction", lambda *a, **k: None)
    monkeypatch.setattr(module_app, "DUREE_MAX_JOB_MINUTES", 0)
    auth_client.post("/api/cours", json={"nom": "physique"})
    premier = auth_client.post("/api/ingest/texte", json={
        "cours": "physique", "texte": "Première notion bloquée."})
    time.sleep(0.01)
    second = auth_client.post("/api/ingest/texte", json={
        "cours": "physique", "texte": "Deuxième notion, plus tard."})
    assert second.status_code == 200
    etat = auth_client.get(f"/api/jobs/{premier.json()['job_id']}").json()
    assert etat["statut"] == "interrompu"


def test_trop_de_concepts_refuses_par_la_route(auth_client):
    concepts = [{"id": f"c{i}", "titre": "t"} for i in range(41)]
    r = auth_client.post("/api/ingest/generer", json={
        "cours": "physique", "source": "s", "concepts": concepts})
    assert r.status_code == 422


def test_arret_d_urgence_depuis_l_interface(auth_client, monkeypatch):
    from flashcards.web import app as module_app

    assert auth_client.post("/api/budget/arret", json={"coupe": True}).json() == {
        "llm_desactive": True, "arret_par_env": False,
    }
    assert auth_client.get("/api/budget").json()["llm_desactive"] is True

    # En mode réel, toute ingestion échoue alors avec un message explicite.
    monkeypatch.setattr(module_app.llm, "mock_actif", lambda: False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-faux")
    auth_client.post("/api/cours", json={"nom": "physique"})
    job_id = auth_client.post("/api/ingest/texte", json={
        "cours": "physique", "texte": "Une notion à extraire."
    }).json()["job_id"]
    etat = auth_client.get(f"/api/jobs/{job_id}").json()
    assert etat["statut"] == "echoue"
    assert "coupés" in etat["message"]

    assert auth_client.post("/api/budget/arret", json={"coupe": False}).json()[
        "llm_desactive"
    ] is False


def test_arret_d_urgence_exige_une_session(client):
    assert client.post("/api/budget/arret", json={"coupe": False}).status_code == 401


def test_modele_hors_liste_refuse_dans_les_reglages(auth_client):
    r = auth_client.patch("/api/reglages", json={"modele": "claude-modele-hors-de-prix"})
    assert r.status_code == 400
    r = auth_client.patch("/api/reglages", json={"modele": "claude-haiku-4-5"})
    assert r.status_code == 200


def test_budget_signale_le_mode_simule(auth_client):
    assert auth_client.get("/api/budget").json()["mode_reel"] is False


# --- Fichiers sur disque ---------------------------------------------------

def test_sauvegarde_lisible_par_le_proprietaire_seul(tmp_path):
    base = tmp_path / "b.db"
    open_db(base).close()
    copie = backup.sauvegarder(base, tmp_path / "sauvegardes")
    assert stat.S_IMODE(copie.stat().st_mode) == 0o600
    assert stat.S_IMODE(copie.parent.stat().st_mode) == 0o700
    # Et la copie est une base cohérente.
    conn = open_db(copie)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_fichiers_envoyes_purges_apres_la_retention(auth_client):
    from flashcards.web import app as module_app

    uploads = module_app.UPLOADS
    uploads.mkdir(parents=True, exist_ok=True)
    vieux, recent = uploads / "vieux.pdf", uploads / "recent.pdf"
    for f in (vieux, recent):
        f.write_bytes(b"%PDF-")
    ancien = time.time() - (module_app.RETENTION_UPLOADS_JOURS + 1) * 86400
    os.utime(vieux, (ancien, ancien))
    try:
        assert module_app._purger_uploads() >= 1
        assert not vieux.exists() and recent.exists()
    finally:
        recent.unlink(missing_ok=True)


# --- Serveur de développement ----------------------------------------------

def test_serve_refuse_une_ecoute_publique_en_mode_developpement(monkeypatch):
    from typer.testing import CliRunner

    from flashcards.cli import app as cli

    monkeypatch.delenv("FLASHCARDS_ENV", raising=False)
    resultat = CliRunner().invoke(cli, ["web", "serve", "--host", "0.0.0.0"])
    assert resultat.exit_code == 1
    assert "FLASHCARDS_ENV" in resultat.output


def test_commandes_d_arret_d_urgence(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from flashcards.cli import app as cli

    monkeypatch.setenv("FLASHCARDS_DB", str(tmp_path / "cli.db"))
    runner = CliRunner()
    assert "coupés" in runner.invoke(cli, ["llm", "off"]).output
    conn = open_db(tmp_path / "cli.db")
    assert budget.llm_desactive(conn)
    assert "COUPÉ" in runner.invoke(cli, ["llm", "status"]).output
    assert "COUPÉ" in runner.invoke(cli, ["budget"]).output
    runner.invoke(cli, ["llm", "on"])
    assert not budget.llm_desactive(conn)
    conn.close()
