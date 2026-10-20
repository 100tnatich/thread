"""Profils de cours, rendu des gabarits, confiance/source_ref, type rédaction."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fsrs import Rating

from flashcards import ingest, llm, profiles
from flashcards.db import open_db
from flashcards.grading import AUTO_EVALUES, AUTO_TYPES, rating_depuis_criteres
from flashcards.models import GenerationLLMResponse, Redaction, Source


@pytest.fixture
def conn():
    """Connexion jetable: registre de consommation + cache anti-doublon."""
    connexion = open_db(Path(tempfile.mkdtemp()) / "t.db")
    yield connexion
    connexion.close()


# --- Rendu des gabarits ----------------------------------------------------

def test_rendre_remplace_les_variables():
    rendu = profiles.rendre(
        "Thème: {cours}\nProfil: {profil_cours}",
        cours="physique", profil_cours="notation U",
    )
    assert "physique" in rendu and "notation U" in rendu
    assert "{" not in rendu


def test_rendre_preserve_json_et_cloze():
    """Le gabarit contient du JSON et `{{c1::valeur}}`: `str.format` casserait."""
    gabarit = (
        'Cours: {cours}\n'
        'Sortie: {"concepts": [...], "avertissements": [...]}\n'
        'Syntaxe cloze: {{c1::valeur}}'
    )
    rendu = profiles.rendre(gabarit, cours="maths")
    assert '{"concepts": [...], "avertissements": [...]}' in rendu
    assert "{{c1::valeur}}" in rendu


def test_rendre_refuse_une_variable_inconnue():
    with pytest.raises(RuntimeError, match="inconnues"):
        profiles.rendre("{cours}", cours="x", nimporte_quoi="y")


def test_rendre_refuse_une_variable_non_fournie():
    with pytest.raises(RuntimeError, match="non fournies"):
        profiles.rendre("Cours: {cours}\nProfil: {profil_cours}", cours="x")


def test_les_prompts_livres_se_rendent_sans_variable_restante():
    extraction = profiles.rendre(
        llm.charger_prompt(llm.PROMPT_EXTRACTION),
        cours="physique", profil_cours="…",
    )
    generation = profiles.rendre(
        llm.charger_prompt(llm.PROMPT_GENERATION),
        cours="physique", profil_cours="…", concepts_valides="[]",
    )
    for rendu in (extraction, generation):
        for variable in profiles.VARIABLES_CONNUES:
            assert "{" + variable + "}" not in rendu
    # La syntaxe cloze du prompt de génération doit survivre au rendu.
    assert "{{c1::valeur}}" in generation


# --- Profils ---------------------------------------------------------------

def test_profil_present_est_charge():
    profil = profiles.charger("physique")
    assert profil.trouve
    assert profil.avertissement is None
    assert "U" in profil.texte


def test_profil_absent_avertit_et_continue():
    profil = profiles.charger("cours-qui-n-existe-pas")
    assert not profil.trouve
    assert profil.texte == profiles.PROFIL_VIDE
    assert "Aucun profil" in profil.avertissement


def test_profil_trouve_malgre_accents_et_espaces(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path)
    (tmp_path / "francais-ecrit.md").write_text("Cours: français écrit")
    profil = profiles.charger("Français Écrit")
    assert profil.trouve


def test_profil_vide_avertit(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path)
    (tmp_path / "vide.md").write_text("   \n")
    profil = profiles.charger("vide")
    assert "vide" in profil.avertissement


def test_avertissement_de_profil_remonte_dans_l_extraction(monkeypatch, conn):
    monkeypatch.setenv("MOCK_LLM", "1")
    entree = llm.Entree(
        voie="texte", cours="cours-inexistant", texte="Une notion a retenir."
    )
    resultat = ingest.extraire(entree, conn=conn)
    assert any("Aucun profil" in a for a in resultat.avertissements)


# --- Champs source_ref / confiance -----------------------------------------

def test_les_questions_portent_source_ref_et_confiance(monkeypatch, conn):
    monkeypatch.setenv("MOCK_LLM", "1")
    entree = llm.Entree(
        voie="texte", cours="physique",
        texte="La tension se mesure en volts et le courant en amperes.",
    )
    resultat = ingest.extraire(entree, conn=conn)
    resume = ingest.produire(entree, resultat.concepts, conn=conn)

    assert resume.questions
    for question in resume.questions:
        assert question.source_ref
        assert question.confiance in ("haute", "moyenne", "basse")


def test_les_concepts_portent_les_nouveaux_champs(monkeypatch, conn):
    monkeypatch.setenv("MOCK_LLM", "1")
    entree = llm.Entree(
        voie="texte", cours="physique", texte="Une notion importante ici."
    )
    for concept in ingest.extraire(entree, conn=conn).concepts:
        assert concept.categorie
        assert concept.importance in ("essentiel", "important", "secondaire")
        assert isinstance(concept.adapte_calcul, bool)
        assert concept.resume


def test_question_hors_concepts_valides_est_ecartee(monkeypatch, conn):
    """Garde-fou: le concept_id vient du LLM, il est vérifié et non cru."""
    monkeypatch.setenv("MOCK_LLM", "1")
    entree = llm.Entree(voie="texte", cours="physique", texte="Notion.")
    concepts = ingest.extraire(entree, conn=conn).concepts

    from flashcards.models import ExactGenere

    def faux(_entree, _lot, **_kwargs):
        return GenerationLLMResponse(questions=[
            ExactGenere(
                concept_id="concept-inconnu", source_ref="p1", confiance="haute",
                enonce="?", reponse_exacte="x",
            )
        ]), profiles.charger("physique")

    monkeypatch.setattr(llm, "generer_questions", faux)
    resume = ingest.produire(entree, concepts, conn=conn)

    assert resume.questions == []
    assert any("écartée" in a for a in resume.avertissements)


# --- Type rédaction --------------------------------------------------------

def test_redaction_est_auto_evaluee_pas_corrigee():
    assert "redaction" in AUTO_EVALUES
    assert "redaction" not in AUTO_TYPES


@pytest.mark.parametrize(
    "coches,total,attendu",
    [
        (4, 4, Rating.Good),      # tous cochés
        (3, 4, Rating.Hard),      # la plupart
        (2, 4, Rating.Hard),      # la moitié
        (1, 4, Rating.Again),     # peu
        (0, 4, Rating.Again),     # aucun
        (6, 6, Rating.Good),
        (2, 6, Rating.Again),
    ],
)
def test_bareme_des_criteres(coches, total, attendu):
    assert rating_depuis_criteres(coches, total) is attendu


def test_facile_reste_un_choix_explicite():
    assert rating_depuis_criteres(4, 4, facile=True) is Rating.Easy
    # Facile n'est possible que si tout est coché: sinon le barème prime.
    assert rating_depuis_criteres(3, 4, facile=True) is Rating.Hard


def test_bareme_refuse_des_entrees_incoherentes():
    with pytest.raises(RuntimeError, match="au moins un critère"):
        rating_depuis_criteres(0, 0)
    with pytest.raises(RuntimeError, match="hors bornes"):
        rating_depuis_criteres(5, 4)


def test_redaction_valide_le_schema():
    q = Redaction(
        id="q1", concept_id="c1", cours="français",
        source=Source(fichier="cours.pdf"), source_ref="section 2", confiance="haute",
        consigne="Rédige une introduction.",
        contraintes="150 mots, trois parties.",
        criteres=["Amorce présente", "Problématique en question", "Plan annoncé", "Pas de « je »"],
        exemple_reference="Texte modèle complet.",
    )
    assert q.type == "redaction"
    assert len(q.criteres) == 4


def test_le_mock_genere_des_redactions_pour_les_cours_de_production(monkeypatch, conn):
    monkeypatch.setenv("MOCK_LLM", "1")
    entree = llm.Entree(
        voie="texte", cours="français",
        texte=(
            "La methode de la dissertation demande une introduction avec amorce, "
            "une problematique et un plan annonce."
        ),
    )
    resultat = ingest.extraire(entree, conn=conn)
    resume = ingest.produire(entree, resultat.concepts, conn=conn)

    redactions = [q for q in resume.questions if q.type == "redaction"]
    assert redactions, "un cours de production doit produire des rédactions"
    for redaction in redactions:
        assert 4 <= len(redaction.criteres) <= 6
        assert redaction.exemple_reference
        assert redaction.contraintes


def test_exercices_d_application_sur_les_concepts_essentiels(monkeypatch, conn):
    """Un concept de formule/méthode essentiel reçoit un exercice en plus."""
    monkeypatch.setenv("MOCK_LLM", "1")
    entree = llm.Entree(
        voie="texte", cours="physique",
        texte="La formule relie tension courant resistance dans un circuit ohmique.",
    )
    resultat = ingest.extraire(entree, conn=conn)
    essentiels_calcul = [
        c for c in resultat.concepts
        if c.importance == "essentiel"
        and (c.categorie in ("formule", "methode") or c.adapte_calcul)
    ]
    if not essentiels_calcul:
        pytest.skip("le jeu simulé n'a pas produit de concept essentiel calculatoire")

    resume = ingest.produire(entree, resultat.concepts, conn=conn)
    for concept in essentiels_calcul:
        questions = [q for q in resume.questions if q.concept_id == concept.id]
        assert any(q.type == "probleme" for q in questions)


def test_redaction_persistee_et_relue(monkeypatch, conn):
    """Une rédaction fait l'aller-retour SQLite sans perdre ses critères."""
    monkeypatch.setenv("MOCK_LLM", "1")
    entree = llm.Entree(
        voie="texte", cours="français",
        texte="La methode de la dissertation demande une introduction structuree.",
    )
    concepts = ingest.extraire(entree, conn=conn).concepts
    ingest.generer(conn, entree, concepts)

    from flashcards import api as engine

    cartes = [c for c in engine.load_cards(conn) if c.type == "redaction"]
    assert cartes, "une rédaction doit être persistée"
    question = cartes[0].question()
    assert question.type == "redaction"
    assert question.criteres
