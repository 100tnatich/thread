"""Workflow « manuel »: valider et finaliser des fichiers produits sans API.

Ici, c'est Claude Code (via l'abonnement, dans une session) qui joue le rôle
du LLM: il écrit des fichiers JSON dans `sorties/`. Ce module les contrôle
avec les mêmes modèles Pydantic que le reste du projet et ne fait AUCUN appel
LLM, ni aucune écriture: il lit, valide, rapporte.

Deux formes de fichier de questions sont acceptées:
- **complet** (produit par `generate`): chaque question porte `id`, `cours`
  et `source`;
- **brut** (produit par Claude Code): seulement `concept_id`, `type`, les
  champs du type, `source_ref` et `confiance`; `cours` et `source`
  viennent de l'en-tête du fichier (ou d'un forçage), et l'`id` est dérivé du
  contenu — un même contenu donne donc toujours le même `id`, ce qui rend
  l'import idempotent.

Une sortie fautive n'est jamais « réparée » ici: on la signale, question par
question, pour qu'elle soit corrigée à la source.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .models import (
    QCM,
    Cloze,
    Concept,
    ConceptList,
    Exact,
    FailedQuestion,
    GenerationResult,
    LibreLLM,
    Probleme,
    Redaction,
)

CLASSES_QUESTION: dict[str, type[BaseModel]] = {
    "qcm": QCM,
    "libre_llm": LibreLLM,
    "cloze": Cloze,
    "probleme": Probleme,
    "exact": Exact,
    "redaction": Redaction,
}

# Champs qui situent une question sans la définir: exclus de l'empreinte,
# pour que deux questions identiques dans deux sessions de
# génération soient reconnues comme doublons.
CHAMPS_IDENTITE = frozenset(
    {"id", "concept_id", "cours", "source", "source_ref", "confiance", "poids"}
)

# Champs que le prompt d'extraction impose pour chaque concept, avec leurs
# anciens noms acceptés à la lecture.
CHAMPS_CONCEPT = {
    "id": ("id",),
    "titre": ("titre",),
    "categorie": ("categorie",),
    "resume": ("resume", "description"),
    "source": ("source", "page"),
    "importance": ("importance",),
    "adapte_calcul": ("adapte_calcul",),
}

_CLOZE = re.compile(r"\{\{c(\d+)::")
_ESPACES = re.compile(r"\s+")
_ESPACE_DE_NOMS = uuid.NAMESPACE_URL
MAX_QUESTIONS = 2000


# --- Rapport ----------------------------------------------------------------

class ErreurElement(BaseModel):
    position: int  # à partir de 1
    reference: str  # concept_id ou id: de quoi retrouver l'élément
    messages: list[str]


class ApercuQuestion(BaseModel):
    position: int
    concept_id: str
    type: str
    resume: str
    confiance: str
    # « nouvelle », « doublon_fichier » (déjà plus haut dans le fichier) ou
    # « deja_en_base » (rempli par l'import, qui seul connaît la base).
    statut: Literal["nouvelle", "doublon_fichier", "deja_en_base"] = "nouvelle"


class LigneCouverture(BaseModel):
    concept_id: str
    titre: str
    nb_questions: int


class Rapport(BaseModel):
    fichier: str
    nature: Literal["concepts", "questions", "inconnu"]
    valide: bool
    cours: str | None = None
    source: str | None = None
    erreurs_fichier: list[str] = Field(default_factory=list)
    erreurs: list[ErreurElement] = Field(default_factory=list)
    avertissements: list[str] = Field(default_factory=list)
    nb_elements: int = 0
    par_type: dict[str, int] = Field(default_factory=dict)
    confiance_basse: list[ApercuQuestion] = Field(default_factory=list)
    apercu: list[ApercuQuestion] = Field(default_factory=list)
    concepts_fichier: str | None = None
    concepts_valides_par_nate: bool | None = None
    couverture: list[LigneCouverture] = Field(default_factory=list)
    concepts_sans_question: list[LigneCouverture] = Field(default_factory=list)

    @property
    def nb_erreurs(self) -> int:
        return len(self.erreurs_fichier) + sum(len(e.messages) for e in self.erreurs)


@dataclass
class Analyse:
    rapport: Rapport
    # Renseigné seulement si le fichier de questions est entièrement valide.
    resultat: GenerationResult | None = None
    concepts: ConceptList | None = None


# --- Empreinte de contenu ---------------------------------------------------

def _normaliser(valeur: Any) -> Any:
    if isinstance(valeur, str):
        return _ESPACES.sub(" ", valeur).strip()
    if isinstance(valeur, list):
        return [_normaliser(v) for v in valeur]
    if isinstance(valeur, dict):
        return {k: _normaliser(v) for k, v in valeur.items()}
    return valeur


def empreinte_contenu(payload: dict) -> str:
    """Hash du CONTENU d'une question (énoncé, réponse…), sans son identité.

    Insensible aux espaces superflus. Sert à reconnaître qu'une question est
    déjà là même si elle porte un autre `id`.
    """
    contenu = {k: _normaliser(v) for k, v in payload.items() if k not in CHAMPS_IDENTITE}
    return hashlib.sha256(
        json.dumps(contenu, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def id_depuis_contenu(cours: str, empreinte: str) -> str:
    """`id` stable pour une question brute: même cours + même contenu = même id."""
    return str(uuid.uuid5(_ESPACE_DE_NOMS, f"{cours}|{empreinte}"))


# --- Messages d'erreur lisibles --------------------------------------------

def _chemin(loc: tuple) -> str:
    return ".".join(str(p) for p in loc)


def formater_erreurs(exc: ValidationError) -> list[str]:
    messages = []
    for e in exc.errors():
        chemin = _chemin(e["loc"])
        genre = e["type"]
        if genre == "missing":
            messages.append(f"champ manquant: {chemin}")
        elif genre in ("string_type", "list_type", "dict_type", "int_type", "bool_type"):
            attendu = {
                "string_type": "du texte", "list_type": "une liste",
                "dict_type": "un objet", "int_type": "un entier",
                "bool_type": "true ou false",
            }[genre]
            messages.append(f"{chemin}: doit être {attendu}")
        elif genre == "literal_error":
            attendu = (e.get("ctx") or {}).get("expected", "")
            messages.append(f"{chemin}: valeur invalide (attendu: {attendu})")
        else:
            messages.append(f"{chemin}: {e['msg']}" if chemin else str(e["msg"]))
    return messages


def _champs_inconnus(brut: dict, modele: type[BaseModel]) -> list[str]:
    connus = set(modele.model_fields) | {"branche"}
    return sorted(set(brut) - connus)


# --- Contrôles de cohérence (au-delà du schéma) -----------------------------

def _controles_coherence(question: BaseModel) -> tuple[list[str], list[str]]:
    """Renvoie (erreurs, avertissements) que le schéma Pydantic ne détecte pas.

    Les erreurs sont les cas où la carte serait inutilisable ou fausse en
    session (réponse hors des propositions, trou sans valeur…).
    """
    erreurs: list[str] = []
    avertissements: list[str] = []
    donnees = question.model_dump()

    for nom, valeur in donnees.items():
        # L'identité (id, cours, source) est contrôlée à part.
        if nom in ("schema_svg", "id", "cours", "source"):
            continue
        if isinstance(valeur, str) and not valeur.strip():
            (avertissements if nom == "source_ref" else erreurs).append(f"champ vide: {nom}")
        if isinstance(valeur, list) and not valeur:
            erreurs.append(f"liste vide: {nom}")

    if isinstance(question, QCM):
        n = len(question.propositions)
        if n < 2:
            erreurs.append("un QCM demande au moins 2 propositions")
        elif n != 4:
            avertissements.append(f"{n} propositions (le prompt en demande 4)")
        hors = [i for i in question.bonnes_reponses if not 0 <= i < n]
        if hors:
            erreurs.append(
                f"bonnes_reponses hors des propositions: {hors} "
                f"(indices valides: 0 à {n - 1})"
            )
    elif isinstance(question, Cloze):
        trous = {f"c{n}" for n in _CLOZE.findall(question.texte)}
        if not trous:
            erreurs.append("aucun trou {{c1::…}} dans le texte")
        elif trous != set(question.valeurs_attendues):
            erreurs.append(
                f"trous du texte {sorted(trous)} ≠ clés de valeurs_attendues "
                f"{sorted(question.valeurs_attendues)}"
            )
    elif isinstance(question, Probleme):
        if question.necessite_dessin and not question.schema_svg:
            avertissements.append("dessin demandé sans schema_svg: l'élève le fera sur feuille")
    elif isinstance(question, Redaction):
        if not 4 <= len(question.criteres) <= 6:
            avertissements.append(
                f"{len(question.criteres)} critères (le prompt en demande 4 à 6)"
            )
    return erreurs, avertissements


def _resume(donnees: dict) -> str:
    texte = donnees.get("enonce") or donnees.get("texte") or donnees.get("consigne") or ""
    texte = _ESPACES.sub(" ", str(texte)).strip()
    return texte if len(texte) <= 120 else texte[:117] + "…"


def _source(valeur: Any, defaut: str) -> Any:
    """`source` d'une question: objet {fichier, page}, ou simple nom de fichier."""
    if not valeur:
        return {"fichier": defaut, "page": None}
    if isinstance(valeur, str):
        return {"fichier": valeur, "page": None}
    return valeur


# --- Lecture ----------------------------------------------------------------

def _lire_json(contenu: str | bytes) -> tuple[Any, str | None]:
    try:
        return json.loads(contenu), None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return None, f"JSON invalide: {e}"


def _charger_concepts(contenu: str | bytes) -> tuple[ConceptList | None, list[str]]:
    obj, erreur = _lire_json(contenu)
    if erreur:
        return None, [erreur]
    if not isinstance(obj, dict) or "concepts" not in obj:
        return None, ["pas un fichier de concepts (clé « concepts » absente)"]
    try:
        return ConceptList.model_validate(obj), []
    except ValidationError as e:
        return None, formater_erreurs(e)


# --- Analyse d'un fichier de concepts --------------------------------------

def _analyser_concepts(obj: dict, rapport: Rapport) -> Analyse:
    rapport.nature = "concepts"
    rapport.cours = obj.get("cours") or obj.get("branche")
    rapport.source = obj.get("source")
    rapport.concepts_valides_par_nate = bool(obj.get("validated", False))

    try:
        ConceptList.model_validate({**obj, "concepts": []})
    except ValidationError as e:
        rapport.erreurs_fichier += [f"en-tête: {m}" for m in formater_erreurs(e)]

    bruts = obj.get("concepts")
    if not isinstance(bruts, list):
        rapport.erreurs_fichier.append("« concepts » doit être une liste")
        bruts = []
    elif not bruts:
        rapport.erreurs_fichier.append("aucun concept dans le fichier")

    rapport.nb_elements = len(bruts)
    ids_vus: dict[str, int] = {}
    for position, brut in enumerate(bruts, 1):
        reference = str(brut.get("id", "?")) if isinstance(brut, dict) else "?"
        messages: list[str] = []
        if not isinstance(brut, dict):
            rapport.erreurs.append(ErreurElement(
                position=position, reference=reference,
                messages=["un concept doit être un objet JSON"],
            ))
            continue
        try:
            concept = Concept.model_validate(brut)
        except ValidationError as e:
            messages += formater_erreurs(e)
            concept = None

        if concept is not None:
            if not concept.titre.strip():
                messages.append("champ vide: titre")
            if concept.id in ids_vus:
                messages.append(
                    f"id « {concept.id} » déjà utilisé par le concept n°{ids_vus[concept.id]}"
                )
            ids_vus.setdefault(concept.id, position)

        absents = [
            nom for nom, alias in CHAMPS_CONCEPT.items() if not any(a in brut for a in alias)
        ]
        if absents:
            rapport.avertissements.append(
                f"concept n°{position} ({reference}): champ(s) absent(s), valeur par "
                f"défaut utilisée: {', '.join(absents)}"
            )
        elif concept is not None and not concept.resume.strip():
            rapport.avertissements.append(f"concept n°{position} ({reference}): résumé vide")

        if messages:
            rapport.erreurs.append(
                ErreurElement(position=position, reference=reference, messages=messages)
            )

    rapport.valide = not rapport.erreurs_fichier and not rapport.erreurs
    signales = obj.get("avertissements")
    if isinstance(signales, list):
        rapport.avertissements += [f"signalé par l'extraction: {a}" for a in signales]

    liste = None
    if rapport.valide:
        liste = ConceptList.model_validate(obj)
    return Analyse(rapport=rapport, concepts=liste)


# --- Analyse d'un fichier de questions -------------------------------------

def _analyser_questions(
    obj: dict,
    rapport: Rapport,
    *,
    nom: str,
    concepts: ConceptList | None,
    cours: str | None,
) -> Analyse:
    rapport.nature = "questions"
    entete_cours = cours or obj.get("cours") or obj.get("branche")
    entete_source = obj.get("source")
    rapport.cours, rapport.source = entete_cours, entete_source or nom
    if not entete_source:
        rapport.avertissements.append(
            f"« source » absente de l'en-tête: le nom du fichier ({nom}) est utilisé"
        )

    bruts = obj.get("questions")
    if not isinstance(bruts, list):
        rapport.erreurs_fichier.append("« questions » doit être une liste")
        bruts = []
    elif not bruts:
        rapport.erreurs_fichier.append("aucune question dans le fichier")
    elif len(bruts) > MAX_QUESTIONS:
        rapport.erreurs_fichier.append(
            f"trop de questions: {len(bruts)} (maximum {MAX_QUESTIONS})"
        )
        bruts = []
    rapport.nb_elements = len(bruts)

    ids_concepts = {c.id: c.titre for c in concepts.concepts} if concepts else None
    valides: list = []
    vues: dict[str, int] = {}  # empreinte -> position de la première occurrence
    ids_vus: dict[str, int] = {}  # id -> position
    par_concept: dict[str, int] = {}
    manque_cours = False

    for position, brut in enumerate(bruts, 1):
        if not isinstance(brut, dict):
            rapport.erreurs.append(ErreurElement(
                position=position, reference="?",
                messages=["une question doit être un objet JSON"],
            ))
            continue

        reference = str(brut.get("concept_id") or brut.get("id") or "?")
        messages: list[str] = []
        type_ = brut.get("type")
        classe = CLASSES_QUESTION.get(type_) if isinstance(type_, str) else None
        if classe is None:
            attendus = ", ".join(CLASSES_QUESTION)
            rapport.erreurs.append(ErreurElement(
                position=position, reference=reference,
                messages=[f"type {type_!r} inconnu ou absent (attendus: {attendus})"],
            ))
            continue

        question_cours = cours or brut.get("cours") or brut.get("branche") or entete_cours
        manque_cours |= not question_cours

        # Question brute: on complète avec l'en-tête. Le marqueur d'id est
        # remplacé par l'id dérivé du contenu une fois la question validée.
        complete = {
            **brut,
            "id": brut.get("id") or "provisoire",
            "cours": question_cours or "",
            "source": _source(brut.get("source"), entete_source or nom),
        }
        complete.pop("branche", None)

        # Contrat du prompt de génération: `source_ref` et `confiance` sont
        # obligatoires. Le modèle complet les laisse optionnels (anciennes
        # cartes), donc on les exige ici pour les questions brutes.
        if not brut.get("id"):
            messages += [
                f"champ manquant: {champ}" for champ in ("source_ref", "confiance")
                if champ not in brut
            ]

        inconnus = _champs_inconnus(brut, classe)
        if inconnus:
            rapport.avertissements.append(
                f"question n°{position} ({reference}): champ(s) inconnu(s) ignoré(s): "
                f"{', '.join(inconnus)}"
            )

        try:
            question = classe.model_validate(complete)
        except ValidationError as e:
            messages += formater_erreurs(e)
            question = None

        if question is not None:
            coh_erreurs, coh_avert = _controles_coherence(question)
            messages += coh_erreurs
            rapport.avertissements += [
                f"question n°{position} ({reference}): {a}" for a in coh_avert
            ]
            if ids_concepts is not None and question.concept_id not in ids_concepts:
                messages.append(
                    f"concept_id « {question.concept_id} » absent de la liste de concepts "
                    f"(aucune question hors des concepts validés)"
                )

        if messages:
            rapport.erreurs.append(
                ErreurElement(position=position, reference=reference, messages=messages)
            )
            continue

        donnees = question.model_dump()
        empreinte = empreinte_contenu(donnees)
        if not brut.get("id"):
            question = question.model_copy(
                update={"id": id_depuis_contenu(question.cours, empreinte)}
            )

        if question.id in ids_vus and empreinte not in vues:
            rapport.erreurs.append(ErreurElement(
                position=position, reference=reference,
                messages=[
                    f"id « {question.id} » déjà utilisé par la question n°{ids_vus[question.id]} "
                    f"(contenu différent)"
                ],
            ))
            continue
        ids_vus.setdefault(question.id, position)

        statut = "nouvelle"
        if empreinte in vues:
            statut = "doublon_fichier"
            rapport.avertissements.append(
                f"question n°{position} ({question.concept_id}): même contenu que la "
                f"question n°{vues[empreinte]} (doublon, ne sera importée qu'une fois)"
            )
        else:
            vues[empreinte] = position
            par_concept[question.concept_id] = par_concept.get(question.concept_id, 0) + 1

        apercu = ApercuQuestion(
            position=position, concept_id=question.concept_id, type=question.type,
            resume=_resume(donnees), confiance=question.confiance, statut=statut,
        )
        rapport.apercu.append(apercu)
        rapport.par_type[question.type] = rapport.par_type.get(question.type, 0) + 1
        if question.confiance == "basse":
            rapport.confiance_basse.append(apercu)
        valides.append(question)

    if bruts and manque_cours:
        rapport.erreurs_fichier.append("champ manquant: cours (ni dans l'en-tête, ni dans les questions)")

    if concepts is not None:
        rapport.couverture = [
            LigneCouverture(concept_id=c.id, titre=c.titre, nb_questions=par_concept.get(c.id, 0))
            for c in concepts.concepts
        ]
        rapport.concepts_sans_question = [l for l in rapport.couverture if l.nb_questions == 0]

    avertissements_fichier = obj.get("avertissements", [])
    if isinstance(avertissements_fichier, list):
        rapport.avertissements += [f"signalé par la génération: {a}" for a in avertissements_fichier]

    rapport.valide = not rapport.erreurs_fichier and not rapport.erreurs
    resultat = None
    if rapport.valide:
        try:
            resultat = GenerationResult(
                source=entete_source or nom,
                cours=entete_cours,
                questions=valides,
                echecs=[FailedQuestion.model_validate(e) for e in obj.get("echecs", [])],
                avertissements=[str(a) for a in avertissements_fichier]
                if isinstance(avertissements_fichier, list) else [],
            )
        except (ValidationError, TypeError) as e:
            rapport.valide = False
            rapport.erreurs_fichier += (
                [f"en-tête: {m}" for m in formater_erreurs(e)]
                if isinstance(e, ValidationError) else [f"« echecs » invalide: {e}"]
            )
    return Analyse(rapport=rapport, resultat=resultat, concepts=concepts)


# --- Point d'entrée ---------------------------------------------------------

def analyser(
    contenu: str | bytes,
    *,
    nom: str,
    concepts_contenu: str | bytes | None = None,
    concepts_nom: str | None = None,
    cours: str | None = None,
) -> Analyse:
    """Valide un fichier de concepts ou de questions. Ne lit ni n'écrit rien d'autre.

    `cours` force la valeur de l'en-tête et des questions
    (l'import web rattache les questions au cours choisi par l'utilisateur).
    `concepts_contenu`, s'il est donné, sert à contrôler les `concept_id` et à
    calculer la couverture.
    """
    rapport = Rapport(fichier=nom, nature="inconnu", valide=False)
    obj, erreur = _lire_json(contenu)
    if erreur:
        rapport.erreurs_fichier.append(erreur)
        return Analyse(rapport=rapport)
    if not isinstance(obj, dict):
        rapport.erreurs_fichier.append("le fichier doit contenir un objet JSON")
        return Analyse(rapport=rapport)

    if "questions" in obj:
        concepts = None
        if concepts_contenu is not None:
            concepts, erreurs = _charger_concepts(concepts_contenu)
            rapport.concepts_fichier = concepts_nom
            if erreurs:
                rapport.erreurs_fichier += [
                    f"fichier de concepts {concepts_nom or ''}: {m}".replace("  ", " ")
                    for m in erreurs
                ]
            elif not concepts.validated:
                rapport.avertissements.append(
                    f"la liste de concepts {concepts_nom or ''} n'est pas marquée "
                    f"validated: true".replace("  ", " ")
                )
            if concepts is not None:
                rapport.concepts_valides_par_nate = concepts.validated
        return _analyser_questions(
            obj, rapport, nom=nom, concepts=concepts, cours=cours
        )
    if "concepts" in obj:
        return _analyser_concepts(obj, rapport)

    rapport.erreurs_fichier.append(
        "ni « concepts » ni « questions »: ce n'est ni une liste de concepts ni un "
        "fichier de questions"
    )
    return Analyse(rapport=rapport)


# --- Affichage texte --------------------------------------------------------

def rapport_en_texte(rapport: Rapport) -> str:
    """Rapport lisible dans un terminal (CLI `validate`)."""
    lignes: list[str] = []
    nature = {"concepts": "liste de concepts", "questions": "questions", "inconnu": "type inconnu"}
    lignes.append(f"Fichier : {rapport.fichier}  ({nature[rapport.nature]})")
    contexte = [
        f"{etiquette} : {valeur}"
        for etiquette, valeur in (
            ("Cours", rapport.cours), ("Source", rapport.source)
        )
        if valeur
    ]
    if contexte:
        lignes.append(" · ".join(contexte))

    unite = "concept(s)" if rapport.nature == "concepts" else "question(s)"
    if rapport.valide:
        lignes.append(f"\nRésultat : VALIDE — {rapport.nb_elements} {unite}")
    else:
        lignes.append(
            f"\nRésultat : INVALIDE — {rapport.nb_erreurs} erreur(s) "
            f"sur {rapport.nb_elements} {unite}"
        )

    if rapport.nature == "concepts" and rapport.concepts_valides_par_nate is not None:
        lignes.append(
            "Validation par Nate : "
            + ("OUI (validated: true)" if rapport.concepts_valides_par_nate
               else "NON (validated: false) — à relire avant de générer")
        )

    if rapport.erreurs_fichier or rapport.erreurs:
        lignes.append("\nErreurs")
        lignes += [f"  Fichier : {m}" for m in rapport.erreurs_fichier]
        nom_element = "Concept" if rapport.nature == "concepts" else "Question"
        for e in rapport.erreurs:
            for m in e.messages:
                lignes.append(f"  {nom_element} n°{e.position} ({e.reference}) : {m}")

    if rapport.avertissements:
        lignes.append(f"\nAvertissements ({len(rapport.avertissements)})")
        lignes += [f"  - {a}" for a in rapport.avertissements]

    if rapport.par_type:
        lignes.append(
            "\nTypes : " + " · ".join(f"{t} {n}" for t, n in sorted(rapport.par_type.items()))
        )

    if rapport.confiance_basse:
        lignes.append(f"\nConfiance BASSE ({len(rapport.confiance_basse)}) — à vérifier en priorité")
        for q in rapport.confiance_basse:
            lignes.append(f"  n°{q.position} ({q.concept_id}, {q.type}) : {q.resume}")

    if rapport.couverture:
        lignes.append(f"\nCouverture (concepts : {rapport.concepts_fichier or 'fichier fourni'})")
        for ligne in rapport.couverture:
            marque = "   ← AUCUNE QUESTION" if ligne.nb_questions == 0 else ""
            lignes.append(f"  {ligne.concept_id:<6} {ligne.nb_questions:>2}  {ligne.titre}{marque}")
        lignes.append(
            f"Concepts sans question : {len(rapport.concepts_sans_question)} "
            f"sur {len(rapport.couverture)}"
        )
    elif rapport.nature == "questions":
        lignes.append("\nCouverture : non calculée (aucun fichier de concepts fourni)")
    return "\n".join(lignes)
