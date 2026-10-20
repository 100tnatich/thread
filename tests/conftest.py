from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from flashcards.config import SchedulerConfig
from flashcards.db import ensure_cours, open_db

TODAY = date(2026, 1, 5)
NOW = datetime(2026, 1, 5, tzinfo=timezone.utc)


VARIABLES_BUDGET = (
    "ANTHROPIC_API_KEY", "FLASHCARDS_LLM_REEL", "LLM_DISABLED",
    "FLASHCARDS_JUGE_MODELE",
    "FLASHCARDS_PLAFONDS_ELEVES", "FLASHCARDS_COUT_MAX_JOUR",
    "FLASHCARDS_COUT_MAX_MOIS", "FLASHCARDS_COUT_MAX_OPERATION",
    "FLASHCARDS_APPELS_MAX_MINUTE",
)


@pytest.fixture(autouse=True)
def _jamais_de_vraie_api(monkeypatch):
    """Règle n°1: aucun test ne consomme de crédit, quel que soit le shell.

    Trois verrous indépendants, posés pour CHAQUE test: mode simulé forcé,
    clé et activation du mode réel retirées de l'environnement, et client du
    SDK remplacé par un piège. Un test qui veut exercer le chemin réel doit
    installer son propre faux client — il ne peut pas atteindre le réseau.
    """
    monkeypatch.setenv("MOCK_LLM", "1")
    for nom in VARIABLES_BUDGET:
        monkeypatch.delenv(nom, raising=False)

    import anthropic
    import httpx

    def piege(*_args, **_kwargs):
        raise AssertionError("un test a tenté d'ouvrir un vrai client Anthropic")

    monkeypatch.setattr(anthropic, "Anthropic", piege)

    # Juge OpenRouter: même principe. La clé (mixte-casse comme dans .env, ou non)
    # est vidée et l'activation coupée — posées À VALEUR EXPLICITE, par prudence
    # (l'appli ne lit pas .env aujourd'hui, mais `load_dotenv` ne remplace jamais une
    # variable déjà posée) — et le transport HTTP est un piège qui lève sur toute requête. Un test du
    # chemin réel installe son propre `httpx.MockTransport`.
    for nom in ("OPENROUTER_API_KEY", "OpenRouter_API_KEY"):
        monkeypatch.setenv(nom, "")
    monkeypatch.setenv("FLASHCARDS_JUGE_REEL", "0")

    def requete_interdite(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"un test a tenté une vraie requête réseau: {request.url}")

    from flashcards import llm

    monkeypatch.setattr(llm, "_TRANSPORT", httpx.MockTransport(requete_interdite))


@pytest.fixture
def config(tmp_path: Path) -> SchedulerConfig:
    return SchedulerConfig(
        db_path=tmp_path / "test.db",
        retention_normale=0.90,
        retention_test=0.97,
        jours_phase_finale=10,
        buffer_jours_avant_test=1,
        plafond_quotidien=40,
    )


@pytest.fixture
def conn(config: SchedulerConfig):
    connection = open_db(config.db_path)
    yield connection
    connection.close()


def make_card(
    conn,
    card_id: str,
    *,
    cours: str = "physique",
    qtype: str = "exact",
    stability: float | None = None,
    difficulty: float | None = None,
    last_review: datetime | None = None,
    state: int = 1,
    poids: float | None = 1.0,
    categorie: str | None = None,
) -> str:
    """Insère une carte avec un état FSRS arbitraire (tests déterministes).

    `poids=1.0` par défaut: chaque carte vaut 1 point, donc « plafond = N
    points » se lit « N cartes » dans les tests de mécanique de file. Les tests
    des poids passent `poids=None` pour tester la résolution par catégorie/type.
    """
    ensure_cours(conn, cours)
    payload = {
        "type": qtype,
        "id": card_id,
        "concept_id": f"concept-{card_id}",
        "cours": cours,
        "source": {"fichier": "test.pdf", "page": None},
        "enonce": f"Question {card_id}?",
        "reponse_exacte": "42",
    }
    conn.execute(
        "INSERT INTO cards (id, concept_id, cours, type, payload,"
        " source_fichier, source_page, imported_at, fsrs_state, fsrs_stability,"
        " fsrs_difficulty, last_review, poids, categorie)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            card_id,
            f"concept-{card_id}",
            cours,
            qtype,
            json.dumps(payload),
            "test.pdf",
            None,
            NOW.isoformat(),
            state,
            stability,
            difficulty,
            last_review.isoformat() if last_review else None,
            poids,
            categorie,
        ),
    )
    conn.commit()
    return card_id


def write_questions_file(path: Path, questions: list[dict]) -> Path:
    path.write_text(
        json.dumps(
            {
                "source": "cours.pdf",
                "cours": "physique",
                "questions": questions,
                "echecs": [],
                "avertissements": [],
            },
            ensure_ascii=False,
        )
    )
    return path


def question_dict(qid: str) -> dict:
    return {
        "type": "exact",
        "id": qid,
        "concept_id": f"concept-{qid}",
        "cours": "physique",
        "source": {"fichier": "cours.pdf", "page": 1},
        # Le contenu doit différer d'une question à l'autre: deux contenus
        # identiques sont des doublons que l'import ignore volontairement.
        "enonce": f"Formule de la loi d'Ohm? ({qid})",
        "reponse_exacte": "U = R x I",
    }


@pytest.fixture
def pas_de_llm(monkeypatch):
    """Garde-fou du workflow manuel: tout appel au wrapper LLM fait échouer le test."""
    from flashcards import llm

    def interdit(*args, **kwargs):
        raise AssertionError("appel LLM interdit dans le workflow manuel")

    for nom in ("_appel_structure", "extraire_concepts", "generer_questions"):
        monkeypatch.setattr(llm, nom, interdit)
