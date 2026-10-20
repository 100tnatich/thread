"""Jeu de données de démonstration: l'interface est remplie dès le 1er lancement.

Aucun appel LLM: les questions sont écrites en dur mais respectent la
taxonomie fermée du prototype 1.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from fsrs import Rating

from . import api as engine
from .db import ensure_cours

COURS_DEMO = [
    ("physique", "#5CC8FF"),
    ("maths", "#8B7CFF"),
    ("informatique", "#4ADE9F"),
    ("allemand", "#F5B84A"),
    ("français", "#FF6B7A"),
]

# (cours, sujet, type, champs spécifiques): le sujet ne sert qu'à nommer le concept
QUESTIONS_DEMO: list[tuple[str, str, str, dict]] = [
    ("physique", "Loi d'Ohm", "probleme", {
        "enonce": "Un résistor de $R = 100\\ \\Omega$ est traversé par un courant "
                  "$I = 0{,}2\\ A$. Calcule la tension $U$ à ses bornes.",
        "solution_etapes": [
            "Identifier la formule: $U = R \\times I$",
            "Remplacer les valeurs: $U = 100 \\times 0{,}2$",
            "Calculer: $U = 20\\ V$",
        ],
        "reponse_finale": "$U = 20\\ V$",
        "necessite_dessin": False,
        "schema_svg": None,
    }),
    ("physique", "Loi d'Ohm", "exact", {
        "enonce": "Quelle est la formule de la résistance équivalente de deux "
                  "résistances en série ?",
        "reponse_exacte": "R_eq = R1 + R2",
    }),
    ("physique", "Loi d'Ohm", "qcm", {
        "enonce": "Dans la notation du prof, quelle lettre désigne la tension ?",
        "propositions": ["V", "U", "T", "E"],
        "bonnes_reponses": [1],
    }),
    ("physique", "Énergie", "cloze", {
        "texte": "L'énergie cinétique vaut {{c1::½mv²}} et l'énergie "
                 "potentielle de pesanteur vaut {{c2::mgh}}.",
        "valeurs_attendues": {"c1": "½mv²", "c2": "mgh"},
    }),
    ("maths", "Dérivées", "probleme", {
        "enonce": "Calcule la dérivée de $f(x) = 3x^2 + 5x - 2$.",
        "solution_etapes": [
            "Dériver terme à terme",
            "$(3x^2)' = 6x$",
            "$(5x)' = 5$, $(-2)' = 0$",
        ],
        "reponse_finale": "$f'(x) = 6x + 5$",
        "necessite_dessin": False,
        "schema_svg": None,
    }),
    ("maths", "Dérivées", "exact", {
        "enonce": "Quelle est la dérivée de $\\sin(x)$ ?",
        "reponse_exacte": "cos(x)",
    }),
    ("maths", "Trigonométrie", "libre_llm", {
        "enonce": "Explique à quoi sert le cercle trigonométrique.",
        "reponse_attendue": "Il permet de visualiser sinus et cosinus comme les "
                            "coordonnées d'un point sur un cercle de rayon 1, et "
                            "de lire les valeurs remarquables et les signes.",
    }),
    ("informatique", "Modèle OSI", "qcm", {
        "enonce": "À quelle couche du modèle OSI appartient le protocole IP ?",
        "propositions": ["Couche 2 — liaison", "Couche 3 — réseau",
                         "Couche 4 — transport", "Couche 7 — application"],
        "bonnes_reponses": [1],
    }),
    ("informatique", "Modèle OSI", "exact", {
        "enonce": "Combien de couches compte le modèle OSI ?",
        "reponse_exacte": "7",
    }),
    ("informatique", "Linux — droits", "cloze", {
        "texte": "La commande {{c1::chmod}} change les droits d'un fichier, "
                 "et {{c2::chown}} change son propriétaire.",
        "valeurs_attendues": {"c1": "chmod", "c2": "chown"},
    }),
    ("informatique", "Linux — droits", "probleme", {
        "enonce": "Quels droits donne `chmod 750 script.sh` ? Détaille par "
                  "catégorie d'utilisateur.",
        "solution_etapes": [
            "Décomposer: 7 = propriétaire, 5 = groupe, 0 = autres",
            "7 = 4+2+1 = lecture + écriture + exécution",
            "5 = 4+0+1 = lecture + exécution",
            "0 = aucun droit",
        ],
        "reponse_finale": "Propriétaire: rwx, groupe: r-x, autres: ---",
        "necessite_dessin": False,
        "schema_svg": None,
    }),
    ("allemand", "Vocabulaire 1", "exact", {
        "enonce": "Traduis en allemand: « l'ordinateur »",
        "reponse_exacte": "der Computer",
    }),
    ("allemand", "Vocabulaire 1", "exact", {
        "enonce": "Traduis en allemand: « le réseau »",
        "reponse_exacte": "das Netzwerk",
    }),
    ("allemand", "Grammaire — cas", "qcm", {
        "enonce": "Quel cas suit la préposition « mit » ?",
        "propositions": ["Nominatif", "Accusatif", "Datif", "Génitif"],
        "bonnes_reponses": [2],
    }),
    ("français", "Figures de style", "exact", {
        "enonce": "Comment s'appelle une comparaison sans outil de comparaison ?",
        "reponse_exacte": "une métaphore",
    }),
    ("français", "Figures de style", "libre_llm", {
        "enonce": "Explique la différence entre une métaphore et une comparaison.",
        "reponse_attendue": "La comparaison utilise un outil de comparaison "
                            "(comme, tel que, ressembler à), la métaphore "
                            "rapproche les deux termes sans outil.",
    }),
    ("français", "Argumentation", "redaction", {
        "consigne": "Rédige l'introduction d'une dissertation sur le thème: "
                    "« Le progrès technique rend-il l'humanité plus libre ? »",
        "contraintes": "150 mots environ. Amorce, problématique, annonce du plan.",
        "criteres": [
            "L'introduction contient une amorce",
            "La problématique est formulée sous forme de question",
            "Le plan est annoncé",
            "Le texte évite la première personne",
        ],
        "exemple_reference": "Depuis la révolution industrielle, chaque innovation "
                             "a promis de libérer l'homme de ses contraintes. "
                             "Le progrès technique rend-il pour autant l'humanité "
                             "plus libre ? Nous verrons d'abord les libertés qu'il "
                             "ouvre, puis les dépendances qu'il crée.",
    }),
    ("français", "Argumentation", "cloze", {
        "texte": "Un texte argumentatif défend une {{c1::thèse}} à l'aide "
                 "d'{{c2::arguments}} illustrés par des exemples.",
        "valeurs_attendues": {"c1": "thèse", "c2": "arguments"},
    }),
]


def seed_demo(conn: sqlite3.Connection, aujourdhui: date | None = None) -> dict:
    """Crée cours, questions et tests de démonstration. Idempotent."""
    if conn.execute("SELECT 1 FROM cards LIMIT 1").fetchone():
        return {"deja_rempli": True}

    aujourdhui = aujourdhui or datetime.now(timezone.utc).date()
    maintenant = datetime.combine(aujourdhui, datetime.min.time(), tzinfo=timezone.utc)

    for nom, couleur in COURS_DEMO:
        ensure_cours(conn, nom)
        conn.execute("UPDATE cours SET couleur = ? WHERE nom = ?", (couleur, nom))

    # Deux matières pour montrer la hiérarchie matière > thème.
    engine.add_matiere(conn, "Sciences", "#5CC8FF")
    engine.add_matiere(conn, "Langues", "#F5B84A")
    for theme, matiere in (("physique", "Sciences"), ("maths", "Sciences"), ("allemand", "Langues")):
        engine.update_cours(conn, theme, matiere=matiere)

    ids = []
    for cours, sujet, qtype, champs in QUESTIONS_DEMO:
        card_id = str(uuid4())
        payload = {
            "type": qtype,
            "id": card_id,
            "concept_id": f"{sujet.lower().replace(' ', '-')}-{len(ids)}",
            "cours": cours,
            "source": {"fichier": "demo", "page": None},
            **champs,
        }
        # Le vocabulaire pèse 0,1 point (voir config.toml), comme le ferait un
        # import avec la liste de concepts.
        categorie = "vocabulaire" if sujet.lower().startswith("vocabulaire") else None
        conn.execute(
            "INSERT INTO cards (id, concept_id, cours, type, payload,"
            " source_fichier, source_page, imported_at, categorie)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (card_id, payload["concept_id"], cours, qtype,
             json.dumps(payload, ensure_ascii=False), "demo", None,
             maintenant.isoformat(), categorie),
        )
        ids.append(card_id)
    conn.commit()

    tests = [
        ("physique", 6, "TE — Loi d'Ohm"),
        ("maths", 13, "TE — Dérivées"),
        ("informatique", 24, "Examen réseaux"),
        ("allemand", 33, "Vocabulaire allemand"),
    ]
    for cours, offset, intitule in tests:
        engine.add_test(
            conn, cours=cours, jour=aujourdhui + timedelta(days=offset), intitule=intitule,
        )

    # Historique partiel: quelques cartes déjà révisées, pour que le tableau
    # de bord et les statistiques ne soient pas vides au premier lancement.
    from .config import SchedulerConfig, load_scheduler_config

    config: SchedulerConfig = load_scheduler_config()
    for i, card_id in enumerate(ids[:6]):
        engine.record_answer(
            conn, card_id,
            Rating.Good if i % 3 else Rating.Hard,
            reviewed_at=maintenant - timedelta(days=3),
            config=config, correct=True, source="auto",
        )

    return {
        "cours": len(COURS_DEMO),
        "questions": len(ids),
        "tests": len(tests),
        "deja_rempli": False,
    }
