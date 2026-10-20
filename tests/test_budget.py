"""Scénarios « catastrophe »: prouver qu'une erreur ne peut pas vider le crédit.

Tous ces tests tournent en mode simulé et comptent les appels: aucun ne
consomme de crédit, et chacun vérifie qu'un appel qui NE DOIT PAS avoir lieu
n'a effectivement pas lieu.
"""
from __future__ import annotations

import ast
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flashcards import budget, ingest, llm
from flashcards.db import open_db

MAINTENANT = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    connexion = open_db(Path(tempfile.mkdtemp()) / "t.db")
    yield connexion
    connexion.close()


@pytest.fixture
def plafonds():
    return budget.charger_plafonds()


@pytest.fixture(autouse=True)
def _mock(monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.delenv("LLM_DISABLED", raising=False)


def _depense(conn, cout, maintenant=MAINTENANT, statut="succes"):
    budget.enregistrer(
        conn, operation="extraction", modele="claude-sonnet-5", statut=statut,
        tokens_entree=1000, tokens_sortie=1000, cout=cout, maintenant=maintenant,
    )


# --- A3.1: point de passage unique -----------------------------------------

def test_aucun_module_hors_llm_n_importe_le_sdk():
    """Le SDK `anthropic` ne doit être touché que par `llm.py`.

    C'est la garantie structurelle: si un autre module appelle le SDK, il
    contourne les plafonds, le registre et le disjoncteur.
    """
    racine = Path(__file__).resolve().parents[1] / "src" / "flashcards"
    coupables = []

    for fichier in racine.rglob("*.py"):
        if fichier.name == "llm.py":
            continue
        arbre = ast.parse(fichier.read_text(), filename=str(fichier))
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.Import):
                noms = [a.name for a in noeud.names]
            elif isinstance(noeud, ast.ImportFrom):
                noms = [noeud.module or ""]
            else:
                continue
            if any(n == "anthropic" or n.startswith("anthropic.") for n in noms):
                coupables.append(f"{fichier.relative_to(racine)}:{noeud.lineno}")

    assert not coupables, (
        "Ces modules importent le SDK anthropic en dehors de llm.py et "
        f"contournent donc les garde-fous de budget: {coupables}"
    )


# --- A4: schéma invalide à chaque réponse ----------------------------------

def test_schema_invalide_ne_boucle_pas(conn, monkeypatch):
    """Une réponse toujours invalide s'arrête après un nombre borné d'essais."""
    appels = {"n": 0}

    def toujours_casse(*_args, **_kwargs):
        appels["n"] += 1
        raise RuntimeError("schéma invalide")

    monkeypatch.setattr(llm, "_appel_structure", toujours_casse)
    monkeypatch.setattr(llm, "mock_actif", lambda: False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-faux")

    entree = llm.Entree(voie="texte", cours="physique", texte="Notion.")
    with pytest.raises(RuntimeError):
        llm.extraire_concepts(entree, conn=conn)

    assert appels["n"] == 1, (
        f"{appels['n']} appels pour une erreur non transitoire: il ne doit y "
        "en avoir qu'un seul (pas de retry sur une erreur qui ne se corrige pas)."
    )


def test_retries_bornes_sur_erreur_transitoire(conn, monkeypatch):
    """Au plus 2 retries sur une erreur réseau, puis on abandonne."""
    import anthropic

    appels = {"n": 0}

    class FauxClient:
        def __init__(self, *_a, **_k):
            self.messages = self

        def parse(self, **_kwargs):
            appels["n"] += 1
            raise anthropic.APIConnectionError(request=None)

    monkeypatch.setattr(anthropic, "Anthropic", FauxClient)
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-faux")

    with pytest.raises(anthropic.APIConnectionError):
        llm._appel_structure(
            conn=conn, plafonds=budget.charger_plafonds(), operation="extraction",
            system_prompt="x", blocs=[{"type": "text", "text": "y"}], label="t",
            modele="claude-sonnet-5", schema=llm.ConceptListRaw,
            tokens_entree_estimes=10, empreinte=None,
        )

    assert appels["n"] == llm.MAX_RETRIES + 1 == 3


def test_aucun_retry_sur_4xx_ni_erreur_de_credit(monkeypatch, tmp_path):
    """400/401/402: réessayer ne corrige rien et double la dépense."""
    import anthropic

    for statut in (400, 401, 402, 403):
        # Registre neuf par statut: sinon les échecs cumulés ouvriraient le
        # disjoncteur et masqueraient ce qu'on veut mesurer.
        conn = open_db(tmp_path / f"r{statut}.db")
        appels = {"n": 0}

        class FauxClient:
            def __init__(self, *_a, **_k):
                self.messages = self

            def parse(self, **_kwargs):
                appels["n"] += 1
                erreur = anthropic.APIStatusError(
                    "refus", response=_FausseReponse(statut), body=None
                )
                raise erreur

        monkeypatch.setattr(anthropic, "Anthropic", FauxClient)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-faux")

        with pytest.raises(anthropic.APIStatusError):
            llm._appel_structure(
                conn=conn, plafonds=budget.charger_plafonds(), operation="extraction",
                system_prompt="x", blocs=[{"type": "text", "text": "y"}], label="t",
                modele="claude-sonnet-5", schema=llm.ConceptListRaw,
                tokens_entree_estimes=10, empreinte=None,
            )
        assert appels["n"] == 1, f"retry effectué sur {statut}"
        conn.close()


class _FausseReponse:
    """Minimum pour qu'`APIStatusError` expose un `status_code`."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        self.headers = {}
        self.request = None


# --- A4: plafonds ----------------------------------------------------------

def test_plafond_journalier_bloque_tout_appel(conn, plafonds):
    _depense(conn, plafonds.cout_max_jour_usd)
    with pytest.raises(budget.BudgetDepasse, match="journalier"):
        budget.autoriser(
            conn, plafonds, operation="extraction", modele="claude-sonnet-5",
            cout_estime=0.01, maintenant=MAINTENANT,
        )


def test_plafond_mensuel_bloque_tout_appel(conn, plafonds):
    # Dépense d'un jour précédent du même mois: hors plafond jour, dans le mois.
    _depense(conn, plafonds.cout_max_mois_usd, MAINTENANT - timedelta(days=2))
    with pytest.raises(budget.BudgetDepasse, match="mensuel"):
        budget.autoriser(
            conn, plafonds, operation="extraction", modele="claude-sonnet-5",
            cout_estime=0.01, maintenant=MAINTENANT,
        )


def test_plafond_par_operation_bloque_une_demande_trop_grosse(conn, plafonds):
    with pytest.raises(budget.BudgetDepasse, match="par opération"):
        budget.autoriser(
            conn, plafonds, operation="generation", modele="claude-opus-5",
            cout_estime=plafonds.cout_max_operation_usd + 0.01, maintenant=MAINTENANT,
        )


def test_un_refus_est_inscrit_au_registre(conn, plafonds):
    _depense(conn, plafonds.cout_max_jour_usd)
    with pytest.raises(budget.BudgetDepasse):
        budget.autoriser(
            conn, plafonds, operation="extraction", modele="claude-sonnet-5",
            cout_estime=0.01, maintenant=MAINTENANT,
        )
    refus = conn.execute(
        "SELECT detail FROM llm_appels WHERE statut = 'refuse'"
    ).fetchall()
    assert len(refus) == 1 and "journalier" in refus[0]["detail"]


def test_cadence_par_minute(conn, plafonds):
    for _ in range(plafonds.appels_max_par_minute):
        _depense(conn, 0.0001)
    with pytest.raises(budget.BudgetDepasse, match="Cadence"):
        budget.autoriser(
            conn, plafonds, operation="extraction", modele="claude-sonnet-5",
            cout_estime=0.001, maintenant=MAINTENANT,
        )


# --- A4: interrupteur et disjoncteur ---------------------------------------

def test_llm_disabled_coupe_tout(conn, plafonds, monkeypatch):
    monkeypatch.setenv("LLM_DISABLED", "1")
    with pytest.raises(budget.LLMDesactive):
        budget.autoriser(
            conn, plafonds, operation="extraction", modele="claude-sonnet-5",
            cout_estime=0.001, maintenant=MAINTENANT,
        )


def test_disjoncteur_apres_trois_echecs(conn, plafonds):
    for _ in range(plafonds.echecs_avant_disjoncteur):
        _depense(conn, 0.0, statut="echec")
    assert budget.disjoncteur_ouvert(conn, plafonds, MAINTENANT)
    with pytest.raises(budget.DisjoncteurOuvert):
        budget.autoriser(
            conn, plafonds, operation="extraction", modele="claude-sonnet-5",
            cout_estime=0.001, maintenant=MAINTENANT,
        )


def test_disjoncteur_se_referme_apres_la_duree(conn, plafonds):
    vieux = MAINTENANT - timedelta(minutes=plafonds.duree_disjoncteur_minutes + 1)
    for _ in range(plafonds.echecs_avant_disjoncteur):
        _depense(conn, 0.0, vieux, statut="echec")
    assert not budget.disjoncteur_ouvert(conn, plafonds, MAINTENANT)


def test_un_succes_referme_le_disjoncteur(conn, plafonds):
    for _ in range(plafonds.echecs_avant_disjoncteur):
        _depense(conn, 0.0, statut="echec")
    _depense(conn, 0.001)
    assert not budget.disjoncteur_ouvert(conn, plafonds, MAINTENANT)


# --- A4: limites d'entrée --------------------------------------------------

def test_texte_trop_long_rejete_sans_appel(plafonds):
    with pytest.raises(budget.EntreeTropGrande, match="Texte trop long"):
        budget.verifier_entree(
            plafonds, texte="x" * (plafonds.max_caracteres_texte + 1), voie="texte"
        )


def test_trop_d_images_rejete_sans_appel(plafonds, tmp_path):
    images = []
    for i in range(plafonds.max_images + 1):
        chemin = tmp_path / f"i{i}.png"
        chemin.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        images.append(chemin)
    with pytest.raises(budget.EntreeTropGrande, match="Trop d'images"):
        budget.verifier_entree(plafonds, fichiers=tuple(images), voie="image")


def test_fichier_trop_lourd_rejete_sans_appel(plafonds, tmp_path):
    gros = tmp_path / "gros.pdf"
    gros.write_bytes(b"0" * (plafonds.max_fichier_octets + 1))
    with pytest.raises(budget.EntreeTropGrande, match="trop lourd"):
        budget.verifier_entree(plafonds, fichiers=(gros,), voie="pdf")


def test_pdf_illisible_rejete_sans_appel(plafonds, tmp_path):
    faux = tmp_path / "faux.pdf"
    faux.write_bytes(b"ceci n'est pas un pdf")
    with pytest.raises(budget.EntreeTropGrande):
        budget.verifier_entree(plafonds, fichiers=(faux,), voie="pdf")


def test_modele_hors_liste_refuse():
    with pytest.raises(budget.BudgetDepasse, match="absent de la table de tarifs"):
        budget.tarif("claude-modele-inconnu-tres-cher")


# --- A4: anti-doublon ------------------------------------------------------

def test_meme_entree_deux_fois_un_seul_appel(conn, monkeypatch):
    """Réenvoyer le même contenu réutilise le résultat au lieu de repayer."""
    appels = {"n": 0}
    vraie = llm._appel_structure

    def compte(**kwargs):
        appels["n"] += 1
        return llm.ConceptListRaw(concepts=[], avertissements=[])

    monkeypatch.setattr(llm, "_appel_structure", compte)
    monkeypatch.setattr(llm, "mock_actif", lambda: False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-faux")

    entree = llm.Entree(
        voie="texte", cours="physique", texte="Même contenu exactement."
    )
    llm.extraire_concepts(entree, conn=conn)
    llm.extraire_concepts(entree, conn=conn)
    assert appels["n"] == 1, "le second envoi aurait dû venir du cache"

    # `forcer` permet de repayer explicitement.
    llm.extraire_concepts(entree, conn=conn, forcer=True)
    assert appels["n"] == 2


def test_empreinte_ignore_le_nom_du_fichier(tmp_path):
    a = tmp_path / "cours.pdf"
    b = tmp_path / "renomme.pdf"
    a.write_bytes(b"%PDF-1.4 contenu")
    b.write_bytes(b"%PDF-1.4 contenu")
    empreinte = lambda f: budget.empreinte_entree(
        operation="extraction", modele="m", texte=None, fichiers=(f,)
    )
    assert empreinte(a) == empreinte(b)


# --- A4: estimation --------------------------------------------------------

def test_estimation_ne_fait_aucun_appel(monkeypatch, tmp_path):
    import anthropic

    def interdit(*_a, **_k):
        raise AssertionError("une estimation ne doit JAMAIS appeler l'API")

    monkeypatch.setattr(anthropic, "Anthropic", interdit)
    entree = llm.Entree(
        voie="texte", cours="physique", texte="Du texte à estimer."
    )
    estimation = llm.estimer(entree, operation="extraction")
    assert estimation.cout_estime_usd > 0
    assert estimation.max_tokens_sortie == llm.MAX_TOKENS_PAR_OPERATION["extraction"]


def test_estimation_majore_le_cout_reel():
    """L'estimation doit être pessimiste, jamais optimiste."""
    tokens = budget.estimer_tokens_entree(texte="x" * 4000)
    assert tokens >= 1000, "marge de sécurité absente"


# --- A4: max_tokens explicite ----------------------------------------------

def test_max_tokens_defini_pour_chaque_operation():
    assert set(llm.MAX_TOKENS_PAR_OPERATION) == {"extraction", "generation"}
    for valeur in llm.MAX_TOKENS_PAR_OPERATION.values():
        assert 0 < valeur <= 16_000


# --- Registre et visibilité ------------------------------------------------

def test_consommation_separe_jour_et_mois(conn):
    _depense(conn, 0.10, MAINTENANT)
    _depense(conn, 0.20, MAINTENANT - timedelta(days=3))
    conso = budget.consommation(conn, MAINTENANT)
    assert conso["cout_jour_usd"] == pytest.approx(0.10)
    assert conso["cout_mois_usd"] == pytest.approx(0.30)


def test_alerte_a_80_pourcent(conn, plafonds):
    _depense(conn, plafonds.cout_max_jour_usd * 0.85)
    alertes = budget.alerte_80_pourcent(conn, plafonds, MAINTENANT)
    assert any("journalier" in a for a in alertes)


def test_les_refus_ne_comptent_pas_comme_depense(conn, plafonds):
    budget.enregistrer(
        conn, operation="extraction", modele="claude-sonnet-5", statut="refuse",
        cout_estime=10.0, maintenant=MAINTENANT,
    )
    assert budget.consommation(conn, MAINTENANT)["cout_jour_usd"] == 0.0


# --- Mode mock -------------------------------------------------------------

def test_le_mode_mock_ne_depense_rien(conn):
    entree = llm.Entree(
        voie="texte", cours="physique", texte="Notion importante."
    )
    ingest.extraire(entree, conn=conn)
    assert budget.consommation(conn)["appels_jour"] == 0


def test_mock_actif_sans_cle_api(monkeypatch):
    monkeypatch.delenv("MOCK_LLM", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.mock_actif(), "sans clé API, le mode simulé doit s'imposer"
