"""Matières (parents optionnels), tests liés à plusieurs cours, archivage, profils hérités."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fsrs import Rating
from typer.testing import CliRunner

from flashcards import api, profiles
from flashcards.cli import app

from .conftest import NOW, TODAY, make_card

runner = CliRunner()


def vieille(conn, cid, cours):
    """Carte due depuis longtemps (planifiée par FSRS)."""
    return make_card(conn, cid, cours=cours, stability=1.0, difficulty=5.0,
                     last_review=datetime(2025, 11, 1, tzinfo=timezone.utc), state=2)


# --- Matières et thèmes -------------------------------------------------------

def test_crud_matiere(conn):
    api.add_matiere(conn, "Anglais", "#5CC8FF", "Cours: Anglais")
    api.add_matiere(conn, "Maths")
    assert [m.nom for m in api.list_matieres(conn)] == ["Anglais", "Maths"]
    assert api.list_matieres(conn)[0].profil == "Cours: Anglais"

    api.update_matiere(conn, "Anglais", couleur="#FF6B7A", nouveau_nom="English")
    assert [m.nom for m in api.list_matieres(conn)] == ["English", "Maths"]
    assert api.list_matieres(conn)[0].couleur == "#FF6B7A"


def test_matiere_doublon_ou_vide_refuse(conn):
    api.add_matiere(conn, "Anglais")
    with pytest.raises(RuntimeError, match="existe déjà"):
        api.add_matiere(conn, "Anglais")
    with pytest.raises(RuntimeError, match="vide"):
        api.add_matiere(conn, "  ")
    api.add_matiere(conn, "Maths")
    with pytest.raises(RuntimeError, match="existe déjà"):
        api.update_matiere(conn, "Maths", nouveau_nom="Anglais")


def test_un_theme_se_range_dans_une_matiere_et_herite_sa_couleur(conn):
    api.add_matiere(conn, "Anglais", "#5CC8FF")
    api.add_cours(conn, "Anglais conditionnel", matiere="Anglais")
    api.add_cours(conn, "Libre")
    api.add_cours(conn, "Autre couleur", "#111111", matiere="Anglais")

    themes = {c.nom: c for c in api.list_cours(conn)}
    assert themes["Anglais conditionnel"].matiere == "Anglais"
    assert themes["Anglais conditionnel"].couleur == "#5CC8FF"
    assert themes["Libre"].matiere is None, "la matière est optionnelle"
    assert themes["Autre couleur"].couleur == "#111111"
    assert api.list_matieres(conn)[0].nb_cours == 2


def test_theme_dans_une_matiere_inconnue_refuse(conn):
    with pytest.raises(RuntimeError, match="Matière inconnue"):
        api.add_cours(conn, "X", matiere="Fantôme")


def test_deplacer_ou_detacher_un_theme(conn):
    api.add_matiere(conn, "A")
    api.add_matiere(conn, "B")
    api.add_cours(conn, "t", matiere="A")
    api.update_cours(conn, "t", matiere="B")
    assert api.list_cours(conn)[0].matiere == "B"
    api.update_cours(conn, "t", matiere=None)
    assert api.list_cours(conn)[0].matiere is None
    api.update_cours(conn, "t", couleur="#222222")  # ne touche pas à la matière
    api.update_cours(conn, "t", matiere="A")
    api.update_cours(conn, "t", couleur="#333333")
    assert api.list_cours(conn)[0].matiere == "A", "matiere=INCHANGE par défaut"


def test_supprimer_une_matiere_garde_ses_themes_et_leurs_cartes(conn):
    api.add_matiere(conn, "Anglais")
    api.add_cours(conn, "conditionnel", matiere="Anglais")
    make_card(conn, "c1", cours="conditionnel")

    nb = api.delete_matiere(conn, "Anglais")

    assert nb == 1
    assert api.list_matieres(conn) == []
    assert api.list_cours(conn)[0].matiere is None
    assert len(api.load_cards(conn)) == 1


def test_renommer_un_theme_garde_sa_matiere_et_ses_liens(conn):
    api.add_matiere(conn, "Anglais")
    api.add_cours(conn, "cond", matiere="Anglais")
    api.add_test(conn, cours="cond", jour=TODAY + timedelta(days=5), intitule="TE")
    api.update_cours(conn, "cond", nouveau_nom="conditionnel")
    theme = api.list_cours(conn)[0]
    assert (theme.nom, theme.matiere) == ("conditionnel", "Anglais")
    assert api.load_exams(conn)[0].cours == frozenset({"conditionnel"})


# --- Tests liés à plusieurs cours --------------------------------------------

def test_un_test_se_lie_a_plusieurs_cours(conn, config):
    for nom in ("conditionnel", "present-perfect"):
        api.add_cours(conn, nom)
        vieille(conn, f"v-{nom}", nom)
    vieille(conn, "hors-test", "autre")
    exam_id = api.add_test(conn, cours=["conditionnel", "present-perfect"],
                           jour=TODAY + timedelta(days=9), intitule="TE anglais")

    exam = api.load_exams(conn)[0]
    assert exam.id == exam_id
    assert exam.cours == frozenset({"conditionnel", "present-perfect"})
    assert exam.couvre("conditionnel") and exam.couvre("present-perfect")
    assert not exam.couvre("autre")

    plan = api.plan_du_jour(conn, TODAY, config)
    assert plan.themes_entretien == {"autre"}, "les deux thèmes liés passent en mode test"
    en_test = {i.card.id: i.prochain_examen for i in plan.items if not i.entretien}
    assert all(e is not None and e.id == exam_id for e in en_test.values())
    assert set(en_test) == {"v-conditionnel", "v-present-perfect"}


def test_un_cours_peut_etre_lie_a_plusieurs_tests(conn):
    api.add_cours(conn, "t")
    api.add_test(conn, cours="t", jour=TODAY + timedelta(days=30), intitule="loin")
    api.add_test(conn, cours=["t"], jour=TODAY + timedelta(days=7), intitule="proche")
    assert len(api.load_exams(conn)) == 2


def test_un_test_exige_au_moins_un_lien(conn):
    with pytest.raises(RuntimeError, match="au moins un cours ou une matière"):
        api.add_test(conn, jour=TODAY, intitule="orphelin")
    assert conn.execute("SELECT COUNT(*) AS n FROM exams").fetchone()["n"] == 0


def test_matiere_inconnue_ne_laisse_pas_de_test_a_moitie_cree(conn):
    api.add_cours(conn, "t")
    with pytest.raises(RuntimeError, match="Matière inconnue"):
        api.add_test(conn, cours="t", matieres=["Fantôme"], jour=TODAY, intitule="X")
    assert conn.execute("SELECT COUNT(*) AS n FROM exams").fetchone()["n"] == 0


def test_lier_tous_les_cours_d_une_matiere(conn, config):
    api.add_matiere(conn, "Anglais")
    for nom in ("cond", "pp", "vocab"):
        api.add_cours(conn, nom, matiere="Anglais")
    api.add_cours(conn, "maths")
    exam_id = api.add_test(conn, cours="maths", jour=TODAY + timedelta(days=100), intitule="Fin d'année")

    nb = api.link_matiere(conn, exam_id, "Anglais")

    assert nb == 3
    exam = api.load_exams(conn)[0]
    assert exam.cours == frozenset({"cond", "pp", "vocab", "maths"})
    assert exam.matieres == frozenset({"Anglais"})


def test_la_liaison_a_la_matiere_couvre_aussi_les_themes_ajoutes_plus_tard(conn):
    api.add_matiere(conn, "Anglais")
    api.add_cours(conn, "cond", matiere="Anglais")
    api.add_test(conn, matieres=["Anglais"], jour=TODAY + timedelta(days=90), intitule="Fin d'année")
    assert api.load_exams(conn)[0].cours == frozenset({"cond"})

    api.add_cours(conn, "nouveau-theme", matiere="Anglais")
    assert api.load_exams(conn)[0].cours == frozenset({"cond", "nouveau-theme"})

    api.update_cours(conn, "nouveau-theme", matiere=None)
    assert api.load_exams(conn)[0].cours == frozenset({"cond"})


def test_les_themes_archives_sont_exclus_de_la_matiere_liee(conn):
    api.add_matiere(conn, "Anglais")
    api.add_cours(conn, "actif", matiere="Anglais")
    api.add_cours(conn, "vieux", matiere="Anglais")
    api.add_test(conn, matieres=["Anglais"], jour=TODAY + timedelta(days=90), intitule="X")
    api.archive_cours(conn, "vieux")
    assert api.load_exams(conn)[0].cours == frozenset({"actif"})


def test_delier_un_cours_ou_une_matiere(conn):
    api.add_matiere(conn, "Anglais")
    api.add_cours(conn, "cond", matiere="Anglais")
    api.add_cours(conn, "autre")
    exam_id = api.add_test(conn, cours=["cond", "autre"], matieres=["Anglais"],
                           jour=TODAY + timedelta(days=9), intitule="X")
    api.unlink_cours(conn, exam_id, "autre")
    api.unlink_matiere(conn, exam_id, "Anglais")
    exam = api.load_exams(conn)[0]
    assert exam.cours == frozenset({"cond"}) and exam.matieres == frozenset()


def test_remplacer_les_liens_d_un_test(conn):
    for nom in ("a", "b", "c"):
        api.add_cours(conn, nom)
    exam_id = api.add_test(conn, cours=["a", "b"], jour=TODAY + timedelta(days=9), intitule="X")
    api.set_test_liens(conn, exam_id, cours=["c"])
    assert api.load_exams(conn)[0].cours == frozenset({"c"})
    with pytest.raises(RuntimeError, match="Aucun test"):
        api.set_test_liens(conn, 999, cours=["a"])


def test_un_test_couvre_ses_themes_entiers(conn):
    api.add_cours(conn, "t")
    api.add_cours(conn, "autre")
    api.add_test(conn, cours="t", jour=TODAY + timedelta(days=9), intitule="tous")
    (examen,) = api.load_exams(conn)
    assert examen.couvre("t") and not examen.couvre("autre")


def test_un_test_ne_peut_pas_etre_lie_a_un_cours_archive(conn):
    api.add_cours(conn, "vieux")
    api.archive_cours(conn, "vieux")
    with pytest.raises(RuntimeError, match="archivé"):
        api.add_test(conn, cours="vieux", jour=TODAY + timedelta(days=9), intitule="X")


def test_supprimer_un_cours_reaffecte_aussi_ses_liens_de_test(conn):
    for nom in ("a", "b"):
        api.add_cours(conn, nom)
    api.add_test(conn, cours=["a", "b"], jour=TODAY + timedelta(days=9), intitule="X")  # lié aux 2
    api.add_test(conn, cours="a", jour=TODAY + timedelta(days=20), intitule="Y")
    api.delete_cours(conn, "a", reaffecter_vers="b")
    for exam in api.load_exams(conn):
        assert exam.cours == frozenset({"b"})


# --- Archivage ----------------------------------------------------------------

def test_archiver_un_cours_est_reversible_et_garde_les_cartes(conn, config):
    vieille(conn, "c1", "ancien")
    vieille(conn, "c2", "actuel")
    assert {i.card.id for i in api.get_today_queue(conn, TODAY, config)} == {"c1", "c2"}

    api.archive_cours(conn, "ancien")

    assert {i.card.id for i in api.get_today_queue(conn, TODAY, config)} == {"c2"}
    assert [c.id for c in api.load_cards(conn)] == ["c2"], "hors planning"
    assert conn.execute("SELECT COUNT(*) AS n FROM cards WHERE cours='ancien'").fetchone()["n"] == 1
    assert next(c for c in api.list_cours(conn) if c.nom == "ancien").archive
    assert next(c for c in api.list_cours(conn) if c.nom == "ancien").nb_cartes == 1

    api.archive_cours(conn, "ancien", False)
    assert {i.card.id for i in api.get_today_queue(conn, TODAY, config)} == {"c1", "c2"}


def test_archiver_garde_l_historique_fsrs(conn, config):
    vieille(conn, "c1", "ancien")
    api.record_answer(conn, "c1", Rating.Good, reviewed_at=NOW, config=config, correct=True)
    avant = dict(conn.execute("SELECT * FROM cards WHERE id='c1'").fetchone())
    api.archive_cours(conn, "ancien")
    api.archive_cours(conn, "ancien", False)
    assert dict(conn.execute("SELECT * FROM cards WHERE id='c1'").fetchone()) == avant
    assert conn.execute("SELECT COUNT(*) AS n FROM reviews").fetchone()["n"] == 1


def test_archiver_un_cours_inconnu(conn):
    with pytest.raises(RuntimeError, match="inconnu"):
        api.archive_cours(conn, "fantôme")


def test_les_themes_archives_sortent_des_stats(conn, config):
    vieille(conn, "c1", "ancien")
    vieille(conn, "c2", "actuel")
    api.archive_cours(conn, "ancien")
    s = api.get_stats(conn, TODAY, config)
    assert s.total_cartes == 1 and s.nb_dues == 1


def test_cours_en_entretien_exclut_archives_et_themes_en_test(conn):
    for nom in ("passe", "sans-test", "en-test", "archive"):
        api.add_cours(conn, nom)
    api.add_test(conn, cours="passe", jour=TODAY - timedelta(days=3), intitule="passé")
    api.add_test(conn, cours="en-test", jour=TODAY + timedelta(days=3), intitule="futur")
    api.archive_cours(conn, "archive")
    assert api.cours_en_entretien(conn, TODAY) == {"passe", "sans-test"}


# --- Profils: la matière définit, le thème surcharge -------------------------

MATIERE = """Cours: Anglais
Niveau: B1
Langue: anglais
Notation et méthode du prof: une faute d'orthographe = -0,5
À éviter: les QCM
Exemples de bonnes questions (2 ou 3):
- (exact) « chat » en anglais ?"""

THEME = """Cours: Anglais conditionnel
Niveau:
Notation et méthode du prof: if + présent, will + infinitif (type 1)
Particularités: seuls les types 1 et 2 sont au programme"""


def test_fusion_le_theme_prime_rubrique_par_rubrique():
    fusion = profiles.fusionner(MATIERE, THEME)
    sections = profiles.decouper(fusion)
    assert sections["Cours"] == "Cours: Anglais conditionnel"
    assert "type 1" in sections["Notation et méthode du prof"]
    assert "orthographe" not in fusion, "la rubrique du thème remplace celle de la matière"
    # Rubrique vide dans le thème: on garde celle de la matière.
    assert sections["Niveau"] == "Niveau: B1"
    # Rubriques propres à un seul niveau: conservées.
    assert sections["Langue"] == "Langue: anglais"
    assert sections["À éviter"] == "À éviter: les QCM"
    assert "types 1 et 2" in sections["Particularités"]
    assert "chat" in sections["Exemples de bonnes questions"]


def test_fusion_garde_l_ordre_du_gabarit():
    fusion = profiles.fusionner(MATIERE, THEME)
    titres = [ligne.split(":")[0].split(" (")[0] for ligne in fusion.splitlines()
              if ligne.split(":")[0].split(" (")[0] in profiles.ENTETES]
    assert titres == [e for e in profiles.ENTETES if e in titres]


def test_fusion_de_textes_libres_met_les_deux_bout_a_bout():
    fusion = profiles.fusionner("Toujours tutoyer.", "Pas de calculatrice.")
    assert fusion.index("tutoyer") < fusion.index("calculatrice")


def test_profil_du_theme_seul_ou_de_la_matiere_seule(conn):
    api.add_matiere(conn, "Anglais", profil=MATIERE)
    api.add_cours(conn, "avec-profil", matiere="Anglais", profil=THEME)
    api.add_cours(conn, "heritier", matiere="Anglais")

    seul_theme = profiles.charger("avec-profil", conn=conn)
    assert seul_theme.origine == "matière + thème" and "type 1" in seul_theme.texte

    heritier = profiles.charger("heritier", conn=conn)
    assert heritier.origine == "matière" and "orthographe" in heritier.texte
    assert heritier.trouve and heritier.avertissement is None


def test_theme_sans_matiere_utilise_son_propre_profil(conn):
    api.add_cours(conn, "solo", profil="Cours: solo\nNiveau: A2")
    p = profiles.charger("solo", conn=conn)
    assert p.origine == "thème" and "A2" in p.texte and p.trouve


def test_les_fichiers_profiles_restent_pris_en_compte(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path)
    (tmp_path / "Anglais.md").write_text(MATIERE)
    (tmp_path / "cond.md").write_text(THEME)
    api.add_matiere(conn, "Anglais")
    api.add_cours(conn, "cond", matiere="Anglais")
    p = profiles.charger("cond", conn=conn)
    assert p.origine == "matière + thème" and "type 1" in p.texte and "B1" in p.texte


def test_le_texte_saisi_prime_sur_le_fichier(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path)
    (tmp_path / "Anglais.md").write_text("Niveau: fichier")
    api.add_matiere(conn, "Anglais", profil="Niveau: interface")
    api.add_cours(conn, "cond", matiere="Anglais")
    assert "interface" in profiles.charger("cond", conn=conn).texte
    assert "fichier" not in profiles.charger("cond", conn=conn).texte


def test_aucun_profil_a_aucun_niveau_avertit(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path)
    api.add_matiere(conn, "Anglais")
    api.add_cours(conn, "cond", matiere="Anglais")
    p = profiles.charger("cond", conn=conn)
    assert not p.trouve and p.texte == profiles.PROFIL_VIDE
    assert "Aucun profil" in p.avertissement


def test_sans_connexion_le_comportement_fichier_seul_est_inchange():
    assert profiles.charger("physique").trouve
    assert profiles.charger("n-existe-pas-xyz").avertissement


def test_le_prompt_injecte_le_profil_fusionne(conn, monkeypatch, tmp_path):
    """`{profil_cours}` reçoit le profil matière + thème, côté CLI comme côté API."""
    from flashcards import llm

    api.add_matiere(conn, "Anglais", profil=MATIERE)
    api.add_cours(conn, "cond", matiere="Anglais", profil=THEME)

    profil = profiles.charger("cond", conn=conn)
    rendu = profiles.rendre(llm.charger_prompt(llm.PROMPT_EXTRACTION), cours="cond",
                            profil_cours=profil.texte)
    assert "type 1" in rendu and "Niveau: B1" in rendu and "{profil_cours}" not in rendu


def test_cli_prompt_utilise_le_profil_fusionne(tmp_path, monkeypatch):
    from flashcards.db import open_db

    db = tmp_path / "cli.db"
    monkeypatch.setenv("FLASHCARDS_DB", str(db))
    conn = open_db(db)
    api.add_matiere(conn, "Anglais", profil=MATIERE)
    api.add_cours(conn, "cond", matiere="Anglais", profil=THEME)
    conn.close()

    res = runner.invoke(app, ["prompt", "extraction", "--cours", "cond"])
    assert res.exit_code == 0, res.output
    assert "type 1" in res.output and "Niveau: B1" in res.output
