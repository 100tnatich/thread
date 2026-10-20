"""Récap théorique d'un thème: lu avant ses questions, en session « par cours » seulement."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from flashcards.cli import app as cli_app
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


def carte_due(cid, cours):
    with base() as conn:
        make_card(conn, cid, cours=cours, stability=1.0, difficulty=5.0, last_review=VIEUX, state=2)


def theme_avec_carte(c, nom, recap=None):
    c.post("/api/cours", json={"nom": nom})
    if recap is not None:
        assert c.patch(f"/api/cours/{nom}", json={"recap": recap}).status_code == 200
    carte_due(f"{nom}-1", nom)


def test_le_recap_se_range_et_se_relit_par_theme(auth_client):  # noqa: F811
    auth_client.post("/api/cours", json={"nom": "anglais"})
    assert auth_client.get("/api/cours").json()[0]["recap"] == ""
    auth_client.patch("/api/cours/anglais", json={"recap": "  ## Rappel\n- if + present  \n"})
    assert auth_client.get("/api/cours").json()[0]["recap"] == "## Rappel\n- if + present"
    auth_client.patch("/api/cours/anglais", json={"recap": ""})
    assert auth_client.get("/api/cours").json()[0]["recap"] == ""


def test_modifier_autre_chose_ne_touche_pas_au_recap(auth_client):  # noqa: F811
    auth_client.post("/api/cours", json={"nom": "anglais"})
    auth_client.patch("/api/cours/anglais", json={"recap": "à garder"})
    auth_client.patch("/api/cours/anglais", json={"couleur": "#112233"})
    assert auth_client.get("/api/cours").json()[0]["recap"] == "à garder"


def test_renommer_un_theme_garde_son_recap(auth_client):  # noqa: F811
    auth_client.post("/api/cours", json={"nom": "ancien"})
    auth_client.patch("/api/cours/ancien", json={"recap": "à garder"})
    auth_client.patch("/api/cours/ancien", json={"nouveau_nom": "nouveau"})
    assert [(c["nom"], c["recap"]) for c in auth_client.get("/api/cours").json()] == [
        ("nouveau", "à garder")
    ]


def test_par_cours_un_recap_par_theme_qui_en_a_un_dans_l_ordre(auth_client):  # noqa: F811
    theme_avec_carte(auth_client, "zzz", recap="récap Z")
    theme_avec_carte(auth_client, "aaa", recap="récap A")
    theme_avec_carte(auth_client, "mmm")  # sans récap
    s = auth_client.get("/api/session", params={"jour": JOUR, "ordre": "par_cours"}).json()
    assert s["ordre"] == "par_cours"
    assert [(r["cours"], r["texte"]) for r in s["recaps"]] == [("aaa", "récap A"), ("zzz", "récap Z")]
    assert all(r["couleur"] for r in s["recaps"])


def test_melange_aucun_recap(auth_client):  # noqa: F811
    theme_avec_carte(auth_client, "aaa", recap="récap A")
    s = auth_client.get("/api/session", params={"jour": JOUR, "ordre": "melange"}).json()
    assert s["ordre"] == "melange" and s["recaps"] == []


def test_theme_sans_carte_due_pas_de_recap(auth_client):  # noqa: F811
    theme_avec_carte(auth_client, "aaa", recap="récap A")
    auth_client.post("/api/cours", json={"nom": "vide"})
    auth_client.patch("/api/cours/vide", json={"recap": "inutile"})
    s = auth_client.get("/api/session", params={"jour": JOUR, "ordre": "par_cours"}).json()
    assert [r["cours"] for r in s["recaps"]] == ["aaa"]


def test_ordre_par_defaut_des_reglages_donne_les_recaps(auth_client):  # noqa: F811
    theme_avec_carte(auth_client, "aaa", recap="récap A")
    s = auth_client.get("/api/session", params={"jour": JOUR}).json()
    assert s["ordre"] == "melange" and s["recaps"] == []
    auth_client.patch("/api/reglages", json={"ordre_session": "par_cours"})
    s = auth_client.get("/api/session", params={"jour": JOUR}).json()
    assert s["ordre"] == "par_cours" and [r["cours"] for r in s["recaps"]] == ["aaa"]


def test_recap_trop_long_refuse(auth_client):  # noqa: F811
    auth_client.post("/api/cours", json={"nom": "aaa"})
    r = auth_client.patch("/api/cours/aaa", json={"recap": "x" * 20001})
    assert r.status_code == 422


def test_cli_recap_enregistre_affiche_et_efface(auth_client, tmp_path):  # noqa: F811
    auth_client.post("/api/cours", json={"nom": "anglais"})
    fichier = tmp_path / "recap.md"
    fichier.write_text("## Rappel\n- if + present\n", encoding="utf-8")
    runner = CliRunner()
    r = runner.invoke(cli_app, ["cours", "recap", "--nom", "anglais", "--fichier", str(fichier)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(cli_app, ["cours", "recap", "--nom", "anglais"])
    assert "if + present" in r.output
    r = runner.invoke(cli_app, ["cours", "recap", "--nom", "anglais", "--efface"])
    assert r.exit_code == 0
    assert "Pas de récap" in runner.invoke(cli_app, ["cours", "recap", "--nom", "anglais"]).output


def test_cli_recap_theme_inconnu(auth_client):  # noqa: F811
    r = CliRunner().invoke(cli_app, ["cours", "recap", "--nom", "inconnu"])
    assert r.exit_code != 0
