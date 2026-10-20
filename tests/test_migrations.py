"""Migration 005 (matières, poids, tests multi-cours, archivage): données conservées."""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from flashcards import api, db


def _base_a_l_ancienne(chemin: Path) -> sqlite3.Connection:
    """Une base dans l'état d'avant la migration 005 (migrations 001 à 004), avec des données."""
    conn = db.connect(chemin)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "CREATE TABLE schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    for fichier in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
        if fichier.name >= "005":
            break
        conn.executescript(fichier.read_text())
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (fichier.name,))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _remplir_ancienne_base(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO cours (nom, couleur) VALUES ('physique', '#5CC8FF')")
    conn.execute("INSERT INTO cours (nom, couleur) VALUES ('maths', '#8B7CFF')")
    payload = json.dumps({"type": "exact", "id": "k1", "enonce": "?", "reponse_exacte": "!"})
    for cid, cours in (("k1", "physique"), ("k2", "physique"), ("k3", "maths")):
        conn.execute(
            "INSERT INTO cards (id, concept_id, cours, chapitre, type, payload, imported_at,"
            " fsrs_state, fsrs_stability, fsrs_difficulty, last_review)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cid, f"c-{cid}", cours, "loi-ohm" if cours == "physique" else "algebre", "exact",
             payload, "2026-01-01T00:00:00+00:00", 2, 12.5, 5.0, "2026-01-02T00:00:00+00:00"),
        )
    conn.execute("INSERT INTO reviews (card_id, reviewed_at, rating, source) VALUES"
                 " ('k1', '2026-01-02T00:00:00+00:00', 3, 'auto')")
    conn.execute("INSERT INTO exams (cours, date, intitule) VALUES ('physique', '2026-02-10', 'TE Ohm')")
    conn.execute("INSERT INTO exams (cours, date, intitule) VALUES ('maths', '2026-03-01', 'TE algèbre')")
    conn.execute("INSERT INTO exam_chapitres (exam_id, chapitre) VALUES (1, 'loi-ohm')")
    conn.execute("INSERT INTO exam_chapitres (exam_id, chapitre) VALUES (2, 'algebre')")
    conn.execute("INSERT INTO settings (cle, valeur) VALUES ('plafond_quotidien', '25')")
    conn.execute("INSERT INTO web_user (id, identifiant, hash_mdp) VALUES (1, 'nate', 'hash-de-nate')")
    conn.commit()


@pytest.fixture
def migree(tmp_path):
    conn = _base_a_l_ancienne(tmp_path / "ancienne.db")
    _remplir_ancienne_base(conn)
    appliquees = db.migrate(conn)
    yield conn, appliquees
    conn.close()


def test_les_migrations_suivantes_sont_appliquees(migree):
    conn, appliquees = migree
    assert appliquees == [
        "005_matieres_poids_entretien.sql", "006_recap_cours.sql", "007_sans_chapitre.sql",
        "008_payload_sans_chapitre.sql", "009_comptes_roles_depot.sql",
    ]
    # 006: récap vide par défaut sur les thèmes existants.
    assert {r["recap"] for r in conn.execute("SELECT recap FROM cours")} == {""}


def test_les_cours_existants_deviennent_des_themes_sans_matiere(migree):
    conn, _ = migree
    lignes = {r["nom"]: dict(r) for r in conn.execute("SELECT * FROM cours")}
    assert set(lignes) == {"physique", "maths"}
    assert lignes["physique"]["couleur"] == "#5CC8FF"
    for ligne in lignes.values():
        assert ligne["matiere_id"] is None and ligne["archive"] == 0 and ligne["profil"] == ""


def test_les_cartes_et_l_historique_sont_intacts(migree):
    conn, _ = migree
    cartes = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM cards")}
    assert set(cartes) == {"k1", "k2", "k3"}
    assert cartes["k1"]["fsrs_stability"] == 12.5 and cartes["k1"]["last_review"].startswith("2026-01-02")
    assert cartes["k1"]["cours"] == "physique" and cartes["k3"]["cours"] == "maths"
    # Nouvelles colonnes: nullable, aucune valeur inventée.
    assert all(c["poids"] is None and c["categorie"] is None for c in cartes.values())
    assert conn.execute("SELECT COUNT(*) AS n FROM reviews").fetchone()["n"] == 1
    assert conn.execute("SELECT valeur FROM settings WHERE cle='plafond_quotidien'").fetchone()[0] == "25"


def test_les_tests_existants_sont_lies_a_leur_cours(migree):
    conn, _ = migree
    colonnes = {r["name"] for r in conn.execute("PRAGMA table_info(exams)")}
    assert colonnes == {"id", "date", "intitule"}, "exams.cours est remplacée par la table de liaison"
    liens = {(r["exam_id"], r["cours"]) for r in conn.execute("SELECT * FROM exam_cours")}
    assert liens == {(1, "physique"), (2, "maths")}

    exams = {e.id: e for e in api.load_exams(conn)}
    assert exams[1].cours == frozenset({"physique"}) and exams[1].date == date(2026, 2, 10)
    assert exams[1].couvre("physique") and not exams[1].couvre("maths")


def test_integrite_referentielle_et_idempotence(migree):
    conn, _ = migree
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.migrate(conn) == []


def test_le_moteur_fonctionne_sur_la_base_migree(migree, config):
    conn, _ = migree
    plan = api.plan_du_jour(conn, date(2026, 1, 20), config)
    assert plan.dus, "les cartes migrées sont planifiables"
    api.add_matiere(conn, "Sciences")
    api.update_cours(conn, "physique", matiere="Sciences")
    assert api.list_cours(conn)[1].matiere == "Sciences"


def test_les_suppressions_en_cascade_fonctionnent_sur_les_nouvelles_tables(migree):
    conn, _ = migree
    api.delete_cours(conn, "physique")
    assert (1, "physique") not in {(r["exam_id"], r["cours"]) for r in conn.execute("SELECT * FROM exam_cours")}
    api.remove_test(conn, 2)
    assert conn.execute("SELECT COUNT(*) AS n FROM exam_cours").fetchone()["n"] == 0


def test_base_neuve_a_le_meme_schema(tmp_path):
    conn = db.open_db(tmp_path / "neuve.db")
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"matieres", "exam_cours", "exam_matieres"} <= tables
    cartes = {r["name"] for r in conn.execute("PRAGMA table_info(cards)")}
    assert {"poids", "categorie"} <= cartes
    conn.close()


def test_migration_008_retire_la_cle_des_payloads_stockes(tmp_path):
    from flashcards import db

    conn = db.connect(tmp_path / "m.db")
    db.migrate(conn)
    conn.execute("INSERT INTO cours (nom, couleur) VALUES ('t', '#fff')")
    conn.execute(
        "INSERT INTO cards (id, concept_id, cours, type, payload, imported_at)"
        " VALUES ('a', 'c', 't', 'exact', ?, '2026-01-01T00:00:00+00:00')",
        (json.dumps({"type": "exact", "ancienne_cle": 1, "enonce": "?"}),),
    )
    sql = (db.MIGRATIONS_DIR / "008_payload_sans_chapitre.sql").read_text()
    conn.execute(sql.replace("$.chapitre", "$.ancienne_cle"))
    assert json.loads(conn.execute("SELECT payload FROM cards").fetchone()[0]) == {
        "type": "exact", "enonce": "?",
    }


def test_le_compte_unique_devient_administrateur(migree):
    conn, _ = migree
    ligne = conn.execute("SELECT identifiant, hash_mdp, role FROM comptes").fetchall()
    assert [tuple(r) for r in ligne] == [("nate", "hash-de-nate", "admin")]
