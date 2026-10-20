"""Schémas d'entrée/sortie de l'API REST.

Volontairement distincts des modèles du moteur: l'API peut évoluer sans
contraindre le moteur, et inversement.
"""
from __future__ import annotations

import datetime as dt
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..manuel import Rapport


# --- Auth ------------------------------------------------------------------

class LoginIn(BaseModel):
    # Bornés: argon2 hache tout ce qu'on lui donne, sur une route publique.
    identifiant: str = Field(max_length=100)
    mot_de_passe: str = Field(max_length=256)


class ChangerMdpIn(BaseModel):
    ancien: str = Field(max_length=256)
    nouveau: str = Field(max_length=256)


# --- Comptes et dépôt -------------------------------------------------------

Role = Literal["admin", "standard", "etendu"]


class CompteOut(BaseModel):
    identifiant: str
    role: Role
    cree_le: str


class CompteIn(BaseModel):
    identifiant: str = Field(min_length=1, max_length=40)
    mot_de_passe: str = Field(max_length=256)
    role: Role = "standard"


class ComptePatch(BaseModel):
    role: Role | None = None
    # Nouveau mot de passe posé par l'admin (sans l'ancien).
    mot_de_passe: str | None = Field(None, max_length=256)


class DepotOut(BaseModel):
    id: int
    cours: str
    nom: str
    taille: int
    depose_par: str
    depose_le: str
    traite_le: str | None


# --- Matières et cours (thèmes) --------------------------------------------

class MatiereOut(BaseModel):
    nom: str
    couleur: str
    profil: str
    nb_cours: int
    nb_cartes: int
    # Part des thèmes de la matière qui sont en mode entretien.
    nb_cours_entretien: int = 0


class MatiereIn(BaseModel):
    nom: str
    couleur: str = "#8B7CFF"
    profil: str = ""


class MatierePatch(BaseModel):
    nouveau_nom: str | None = None
    couleur: str | None = None
    profil: str | None = None


class CoursOut(BaseModel):
    """Un cours = un thème, rangé (ou non) dans une matière."""

    nom: str
    couleur: str
    nb_cartes: int
    points: float = 0.0
    matiere: str | None = None
    archive: bool = False
    profil: str = ""
    # Récap théorique (Markdown) lu avant une session sur ce thème.
    recap: str = ""
    # En entretien: aucun test à venir, pas archivé.
    en_entretien: bool = False


class CoursIn(BaseModel):
    nom: str
    # Sans couleur, le thème prend celle de sa matière.
    couleur: str | None = None
    matiere: str | None = None
    profil: str = ""


class CoursPatch(BaseModel):
    nouveau_nom: str | None = None
    couleur: str | None = None
    # `matiere: null` détache le thème de sa matière (distinct de « absent »).
    matiere: str | None = None
    profil: str | None = None
    recap: str | None = Field(default=None, max_length=20000)
    archive: bool | None = None


class CoursSuppression(BaseModel):
    reaffecter_vers: str | None = None


# --- Tests -----------------------------------------------------------------

def _en_liste(valeur):
    """Accepte « un cours » comme avant, ou une liste."""
    if valeur is None:
        return []
    return [valeur] if isinstance(valeur, str) else valeur


class TestIn(BaseModel):
    # Un test se lie à plusieurs cours et/ou à des matières entières.
    cours: list[str] = Field(default_factory=list)
    matieres: list[str] = Field(default_factory=list)
    date: date
    intitule: str

    @field_validator("cours", "matieres", mode="before")
    @classmethod
    def _liste(cls, v):
        return _en_liste(v)


class TestPatch(BaseModel):
    # `dt.date`: un champ nommé `date` avec défaut masquerait le type `date`.
    date: dt.date | None = None
    # Si fournis, REMPLACENT les liens du test.
    cours: list[str] | None = None
    matieres: list[str] | None = None

    @field_validator("cours", "matieres", mode="before")
    @classmethod
    def _liste(cls, v):
        return None if v is None else _en_liste(v)


class TestOut(BaseModel):
    id: int
    # Thèmes couverts (liens directs + thèmes des matières liées).
    cours: list[str]
    matieres: list[str] = Field(default_factory=list)
    date: date
    intitule: str


# --- Session ---------------------------------------------------------------

class QuestionOut(BaseModel):
    """La question telle que l'interface doit l'afficher.

    `payload` contient le modèle Pydantic complet du prototype 1: le front
    choisit son gabarit selon `type`.
    """

    card_id: str
    type: str
    cours: str
    couleur_cours: str
    payload: dict[str, Any]
    jours_retard: int
    nouvelle: bool
    # Poids en points, et carte d'un thème en entretien.
    poids: float = 0.0
    entretien: bool = False


class RecapOut(BaseModel):
    """Rappel théorique d'un thème présent dans la session (Markdown, non fiable)."""

    cours: str
    couleur: str
    texte: str


class QueueOut(BaseModel):
    jour: date
    session_id: str
    total: int
    total_dues: int
    # Plafond et total en POINTS.
    plafond: float
    points: float = 0.0
    points_entretien: float = 0.0
    # Correction par IA des réponses libres: "reel" (OpenRouter), "simule"
    # (verdicts factices, tests) ou "indisponible" (auto-évaluation).
    juge: Literal["reel", "simule", "indisponible"] = "indisponible"
    # Ordre réellement appliqué (« melange » ou « par_cours »).
    ordre: str = "melange"
    # Récaps des thèmes de la session qui en ont un (mode « par cours » seulement),
    # dans l'ordre des questions.
    recaps: list[RecapOut] = []
    questions: list[QuestionOut]


class ReponseIn(BaseModel):
    card_id: str
    rating: Literal[1, 2, 3, 4]
    correct: bool | None = None
    source: Literal["auto", "manuel", "juge"] = "auto"
    duration_ms: int | None = None
    jour: date | None = None


class JugerIn(BaseModel):
    """Réponse de l'élève à faire corriger par le juge (`libre_llm` ou `redaction`)."""

    card_id: str
    reponse: str = Field(min_length=1, max_length=20_000)
    # Langue du commentaire: celle de l'interface.
    langue: Literal["fr", "en"] = "fr"


class JugementOut(BaseModel):
    # Faux = pas de correction automatique (juge inactif, plafond, panne): la
    # `raison` l'explique et l'interface retombe sur l'auto-évaluation.
    disponible: bool
    etat: Literal["reel", "simule", "indisponible"]
    # Note FSRS suggérée (1..4): l'élève peut la changer avant de valider.
    note_suggeree: int | None = None
    verdict: Literal["juste", "partiel", "faux"] | None = None
    commentaire: str | None = None
    criteres: list[bool] | None = None
    raison: str | None = None


class SkipIn(BaseModel):
    card_id: str
    session_id: str
    raison: Literal["feuille", "redaction", "pas_le_temps"] | None = None
    jour: date | None = None


class SkipOut(BaseModel):
    nb_skips: int
    reviendra: bool


# --- Aujourd'hui / stats ---------------------------------------------------

class CoursCharge(BaseModel):
    cours: str
    couleur: str
    nb: int
    points: float = 0.0
    entretien: bool = False


class TodayOut(BaseModel):
    jour: date
    total: int
    total_dues: int
    nb_nouvelles: int
    nb_en_retard: int
    par_cours: list[CoursCharge]
    prochain_test: TestOut | None
    jours_avant_test: int | None
    # Points: ce que pèse la file du jour, ce qui est dû au total, le plafond.
    points: float = 0.0
    points_dus: float = 0.0
    plafond: float = 0.0
    # Entretien: part du jour consacrée aux thèmes sans test à venir.
    nb_entretien: int = 0
    points_entretien: float = 0.0
    part_entretien: float = 0.0
    themes_entretien: list[str] = Field(default_factory=list)
    alertes: list[str] = Field(default_factory=list)


class ChargeJour(BaseModel):
    jour: date
    nb: int
    points: float = 0.0
    nb_entretien: int = 0
    points_entretien: float = 0.0


class ExamStatOut(BaseModel):
    test: TestOut
    nb_cartes: int
    nb_jamais_revisees: int
    retention_estimee: float


class QuotaOut(BaseModel):
    theme: str
    quota: float
    fait_avant_aujourdhui: float
    planifie_fsrs: float
    manque: float


class StatsOut(BaseModel):
    jour: date
    total_cartes: int
    nb_dues: int
    nb_nouvelles: int
    nb_en_retard: int
    charge_14j: list[ChargeJour]
    tests: list[ExamStatOut]
    points_dus: float = 0.0
    points_jour: float = 0.0
    plafond: float = 0.0
    part_entretien: float = 0.0
    points_entretien_jour: float = 0.0
    nb_themes: int = 0
    themes_entretien: list[str] = Field(default_factory=list)
    quotas: list[QuotaOut] = Field(default_factory=list)
    alertes: list[str] = Field(default_factory=list)


# --- Bibliothèque ----------------------------------------------------------

class CarteOut(BaseModel):
    id: str
    concept_id: str
    cours: str
    type: str
    payload: dict[str, Any]
    desactivee: bool
    note_qualite: str | None
    nb_revisions: int
    stabilite: float | None
    source_ref: str | None = None
    confiance: Literal["haute", "moyenne", "basse"] = "moyenne"
    # Poids explicite (nullable), catégorie du concept, et poids résolu en points.
    poids: float | None = None
    categorie: str | None = None
    points: float = 0.0


class CartePatch(BaseModel):
    payload: dict[str, Any] | None = None
    desactivee: bool | None = None
    note_qualite: str | None = None
    cours: str | None = None
    # `poids: null` efface le poids explicite (retour au poids par catégorie/type).
    poids: float | None = Field(None, gt=0)


# --- Ingestion -------------------------------------------------------------

class ConceptOut(BaseModel):
    id: str = Field(max_length=100)
    titre: str = Field(max_length=500)
    categorie: Literal[
        "definition", "propriete", "formule", "methode", "fait",
        "vocabulaire", "exemple_type",
    ] = "definition"
    # Ces champs partent dans le prompt: leur taille est une dépense.
    resume: str = Field("", max_length=4000)
    source: str | None = Field(None, max_length=500)
    importance: Literal["essentiel", "important", "secondaire"] = "important"
    adapte_calcul: bool = False


class ExtractionTexteIn(BaseModel):
    cours: str = Field(max_length=200)
    # Borne de premier rempart: le plafond configurable est vérifié ensuite.
    texte: str = Field(max_length=200_000)


class ExtractionOut(BaseModel):
    concepts: list[ConceptOut]
    avertissements: list[str]
    source: str


class GenerationIn(BaseModel):
    cours: str = Field(max_length=200)
    source: str = Field(max_length=300)
    # 40 concepts = 5 appels au plus (voir `llm.MAX_CONCEPTS_PAR_GENERATION`).
    concepts: list[ConceptOut] = Field(max_length=40)
    # Référence le job d'extraction pour retrouver la matière côté serveur
    # (le nouveau prompt de génération la rejoint). Les chemins disque ne
    # transitent jamais par le navigateur.
    extraction_job_id: str | None = None


class CouvertureEntree(BaseModel):
    concept_id: str
    titre: str
    nb_questions: int


class GenerationOut(BaseModel):
    questions_creees: int
    echecs: list[dict[str, str]]
    avertissements: list[str]
    concepts_sans_question: list[str]
    details: list[CouvertureEntree]


class BudgetOut(BaseModel):
    cout_jour_usd: float
    cout_mois_usd: float
    appels_jour: int
    appels_mois: int
    plafond_jour_usd: float
    plafond_mois_usd: float
    plafond_operation_usd: float
    llm_desactive: bool
    # Coupé par LLM_DISABLED=1: l'interface ne peut pas le lever.
    arret_par_env: bool = False
    # Faux = mode simulé (aucune dépense possible).
    mode_reel: bool = False
    disjoncteur_ouvert: bool
    alertes: list[str]
    appels_evites: int
    refus: int


class ArretIn(BaseModel):
    coupe: bool


class EstimationOut(BaseModel):
    operation: str
    modele: str
    tokens_entree: int
    max_tokens_sortie: int
    cout_estime_usd: float


class JobOut(BaseModel):
    id: str
    type: str
    statut: Literal["en_cours", "termine", "echoue", "interrompu"]
    progression: int
    message: str | None = None
    resultat: dict[str, Any] | None = None


# --- Réglages --------------------------------------------------------------

class ReglagesOut(BaseModel):
    # Plafond quotidien en POINTS.
    plafond_quotidien: float
    retention_normale: float
    retention_test: float
    jours_phase_finale: int
    buffer_jours_avant_test: int
    entretien_quota_hebdo: float
    entretien_plafond_jour: float
    entretien_poids_max: float
    poids_type: dict[str, float]
    poids_categorie: dict[str, float]
    modele: str
    ordre_session: str
    mock_llm: bool
    juge_etat: Literal["reel", "simule", "indisponible"] = "indisponible"
    juge_modele: str = ""
    profils: list[str] = Field(default_factory=list)


class ReglagesPatch(BaseModel):
    plafond_quotidien: float | None = Field(None, gt=0, le=500)
    retention_normale: float | None = Field(None, gt=0.5, lt=1.0)
    retention_test: float | None = Field(None, gt=0.5, lt=1.0)
    jours_phase_finale: int | None = Field(None, ge=1, le=60)
    buffer_jours_avant_test: int | None = Field(None, ge=0, le=14)
    entretien_quota_hebdo: float | None = Field(None, ge=0, le=100)
    entretien_plafond_jour: float | None = Field(None, ge=0, le=100)
    entretien_poids_max: float | None = Field(None, gt=0, le=20)
    # Poids par type / par catégorie (points): seules les clés connues sont acceptées.
    poids_type: dict[str, float] | None = None
    poids_categorie: dict[str, float] | None = None
    modele: str | None = None
    ordre_session: Literal["melange", "par_cours"] | None = None


# --- Import de questions déjà générées (sans LLM) ---------------------------

class ImportApercuOut(BaseModel):
    """Ce que l'import ferait, sans rien écrire."""

    rapport: Rapport
    importable: bool
    nouvelles: int
    deja_en_base: int
    doublons_fichier: int


class ImportOut(BaseModel):
    importees: int
    deja_presentes: int
    doublons_contenu: int
    cours: list[str]
