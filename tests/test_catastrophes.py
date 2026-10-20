"""Scénarios « catastrophe », deuxième série: le vrai chemin d'appel.

`test_budget.py` vérifie les briques une à une. Ici on traverse le wrapper
entier avec un faux client du SDK qui COMPTE les appels: chaque test prouve
qu'un appel qui ne doit pas partir ne part pas, et que ce qui a pu être
facturé est bien compté. Rien ne touche le réseau (voir `conftest.py`).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from flashcards import budget, ingest, llm
from flashcards.db import open_db
from flashcards.models import Concept, ConceptListRaw, GenerationLLMResponse


class _Usage:
    input_tokens = 1_000
    output_tokens = 500


class _Reponse:
    usage = _Usage()
    stop_reason = "end_turn"

    def __init__(self, sortie):
        self.parsed_output = sortie


class FauxSDK:
    """Remplace `anthropic.Anthropic`. `comportement(n)` décide de la réponse."""

    def __init__(self, comportement):
        self.appels = 0
        self.options_client: dict = {}
        self.requetes: list[dict] = []
        self._comportement = comportement
        self._verrou = threading.Lock()

    def __call__(self, *_args, **options):
        self.options_client = options
        return self

    @property
    def messages(self):
        return self

    def parse(self, **requete):
        with self._verrou:
            self.appels += 1
            numero = self.appels
        self.requetes.append(requete)
        return self._comportement(numero, requete)


@pytest.fixture
def conn(tmp_path: Path):
    connexion = open_db(tmp_path / "c.db")
    yield connexion
    connexion.close()


@pytest.fixture
def reel(monkeypatch):
    """Active le chemin réel du wrapper, branché sur un faux SDK à installer."""
    monkeypatch.delenv("MOCK_LLM", raising=False)
    monkeypatch.setenv("FLASHCARDS_LLM_REEL", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-faux")
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)

    def installer(comportement) -> FauxSDK:
        import anthropic

        faux = FauxSDK(comportement)
        monkeypatch.setattr(anthropic, "Anthropic", faux)
        return faux

    return installer


def _texte(contenu: str = "La loi d'Ohm relie tension et courant.") -> llm.Entree:
    return llm.Entree(voie="texte", cours="physique", texte=contenu)


def _concepts(n: int) -> list[Concept]:
    return [
        Concept(id=f"c{i}", titre=f"Notion {i}", categorie="definition",
                resume="Résumé.", source="p. 1", importance="important")
        for i in range(n)
    ]


def _vide(_n, requete):
    schema = requete["output_format"]
    if schema is ConceptListRaw:
        return _Reponse(ConceptListRaw(concepts=[], avertissements=[]))
    return _Reponse(GenerationLLMResponse(questions=[], avertissements=[]))


def _depense(conn) -> float:
    return budget.consommation(conn)["cout_jour_usd"]


# --- Activation explicite du mode réel -------------------------------------

def test_une_cle_seule_ne_suffit_pas_a_passer_en_reel(monkeypatch):
    """Clé exportée dans le shell + `pytest` ou `flashcards extract`: simulé."""
    monkeypatch.delenv("MOCK_LLM", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-vraie-cle")
    assert llm.mock_actif()

    monkeypatch.setenv("FLASHCARDS_LLM_REEL", "1")
    assert not llm.mock_actif()

    monkeypatch.setenv("MOCK_LLM", "1")
    assert llm.mock_actif(), "MOCK_LLM=1 doit garder le dernier mot"


def test_le_client_reel_est_borne(conn, reel):
    """Pas de retry caché du SDK, délai explicite, max_tokens explicite."""
    faux = reel(_vide)
    llm.extraire_concepts(_texte(), conn=conn)
    assert faux.options_client["max_retries"] == 0
    assert faux.options_client["timeout"] == llm.TIMEOUT_SECONDES <= 600
    assert faux.requetes[0]["max_tokens"] == llm.MAX_TOKENS_PAR_OPERATION["extraction"]


# --- Schéma invalide à chaque réponse --------------------------------------

def test_reponse_invalide_a_chaque_fois_s_arrete_et_reste_comptee(conn, reel):
    """Le modèle répond, mais jamais rien d'exploitable.

    Aucune nouvelle tentative par lot, le disjoncteur coupe après 3 échecs,
    et chaque réponse reçue (donc facturée) reste au registre.
    """
    def casse(_n, _requete):
        raise ValueError("sortie non conforme au schéma")

    faux = reel(casse)
    resume = ingest.produire(_texte(), _concepts(40), conn=conn)  # 5 lots

    assert faux.appels == 3, f"{faux.appels} appels: la boucle devait s'arrêter à 3"
    assert resume.questions_creees == 0
    assert len(resume.echecs) == 40, "chaque concept doit être marqué échoué"
    assert _depense(conn) > 0, "des réponses reçues valent 0 USD au registre"
    assert budget.disjoncteur_ouvert(conn, budget.charger_plafonds())

    # Relancer tout de suite ne produit aucun appel de plus.
    with pytest.raises(budget.DisjoncteurOuvert):
        ingest.produire(_texte(), _concepts(8), conn=conn, forcer=True)
    assert faux.appels == 3


def test_reponse_tronquee_n_est_pas_rejouee(conn, reel):
    def tronquee(_n, requete):
        reponse = _vide(_n, requete)
        reponse.stop_reason = "max_tokens"
        return reponse

    faux = reel(tronquee)
    with pytest.raises(RuntimeError, match="tronquée"):
        llm.extraire_concepts(_texte(), conn=conn)
    assert faux.appels == 1
    assert _depense(conn) > 0


# --- Retries ---------------------------------------------------------------

def test_delai_depasse_pas_de_retry_et_cout_conserve(conn, reel):
    """Un délai dépassé a pu être facturé: ni rejoué, ni compté zéro."""
    import anthropic

    def delai(_n, _requete):
        raise anthropic.APITimeoutError(request=None)

    faux = reel(delai)
    with pytest.raises(anthropic.APITimeoutError):
        llm.extraire_concepts(_texte(), conn=conn)
    assert faux.appels == 1
    ligne = conn.execute("SELECT statut, cout_usd, cout_estime_usd FROM llm_appels").fetchone()
    assert ligne["statut"] == "echec"
    assert ligne["cout_usd"] == ligne["cout_estime_usd"] > 0


@pytest.mark.parametrize("statut", [400, 401, 402, 403, 404, 413, 422])
def test_erreur_4xx_ou_credit_un_seul_appel_cout_nul(conn, reel, statut):
    import anthropic

    class Rep:
        status_code = statut
        headers: dict = {}
        request = None

    def refus(_n, _requete):
        raise anthropic.APIStatusError("credit balance too low", response=Rep(), body=None)

    faux = reel(refus)
    with pytest.raises(anthropic.APIStatusError):
        llm.extraire_concepts(_texte(), conn=conn)
    assert faux.appels == 1, f"retry sur {statut}"
    assert _depense(conn) == 0.0, "une erreur 4xx n'est pas facturée"


def test_erreur_5xx_au_plus_deux_retries_puis_disjoncteur(conn, reel):
    import anthropic

    class Rep:
        status_code = 529
        headers: dict = {}
        request = None

    def surcharge(_n, _requete):
        raise anthropic.APIStatusError("overloaded", response=Rep(), body=None)

    faux = reel(surcharge)
    with pytest.raises(anthropic.APIStatusError):
        llm.extraire_concepts(_texte(), conn=conn)
    assert faux.appels == llm.MAX_RETRIES + 1 == 3

    with pytest.raises(budget.DisjoncteurOuvert):
        llm.extraire_concepts(_texte("Autre matière, autre empreinte."), conn=conn)
    assert faux.appels == 3


def test_chaque_retry_repasse_par_les_plafonds(conn, reel, monkeypatch):
    """Un retry est une dépense: s'il ne tient plus sous le plafond, il est refusé."""
    import anthropic

    def delai_puis_rien(_n, _requete):
        raise anthropic.APIConnectionError(request=None)

    faux = reel(delai_puis_rien)
    monkeypatch.setenv("FLASHCARDS_APPELS_MAX_MINUTE", "2")
    with pytest.raises(budget.BudgetDepasse, match="Cadence"):
        llm.extraire_concepts(_texte(), conn=conn)
    assert faux.appels == 2


# --- Plafonds --------------------------------------------------------------

def test_plafond_journalier_atteint_zero_appel_message_clair(conn, reel):
    faux = reel(_vide)
    budget.enregistrer(
        conn, operation="extraction", modele="claude-sonnet-5", statut="succes",
        cout=budget.charger_plafonds().cout_max_jour_usd,
    )
    with pytest.raises(budget.BudgetDepasse, match="Plafond journalier atteint"):
        llm.extraire_concepts(_texte(), conn=conn)
    assert faux.appels == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM llm_appels WHERE statut = 'refuse'"
    ).fetchone()[0] == 1, "le refus doit être journalisé"


def test_des_echecs_facturables_finissent_par_atteindre_le_plafond(conn, reel, monkeypatch):
    """Avant: un échec valait 0 USD, donc le plafond ne se déclenchait jamais."""
    import anthropic

    def delai(_n, _requete):
        raise anthropic.APITimeoutError(request=None)

    faux = reel(delai)
    monkeypatch.setenv("FLASHCARDS_ECHECS_DISJONCTEUR", "1000")  # hors sujet ici
    monkeypatch.setenv("FLASHCARDS_APPELS_MAX_MINUTE", "1000")
    monkeypatch.setenv("FLASHCARDS_COUT_MAX_JOUR", "0.10")

    refuse = False
    for i in range(50):
        try:
            llm.extraire_concepts(_texte(f"Matière numéro {i}."), conn=conn)
        except anthropic.APITimeoutError:
            continue
        except budget.BudgetDepasse:
            refuse = True
            break
    assert refuse, "50 appels en échec sans jamais atteindre le plafond journalier"
    assert _depense(conn) <= 0.10
    assert faux.appels < 50


def test_la_reservation_compte_avant_la_reponse(conn):
    """Deux processus ne peuvent pas passer tous deux sous le plafond."""
    plafonds = budget.charger_plafonds()

    def reserver() -> int:
        return budget.reserver(
            conn, plafonds, operation="extraction", modele="claude-sonnet-5",
            cout_estime=0.45,
        )

    premier = reserver()
    assert _depense(conn) == pytest.approx(0.45)
    reserver()  # 0,90 USD réservés: encore sous le plafond du jour (1,00)
    with pytest.raises(budget.BudgetDepasse, match="journalier"):
        reserver()

    budget.solder(conn, premier, statut="succes", cout=0.02, tokens_entree=1, tokens_sortie=1)
    assert _depense(conn) == pytest.approx(0.47)


def test_appel_coupe_en_plein_vol_garde_son_cout(conn):
    plafonds = budget.charger_plafonds()
    budget.reserver(
        conn, plafonds, operation="extraction", modele="claude-sonnet-5", cout_estime=0.30
    )
    assert budget.clore_reservations_orphelines(conn) == 1
    assert _depense(conn) == pytest.approx(0.30)
    assert conn.execute("SELECT statut FROM llm_appels").fetchone()[0] == "echec"


@pytest.mark.parametrize("valeur", ["nan", "inf", "-1", "1e400"])
def test_plafond_absurde_refuse(monkeypatch, valeur):
    """`nan` rendait toute comparaison fausse: plus aucun refus possible."""
    monkeypatch.setenv("FLASHCARDS_COUT_MAX_JOUR", valeur)
    with pytest.raises(RuntimeError, match="invalide"):
        budget.charger_plafonds()


def test_plafond_eleve_demande_une_confirmation(monkeypatch):
    monkeypatch.setenv("FLASHCARDS_COUT_MAX_JOUR", "100")  # 100 au lieu de 1.00
    with pytest.raises(RuntimeError, match="garde-fou"):
        budget.charger_plafonds()
    monkeypatch.setenv("FLASHCARDS_PLAFONDS_ELEVES", "1")
    assert budget.charger_plafonds().cout_max_jour_usd == 100


def test_plafonds_par_defaut():
    plafonds = budget.charger_plafonds()
    assert plafonds.cout_max_jour_usd == 1.00
    assert plafonds.cout_max_mois_usd == 5.00
    assert plafonds.cout_max_operation_usd == 0.50
    assert plafonds.max_fichier_mo == 20
    assert plafonds.max_pages_pdf == 60
    assert plafonds.max_images == 10


# --- Plafond par opération = toute l'action de l'utilisateur ----------------

def test_generation_trop_chere_refusee_avant_le_premier_appel(conn, reel, monkeypatch):
    """5 lots à 0,15 USD = 0,75 USD: refusé en bloc, pas après 3 lots payés."""
    faux = reel(_vide)
    monkeypatch.setattr(llm, "_tokens_entree", lambda *_a, **_k: 35_000)
    with pytest.raises(budget.BudgetDepasse, match="par opération"):
        ingest.produire(_texte(), _concepts(40), conn=conn)
    assert faux.appels == 0


def test_trop_de_concepts_refuse_sans_appel(conn, reel):
    faux = reel(_vide)
    with pytest.raises(budget.EntreeTropGrande, match="Trop de concepts"):
        ingest.produire(
            _texte(), _concepts(llm.MAX_CONCEPTS_PAR_GENERATION + 1), conn=conn
        )
    assert faux.appels == 0


def test_generation_arretee_par_le_budget_garde_les_lots_payes(conn, reel, monkeypatch):
    faux = reel(_vide)
    monkeypatch.setenv("FLASHCARDS_APPELS_MAX_MINUTE", "2")
    resume = ingest.produire(_texte(), _concepts(24), conn=conn)  # 3 lots
    assert faux.appels == 2
    assert any("arrêtée au lot 3/3" in a for a in resume.avertissements)
    dernier_lot = {f"c{i}" for i in range(16, 24)}
    raisons = [e.raison for e in resume.echecs if e.concept_id in dernier_lot]
    assert len(raisons) == 8 and all("Cadence" in r for r in raisons)


# --- Interrupteur d'urgence ------------------------------------------------

def test_arret_d_urgence_en_base_coupe_tout(conn, reel):
    faux = reel(_vide)
    budget.basculer_arret(conn, True)
    with pytest.raises(budget.LLMDesactive):
        llm.extraire_concepts(_texte(), conn=conn)
    assert faux.appels == 0

    budget.basculer_arret(conn, False)
    llm.extraire_concepts(_texte(), conn=conn)
    assert faux.appels == 1


def test_llm_disabled_env_coupe_tout_meme_si_la_base_dit_non(conn, reel, monkeypatch):
    faux = reel(_vide)
    budget.basculer_arret(conn, False)
    monkeypatch.setenv("LLM_DISABLED", "1")
    with pytest.raises(budget.LLMDesactive):
        llm.extraire_concepts(_texte(), conn=conn)
    assert faux.appels == 0


# --- Anti-doublon ----------------------------------------------------------

def test_meme_fichier_envoye_deux_fois_un_seul_appel(conn, reel, tmp_path):
    faux = reel(_vide)
    a = tmp_path / "cours.png"
    b = tmp_path / "copie renommée.png"
    for chemin in (a, b):
        chemin.write_bytes(b"\x89PNG\r\n\x1a\n" + b"contenu identique")

    for chemin in (a, b):
        llm.extraire_concepts(
            llm.Entree(voie="image", cours="physique", fichiers=(chemin,)),
            conn=conn,
        )
    assert faux.appels == 1


def test_deux_demandes_identiques_en_meme_temps_un_seul_appel(tmp_path, reel):
    """Deux onglets, deux threads: le second attend puis lit le cache."""
    def lent(_n, requete):
        time.sleep(0.2)
        return _vide(_n, requete)

    faux = reel(lent)
    base = tmp_path / "concurrent.db"
    open_db(base).close()
    erreurs: list[Exception] = []

    def travail():
        connexion = open_db(base)
        try:
            llm.extraire_concepts(_texte(), conn=connexion)
        except Exception as exc:  # remonté par l'assert ci-dessous
            erreurs.append(exc)
        finally:
            connexion.close()

    fils = [threading.Thread(target=travail) for _ in range(4)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join()
    assert not erreurs
    assert faux.appels == 1


def test_journal_illisible_ne_fait_pas_perdre_une_reponse_payee(conn, reel, monkeypatch, tmp_path):
    faux = reel(_vide)
    bloque = tmp_path / "fichier"
    bloque.write_text("pas un dossier")
    monkeypatch.setattr(llm, "USAGE_LOG", bloque / "usage.jsonl")
    llm.extraire_concepts(_texte(), conn=conn)
    llm.extraire_concepts(_texte(), conn=conn)  # servi par le cache
    assert faux.appels == 1


# --- Estimation ------------------------------------------------------------

def test_estimation_compte_le_prompt_et_majore_le_reel(conn, reel):
    """L'estimation doit couvrir le prompt système, pas seulement la matière."""
    estimation = llm.estimer(_texte(), operation="extraction")
    prompt = llm.charger_prompt(llm.PROMPT_EXTRACTION)
    assert estimation.tokens_entree >= len(prompt) / 4

    lots = llm.estimer(_texte(), operation="generation", concepts=_concepts(24))
    un_lot = llm.estimer(_texte(), operation="generation", concepts=_concepts(8))
    assert lots.cout_estime_usd > 2.5 * un_lot.cout_estime_usd, (
        "une génération en 3 lots doit être chiffrée comme 3 appels"
    )


def test_pdf_estime_plus_cher_que_son_texte():
    assert budget.estimer_tokens_entree(pages_pdf=10) >= 10 * 3_000
    assert budget.estimer_tokens_entree(nb_images=1) >= 4_784


def test_envoi_total_trop_lourd_rejete(tmp_path):
    plafonds = budget.charger_plafonds()
    images = []
    for i in range(3):
        chemin = tmp_path / f"i{i}.png"
        chemin.write_bytes(b"0" * (10 * 1024 * 1024))
        images.append(chemin)
    with pytest.raises(budget.EntreeTropGrande, match="au total"):
        budget.verifier_entree(plafonds, fichiers=tuple(images), voie="image")


def test_date_du_registre_en_utc(conn):
    """Le plafond du jour repart à minuit UTC, pas avant."""
    hier = datetime(2026, 1, 4, 23, 59, tzinfo=timezone.utc)
    budget.enregistrer(
        conn, operation="extraction", modele="claude-sonnet-5", statut="succes",
        cout=0.9, maintenant=hier,
    )
    maintenant = datetime(2026, 1, 5, 0, 1, tzinfo=timezone.utc)
    conso = budget.consommation(conn, maintenant)
    assert conso["cout_jour_usd"] == 0.0
    assert conso["cout_mois_usd"] == pytest.approx(0.9)
