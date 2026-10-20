from __future__ import annotations

from pathlib import Path

import pytest

from flashcards import manuel
from flashcards.importer import import_questions, importer_resultat, marquer_deja_en_base

from .conftest import NOW, question_dict, write_questions_file
from .fabriques_manuel import (
    ecrire, fichier_concepts, fichier_questions, json_texte, q_exact, q_qcm,
)


def test_import_simple(conn, tmp_path: Path):
    path = write_questions_file(
        tmp_path / "cours.questions.json", [question_dict("q1"), question_dict("q2")]
    )
    rapport = import_questions(conn, path, now=NOW)

    assert rapport.importees == 2
    assert rapport.deja_presentes == 0
    assert rapport.cours == ["physique"]
    assert conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"] == 2
    assert conn.execute("SELECT COUNT(*) AS n FROM cours").fetchone()["n"] == 1


def test_import_idempotent_preserve_etat_fsrs(conn, tmp_path: Path):
    path = write_questions_file(tmp_path / "cours.questions.json", [question_dict("q1")])
    import_questions(conn, path, now=NOW)

    conn.execute("UPDATE cards SET fsrs_stability = 42.0 WHERE id = 'q1'")
    conn.commit()

    rapport = import_questions(conn, path, now=NOW)
    assert rapport.importees == 0
    assert rapport.deja_presentes == 1

    stability = conn.execute("SELECT fsrs_stability FROM cards WHERE id='q1'").fetchone()[0]
    assert stability == 42.0, "réimporter ne doit pas écraser la progression"


def test_import_json_invalide(conn, tmp_path: Path):
    path = tmp_path / "casse.json"
    path.write_text("{ ceci n'est pas du json")

    with pytest.raises(RuntimeError, match="valide"):
        import_questions(conn, path, now=NOW)


def test_import_json_valide_mais_mauvais_schema(conn, tmp_path: Path):
    path = tmp_path / "mauvais.json"
    path.write_text('{"questions": [{"type": "inconnu", "enonce": "?"}]}')

    with pytest.raises(RuntimeError, match="pas un fichier de questions valide"):
        import_questions(conn, path, now=NOW)


def test_import_fichier_absent(conn, tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        import_questions(conn, tmp_path / "absent.json", now=NOW)


# --- Workflow manuel: fichiers bruts, doublons de contenu -------------------

def _nb_cartes(conn) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]


@pytest.mark.usefixtures("pas_de_llm")
def test_import_fichier_brut_valide(conn, tmp_path: Path):
    path = ecrire(
        tmp_path / "ch1_questions.json", fichier_questions([q_exact("c1"), q_qcm("c2")])
    )
    rapport = import_questions(conn, path, now=NOW)

    assert (rapport.importees, rapport.deja_presentes, rapport.doublons_contenu) == (2, 0, 0)
    assert rapport.cours == ["physique"]
    ligne = conn.execute("SELECT * FROM cards WHERE concept_id = 'c2'").fetchone()
    assert ligne["type"] == "qcm" and ligne["cours"] == "physique"
    assert ligne["source_fichier"] == "cours.pdf"


@pytest.mark.usefixtures("pas_de_llm")
def test_reimport_brut_idempotent_et_garde_l_etat_fsrs(conn, tmp_path: Path):
    path = ecrire(tmp_path / "ch1_questions.json", fichier_questions([q_exact("c1")]))
    import_questions(conn, path, now=NOW)
    conn.execute("UPDATE cards SET fsrs_stability = 7.5")
    conn.commit()

    rapport = import_questions(conn, path, now=NOW)

    assert (rapport.importees, rapport.deja_presentes) == (0, 1)
    assert _nb_cartes(conn) == 1
    assert conn.execute("SELECT fsrs_stability FROM cards").fetchone()[0] == 7.5


def test_doublon_de_contenu_sous_un_autre_id_est_ignore(conn, tmp_path: Path):
    premier = write_questions_file(tmp_path / "a.json", [question_dict("q1")])
    import_questions(conn, premier, now=NOW)

    # Même énoncé, même réponse, autre id (ex.: générée une 2e fois).
    copie = question_dict("autre-id")
    copie["enonce"] = question_dict("q1")["enonce"]
    second = write_questions_file(tmp_path / "b.json", [copie])
    rapport = import_questions(conn, second, now=NOW)

    assert (rapport.importees, rapport.doublons_contenu) == (0, 1)
    assert _nb_cartes(conn) == 1


def test_meme_contenu_dans_un_autre_cours_n_est_pas_un_doublon(conn, tmp_path: Path):
    obj = fichier_questions([q_exact("c1")])
    import_questions(conn, ecrire(tmp_path / "a.json", obj), now=NOW)
    autre = ecrire(tmp_path / "b.json", {**obj, "cours": "maths"})
    rapport = import_questions(conn, autre, now=NOW)

    assert rapport.importees == 1
    assert _nb_cartes(conn) == 2


def test_doublon_dans_le_meme_fichier_importe_une_seule_carte(conn, tmp_path: Path):
    path = ecrire(
        tmp_path / "a.json",
        fichier_questions([q_exact("c1", "Même ?"), q_exact("c1", "Même ?")]),
    )
    rapport = import_questions(conn, path, now=NOW)
    assert rapport.importees == 1
    assert _nb_cartes(conn) == 1


def test_import_refuse_en_bloc_si_une_question_est_invalide(conn, tmp_path: Path):
    mauvais = q_qcm("c2")
    del mauvais["propositions"]
    path = ecrire(tmp_path / "a.json", fichier_questions([q_exact("c1"), mauvais]))

    with pytest.raises(RuntimeError, match="champ manquant: propositions"):
        import_questions(conn, path, now=NOW)

    assert _nb_cartes(conn) == 0, "rien ne doit être importé à moitié"


def test_import_refuse_une_liste_de_concepts(conn, tmp_path: Path):
    path = ecrire(tmp_path / "c.json", fichier_concepts("c1"))
    with pytest.raises(RuntimeError, match="pas un fichier de questions valide"):
        import_questions(conn, path, now=NOW)


def test_apercu_marque_ce_qui_existe_deja_en_base(conn, tmp_path: Path):
    contenu = fichier_questions([q_exact("c1", "Déjà là ?"), q_exact("c1", "Nouvelle ?")])
    import_questions(
        conn, ecrire(tmp_path / "a.json", fichier_questions([q_exact("c1", "Déjà là ?")])),
        now=NOW,
    )

    analyse = manuel.analyser(json_texte(contenu), nom="b.json")
    avant = _nb_cartes(conn)
    marquer_deja_en_base(conn, analyse)

    assert [a.statut for a in analyse.rapport.apercu] == ["deja_en_base", "nouvelle"]
    assert _nb_cartes(conn) == avant, "l'aperçu n'écrit rien"
    # Et l'import le confirme: la question déjà là est reconnue (même id dérivé
    # du contenu), seule la nouvelle est écrite.
    rapport = importer_resultat(conn, analyse.resultat, fichier="b.json", now=NOW)
    assert (rapport.importees, rapport.deja_presentes, rapport.doublons_contenu) == (1, 1, 0)
