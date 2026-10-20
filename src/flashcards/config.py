"""Chargement de la config (config.toml) et de la clé API (.env)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PROJECT_ROOT / "prompts"
LOGS_DIR = PROJECT_ROOT / "logs"
USAGE_LOG = LOGS_DIR / "usage.jsonl"


@dataclass(frozen=True)
class Pricing:
    input_per_mtok: float
    output_per_mtok: float


# Poids par défaut (points). Doublés dans config.toml, qui prime: ils servent
# aux configs construites à la main (tests) et de repli si une clé manque.
POIDS_TYPE_DEFAUT = {
    "exact": 0.5, "cloze": 0.5, "qcm": 0.5,
    "libre_llm": 1.0, "probleme": 2.0, "redaction": 4.0,
}
POIDS_CATEGORIE_DEFAUT = {"vocabulaire": 0.1}


@dataclass(frozen=True)
class SchedulerConfig:
    db_path: Path
    retention_normale: float
    retention_test: float
    jours_phase_finale: int
    buffer_jours_avant_test: int
    # En POINTS (une carte pèse 0,1 à 4 points selon son type).
    plafond_quotidien: float
    poids_type: dict[str, float] = field(default_factory=lambda: dict(POIDS_TYPE_DEFAUT))
    poids_categorie: dict[str, float] = field(
        default_factory=lambda: dict(POIDS_CATEGORIE_DEFAUT)
    )
    # Mode entretien (points).
    entretien_quota_hebdo: float = 3.0
    entretien_plafond_jour: float = 8.0
    entretien_poids_max: float = 2.0
    # Répartition entre thèmes en mode test: un thème dont le prochain test est à
    # au plus ce nombre de jours (0 = le jour même) n'est pas plafonné à sa part.
    jours_focus_test: int = 2


@dataclass(frozen=True)
class Config:
    model_name: str
    max_tokens: int
    pricing: Pricing
    scheduler: SchedulerConfig
    budget_brut: dict


def load_config(path: Path | None = None) -> Config:
    path = path or (PROJECT_ROOT / "config.toml")
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    sched = raw["scheduler"]
    # FLASHCARDS_DB prime sur config.toml: c'est ce qui permet au conteneur
    # Docker et aux tests de viser une base jetable. Centralisé ici pour que
    # le CLI et l'API web ne puissent pas diverger.
    import os

    db_path = Path(os.environ.get("FLASHCARDS_DB") or sched["db_path"])
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    return Config(
        model_name=raw["model"]["name"],
        max_tokens=raw["model"]["max_tokens"],
        pricing=Pricing(
            input_per_mtok=raw["pricing"]["input_per_mtok"],
            output_per_mtok=raw["pricing"]["output_per_mtok"],
        ),
        budget_brut=raw.get("budget", {}),
        scheduler=SchedulerConfig(
            db_path=db_path,
            retention_normale=sched["retention_normale"],
            retention_test=sched["retention_test"],
            jours_phase_finale=sched["jours_phase_finale"],
            buffer_jours_avant_test=sched["buffer_jours_avant_test"],
            plafond_quotidien=sched["plafond_quotidien"],
            poids_type={**POIDS_TYPE_DEFAUT, **sched.get("poids_type", {})},
            poids_categorie={**POIDS_CATEGORIE_DEFAUT, **sched.get("poids_categorie", {})},
            entretien_quota_hebdo=sched.get("entretien_quota_hebdo", 3.0),
            entretien_plafond_jour=sched.get("entretien_plafond_jour", 8.0),
            entretien_poids_max=sched.get("entretien_poids_max", 2.0),
            jours_focus_test=sched.get("jours_focus_test", 2),
        ),
    )


def load_scheduler_config(path: Path | None = None) -> SchedulerConfig:
    return load_config(path).scheduler


def load_api_key() -> str:
    load_dotenv(PROJECT_ROOT / ".env")
    import os

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY manquante. Copie .env.example vers .env et "
            "renseigne ta clé."
        )
    return key
