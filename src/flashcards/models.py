"""Modèles de données: concepts validés, taxonomie fermée de questions, rapports."""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


# --- Étape A: plan de concepts ---------------------------------------------

Categorie = Literal[
    "definition", "propriete", "formule", "methode", "fait", "vocabulaire", "exemple_type"
]
Importance = Literal["essentiel", "important", "secondaire"]
Confiance = Literal["haute", "moyenne", "basse"]


class Concept(BaseModel):
    """Un concept testable, tel que le prompt d'extraction le définit.

    `resume` accepte aussi l'ancien nom `description` et `source` l'ancien
    `page`, pour que les fichiers `.concepts.json` produits avant la refonte
    des prompts restent lisibles.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    titre: str
    categorie: Categorie = "definition"
    resume: str = Field(
        default="",
        validation_alias=AliasChoices("resume", "description"),
        serialization_alias="resume",
    )
    source: str | None = Field(
        default=None,
        validation_alias=AliasChoices("source", "page"),
        serialization_alias="source",
    )
    importance: Importance = "important"
    adapte_calcul: bool = False

    @field_validator("source", mode="before")
    @classmethod
    def _source_en_texte(cls, valeur):
        # L'ancien champ `page` était un entier; la nouvelle `source` est
        # libre ("page 3", "exercice 2b", "section 1.4").
        if valeur is None or isinstance(valeur, str):
            return valeur
        return f"page {valeur}"


class ConceptListRaw(BaseModel):
    """Forme brute attendue du LLM à l'étape A (sans cours/source/validated,
    ajoutés par le code)."""

    concepts: list[Concept]
    avertissements: list[str] = Field(default_factory=list)


class ConceptList(BaseModel):
    # Les fichiers générés par le prototype 1 écrivaient "branche": on accepte
    # les deux à la lecture et on écrit toujours "cours".
    model_config = ConfigDict(populate_by_name=True)

    cours: str = Field(
        validation_alias=AliasChoices("cours", "branche"),
        serialization_alias="cours",
    )
    source: str
    concepts: list[Concept]
    avertissements: list[str] = Field(default_factory=list)
    validated: bool = False


# --- Étape B: contenu de question tel que renvoyé par le LLM ---------------
# (sans id/concept_id/cours/source: ces champs sont ajoutés par le
# code après validation, pour ne jamais dépendre du LLM pour le lien vers le
# concept source.)

class QCMContent(BaseModel):
    type: Literal["qcm"] = "qcm"
    enonce: str
    propositions: list[str]
    bonnes_reponses: list[int]


class LibreLLMContent(BaseModel):
    type: Literal["libre_llm"] = "libre_llm"
    enonce: str
    reponse_attendue: str


class ClozeContent(BaseModel):
    type: Literal["cloze"] = "cloze"
    texte: str
    valeurs_attendues: dict[str, str]


class ProblemeContent(BaseModel):
    type: Literal["probleme"] = "probleme"
    enonce: str
    solution_etapes: list[str]
    reponse_finale: str
    necessite_dessin: bool = False
    schema_svg: str | None = None


class ExactContent(BaseModel):
    type: Literal["exact"] = "exact"
    enonce: str
    reponse_exacte: str


class RedactionContent(BaseModel):
    """Production écrite: l'élève rédige, puis coche les critères remplis.

    Pas de correction automatique — comme `probleme`, l'auto-évaluation se
    fait en comparant à `exemple_reference`. Les critères sont ce qui rend
    cette auto-évaluation objective: chacun doit être une phrase courte que
    l'élève peut vérifier lui-même sur sa copie.
    """

    type: Literal["redaction"] = "redaction"
    consigne: str
    contraintes: str
    criteres: list[str]
    exemple_reference: str


QuestionContent = Annotated[
    Union[
        QCMContent, LibreLLMContent, ClozeContent, ProblemeContent, ExactContent,
        RedactionContent,
    ],
    Field(discriminator="type"),
]


# Ce que le LLM renvoie à l'étape B: le concept_id, la référence dans la
# matière et la confiance, plus les champs du type choisi. Les champs
# d'identité (id, cours, source) sont ajoutés par le code — jamais
# demandés au LLM.

class _QuestionGenereeBase(BaseModel):
    concept_id: str
    source_ref: str
    confiance: Confiance


class QcmGenere(_QuestionGenereeBase, QCMContent):
    pass


class LibreLLMGenere(_QuestionGenereeBase, LibreLLMContent):
    pass


class ClozeGenere(_QuestionGenereeBase, ClozeContent):
    pass


class ProblemeGenere(_QuestionGenereeBase, ProblemeContent):
    pass


class ExactGenere(_QuestionGenereeBase, ExactContent):
    pass


class RedactionGenere(_QuestionGenereeBase, RedactionContent):
    pass


# Union SANS discriminator: `discriminator` n'est pas un mot-clé JSON Schema
# supporté par les sorties structurées de l'API, et chaque variante porte déjà
# un `type` littéral distinct — Pydantic sait donc trancher seul.
QuestionGeneree = Union[
    QcmGenere, LibreLLMGenere, ClozeGenere, ProblemeGenere, ExactGenere, RedactionGenere
]


class GenerationLLMResponse(BaseModel):
    """Sortie structurée de l'étape B: une liste de questions + avertissements.

    `avertissements` recueille les doutes, les passages illisibles et les
    concepts validés pour lesquels le LLM n'a pas pu produire de question
    fiable — préféré à une question inventée.
    """

    questions: list[QuestionGeneree] = Field(default_factory=list)
    avertissements: list[str] = Field(default_factory=list)


# --- Question complète, persistée dans <nom>.questions.json -----------------
# NB prototype 2 (FSRS): chaque carte aura un état de répétition espacée
# stocké séparément, indexé par `id`. Ne rien ajouter ici pour l'instant,
# mais le champ `id` stable est l'ancrage prévu pour ce futur lien.

class Source(BaseModel):
    fichier: str
    page: int | None = None


class QuestionBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    concept_id: str
    cours: str = Field(
        validation_alias=AliasChoices("cours", "branche"),
        serialization_alias="cours",
    )
    source: Source
    # Où l'information se trouve dans la matière (page, section, exercice),
    # tel que le LLM l'a repéré. Optionnel: les cartes créées avant la refonte
    # des prompts (et celles du jeu de démo) n'en ont pas.
    source_ref: str | None = None
    # Confiance du LLM dans la question, surtout pour les calculs de `probleme`
    # dont l'élève se sert pour s'auto-évaluer.
    confiance: Confiance = "moyenne"
    # Poids explicite en points (nullable): prime sur la catégorie du concept
    # et sur le poids du type (voir `poids.py`). Jamais demandé au LLM.
    poids: float | None = Field(default=None, gt=0)


class QCM(QuestionBase, QCMContent):
    pass


class LibreLLM(QuestionBase, LibreLLMContent):
    pass


class Cloze(QuestionBase, ClozeContent):
    pass


class Probleme(QuestionBase, ProblemeContent):
    pass


class Exact(QuestionBase, ExactContent):
    pass


class Redaction(QuestionBase, RedactionContent):
    pass


Question = Annotated[
    Union[QCM, LibreLLM, Cloze, Probleme, Exact, Redaction],
    Field(discriminator="type"),
]


class FailedQuestion(BaseModel):
    concept_id: str
    concept_titre: str
    raison: str


class GenerationResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: str
    cours: str = Field(
        validation_alias=AliasChoices("cours", "branche"),
        serialization_alias="cours",
    )
    questions: list[Question]
    echecs: list[FailedQuestion] = Field(default_factory=list)
    avertissements: list[str] = Field(default_factory=list)


class CoverageEntry(BaseModel):
    concept_id: str
    titre: str
    nb_questions: int


class CoverageReport(BaseModel):
    source: str
    total_concepts: int
    concepts_sans_question: list[str]
    details: list[CoverageEntry]


# --- Étape review: notation manuelle des questions générées ----------------

Note = Literal["bonne", "moyenne", "mauvaise"]


class ReviewEntry(BaseModel):
    question_id: str
    note: Note
    commentaire: str | None = None


class ReviewFile(BaseModel):
    source: str
    entries: list[ReviewEntry] = Field(default_factory=list)


# --- Sortie du juge (réponses libres et rédactions) --------------------------
# Format de ce que le juge LLM doit renvoyer. `extra="forbid"` produit
# `additionalProperties: false`, exigé par les sorties structurées strictes.
# C'est une donnée non fiable: elle est validée avant toute utilisation.

class VerdictLibreLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["juste", "partiel", "faux"] = Field(
        description="juste: l'essentiel de la réponse attendue est présent et correct. "
        "partiel: une partie importante est juste mais incomplète ou imprécise. "
        "faux: absent, hors sujet ou contraire à la réponse attendue."
    )
    commentaire: str = Field(
        description="1 à 3 phrases, en français, adressées à l'élève (tutoiement): "
        "ce qui est juste, ce qui manque ou est faux."
    )


class VerdictRedactionLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteres: list[bool] = Field(
        description="Un booléen par critère, dans l'ordre exact de la liste fournie: "
        "true si le texte de l'élève remplit ce critère."
    )
    commentaire: str = Field(
        description="2 à 4 phrases, en français, adressées à l'élève (tutoiement): "
        "points forts et ce qui doit être corrigé en priorité."
    )
