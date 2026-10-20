"""CLI: tests liés à plusieurs cours / à une matière, archivage, today et stats en points."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from flashcards import api
from flashcards.cli import app
from flashcards.db import open_db

from .conftest import make_card

runner = CliRunner()
JOUR = "2026-01-05"


@pytest.fixture
def base(tmp_path, monkeypatch):
    chemin = tmp_path / "cli.db"
    monkeypatch.setenv("FLASHCARDS_DB", str(chemin))
    conn = open_db(chemin)
    yield conn
    conn.close()


def cli(*args: str):
    res = runner.invoke(app, list(args))
    return res


def test_test_add_avec_plusieurs_cours(base):
    res = cli("test", "add", "--cours", "conditionnel", "--cours", "present-perfect",
              "--date", "2026-02-10", "--intitule", "TE anglais")
    assert res.exit_code == 0, res.output
    assert "2 cours" in res.output and "conditionnel" in res.output
    assert api.load_exams(base)[0].cours == frozenset({"conditionnel", "present-perfect"})


def test_test_add_toute_une_matiere(base):
    api.add_matiere(base, "Anglais")
    for nom in ("cond", "pp"):
        api.add_cours(base, nom, matiere="Anglais")
    res = cli("test", "add", "--matiere", "Anglais", "--date", "2026-06-20",
              "--intitule", "Examen de fin d'année")
    assert res.exit_code == 0, res.output
    assert "matière(s) Anglais (entière)" in res.output
    assert api.load_exams(base)[0].cours == frozenset({"cond", "pp"})


def test_test_add_sans_aucun_lien_est_refuse(base):
    res = cli("test", "add", "--date", "2026-02-10", "--intitule", "TE")
    assert res.exit_code == 1
    assert "au moins un cours ou une matière" in res.output


def test_test_list_montre_les_liens(base):
    api.add_matiere(base, "Anglais")
    api.add_cours(base, "cond", matiere="Anglais")
    api.add_test(base, cours="cond", matieres=["Anglais"], jour=date(2026, 2, 10), intitule="TE")
    res = cli("test", "list")
    assert res.exit_code == 0
    assert "TE" in res.output and "Anglais" in res.output and "cond" in res.output


def test_test_move_change_la_date_et_les_liens(base):
    api.add_matiere(base, "Anglais")
    api.add_cours(base, "cond", matiere="Anglais")
    api.add_cours(base, "autre")
    exam_id = api.add_test(base, cours="cond", jour=date(2026, 2, 10), intitule="TE")

    res = cli("test", "move", str(exam_id), "--date", "2026-03-01",
              "--ajouter-cours", "autre", "--ajouter-matiere", "Anglais")
    assert res.exit_code == 0, res.output
    exam = api.load_exams(base)[0]
    assert exam.date == date(2026, 3, 1)
    assert exam.cours == frozenset({"cond", "autre"}) and exam.matieres == frozenset({"Anglais"})

    res = cli("test", "move", str(exam_id), "--retirer-cours", "autre", "--retirer-matiere", "Anglais")
    assert res.exit_code == 0
    assert api.load_exams(base)[0].cours == frozenset({"cond"})


def test_test_move_sans_option_est_refuse(base):
    exam_id = api.add_test(base, cours="x", jour=date(2026, 2, 10), intitule="TE")
    res = cli("test", "move", str(exam_id))
    assert res.exit_code == 1 and "Rien à faire" in res.output


def test_matiere_et_cours_en_ligne_de_commande(base):
    assert cli("matiere", "add", "--nom", "Anglais").exit_code == 0
    assert cli("cours", "add", "--nom", "cond", "--matiere", "Anglais").exit_code == 0
    res = cli("matiere", "list")
    assert "Anglais" in res.output and "cond" in res.output

    assert cli("cours", "archive", "--nom", "cond").exit_code == 0
    assert "archivé" in cli("cours", "list").output
    assert cli("cours", "unarchive", "--nom", "cond").exit_code == 0
    assert "archivé" not in cli("cours", "list").output.replace("[archivé]", "")

    assert cli("cours", "set-matiere", "--nom", "cond").exit_code == 0  # détache
    assert api.list_cours(base)[0].matiere is None
    assert cli("matiere", "delete", "--nom", "Anglais").exit_code == 0


def test_today_affiche_cartes_points_et_entretien(base):
    vieux = datetime(2025, 11, 1, tzinfo=timezone.utc)
    for i in range(3):
        make_card(base, f"t{i}", cours="actuel", qtype="probleme", poids=None, stability=1.0,
                  difficulty=5.0, last_review=vieux, state=2)
    make_card(base, "e1", cours="ancien", stability=300.0, difficulty=5.0,
              last_review=datetime(2025, 11, 26, tzinfo=timezone.utc), state=2, poids=0.5)
    api.add_test(base, cours="actuel", jour=date(2026, 2, 20), intitule="TE actuel")

    res = cli("today", "--jour", JOUR)

    assert res.exit_code == 0, res.output
    assert "environ 6.5 point(s)" in res.output, res.output
    assert "plafond 40 pts" in res.output
    assert "actuel" in res.output and "(entretien)" in res.output
    assert "Entretien: 1 thème(s)" in res.output and "8%" in res.output
    assert "TE actuel" in res.output


def test_stats_affiche_points_cartes_et_entretien(base):
    for i in range(5):
        make_card(base, f"n{i}", cours="ancien", qtype="probleme", poids=None)
    res = cli("stats", "--jour", JOUR)
    assert res.exit_code == 0, res.output
    assert "10 pts" in res.output and "File du jour: 5 carte(s)" in res.output
    assert "Charge prévue" in res.output and " q" in res.output and "pts" in res.output


def test_stats_signale_une_capacite_d_entretien_insuffisante(base, monkeypatch):
    # 20 thèmes en entretien × 3 pts = 60 > 7 × 8 = 56.
    for t in range(20):
        make_card(base, f"c{t}", cours=f"theme{t}", stability=300.0, difficulty=5.0,
                  last_review=datetime(2025, 11, 26, tzinfo=timezone.utc), state=2)
    res = cli("stats", "--jour", JOUR)
    assert "⚠" in res.output and "Entretien: 60 points demandés" in res.output


def test_session_cli_affiche_la_progression_en_points(base, monkeypatch):
    make_card(base, "a", qtype="exact", poids=0.5)
    make_card(base, "b", qtype="exact", poids=0.5)
    monkeypatch.setattr("builtins.input", lambda *_: "q")
    res = cli("session", "--jour", JOUR)
    assert res.exit_code == 0, res.output
    assert "2 question(s), environ 1 point(s)" in res.output
    assert "0/1 pts" in res.output


def test_simulate_cli_scenario_inconnu(base):
    res = cli("simulate", "--scenario", "nimporte")
    assert res.exit_code == 1 and "Scénario inconnu" in res.output
