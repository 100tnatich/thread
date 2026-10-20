"""Simulateur: vérifie que le planning se comporte correctement sans LLM.

Un «élève virtuel» répond pendant N jours. Sa probabilité de répondre juste
est la rétrivabilité prédite par FSRS pour la carte au moment de la révision
(un élève réaliste oublie d'autant plus qu'il a attendu), avec un plancher et
un plafond pour éviter un élève parfait ou désespéré.

Deux scénarios:
- `simple`: 5 cours, 4 examens, cartes de types aléatoires (le scénario d'origine);
- `annee`: une année scolaire — 4 matières, 20 thèmes dont 12 en mode entretien
  (leurs tests sont passés), un examen de fin d'année futur lié à TOUTE une
  matière, 300 cartes de vocabulaire, des cartes de calcul et de rédaction, des
  lots de cartes injectés au fil de l'eau, et un nouveau test qui sort un thème de
  l'entretien en cours de route. Le rapport est en points ET en cartes, avec
  une détection d'anomalies.
"""
from __future__ import annotations

import json
import random
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from fsrs import Rating

from . import api
from .config import SchedulerConfig
from .db import ensure_cours, open_db
from .fsrs_engine import retrievability_at
from .planning import bornes_semaine

COURS = {
    "physique": ["cinematique", "loi-ohm", "energie", "optique"],
    "maths": ["fonctions", "derivees", "trigonometrie", "vecteurs"],
    "allemand": ["vocabulaire-1", "vocabulaire-2", "grammaire-cas"],
    "informatique": ["reseaux-osi", "linux-droits", "sql-jointures"],
    "francais": ["figures-style", "argumentation"],
}

# Un élève ni parfait ni nul: même une carte fraîchement revue peut être
# ratée, et une carte très oubliée reste parfois devinée.
P_MIN = 0.05
P_MAX = 0.97


@dataclass
class SimConfig:
    jours: int = 90
    nb_cartes: int = 320
    graine: int = 42
    # Probabilité de sauter une journée (fatigue, travail, etc.).
    p_saut: float = 0.12
    # Saut forcé de 3 jours consécutifs, pour mesurer l'effet de la dette.
    saut_force_debut: int | None = 30
    scenario: str = "simple"  # « simple » ou « annee »


@dataclass
class SimResult:
    # Charge en POINTS; les cartes sont dans `cartes_par_jour`.
    charge_par_jour: list[tuple[date, float]]
    dette_par_jour: list[tuple[date, int]]
    jours_sautes: list[date]
    examens: list[dict]
    charge_min: float
    charge_moyenne: float
    charge_max: float
    total_revisions: int
    taux_reussite: float
    effet_saut_force: dict = field(default_factory=dict)
    cartes_par_jour: list[tuple[date, int]] = field(default_factory=list)
    dette_points_par_jour: list[tuple[date, float]] = field(default_factory=list)
    # Part de la dette qui est de l'entretien (cartes de thèmes sans test à venir).
    dette_entretien_points_par_jour: list[tuple[date, float]] = field(default_factory=list)
    entretien_points_par_jour: list[tuple[date, float]] = field(default_factory=list)
    entretien_cartes_par_jour: list[tuple[date, int]] = field(default_factory=list)
    nb_themes_entretien_par_jour: list[tuple[date, int]] = field(default_factory=list)
    points_total: float = 0.0
    plafond: float = 0.0
    rapport_entretien: dict = field(default_factory=dict)
    rapport_charge: dict = field(default_factory=dict)
    anomalies: list[str] = field(default_factory=list)
    resume_scenario: dict = field(default_factory=dict)


def seed_fake_cards(
    conn: sqlite3.Connection,
    nb_cartes: int,
    rng: random.Random,
    now: datetime,
) -> int:
    """Crée des cartes factices (aucun appel LLM) avec des payloads valides."""
    types = ["qcm", "exact", "cloze", "probleme", "libre_llm", "redaction"]
    cree = 0
    for i in range(nb_cartes):
        cours = rng.choice(list(COURS))
        sujet = rng.choice(COURS[cours])
        qtype = rng.choice(types)
        # Id dérivé du rng (et non uuid4) pour que la simulation soit
        # reproductible: l'id pilote l'étalement des échéances pré-examen.
        card_id = str(UUID(bytes=rng.randbytes(16), version=4))
        payload = _fake_payload(qtype, card_id, cours, sujet, i)
        ensure_cours(conn, cours)
        conn.execute(
            "INSERT INTO cards (id, concept_id, cours, type, payload,"
            " source_fichier, source_page, imported_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                card_id,
                f"{sujet}-c{i}",
                cours,
                qtype,
                json.dumps(payload, ensure_ascii=False),
                "simulation.pdf",
                None,
                now.isoformat(),
            ),
        )
        cree += 1
    conn.commit()
    return cree


def _fake_payload(qtype: str, card_id: str, cours: str, sujet: str, i: int) -> dict:
    commun = {
        "type": qtype,
        "id": card_id,
        "concept_id": f"{sujet}-c{i}",
        "cours": cours,
        "source": {"fichier": "simulation.pdf", "page": None},
    }
    match qtype:
        case "qcm":
            return {
                **commun,
                "enonce": f"[sim] Question {i} sur {sujet}?",
                "propositions": ["A", "B", "C"],
                "bonnes_reponses": [i % 3],
            }
        case "exact":
            return {**commun, "enonce": f"[sim] Terme {i} de {sujet}?", "reponse_exacte": f"rep{i}"}
        case "cloze":
            return {
                **commun,
                "texte": f"[sim] {sujet}: {{{{c1::valeur{i}}}}}.",
                "valeurs_attendues": {"c1": f"valeur{i}"},
            }
        case "probleme":
            return {
                **commun,
                "enonce": f"[sim] Calcule l'exercice {i} de {sujet}.",
                "solution_etapes": ["étape 1", "étape 2"],
                "reponse_finale": f"{i}",
                "necessite_dessin": False,
                "schema_svg": None,
            }
        case "libre_llm":
            return {
                **commun,
                "enonce": f"[sim] Explique le point {i} de {sujet}.",
                "reponse_attendue": f"explication {i}",
            }
        case "redaction":
            return {
                **commun,
                "consigne": f"[sim] Rédige un texte appliquant le point {i} de {sujet}.",
                "contraintes": "150 mots, trois paragraphes.",
                "criteres": [
                    "Le propos est annoncé",
                    "Chaque paragraphe développe une idée",
                    "La conclusion répond à la consigne",
                    "Le registre est adapté",
                ],
                "exemple_reference": f"[sim] Texte modèle {i}.",
            }
    raise AssertionError(qtype)


def seed_fake_exams(conn: sqlite3.Connection, start: date) -> list[int]:
    """4 examens à des dates différentes, sur des cours différentes."""
    plan = [
        ("physique", 21, "Travail écrit physique"),
        ("maths", 35, "TE maths — dérivées"),
        ("allemand", 50, "Vocabulaire allemand"),
        ("informatique", 75, "Examen réseaux"),
    ]
    ids = []
    for cours, offset, intitule in plan:
        ids.append(
            api.add_test(
                conn,
                cours=cours,
                jour=start + timedelta(days=offset),
                intitule=intitule,
            )
        )
    return ids


# --- Scénario « année » -------------------------------------------------------

# (type, catégorie du concept, nombre de cartes)
Mix = tuple[tuple[str, str | None, int], ...]


@dataclass(frozen=True)
class LotCartes:
    """Un lot de cartes injecté au jour `offset` (la matière arrive au fil de l'eau)."""

    offset: int
    sujet: str
    mix: Mix


@dataclass(frozen=True)
class ThemeSim:
    matiere: str
    nom: str
    # En entretien dès le départ: ses tests sont passés, ses cartes ont un historique.
    entretien: bool
    lots: tuple[LotCartes, ...]
    # Test futur propre au thème: (jour du test, intitulé).
    test: tuple[int, str] | None = None


def _calcul() -> Mix:
    return (("probleme", None, 8), ("exact", None, 4), ("cloze", None, 4),
            ("qcm", None, 3), ("libre_llm", None, 3))


def _redaction() -> Mix:
    return (("redaction", None, 4), ("libre_llm", None, 6), ("exact", None, 3), ("cloze", None, 3))


def _general() -> Mix:
    return (("exact", None, 5), ("cloze", None, 6), ("qcm", None, 5),
            ("libre_llm", None, 4), ("probleme", None, 2))


def scenario_annee() -> list[ThemeSim]:
    """20 thèmes, 4 matières; 12 thèmes en entretien; 300 cartes de vocabulaire."""
    def lots(nom: str, mix: Mix, offsets=(0,)) -> tuple[LotCartes, ...]:
        return tuple(LotCartes(o, f"{nom} — ch{i + 1}", mix) for i, o in enumerate(offsets))

    vocab = lambda n: (("exact", "vocabulaire", n),)  # noqa: E731
    return [
        # Anglais: examen de fin d'année lié à TOUTE la matière (ajouté plus bas).
        ThemeSim("Anglais", "Anglais conditionnel", False, lots("Anglais conditionnel", _general(), (0, 14)),
                 test=(30, "Test conditionnel")),
        ThemeSim("Anglais", "English present perfect", False, lots("English present perfect", _general(), (0, 20))),
        ThemeSim("Anglais", "English vocabulary unit 1", False, lots("English vocabulary unit 1", vocab(100))),
        ThemeSim("Anglais", "English vocabulary unit 2", False, lots("English vocabulary unit 2", vocab(100), (21,))),
        # Maths: 4 thèmes terminés, 1 en cours.
        ThemeSim("Maths", "Maths fonctions", True, lots("Maths fonctions", _calcul())),
        ThemeSim("Maths", "Maths dérivées", True, lots("Maths dérivées", _calcul())),
        ThemeSim("Maths", "Maths trigonométrie", True, lots("Maths trigonométrie", _calcul())),
        ThemeSim("Maths", "Maths vecteurs", True, lots("Maths vecteurs", _calcul())),
        ThemeSim("Maths", "Maths probabilités", False, lots("Maths probabilités", _calcul(), (0, 10)),
                 test=(25, "TE probabilités")),
        # Informatique: 4 terminés, 2 en cours.
        ThemeSim("Informatique", "Info réseaux", True, lots("Info réseaux", _general())),
        ThemeSim("Informatique", "Info linux", True, lots("Info linux", _general())),
        ThemeSim("Informatique", "Info sql", True, lots("Info sql", _general())),
        ThemeSim("Informatique", "Info programmation", True, lots("Info programmation", _general())),
        ThemeSim("Informatique", "Info sécurité", False, lots("Info sécurité", _general(), (0, 12)),
                 test=(45, "Examen sécurité")),
        ThemeSim("Informatique", "Info virtualisation", False, lots("Info virtualisation", _general(), (15, 30)),
                 test=(70, "Examen virtualisation")),
        # Français: 4 terminés (rédaction), dont un riche en vocabulaire; 1 en cours.
        ThemeSim("Français", "Fr argumentation", True, lots("Fr argumentation", _redaction())),
        ThemeSim("Français", "Fr figures de style", True,
                 (LotCartes(0, "Fr figures de style — ch1", _redaction() + vocab(100)),)),
        ThemeSim("Français", "Fr dissertation", True, lots("Fr dissertation", _redaction())),
        ThemeSim("Français", "Fr résumé", True, lots("Fr résumé", _redaction())),
        ThemeSim("Français", "Fr analyse de texte", False, lots("Fr analyse de texte", _redaction(), (0, 18)),
                 test=(60, "TE analyse")),
    ]


# Jours (depuis le départ) de l'examen de fin d'année Anglais et du test tardif
# qui sort « Info linux » de l'entretien.
JOUR_EXAMEN_ANGLAIS = 100
JOUR_NOUVEAU_TEST_LINUX = 40
JOUR_TEST_LINUX = 55


def _inserer_cartes(
    conn: sqlite3.Connection,
    rng: random.Random,
    now: datetime,
    theme: ThemeSim,
    lot: LotCartes,
    compteur: int,
    *,
    historique: bool,
) -> int:
    """Insère les cartes d'un lot. `historique`: thème déjà étudié (entretien)."""
    cree = 0
    for qtype, categorie, n in lot.mix:
        for _ in range(n):
            i = compteur + cree
            card_id = str(UUID(bytes=rng.randbytes(16), version=4))
            payload = _fake_payload(qtype, card_id, theme.nom, lot.sujet, i)
            etat = (1, None, None, None)
            if historique:
                # Carte déjà apprise: état FSRS plausible, dernière révision
                # étalée sur la période (le thème a été passé il y a un moment).
                stabilite = rng.uniform(8.0, 60.0)
                vue = now - timedelta(days=rng.randint(1, int(stabilite * 1.2)))
                etat = (2, stabilite, rng.uniform(3.0, 7.0), vue.isoformat())
            conn.execute(
                "INSERT INTO cards (id, concept_id, cours, type, payload,"
                " source_fichier, source_page, imported_at, categorie,"
                " fsrs_state, fsrs_stability, fsrs_difficulty, last_review)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    card_id, f"{lot.sujet}-c{i}", theme.nom, qtype,
                    json.dumps(payload, ensure_ascii=False), "simulation.pdf", None,
                    now.isoformat(), categorie, *etat,
                ),
            )
            cree += 1
    conn.commit()
    return cree


def seed_scenario_annee(conn: sqlite3.Connection, rng: random.Random, start: date) -> dict:
    """Matières, thèmes, tests (passés et futurs) et cartes initiales du scénario."""
    now = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    themes = scenario_annee()
    couleurs = {"Anglais": "#5CC8FF", "Maths": "#8B7CFF", "Informatique": "#4ADE9F", "Français": "#F5B84A"}
    for matiere, couleur in couleurs.items():
        api.add_matiere(conn, matiere, couleur)

    compteur = 0
    for theme in themes:
        api.add_cours(conn, theme.nom, matiere=theme.matiere)
        for lot in theme.lots:
            if lot.offset == 0:
                compteur += _inserer_cartes(
                    conn, rng, now, theme, lot, compteur, historique=theme.entretien
                )
        if theme.entretien:
            # Thème « passé »: un test dans le passé, lié à lui seul.
            api.add_test(
                conn, cours=theme.nom, jour=start - timedelta(days=rng.randint(5, 60)),
                intitule=f"Test passé — {theme.nom}",
            )
        elif theme.test:
            jour, intitule = theme.test
            api.add_test(conn, cours=theme.nom, jour=start + timedelta(days=jour), intitule=intitule)

    api.add_test(
        conn, matieres=["Anglais"], jour=start + timedelta(days=JOUR_EXAMEN_ANGLAIS),
        intitule="Examen de fin d'année — Anglais (toute la matière)",
    )
    return {"themes": len(themes), "cartes": compteur}


def evenements_du_jour(
    conn: sqlite3.Connection, rng: random.Random, start: date, offset: int, compteur: int
) -> int:
    """Lots de cartes injectés et tests ajoutés en cours de route. Renvoie le nb de cartes créées."""
    now = datetime.combine(start + timedelta(days=offset), datetime.min.time(), tzinfo=timezone.utc)
    cree = 0
    for theme in scenario_annee():
        for lot in theme.lots:
            if lot.offset == offset and offset > 0:
                cree += _inserer_cartes(
                    conn, rng, now, theme, lot, compteur + cree, historique=False
                )
    if offset == JOUR_NOUVEAU_TEST_LINUX:
        # Un thème en entretien reçoit un test futur: il repasse en mode test.
        api.add_test(
            conn, cours="Info linux", jour=start + timedelta(days=JOUR_TEST_LINUX),
            intitule="Test linux (annoncé tardivement)",
        )
    return cree


def _p_correct(card, moment: datetime) -> float:
    if card.last_review is None:
        # Première rencontre: l'élève ne connaît pas encore la carte.
        return 0.35
    return min(P_MAX, max(P_MIN, retrievability_at(card, moment)))


def _rating_eleve(correct: bool, rng: random.Random) -> Rating:
    if not correct:
        return Rating.Again
    # Un élève réel ne clique pas toujours "Good": un peu de Hard/Easy.
    return rng.choices([Rating.Hard, Rating.Good, Rating.Easy], weights=[0.2, 0.65, 0.15])[0]


def run_simulation(
    conn: sqlite3.Connection,
    config: SchedulerConfig,
    sim: SimConfig,
    start: date,
) -> SimResult:
    rng = random.Random(sim.graine)
    rng_scenario = random.Random(sim.graine + 2)  # injections en cours de route: flux distinct de celui du départ (sinon mêmes id)

    charge: list[tuple[date, float]] = []
    cartes_j: list[tuple[date, int]] = []
    dette: list[tuple[date, int]] = []
    dette_pts: list[tuple[date, float]] = []
    dette_ent: list[tuple[date, float]] = []
    ent_pts: list[tuple[date, float]] = []
    ent_cartes: list[tuple[date, int]] = []
    nb_themes_ent: list[tuple[date, int]] = []
    sautes: list[date] = []
    total = 0
    justes = 0
    points_total = 0.0
    entretien_par_semaine: dict[tuple[str, date], float] = {}
    jours_entretien: dict[str, set[date]] = {}
    themes_par_jour: dict[date, frozenset[str]] = {}
    max_carte = 0.0
    compteur = conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]

    saut_force = (
        range(sim.saut_force_debut, sim.saut_force_debut + 3)
        if sim.saut_force_debut is not None
        else range(0)
    )

    for offset in range(sim.jours):
        jour = start + timedelta(days=offset)
        moment = datetime.combine(jour, datetime.min.time(), tzinfo=timezone.utc)

        if sim.scenario == "annee" and offset > 0:
            compteur += evenements_du_jour(conn, rng_scenario, start, offset, compteur)

        cards = api.load_cards(conn)
        exams = api.load_exams(conn)
        plan = api.construire_file(cards, exams, jour, config)
        dette.append((jour, len(plan.dus)))
        dette_pts.append((jour, plan.points_dus))
        dette_ent.append((jour, sum(i.points for i in plan.dus if i.entretien)))
        themes_par_jour[jour] = plan.themes_entretien
        nb_themes_ent.append((jour, len(plan.themes_entretien)))

        if offset in saut_force or rng.random() < sim.p_saut:
            sautes.append(jour)
            charge.append((jour, 0.0))
            cartes_j.append((jour, 0))
            ent_pts.append((jour, 0.0))
            ent_cartes.append((jour, 0))
            continue

        file_du_jour = plan.items
        charge.append((jour, plan.points))
        cartes_j.append((jour, len(file_du_jour)))
        ent_pts.append((jour, plan.points_entretien))
        ent_cartes.append((jour, plan.nb_entretien))
        lundi, _ = bornes_semaine(jour)

        for item in file_du_jour:
            p = _p_correct(item.card.card, moment)
            correct = rng.random() < p
            rating = _rating_eleve(correct, rng)
            api.record_answer(
                conn,
                item.card.id,
                rating,
                reviewed_at=moment,
                config=config,
                correct=correct,
                source="simulation",
            )
            total += 1
            justes += int(correct)
            points_total += item.points
            max_carte = max(max_carte, item.points)
            if item.entretien:
                cle = (item.card.cours, lundi)
                entretien_par_semaine[cle] = entretien_par_semaine.get(cle, 0.0) + item.points
                jours_entretien.setdefault(item.card.cours, set()).add(jour)

    charges = [n for _, n in charge]
    result = SimResult(
        charge_par_jour=charge,
        dette_par_jour=dette,
        jours_sautes=sautes,
        examens=_exam_report(conn, config, start, sim),
        charge_min=min(charges),
        charge_moyenne=sum(charges) / len(charges),
        charge_max=max(charges),
        total_revisions=total,
        taux_reussite=(justes / total) if total else 0.0,
        effet_saut_force=_effet_saut(dette_pts, charge, sim),
        cartes_par_jour=cartes_j,
        dette_points_par_jour=dette_pts,
        dette_entretien_points_par_jour=dette_ent,
        entretien_points_par_jour=ent_pts,
        entretien_cartes_par_jour=ent_cartes,
        nb_themes_entretien_par_jour=nb_themes_ent,
        points_total=points_total,
        plafond=config.plafond_quotidien,
    )
    result.rapport_charge = _rapport_charge(result, max_carte)
    result.rapport_entretien = _rapport_entretien(
        result, config, entretien_par_semaine, jours_entretien, themes_par_jour, start
    )
    result.resume_scenario = _resume_scenario(conn, config, start)
    result.anomalies = _anomalies(result, config)
    return result


def _centile(valeurs: list[float], q: float) -> float:
    ordre = sorted(valeurs)
    return ordre[min(len(ordre) - 1, int(q * len(ordre)))] if ordre else 0.0


def _rapport_charge(r: SimResult, max_carte: float) -> dict:
    """Charge quotidienne en points ET en cartes (jours actifs, hors jours sautés)."""
    pts = [n for (j, n) in r.charge_par_jour if j not in set(r.jours_sautes)]
    cartes = [n for (j, n) in r.cartes_par_jour if j not in set(r.jours_sautes)]
    if not pts:
        return {}
    return {
        "points_moyenne": statistics.mean(pts),
        "points_mediane": statistics.median(pts),
        "points_p95": _centile(pts, 0.95),
        "points_max": max(pts),
        "cartes_moyenne": statistics.mean(cartes),
        "cartes_max": max(cartes),
        "jours_au_plafond": sum(1 for n in pts if n >= r.plafond - 0.5),
        "jours_actifs": len(pts),
        "points_par_carte_moyen": (sum(pts) / sum(cartes)) if sum(cartes) else 0.0,
        "plus_lourde_carte": max_carte,
    }


def _resume_scenario(conn: sqlite3.Connection, config: SchedulerConfig, start: date) -> dict:
    cartes = api.load_cards(conn)
    par_type: dict[str, int] = {}
    vocabulaire = 0
    for c in cartes:
        par_type[c.type] = par_type.get(c.type, 0) + 1
        vocabulaire += int(c.categorie == "vocabulaire")
    return {
        "themes": conn.execute("SELECT COUNT(*) AS n FROM cours").fetchone()["n"],
        "matieres": conn.execute("SELECT COUNT(*) AS n FROM matieres").fetchone()["n"],
        "cartes": len(cartes),
        "vocabulaire": vocabulaire,
        "par_type": par_type,
        "points_total_cartes": sum(c.points(config) for c in cartes),
    }


def _rapport_entretien(
    r: SimResult,
    config: SchedulerConfig,
    par_semaine: dict[tuple[str, date], float],
    jours_entretien: dict[str, set[date]],
    themes_par_jour: dict[date, frozenset[str]],
    start: date,
) -> dict:
    """Quota hebdomadaire tenu? Plafond d'entretien respecté? Rotation équitable?"""
    if not any(themes_par_jour.values()):
        return {}
    sautes = set(r.jours_sautes)
    dernier = max(themes_par_jour)

    # Semaines complètes (lundi→dimanche) entièrement dans la simulation et où
    # le thème est en entretien chaque jour.
    semaines_pleines: list[tuple[str, date]] = []
    toutes = {t for ts in themes_par_jour.values() for t in ts}
    lundis = sorted({bornes_semaine(j)[0] for j in themes_par_jour})
    for lundi in lundis:
        jours = [lundi + timedelta(days=k) for k in range(7)]
        if any(j not in themes_par_jour for j in jours) or jours[-1] > dernier:
            continue
        for theme in toutes:
            if all(theme in themes_par_jour[j] for j in jours):
                semaines_pleines.append((theme, lundi))

    faits = [par_semaine.get(cle, 0.0) for cle in semaines_pleines]
    sans_saut = [
        par_semaine.get(cle, 0.0) for cle in semaines_pleines
        if not any((cle[1] + timedelta(days=k)) in sautes for k in range(7))
    ]
    quota = config.entretien_quota_hebdo
    tenu = lambda xs: (sum(1 for x in xs if x + 1e-9 >= quota) / len(xs)) if xs else None  # noqa: E731

    par_jour = [n for (j, n) in r.entretien_points_par_jour if j not in sautes]
    cap = config.entretien_plafond_jour
    # Rotation: jours de révision RAPPORTÉS au nombre de jours passés en entretien
    # (un thème entré en entretien tard n'est pas « moins bien servi »).
    jours_en_entretien = {t: sum(1 for ts in themes_par_jour.values() if t in ts) for t in toutes}
    ratios = {
        t: len(jours_entretien.get(t, ())) / n
        for t, n in jours_en_entretien.items() if n >= 14
    }
    valeurs = list(ratios.values())
    nb_ent = [n for _, n in r.nb_themes_entretien_par_jour]
    return {
        "themes_entretien_min": min(nb_ent), "themes_entretien_max": max(nb_ent),
        "quota_hebdo": quota, "plafond_jour": cap,
        "semaines_theme_completes": len(semaines_pleines),
        "quota_tenu_pct": tenu(faits),
        "quota_tenu_sans_saut_pct": tenu(sans_saut),
        "semaines_sans_saut": len(sans_saut),
        "points_semaine_theme_moyenne": statistics.mean(faits) if faits else None,
        "points_semaine_theme_min": min(faits) if faits else None,
        "points_semaine_theme_max": max(faits) if faits else None,
        "points_par_jour_moyenne": statistics.mean(par_jour) if par_jour else 0.0,
        "points_par_jour_max": max(par_jour) if par_jour else 0.0,
        "jours_plafond_entretien_depasse": sum(1 for n in par_jour if n > cap + 1e-6),
        "part_moyenne": (
            sum(n for _, n in r.entretien_points_par_jour) / r.points_total if r.points_total else 0.0
        ),
        "themes_jamais_revises": sorted(t for t in toutes if t not in jours_entretien),
        "rotation_min": min(valeurs) if valeurs else 0.0,
        "rotation_max": max(valeurs) if valeurs else 0.0,
        "capacite_hebdo": 7 * cap,
        "demande_max_hebdo": max(nb_ent) * quota,
        "cartes_max_jour": max((n for _, n in r.entretien_cartes_par_jour), default=0),
    }


def _anomalies(r: SimResult, config: SchedulerConfig) -> list[str]:
    """Comportements bizarres, signalés au lieu d'être passés sous silence."""
    anomalies: list[str] = []
    eps = 1e-6
    for jour, n in r.charge_par_jour:
        if n > config.plafond_quotidien + eps:
            anomalies.append(f"{jour}: charge {n:.2f} pts > plafond {config.plafond_quotidien:g}.")
    e = r.rapport_entretien
    if e:
        if e["jours_plafond_entretien_depasse"]:
            anomalies.append(
                f"Plafond d'entretien ({e['plafond_jour']:g}) dépassé {e['jours_plafond_entretien_depasse']} jour(s)."
            )
        if e["themes_jamais_revises"]:
            anomalies.append(
                "Thèmes en entretien jamais révisés: " + ", ".join(e["themes_jamais_revises"]) + "."
            )
        pct = e["quota_tenu_sans_saut_pct"]
        if pct is not None and pct < 0.8:
            anomalies.append(
                f"Quota hebdomadaire tenu seulement {pct:.0%} des semaines sans jour sauté "
                f"({e['quota_hebdo']:g} pts/thème)."
            )
        if e["rotation_min"] and e["rotation_max"] > 3 * e["rotation_min"]:
            anomalies.append(
                f"Rotation inégale: un thème est révisé {e['rotation_min']:.0%} de ses jours "
                f"d'entretien, un autre {e['rotation_max']:.0%}."
            )
        if e["demande_max_hebdo"] > e["capacite_hebdo"] + eps:
            anomalies.append(
                f"Capacité d'entretien insuffisante: jusqu'à {e['themes_entretien_max']} thèmes en "
                f"entretien × {e['quota_hebdo']:g} pts = {e['demande_max_hebdo']:g} pts/semaine pour "
                f"une capacité de {e['capacite_hebdo']:g} ({e['plafond_jour']:g}/jour): les quotas "
                f"ne peuvent plus tous être tenus et la dette d'entretien gonfle. Relève "
                f"`entretien_plafond_jour` ou baisse `entretien_quota_hebdo`."
            )
        if e["points_par_jour_moyenne"] > 0.95 * e["plafond_jour"]:
            anomalies.append("L'entretien sature son plafond quotidien presque tous les jours.")
    c = r.rapport_charge
    if c and c["jours_au_plafond"] > 0.5 * c["jours_actifs"]:
        anomalies.append(
            f"Plafond de {config.plafond_quotidien:g} pts atteint {c['jours_au_plafond']} jours "
            f"sur {c['jours_actifs']}: charge soutenue au maximum."
        )
    lourds = [(j, n) for j, n in r.cartes_par_jour if n > 100]
    if lourds:
        anomalies.append(
            f"{len(lourds)} jour(s) à plus de 100 cartes (max {max(n for _, n in lourds)}): le plafond "
            f"est en points, et une carte de vocabulaire ne pèse que 0,1 — le nombre de cartes "
            f"n'est pas borné."
        )
    if e and r.dette_entretien_points_par_jour:
        fin = r.dette_entretien_points_par_jour[-1][1]
        jours_de_plafond = fin / e["plafond_jour"] if e["plafond_jour"] else 0.0
        if jours_de_plafond > 7:
            anomalies.append(
                f"Dette d'entretien persistante: {fin:.0f} pts dus à la fin, soit "
                f"{jours_de_plafond:.0f} jours de plafond d'entretien à résorber (après un gros "
                f"examen, tout un lot de cartes retombe en entretien d'un coup)."
            )
    dettes = [n for _, n in r.dette_points_par_jour]
    if len(dettes) > 20 and dettes[-1] > 2 * max(dettes[7], config.plafond_quotidien):
        anomalies.append(
            f"La dette gonfle: {dettes[-1]:.0f} pts dus à la fin, {dettes[7]:.0f} au jour 7."
        )
    if c and c["plus_lourde_carte"] > config.plafond_quotidien + eps:
        anomalies.append("Une carte pèse plus que le plafond (garde-fou non respecté).")
    return anomalies


def _exam_report(
    conn: sqlite3.Connection, config: SchedulerConfig, start: date, sim: SimConfig
) -> list[dict]:
    """État réel des cartes la veille de chaque examen, reconstitué depuis l'historique."""
    rapport = []
    for exam in api.load_exams(conn):
        if exam.date < start or exam.date > start + timedelta(days=sim.jours):
            continue  # test passé au départ (thème en entretien) ou au-delà de l'horizon
        concernees = [c for c in api.load_cards(conn) if exam.couvre(c.cours)]
        if not concernees:
            continue
        moment = datetime.combine(exam.date, datetime.min.time(), tzinfo=timezone.utc)

        # Nombre de révisions effectuées avant l'examen, par carte.
        jamais = 0
        retentions = []
        for carte in concernees:
            nb = conn.execute(
                "SELECT COUNT(*) AS n FROM reviews WHERE card_id = ? AND reviewed_at < ?",
                (carte.id, moment.isoformat()),
            ).fetchone()["n"]
            if nb == 0:
                jamais += 1
                retentions.append(0.0)
            else:
                retentions.append(_retention_a_la_date(conn, carte.id, moment, config))

        rapport.append(
            {
                "intitule": exam.intitule,
                "cours": exam.cours_txt,
                "date": exam.date.isoformat(),
                "jour_sim": (exam.date - start).days,
                "nb_cartes": len(concernees),
                "nb_jamais_revisees_avant": jamais,
                "retention_moyenne": sum(retentions) / len(retentions),
                "retention_min": min(retentions),
            }
        )
    return rapport


def _retention_a_la_date(
    conn: sqlite3.Connection, card_id: str, moment: datetime, config: SchedulerConfig
) -> float:
    """Rejoue l'historique réel de la carte jusqu'à `moment` et mesure la rétention.

    On ne peut pas lire l'état final en base (il inclut les révisions
    postérieures à l'examen), d'où le rejeu. Une carte déjà apprise au départ
    (scénario « année ») repart de son état initial, pas d'une carte vierge.
    """
    from fsrs import Card

    from .fsrs_engine import apply_review

    logs = conn.execute(
        "SELECT reviewed_at, rating FROM reviews WHERE card_id = ? AND reviewed_at < ?"
        " ORDER BY reviewed_at",
        (card_id, moment.isoformat()),
    ).fetchall()
    if not logs:
        return 0.0

    carte = Card(card_id=0)
    for log in logs:
        carte = apply_review(
            carte,
            Rating(log["rating"]),
            datetime.fromisoformat(log["reviewed_at"]),
            config.retention_normale,
        )
    return retrievability_at(carte, moment)


def _effet_saut(
    dette: list[tuple[date, float]], charge: list[tuple[date, float]], sim: SimConfig
) -> dict:
    if sim.saut_force_debut is None:
        return {}
    debut = sim.saut_force_debut
    fin = debut + 3
    if fin + 5 >= len(dette):
        return {}
    return {
        "jour_debut_saut": debut,
        "dette_avant": dette[debut - 1][1],
        "dette_apres_3j": dette[fin][1],
        "charge_5j_suivants": [n for _, n in charge[fin : fin + 5]],
        "jours_pour_resorber": _jours_resorption(dette, fin, dette[debut - 1][1]),
    }


def _jours_resorption(dette: list[tuple[date, float]], depart: int, niveau_cible: float) -> int | None:
    for i in range(depart, len(dette)):
        if dette[i][1] <= niveau_cible:
            return i - depart
    return None


def plot_charge(result: SimResult, out_path: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    jours = list(range(len(result.charge_par_jour)))
    totaux = [n for _, n in result.charge_par_jour]
    entretien = [n for _, n in result.entretien_points_par_jour] or [0.0] * len(jours)
    test = [max(0.0, t - e) for t, e in zip(totaux, entretien)]
    dettes = [n for _, n in result.dette_points_par_jour]

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.bar(jours, test, color="#4C78A8", label="points — mode test / nouvelles cartes")
    ax.bar(jours, entretien, bottom=test, color="#B279A2", label="points — entretien")
    ax.plot(jours, dettes, color="#E45756", linewidth=1.2, label="points dus (dette)")
    if result.plafond:
        ax.axhline(result.plafond, color="grey", linestyle=":", linewidth=1, label="plafond")

    start = result.charge_par_jour[0][0]
    haut = max(dettes + [result.plafond, 1.0])
    for exam in result.examens:
        ax.axvline(exam["jour_sim"], color="#54A24B", linestyle="--", alpha=0.8)
        ax.text(
            exam["jour_sim"],
            haut * 0.95,
            exam["cours"][:4],
            rotation=90,
            fontsize=8,
            color="#54A24B",
            va="top",
        )

    for jour in result.jours_sautes:
        ax.axvspan((jour - start).days - 0.5, (jour - start).days + 0.5, color="grey", alpha=0.15)

    ax.set_xlabel("jour de simulation")
    ax.set_ylabel("points")
    ax.set_title("Charge quotidienne en points (zones grises = jours sautés, pointillés = examens)")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path


def simulate(
    db_path: Path,
    config: SchedulerConfig,
    sim: SimConfig,
    start: date,
    *,
    reset: bool = True,
) -> SimResult:
    """Prépare une base de simulation isolée, puis déroule les N jours."""
    if reset and db_path.exists():
        db_path.unlink()
    conn = open_db(db_path)
    now = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    rng = random.Random(sim.graine)
    if sim.scenario == "annee":
        if not conn.execute("SELECT 1 FROM cards LIMIT 1").fetchone():
            seed_scenario_annee(conn, random.Random(sim.graine + 1), start)
    else:
        if not conn.execute("SELECT 1 FROM cards LIMIT 1").fetchone():
            seed_fake_cards(conn, sim.nb_cartes, rng, now)
        if not conn.execute("SELECT 1 FROM exams LIMIT 1").fetchone():
            seed_fake_exams(conn, start)
    try:
        return run_simulation(conn, config, sim, start)
    finally:
        conn.close()
