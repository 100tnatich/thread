"""Couche LLM: **unique** point de passage vers les API de modèles.

- Anthropic (extraction des concepts, génération des questions);
- OpenRouter (juge des réponses libres et des rédactions, voir « Juge » plus bas).

Aucun autre module du projet n'a le droit d'importer `anthropic` ni d'appeler
le SDK — un test (`tests/test_budget.py`) échoue si c'est le cas. Le juge passe
par le même budget (plafonds, réservation, disjoncteur, arrêt d'urgence).

Trois garanties:
- **Sortie structurée.** `messages.parse()` avec le schéma Pydantic: le JSON
  ne peut être ni malformé ni hors taxonomie.
- **Budget.** Chaque tentative est estimée puis réservée par
  `budget.reserver()` (plafonds jour/mois/opération, cadence, disjoncteur,
  interrupteur d'urgence) avant de partir, et soldée au coût réel ensuite.
- **Mode simulé par défaut.** La vraie API n'est appelée que si
  `FLASHCARDS_LLM_REEL=1` ET une clé sont présents, et que `MOCK_LLM=1` ne
  l'est pas. Dans tous les autres cas: objets déterministes, sans réseau et
  sans dépense.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Literal, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from . import budget
from .config import PROMPTS_DIR, USAGE_LOG, load_config
from .models import (
    Concept,
    ConceptListRaw,
    GenerationLLMResponse,
    QcmGenere,
    ClozeGenere,
    ExactGenere,
    LibreLLMGenere,
    ProblemeGenere,
    RedactionGenere,
    VerdictLibreLLM,
    VerdictRedactionLLM,
)
from .profiles import Profil, charger as charger_profil, rendre

Voie = Literal["pdf", "image", "texte"]

PROMPT_EXTRACTION = "extraction.md"
PROMPT_GENERATION = "generation.md"

MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

M = TypeVar("M", bound=BaseModel)


def reel_demande() -> bool:
    return os.environ.get("FLASHCARDS_LLM_REEL") == "1"


def mock_actif() -> bool:
    """Simulé par défaut; la vraie API demande une activation explicite.

    Une clé présente dans l'environnement ne suffit PAS: sans
    `FLASHCARDS_LLM_REEL=1`, un `pytest` ou un `flashcards extract` lancé dans
    un shell où la clé est exportée dépenserait du crédit sans qu'on l'ait
    demandé. `MOCK_LLM=1` garde le dernier mot.
    """
    if os.environ.get("MOCK_LLM") == "1":
        return True
    if not reel_demande():
        return True
    return not os.environ.get("ANTHROPIC_API_KEY")


@dataclass(frozen=True)
class Entree:
    """La matière fournie: un PDF, des images, ou du texte collé."""

    voie: Voie
    cours: str
    texte: str | None = None
    fichiers: tuple[Path, ...] = ()

    def nom_source(self) -> str:
        if self.fichiers:
            return self.fichiers[0].name
        return f"texte-{self.cours}"


def charger_prompt(nom: str) -> str:
    return (PROMPTS_DIR / nom).read_text()


# --- Construction du message -----------------------------------------------

def _blocs_document(entree: Entree) -> list[dict]:
    """La matière elle-même: PDF natif, images, ou texte."""
    blocs: list[dict] = []
    for fichier in entree.fichiers:
        data = base64.standard_b64encode(fichier.read_bytes()).decode("ascii")
        if fichier.suffix.lower() == ".pdf":
            blocs.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": data},
            })
        else:
            media = MEDIA_TYPES.get(fichier.suffix.lower())
            if media is None:
                raise RuntimeError(f"Format d'image non supporté: {fichier.suffix}")
            blocs.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media, "data": data},
            })

    if entree.voie == "texte":
        blocs.append({"type": "text", "text": f"Matière:\n\n{entree.texte}"})
    return blocs


# `max_tokens` explicite par opération, jamais la valeur globale: une
# extraction renvoie une liste de concepts courte, une génération des
# questions plus longues. Majorer inutilement augmente le coût estimé donc
# la probabilité d'un refus légitime.
MAX_TOKENS_PAR_OPERATION = {
    "extraction": 4_000,
    "generation": 8_000,
}

# Retries: au plus 2, uniquement sur erreurs transitoires. Jamais sur 4xx,
# authentification ou crédit — réessayer n'y change rien et coûte deux fois.
MAX_RETRIES = 2
BACKOFF_SECONDES = (1.0, 4.0)
STATUTS_TRANSITOIRES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})

# Délai explicite: le SDK attend 10 minutes par défaut, pendant lesquelles
# l'unique créneau de tâche reste occupé.
TIMEOUT_SECONDES = 300.0

# Un seul appel réel à la fois dans le processus. Deux demandes identiques
# simultanées: la seconde attend, puis trouve le résultat de la première
# dans le cache au lieu de repayer.
_VERROU_APPEL = threading.Lock()


def _est_transitoire(exc: Exception) -> bool:
    """Un échec mérite-t-il une nouvelle tentative ?

    Par défaut: non. Seules les erreurs de connexion et les statuts
    explicitement transitoires sont rejoués. Un délai dépassé ne l'est PAS:
    la requête est partie, le modèle a peut-être tout généré, et la rejouer
    paierait une seconde fois.
    """
    import anthropic

    if isinstance(exc, anthropic.APITimeoutError):
        return False
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    statut = getattr(exc, "status_code", None)
    return statut in STATUTS_TRANSITOIRES


def _non_facture(exc: Exception) -> bool:
    """Peut-on être certain que cet échec n'a rien coûté ?

    Oui si l'API a répondu par un statut d'erreur (4xx/5xx: rien n'est
    facturé) ou si la connexion n'a jamais abouti. Dans le doute (délai
    dépassé, réponse reçue mais inexploitable) le coût réservé est conservé:
    mieux vaut un plafond atteint trop tôt qu'une dépense invisible.
    """
    import anthropic

    if isinstance(exc, anthropic.APITimeoutError):
        return False
    return isinstance(exc, (anthropic.APIStatusError, anthropic.APIConnectionError))


def _log_fichier(label: str, model: str, usage) -> None:
    """Journal lisible en plus du registre SQLite (pratique en CLI).

    Au mieux: le registre SQLite fait foi. Un dossier de logs non
    inscriptible ne doit jamais faire perdre une réponse déjà payée.
    """
    try:
        cout = budget.cout_usd(model, usage.input_tokens, usage.output_tokens)
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(USAGE_LOG, "a") as f:
            f.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "label": label,
                "model": model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cost_estimate_usd": round(cout, 6),
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _tokens_factures(usage) -> int:
    """Tokens d'entrée, écritures et lectures de cache comprises (majorant)."""
    return (
        usage.input_tokens
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
    )


def _appel_structure(
    *,
    conn: sqlite3.Connection,
    plafonds: budget.Plafonds,
    operation: str,
    system_prompt: str,
    blocs: list[dict],
    label: str,
    modele: str,
    schema: type[M],
    tokens_entree_estimes: int,
    empreinte: str | None,
) -> M:
    """Appel réel, sous budget, avec sortie structurée garantie."""
    import anthropic

    max_tokens = MAX_TOKENS_PAR_OPERATION[operation]
    cout_estime = budget.estimer_cout(modele, tokens_entree_estimes, max_tokens)

    client = None
    for tentative in range(MAX_RETRIES + 1):
        # Refuse AVANT toute dépense (BudgetDepasse / LLMDesactive /
        # DisjoncteurOuvert, inscrits au registre), sinon réserve le coût
        # estimé. Refait à CHAQUE tentative: un retry est une dépense comme
        # une autre.
        appel_id = budget.reserver(
            conn, plafonds, operation=operation, modele=modele,
            cout_estime=cout_estime, empreinte=empreinte,
        )
        debut = time.monotonic()
        try:
            if client is None:
                # Ouvert seulement une fois l'appel autorisé. max_retries=0:
                # on pilote nous-mêmes la politique de retry, le SDK ne doit
                # pas rejouer dans notre dos.
                client = anthropic.Anthropic(
                    api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0,
                    timeout=TIMEOUT_SECONDES,
                )
            reponse = client.messages.parse(
                model=modele,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": blocs}],
                output_format=schema,
            )
        except Exception as exc:
            budget.solder(
                conn, appel_id, statut="echec",
                cout=0.0 if _non_facture(exc) else cout_estime,
                detail=f"{type(exc).__name__}: {exc}"[:500],
                duree_ms=int((time.monotonic() - debut) * 1000),
            )
            if not _est_transitoire(exc) or tentative == MAX_RETRIES:
                raise
            time.sleep(BACKOFF_SECONDES[min(tentative, len(BACKOFF_SECONDES) - 1)])
            continue

        tokens_entree = _tokens_factures(reponse.usage)
        refus = reponse.stop_reason == "refusal"
        budget.solder(
            conn, appel_id, statut="succes",
            cout=budget.cout_usd(modele, tokens_entree, reponse.usage.output_tokens),
            tokens_entree=tokens_entree,
            tokens_sortie=reponse.usage.output_tokens,
            detail="refus du modèle" if refus else None,
            duree_ms=int((time.monotonic() - debut) * 1000),
        )
        _log_fichier(label, modele, reponse.usage)

        if refus:
            raise RuntimeError(
                "Le modèle a refusé de traiter cette matière. Vérifie le contenu envoyé."
            )
        if reponse.stop_reason == "max_tokens":
            raise RuntimeError(
                "Réponse tronquée (max_tokens atteint). Réduis la taille du "
                "document ou le nombre de concepts validés."
            )
        if reponse.parsed_output is None:
            raise RuntimeError("Le modèle n'a renvoyé aucune sortie structurée.")
        return reponse.parsed_output

    raise RuntimeError("Appel LLM impossible.")


def _executer(
    *,
    conn: sqlite3.Connection,
    plafonds: budget.Plafonds,
    operation: str,
    system_prompt: str,
    blocs: list[dict],
    label: str,
    modele: str,
    schema: type[M],
    tokens_entree_estimes: int,
    empreinte: str,
    forcer: bool,
) -> M:
    """Cache anti-doublon + appel, sous verrou.

    Le cache est relu APRÈS la prise du verrou: une demande identique
    arrivée pendant l'appel d'une autre repart avec son résultat.
    """
    with _VERROU_APPEL:
        if not forcer:
            cache = budget.lire_cache(conn, empreinte)
            if cache is not None:
                return schema.model_validate(cache)
        resultat = _appel_structure(
            conn=conn, plafonds=plafonds, operation=operation,
            system_prompt=system_prompt, blocs=blocs, label=label,
            modele=modele, schema=schema,
            tokens_entree_estimes=tokens_entree_estimes, empreinte=empreinte,
        )
        budget.ecrire_cache(
            conn, empreinte, operation, modele, resultat.model_dump(by_alias=True)
        )
        return resultat


# --- Mock déterministe -----------------------------------------------------

def _mots_cles(entree: Entree) -> list[str]:
    source = entree.texte or entree.cours
    mots = re.findall(r"\b[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\-']{4,}\b", source)
    vus: list[str] = []
    for mot in mots:
        bas = mot.lower()
        if bas not in vus:
            vus.append(bas)
    return vus[:6] or [entree.cours.lower().replace(" ", "-")]


def _slug(valeur: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", valeur.lower()).strip("-") or "concept"


_CATEGORIES = ["definition", "formule", "methode", "vocabulaire", "propriete"]
_IMPORTANCES = ["essentiel", "important", "secondaire"]


def _mock_extraction(entree: Entree) -> ConceptListRaw:
    if entree.voie == "texte" and not (entree.texte or "").strip():
        return ConceptListRaw(
            concepts=[],
            avertissements=["Le texte fourni est vide: rien à extraire."],
        )

    mots = _mots_cles(entree)
    concepts = []
    for i, mot in enumerate(mots[:5]):
        concepts.append(Concept(
            id=f"c{i + 1}",
            titre=mot.capitalize(),
            categorie=_CATEGORIES[i % len(_CATEGORIES)],
            resume=(
                f"[MOCK] Concept « {mot} » du thème {entree.cours}. "
                f"Généré sans appel LLM."
            ),
            source=f"page {i + 1}" if entree.voie != "texte" else "texte fourni",
            # Formules et méthodes sont les concepts qu'on travaille par
            # exercices: les marquer "essentiel" fait passer le mode simulé
            # par le chemin «exercices d'application» du prompt.
            importance=(
                "essentiel"
                if _CATEGORIES[i % len(_CATEGORIES)] in ("formule", "methode")
                else _IMPORTANCES[(i % 2) + 1]
            ),
            adapte_calcul=_CATEGORIES[i % len(_CATEGORIES)] == "formule",
        ))

    avertissements = []
    if entree.voie == "image":
        avertissements.append("[MOCK] Mode simulé: aucune image n'a réellement été lue.")
    return ConceptListRaw(concepts=concepts, avertissements=avertissements)


# Cours où la production écrite est le mode d'évaluation naturel: le mock y
# génère des `redaction`, comme le ferait le prompt réel.
COURS_PRODUCTION = ("français", "francais", "allemand", "communication", "redaction")


def _mock_redaction(concept: Concept, cours: str) -> RedactionGenere:
    return RedactionGenere(
        concept_id=concept.id,
        source_ref=concept.source or "source non précisée",
        confiance="moyenne",
        consigne=(
            f"[MOCK] En appliquant « {concept.titre} », rédige un texte court "
            f"destiné à un camarade de ta classe."
        ),
        contraintes="150 mots environ, trois paragraphes, registre courant.",
        criteres=[
            "Le texte applique la méthode du concept",
            "L'introduction annonce le propos",
            "Chaque paragraphe développe une seule idée",
            "La conclusion répond à la consigne",
        ],
        exemple_reference=(
            f"[MOCK] Texte modèle appliquant « {concept.titre} », "
            f"en trois paragraphes, de la longueur demandée."
        ),
    )


def _mock_question(concept: Concept, cours: str = "") -> object:
    """Une question conforme au schéma, typée selon la catégorie du concept."""
    graine = int.from_bytes(hashlib.md5(concept.id.encode()).digest()[:2], "big")
    commun = {
        "concept_id": concept.id,
        "source_ref": concept.source or "source non précisée",
        # Un `probleme` mocké contient un calcul bidon: on le marque "basse"
        # pour que le garde-fou de confiance soit visible dès le mode simulé.
        "confiance": "basse" if concept.adapte_calcul else "haute",
    }
    titre = concept.titre

    production = any(c in cours.lower() for c in COURS_PRODUCTION)
    if production and concept.categorie == "methode":
        return _mock_redaction(concept, cours)

    if concept.adapte_calcul:
        return ProblemeGenere(
            **commun,
            enonce=f"[MOCK] Applique « {titre} » à un cas simple et détaille ta démarche.",
            solution_etapes=[
                f"Rappeler la définition de {titre}",
                "Appliquer au cas donné",
                "Conclure avec l'unité",
            ],
            reponse_finale=f"Application correcte de {titre}",
            necessite_dessin=False,
            schema_svg=None,
        )

    match graine % 4:
        case 0:
            return ExactGenere(
                **commun,
                enonce=f"[MOCK] Donne le terme exact pour: {concept.resume[:60]}",
                reponse_exacte=titre,
            )
        case 1:
            return QcmGenere(
                **commun,
                enonce=f"[MOCK] Laquelle de ces affirmations décrit « {titre} » ?",
                propositions=[
                    f"{titre} est le concept décrit dans ce thème",
                    f"{titre} est une unité de mesure sans rapport",
                    f"{titre} désigne une étape de calcul intermédiaire",
                    f"{titre} est un synonyme de l'exemple précédent",
                ],
                bonnes_reponses=[0],
            )
        case 2:
            return ClozeGenere(
                **commun,
                texte=f"[MOCK] Le concept clé de ce passage est {{{{c1::{titre}}}}}.",
                valeurs_attendues={"c1": titre},
            )
        case _:
            return LibreLLMGenere(
                **commun,
                enonce=f"[MOCK] Explique avec tes mots: {titre}.",
                reponse_attendue=concept.resume,
            )


def _mock_generation(concepts: list[Concept], cours: str = "") -> GenerationLLMResponse:
    questions = [_mock_question(c, cours) for c in concepts]
    # Exercices d'application: un concept de méthode/formule essentiel reçoit
    # en plus un exercice qui force à APPLIQUER, comme le demande le prompt.
    production = any(c in cours.lower() for c in COURS_PRODUCTION)
    for concept in concepts:
        if concept.importance != "essentiel":
            continue
        if concept.categorie in ("formule", "methode") or concept.adapte_calcul:
            if production:
                questions.append(_mock_redaction(concept, cours))
            else:
                questions.append(ProblemeGenere(
                    concept_id=concept.id,
                    source_ref=concept.source or "source non précisée",
                    confiance="basse",
                    enonce=f"[MOCK] Exercice d'application de « {concept.titre} ».",
                    solution_etapes=["Écrire la formule", "Remplacer", "Calculer avec l'unité"],
                    reponse_finale="[MOCK] résultat avec son unité",
                    necessite_dessin=False,
                    schema_svg=None,
                ))
    # Les concepts "essentiel" méritent 2 à 3 questions au total.
    for concept in concepts:
        if concept.importance == "essentiel":
            questions.append(LibreLLMGenere(
                concept_id=concept.id,
                source_ref=concept.source or "source non précisée",
                confiance="moyenne",
                enonce=f"[MOCK] Dans quel cas applique-t-on « {concept.titre} » ?",
                reponse_attendue=concept.resume,
            ))
    return GenerationLLMResponse(
        questions=questions,
        avertissements=["[MOCK] Mode simulé: questions générées sans appel LLM."],
    )


# --- Interface publique ----------------------------------------------------

def _plafonds() -> budget.Plafonds:
    return budget.charger_plafonds(load_config().budget_brut)


def _modele_valide(modele: str | None) -> str:
    """Le modèle vient d'un réglage: il est validé contre une liste fermée.

    Sans cela, un réglage (ou une injection dans la base) pourrait pointer
    vers un modèle bien plus cher que prévu.
    """
    choisi = modele or load_config().model_name
    if choisi not in budget.MODELES_AUTORISES:
        raise budget.BudgetDepasse(
            f"Modèle « {choisi} » non autorisé. Modèles permis: "
            f"{', '.join(sorted(budget.MODELES_AUTORISES))}."
        )
    return choisi


def _tokens_entree(entree: Entree, extra_texte: str = "") -> int:
    pages = 0
    images = 0
    for fichier in entree.fichiers:
        if fichier.suffix.lower() == ".pdf":
            pages += budget.compter_pages_pdf(fichier)
        else:
            images += 1
    return budget.estimer_tokens_entree(
        texte=(entree.texte or "") + extra_texte, pages_pdf=pages, nb_images=images
    )


@dataclass(frozen=True)
class Estimation:
    operation: str
    modele: str
    tokens_entree: int
    max_tokens_sortie: int
    cout_estime_usd: float


def estimer(
    entree: Entree, *, operation: str = "extraction", modele: str | None = None,
    concepts: list[Concept] | None = None,
) -> Estimation:
    """Coût estimé SANS appeler l'API (`--dry-run` / bouton d'estimation)."""
    choisi = _modele_valide(modele)
    plafonds = _plafonds()
    budget.verifier_entree(
        plafonds, fichiers=entree.fichiers, texte=entree.texte, voie=entree.voie
    )

    profil = charger_profil(entree.cours)
    max_sortie = MAX_TOKENS_PAR_OPERATION[operation]
    if operation == "generation":
        # Une génération = un appel par lot de concepts, chacun rejoignant
        # la matière: on additionne tous les lots.
        lots = lots_de(concepts or [])
        tokens = sum(
            _tokens_entree(entree, _prompt_generation(entree, lot, profil))
            for lot in lots
        )
        sortie = max_sortie * len(lots)
    else:
        tokens = _tokens_entree(entree, _prompt_extraction(entree, profil))
        sortie = max_sortie
    return Estimation(
        operation=operation,
        modele=choisi,
        tokens_entree=tokens,
        max_tokens_sortie=sortie,
        cout_estime_usd=round(budget.estimer_cout(choisi, tokens, sortie), 6),
    )


# Les concepts sont envoyés par lots: un thème de 30 concepts demanderait
# sinon une seule réponse énorme, avec un risque de troncature à max_tokens.
TAILLE_LOT = 8
# Borne dure sur une génération: 40 concepts = 5 appels au plus.
MAX_CONCEPTS_PAR_GENERATION = 40


def lots_de(concepts: list[Concept]) -> list[list[Concept]]:
    return [concepts[i : i + TAILLE_LOT] for i in range(0, len(concepts), TAILLE_LOT)]


def _prompt_extraction(entree: Entree, profil: Profil) -> str:
    return rendre(
        charger_prompt(PROMPT_EXTRACTION),
        cours=entree.cours,
        profil_cours=profil.texte,
    )


def _prompt_generation(entree: Entree, concepts: list[Concept], profil: Profil) -> str:
    return rendre(
        charger_prompt(PROMPT_GENERATION),
        cours=entree.cours,
        profil_cours=profil.texte,
        concepts_valides=json.dumps(
            [c.model_dump(by_alias=True) for c in concepts], ensure_ascii=False, indent=2
        ),
    )


def verifier_budget_generation(
    entree: Entree,
    concepts: list[Concept],
    *,
    conn: sqlite3.Connection,
    modele: str | None = None,
    profil: Profil | None = None,
    forcer: bool = False,
) -> None:
    """Refuse une génération ENTIÈRE avant son premier appel.

    Le plafond par opération porte sur ce que l'utilisateur a déclenché
    (tous les lots), pas sur chaque appel pris isolément: sinon une
    génération en 5 lots pourrait coûter 5 fois le plafond. Les lots déjà
    en cache ne comptent pas.
    """
    if len(concepts) > MAX_CONCEPTS_PAR_GENERATION:
        raise budget.EntreeTropGrande(
            f"Trop de concepts pour une seule génération: {len(concepts)}, "
            f"maximum {MAX_CONCEPTS_PAR_GENERATION}. Génère en plusieurs fois."
        )
    if mock_actif():
        return

    choisi = _modele_valide(modele)
    profil = profil or charger_profil(entree.cours, conn=conn)
    plafonds = _plafonds()
    max_sortie = MAX_TOKENS_PAR_OPERATION["generation"]
    total = 0.0
    for lot in lots_de(concepts):
        system_prompt = _prompt_generation(entree, lot, profil)
        if not forcer:
            empreinte = budget.empreinte_entree(
                operation="generation", modele=choisi, texte=entree.texte,
                fichiers=entree.fichiers, extra=system_prompt,
            )
            if budget.cache_existe(conn, empreinte):
                continue
        total += budget.estimer_cout(
            choisi, _tokens_entree(entree, system_prompt), max_sortie
        )
    if total > 0:
        # Mêmes contrôles que pour un appel (interrupteur, plafonds jour et
        # mois, plafond par opération), appliqués au total. Rien n'est réservé
        # ici: chaque lot réserve sa part au moment de partir.
        budget.autoriser(
            conn, plafonds, operation="generation", modele=choisi, cout_estime=total
        )


def extraire_concepts(
    entree: Entree,
    *,
    conn: sqlite3.Connection,
    modele: str | None = None,
    profil: Profil | None = None,
    forcer: bool = False,
) -> tuple[ConceptListRaw, Profil]:
    """Étape A. Renvoie les concepts extraits et le profil de cours utilisé."""
    profil = profil or charger_profil(entree.cours, conn=conn)
    plafonds = _plafonds()
    budget.verifier_entree(
        plafonds, fichiers=entree.fichiers, texte=entree.texte, voie=entree.voie
    )

    if mock_actif():
        return _mock_extraction(entree), profil

    choisi = _modele_valide(modele)
    system_prompt = _prompt_extraction(entree, profil)
    empreinte = budget.empreinte_entree(
        operation="extraction", modele=choisi, texte=entree.texte,
        fichiers=entree.fichiers, extra=system_prompt,
    )
    resultat = _executer(
        conn=conn, plafonds=plafonds, operation="extraction",
        system_prompt=system_prompt, blocs=_blocs_document(entree),
        label=f"extraction:{entree.voie}:{entree.nom_source()}",
        modele=choisi, schema=ConceptListRaw,
        tokens_entree_estimes=_tokens_entree(entree, system_prompt),
        empreinte=empreinte, forcer=forcer,
    )
    return resultat, profil


def generer_questions(
    entree: Entree,
    concepts: list[Concept],
    *,
    conn: sqlite3.Connection,
    modele: str | None = None,
    profil: Profil | None = None,
    forcer: bool = False,
) -> tuple[GenerationLLMResponse, Profil]:
    """Étape B. Génère les questions pour un lot de concepts VALIDÉS.

    Le prompt reçoit la matière source en plus des concepts, pour pouvoir
    reprendre les valeurs et la notation exactes du document.
    """
    profil = profil or charger_profil(entree.cours, conn=conn)
    plafonds = _plafonds()
    budget.verifier_entree(
        plafonds, fichiers=entree.fichiers, texte=entree.texte, voie=entree.voie
    )

    if mock_actif():
        return _mock_generation(concepts, entree.cours), profil

    choisi = _modele_valide(modele)
    system_prompt = _prompt_generation(entree, concepts, profil)
    empreinte = budget.empreinte_entree(
        operation="generation", modele=choisi, texte=entree.texte,
        fichiers=entree.fichiers, extra=system_prompt,
    )
    resultat = _executer(
        conn=conn, plafonds=plafonds, operation="generation",
        system_prompt=system_prompt,
        blocs=_blocs_document(entree) or [{"type": "text", "text": "(matière non jointe)"}],
        label=f"generation:{entree.cours}:{len(concepts)}concepts",
        modele=choisi, schema=GenerationLLMResponse,
        tokens_entree_estimes=_tokens_entree(entree, system_prompt),
        empreinte=empreinte, forcer=forcer,
    )
    return resultat, profil


# === Juge (OpenRouter) ======================================================
#
# Corrige les types à réponse libre: `libre_llm` (une réponse en ses propres
# mots) et `redaction` (un texte jugé critère par critère). Même règles que le
# reste du module:
#   - rien ne part sans `budget.reserver` (plafonds, cadence, disjoncteur,
#     interrupteur d'urgence), et le coût réservé est soldé au coût réel
#     (`usage.cost` renvoyé par OpenRouter, sinon tarif × tokens);
#   - retries bornés, jamais sur 400/401/402/403/404, ni sur un délai dépassé;
#   - sortie structurée (`response_format: json_schema`, strict), puis validée:
#     c'est une donnée non fiable;
#   - la réponse de l'élève est une donnée, jamais une consigne (balisage +
#     prompts/juge_*.md).
#
# Trois états, voir `etat_juge()`: "reel" (OpenRouter), "simule" (MOCK_LLM=1,
# verdicts déterministes, aucun réseau) et "indisponible" (pas activé: l'interface
# retombe sur l'auto-évaluation).
# Doc: https://openrouter.ai/docs/api/reference/overview
#      https://openrouter.ai/docs/guides/features/structured-outputs

URL_OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
PROMPT_JUGE_LIBRE = "juge_libre.md"
PROMPT_JUGE_REDACTION = "juge_redaction.md"
JUGE_MODELE_DEFAUT = "anthropic/claude-haiku-5.5"

# Sortie bornée par opération. Le raisonnement du modèle est coupé (voir le
# corps de requête): sinon il s'ajouterait à ces tokens et pourrait tout manger.
MAX_TOKENS_JUGE = {"jugement_libre": 600, "jugement_redaction": 900}

# Limites d'entrée du juge, appliquées avant tout appel.
MAX_CARACTERES_REPONSE_LIBRE = 3_000
MAX_CARACTERES_TEXTE_REDACTION = 12_000

# Un juge est interactif (l'élève attend): délai et retries courts.
TIMEOUT_JUGE_SECONDES = 60.0
MAX_RETRIES_JUGE = 1
BACKOFF_JUGE_SECONDES = 1.0
RETRY_AFTER_MAX_SECONDES = 3.0
STATUTS_TRANSITOIRES_JUGE = frozenset({408, 429, 502, 503})

# Transport HTTP injectable: les tests y posent un `httpx.MockTransport`, ce qui
# exerce tout le vrai chemin (corps, en-têtes, erreurs) sans jamais ouvrir de
# connexion. `None` = vrai réseau.
_TRANSPORT: httpx.BaseTransport | None = None

EtatJuge = Literal["reel", "simule", "indisponible"]


class JugeIndisponible(RuntimeError):
    """Le juge n'est pas activé (pas de clé, ou FLASHCARDS_JUGE_REEL absent)."""


class ErreurOpenRouter(RuntimeError):
    """Échec d'un appel au juge.

    `facture=False`: l'API a répondu par une erreur HTTP, rien n'a été facturé.
    `transitoire`: rejouer a une chance de réussir.
    """

    def __init__(
        self, message: str, *, transitoire: bool = False, facture: bool = True,
        attente: float | None = None,
    ):
        super().__init__(message)
        self.transitoire = transitoire
        self.facture = facture
        self.attente = attente


def _cle_openrouter() -> str | None:
    """Clé OpenRouter, quelle que soit la casse du nom (`OpenRouter_API_KEY` dans .env)."""
    for nom, valeur in os.environ.items():
        if nom.upper() == "OPENROUTER_API_KEY" and valeur.strip():
            return valeur.strip()
    return None


def etat_juge() -> EtatJuge:
    """`MOCK_LLM=1` garde le dernier mot (tests, démo): verdicts simulés.

    Sinon le vrai juge demande une activation explicite (`FLASHCARDS_JUGE_REEL=1`)
    ET une clé: une clé seule, dans un shell ou un .env, ne dépense rien.
    """
    if os.environ.get("MOCK_LLM") == "1":
        return "simule"
    if os.environ.get("FLASHCARDS_JUGE_REEL") == "1" and _cle_openrouter():
        return "reel"
    return "indisponible"


def juge_modele() -> str:
    """Modèle du juge: liste fermée (budget.TARIFS_JUGE), réglable par l'environnement."""
    choisi = os.environ.get("FLASHCARDS_JUGE_MODELE", "").strip() or JUGE_MODELE_DEFAUT
    if choisi not in budget.MODELES_JUGE_AUTORISES:
        raise budget.BudgetDepasse(
            f"Modèle de juge « {choisi} » non autorisé. Modèles permis: "
            f"{', '.join(sorted(budget.MODELES_JUGE_AUTORISES))}."
        )
    return choisi


def _poster_openrouter(corps: dict, cle: str, timeout: float) -> httpx.Response:
    """LE point où part la requête réseau (et le seul)."""
    with httpx.Client(transport=_TRANSPORT, timeout=timeout) as client:
        return client.post(
            URL_OPENROUTER,
            json=corps,
            headers={
                "Authorization": f"Bearer {cle}",
                # Attribution facultative (https://openrouter.ai/docs/app-attribution).
                "X-Title": "Flashcards",
            },
        )


def _message_erreur_http(statut: int, corps: object) -> str:
    detail = ""
    if isinstance(corps, dict) and isinstance(corps.get("error"), dict):
        detail = str(corps["error"].get("message") or "")[:300]
    base = {
        401: "Clé OpenRouter refusée (401): vérifie OPENROUTER_API_KEY.",
        402: "Crédit OpenRouter insuffisant (402): recharge ton compte OpenRouter.",
        403: "Requête refusée par OpenRouter (403)",
        404: "Aucun fournisseur disponible pour ce modèle (404)",
        408: "OpenRouter n'a pas répondu à temps (408).",
        429: "OpenRouter limite le débit (429): réessaie dans un instant.",
        502: "Le modèle du juge est en panne (502).",
        503: "Aucun fournisseur disponible pour le juge (503).",
    }.get(statut, f"Erreur OpenRouter ({statut})")
    # Le message du serveur complète, il ne remplace pas: jamais la clé là-dedans.
    return f"{base}: {detail}" if detail and statut not in (401, 402) else base


def _lire_reponse_openrouter(reponse: httpx.Response) -> dict:
    """La réponse JSON d'un appel réussi, ou `ErreurOpenRouter`."""
    try:
        corps = reponse.json()
    except ValueError:
        corps = None

    if reponse.status_code != 200:
        attente = None
        brut = reponse.headers.get("Retry-After")
        if brut:
            try:
                attente = min(float(brut), RETRY_AFTER_MAX_SECONDES)
            except ValueError:
                attente = None
        raise ErreurOpenRouter(
            _message_erreur_http(reponse.status_code, corps),
            transitoire=reponse.status_code in STATUTS_TRANSITOIRES_JUGE,
            facture=False,  # statut d'erreur: rien n'est facturé
            attente=attente,
        )

    if not isinstance(corps, dict):
        raise ErreurOpenRouter("Réponse OpenRouter illisible (pas du JSON).")
    # Statut 200 mais erreur dans le corps: survenue pendant la génération.
    if "error" in corps and not corps.get("choices"):
        raise ErreurOpenRouter(_message_erreur_http(
            int(corps["error"].get("code") or 0) if isinstance(corps["error"], dict) else 0,
            corps,
        ))
    return corps


def _cout_reel(donnees: dict, modele: str) -> tuple[float, int, int]:
    """(coût USD, tokens d'entrée, tokens de sortie) d'une réponse reçue.

    `usage.cost` (crédits OpenRouter = USD) fait foi; à défaut, tarif × tokens.
    """
    usage = donnees.get("usage") or {}
    entree = int(usage.get("prompt_tokens") or 0)
    sortie = int(usage.get("completion_tokens") or 0)
    cout = usage.get("cost")
    if isinstance(cout, (int, float)) and not isinstance(cout, bool) and cout >= 0:
        return float(cout), entree, sortie
    return budget.cout_usd(modele, entree, sortie), entree, sortie


_CLOTURE_MARKDOWN = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _extraire_sortie(
    donnees: dict, schema: type[M], valider: Callable[[M], None] | None
) -> M:
    choix = (donnees.get("choices") or [None])[0]
    if not isinstance(choix, dict):
        raise ErreurOpenRouter("Réponse du juge vide.")
    if choix.get("error"):
        raise ErreurOpenRouter("Le fournisseur du juge a échoué en cours de réponse.")
    fin = choix.get("finish_reason")
    if fin == "length":
        raise ErreurOpenRouter("Réponse du juge tronquée (max_tokens atteint).")
    if fin == "content_filter":
        raise ErreurOpenRouter("Le juge a refusé de traiter ce texte (filtre de contenu).")
    message = choix.get("message") or {}
    if message.get("refusal"):
        raise ErreurOpenRouter("Le juge a refusé de traiter ce texte.")
    contenu = message.get("content")
    if not isinstance(contenu, str) or not contenu.strip():
        raise ErreurOpenRouter("Le juge n'a renvoyé aucune sortie.")
    contenu = contenu.strip()
    cloture = _CLOTURE_MARKDOWN.match(contenu)
    if cloture:
        contenu = cloture.group(1)
    try:
        sortie = schema.model_validate_json(contenu)
    except ValidationError as exc:
        raise ErreurOpenRouter("Réponse du juge hors schéma.") from exc
    if valider is not None:
        valider(sortie)
    return sortie


# Modèles dont OpenRouter ne liste pas `temperature` dans `supported_parameters`
# (https://openrouter.ai/api/v1/models). Avec `require_parameters`, l'envoyer
# donne « 404 No endpoints found that can handle the requested parameters ».
MODELES_SANS_TEMPERATURE = frozenset({"anthropic/claude-haiku-5.5"})


def _corps_requete(
    modele: str, systeme: str, utilisateur: str, schema: type[BaseModel],
    nom_schema: str, max_tokens: int,
) -> dict:
    corps = {
        "model": modele,
        "messages": [
            {"role": "system", "content": systeme},
            {"role": "user", "content": utilisateur},
        ],
        "max_tokens": max_tokens,
        "stream": False,
        # Sortie structurée stricte (doc « Structured Outputs »).
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": nom_schema,
                "strict": True,
                "schema": schema.model_json_schema(),
            },
        },
        # N'accepter que des fournisseurs qui gèrent response_format, sinon la
        # contrainte de schéma serait ignorée en silence.
        "provider": {"require_parameters": True},
        # Pas de raisonnement: il compterait dans max_tokens (doc « Reasoning
        # Tokens ») et se facturerait pour un verdict de quelques lignes.
        "reasoning": {"enabled": False},
    }
    if modele not in MODELES_SANS_TEMPERATURE:
        corps["temperature"] = 0
    return corps


def _appel_juge(
    *,
    conn: sqlite3.Connection,
    plafonds: budget.Plafonds,
    operation: str,
    modele: str,
    systeme: str,
    utilisateur: str,
    schema: type[M],
    nom_schema: str,
    empreinte: str,
    valider: Callable[[M], None] | None = None,
) -> M:
    """Appel réel au juge, sous budget, avec sortie structurée validée."""
    max_tokens = MAX_TOKENS_JUGE[operation]
    cout_estime = budget.estimer_cout(
        modele, budget.estimer_tokens_entree(texte=systeme + utilisateur), max_tokens
    )
    corps = _corps_requete(modele, systeme, utilisateur, schema, nom_schema, max_tokens)
    cle = _cle_openrouter()
    if cle is None:
        raise JugeIndisponible("Clé OpenRouter absente.")

    for tentative in range(MAX_RETRIES_JUGE + 1):
        # Refuse AVANT toute dépense, sinon réserve le coût estimé. Refait à
        # chaque tentative: un retry est une dépense comme une autre.
        appel_id = budget.reserver(
            conn, plafonds, operation=operation, modele=modele,
            cout_estime=cout_estime, empreinte=empreinte,
        )
        debut = time.monotonic()
        try:
            reponse = _poster_openrouter(corps, cle, TIMEOUT_JUGE_SECONDES)
            donnees = _lire_reponse_openrouter(reponse)
        except Exception as exc:
            if isinstance(exc, httpx.TimeoutException):
                # La requête est peut-être partie: on garde le coût réservé.
                facture, transitoire, erreur = True, False, ErreurOpenRouter(
                    "OpenRouter n'a pas répondu à temps.", transitoire=False
                )
            elif isinstance(exc, httpx.TransportError):
                # Connexion jamais établie: rien n'a été facturé.
                facture, transitoire, erreur = False, True, ErreurOpenRouter(
                    "Connexion à OpenRouter impossible.", transitoire=True, facture=False
                )
            elif isinstance(exc, ErreurOpenRouter):
                facture, transitoire, erreur = exc.facture, exc.transitoire, exc
            else:
                facture, transitoire, erreur = True, False, exc
            budget.solder(
                conn, appel_id, statut="echec",
                cout=cout_estime if facture else 0.0,
                detail=f"{type(exc).__name__}: {exc}"[:500],
                duree_ms=int((time.monotonic() - debut) * 1000),
            )
            if not transitoire or tentative == MAX_RETRIES_JUGE:
                if erreur is exc:
                    raise
                raise erreur from exc
            attente = getattr(erreur, "attente", None)
            time.sleep(attente if attente is not None else BACKOFF_JUGE_SECONDES)
            continue

        cout, tokens_entree, tokens_sortie = _cout_reel(donnees, modele)
        duree_ms = int((time.monotonic() - debut) * 1000)
        try:
            sortie = _extraire_sortie(donnees, schema, valider)
        except ErreurOpenRouter as exc:
            # Réponse reçue, donc facturée, mais inexploitable: coût réel conservé.
            budget.solder(
                conn, appel_id, statut="echec", cout=cout,
                tokens_entree=tokens_entree, tokens_sortie=tokens_sortie,
                detail=str(exc)[:500], duree_ms=duree_ms,
            )
            raise
        budget.solder(
            conn, appel_id, statut="succes", cout=cout,
            tokens_entree=tokens_entree, tokens_sortie=tokens_sortie,
            duree_ms=duree_ms,
        )
        _log_fichier(
            operation, modele,
            SimpleNamespace(input_tokens=tokens_entree, output_tokens=tokens_sortie),
        )
        return sortie

    raise RuntimeError("Appel au juge impossible.")


def _executer_juge(
    *, conn: sqlite3.Connection, operation: str, systeme: str, utilisateur: str,
    schema: type[M], nom_schema: str, valider: Callable[[M], None] | None = None,
) -> M:
    """Cache anti-doublon + appel, sous le même verrou que le reste du module."""
    modele = juge_modele()
    plafonds = _plafonds()
    empreinte = budget.empreinte_entree(
        operation=operation, modele=modele, texte=utilisateur, fichiers=(), extra=systeme
    )
    with _VERROU_APPEL:
        cache = budget.lire_cache(conn, empreinte)
        if cache is not None:
            return schema.model_validate(cache)
        resultat = _appel_juge(
            conn=conn, plafonds=plafonds, operation=operation, modele=modele,
            systeme=systeme, utilisateur=utilisateur, schema=schema,
            nom_schema=nom_schema, empreinte=empreinte, valider=valider,
        )
        budget.ecrire_cache(conn, empreinte, operation, modele, resultat.model_dump())
        return resultat


def _neutraliser(texte: str, *balises: str) -> str:
    """Empêche une donnée de refermer elle-même la balise qui la contient."""
    for balise in balises:
        texte = re.sub(f"</\\s*{balise}", f"<\\/{balise}", texte, flags=re.IGNORECASE)
    return texte


_BALISES = (
    "question", "reponse_attendue", "reponse_eleve", "langue_commentaire",
    "consigne", "contraintes", "criteres", "exemple_reference", "texte_eleve",
)
_MOTS = re.compile(r"[\wÀ-ÿ]{3,}")


def _langue_commentaire(langue: str) -> str:
    return "en" if langue == "en" else "fr"


def _mock_juge_libre(reponse_attendue: str, reponse_donnee: str) -> VerdictLibreLLM:
    """Verdict déterministe: part des mots de la réponse attendue retrouvés."""
    attendus = {m.lower() for m in _MOTS.findall(reponse_attendue)}
    donnes = {m.lower() for m in _MOTS.findall(reponse_donnee)}
    part = len(attendus & donnes) / len(attendus) if attendus else 0.0
    verdict = "juste" if part >= 0.6 else "partiel" if part >= 0.25 else "faux"
    return VerdictLibreLLM(
        verdict=verdict,
        commentaire=f"[MOCK] Correction simulée ({part:.0%} des mots clés retrouvés).",
    )


def _mock_juge_redaction(criteres: list[str], texte: str) -> VerdictRedactionLLM:
    """Verdict déterministe: un critère de plus rempli tous les 15 mots écrits."""
    remplis = min(len(criteres), len(texte.split()) // 15)
    return VerdictRedactionLLM(
        criteres=[i < remplis for i in range(len(criteres))],
        commentaire=f"[MOCK] Correction simulée ({remplis}/{len(criteres)} critères).",
    )


def juger_libre(
    *, conn: sqlite3.Connection, enonce: str, reponse_attendue: str,
    reponse_donnee: str, langue: str = "fr",
) -> VerdictLibreLLM:
    """Corrige une réponse libre (`libre_llm`)."""
    reponse_donnee = reponse_donnee.strip()
    if not reponse_donnee:
        raise RuntimeError("Réponse vide: rien à corriger.")
    if len(reponse_donnee) > MAX_CARACTERES_REPONSE_LIBRE:
        raise budget.EntreeTropGrande(
            f"Réponse trop longue pour la correction: {len(reponse_donnee)} caractères, "
            f"maximum {MAX_CARACTERES_REPONSE_LIBRE}."
        )
    etat = etat_juge()
    if etat == "simule":
        return _mock_juge_libre(reponse_attendue, reponse_donnee)
    if etat != "reel":
        raise JugeIndisponible(MESSAGE_JUGE_INACTIF)

    utilisateur = (
        f"<question>\n{_neutraliser(enonce, *_BALISES)}\n</question>\n"
        f"<reponse_attendue>\n{_neutraliser(reponse_attendue, *_BALISES)}\n</reponse_attendue>\n"
        f"<reponse_eleve>\n{_neutraliser(reponse_donnee, *_BALISES)}\n</reponse_eleve>\n"
        f"<langue_commentaire>{_langue_commentaire(langue)}</langue_commentaire>"
    )
    return _executer_juge(
        conn=conn, operation="jugement_libre",
        systeme=charger_prompt(PROMPT_JUGE_LIBRE), utilisateur=utilisateur,
        schema=VerdictLibreLLM, nom_schema="verdict_reponse_libre",
    )


def juger_redaction(
    *, conn: sqlite3.Connection, consigne: str, contraintes: str,
    criteres: list[str], exemple_reference: str, texte: str, langue: str = "fr",
) -> VerdictRedactionLLM:
    """Corrige une rédaction critère par critère (`redaction`)."""
    texte = texte.strip()
    if not texte:
        raise RuntimeError("Texte vide: rien à corriger.")
    if not criteres:
        raise RuntimeError("Une rédaction doit avoir au moins un critère.")
    if len(texte) > MAX_CARACTERES_TEXTE_REDACTION:
        raise budget.EntreeTropGrande(
            f"Texte trop long pour la correction: {len(texte)} caractères, "
            f"maximum {MAX_CARACTERES_TEXTE_REDACTION}."
        )
    etat = etat_juge()
    if etat == "simule":
        return _mock_juge_redaction(criteres, texte)
    if etat != "reel":
        raise JugeIndisponible(MESSAGE_JUGE_INACTIF)

    liste = "\n".join(f"{i}. {_neutraliser(c, *_BALISES)}" for i, c in enumerate(criteres, 1))
    utilisateur = (
        f"<consigne>\n{_neutraliser(consigne, *_BALISES)}\n</consigne>\n"
        f"<contraintes>\n{_neutraliser(contraintes, *_BALISES)}\n</contraintes>\n"
        f"<criteres>\n{liste}\n</criteres>\n"
        f"<exemple_reference>\n{_neutraliser(exemple_reference, *_BALISES)}\n</exemple_reference>\n"
        f"<texte_eleve>\n{_neutraliser(texte, *_BALISES)}\n</texte_eleve>\n"
        f"<langue_commentaire>{_langue_commentaire(langue)}</langue_commentaire>"
    )

    def _bon_nombre(sortie: VerdictRedactionLLM) -> None:
        if len(sortie.criteres) != len(criteres):
            raise ErreurOpenRouter(
                f"Le juge a rendu {len(sortie.criteres)} critères au lieu de {len(criteres)}."
            )

    return _executer_juge(
        conn=conn, operation="jugement_redaction",
        systeme=charger_prompt(PROMPT_JUGE_REDACTION), utilisateur=utilisateur,
        schema=VerdictRedactionLLM, nom_schema="verdict_redaction", valider=_bon_nombre,
    )


MESSAGE_JUGE_INACTIF = (
    "Correction par IA non activée (FLASHCARDS_JUGE_REEL=1 et clé OPENROUTER_API_KEY "
    "requises): auto-évalue-toi."
)
