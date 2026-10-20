"""Logique du moteur de planification, sans aucun I/O terminal.

C'est le module que le CLI (et plus tard un front-end) consomme. Toutes les
fonctions prennent la date du jour en paramètre: jamais de `datetime.now()`
caché dans la logique, pour que tests et simulations soient déterministes.

Ce module fait l'accès à la base (chargement, écriture, CRUD); la décision
de la journée (budget en points, mode entretien) vit dans `planning`.
"""
from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from fsrs import Rating

from .config import SchedulerConfig
from .db import ensure_cours
from .fsrs_engine import apply_review, card_from_row, card_state_for_db
from .heuristic import (
    Exam,
    effective_due,
    exams_for_card,
    project_retention_at,
    retention_target,
)
from .planning import (  # noqa: F401  (ré-exportés: l'API publique du moteur)
    CardRow,
    FileDuJour,
    QueueItem,
    construire_file,
    due_items,
    themes_en_entretien,
    themes_en_test,
)

# --- Chargement ------------------------------------------------------------

def load_cards(conn: sqlite3.Connection) -> list[CardRow]:
    """Cartes actives: ni désactivées, ni dans un thème archivé."""
    rows = conn.execute(
        "SELECT k.id, k.concept_id, k.cours, k.type, k.payload,"
        " k.poids, k.categorie,"
        " k.fsrs_state, k.fsrs_step, k.fsrs_stability, k.fsrs_difficulty, k.last_review"
        " FROM cards k JOIN cours c ON c.nom = k.cours"
        " WHERE k.desactivee = 0 AND c.archive = 0"
    ).fetchall()
    return [
        CardRow(
            id=row["id"],
            concept_id=row["concept_id"],
            cours=row["cours"],
            type=row["type"],
            payload=row["payload"],
            card=card_from_row(row),
            poids=row["poids"],
            categorie=row["categorie"],
        )
        for row in rows
    ]


def load_exams(conn: sqlite3.Connection) -> list[Exam]:
    """Tests, avec les thèmes résolus: liens directs + thèmes (non archivés)
    des matières liées."""
    exams = []
    for row in conn.execute("SELECT id, date, intitule FROM exams ORDER BY date, id"):
        exam_id = row["id"]
        cours = {
            r["cours"]
            for r in conn.execute(
                "SELECT ec.cours FROM exam_cours ec JOIN cours c ON c.nom = ec.cours"
                " WHERE ec.exam_id = ? AND c.archive = 0",
                (exam_id,),
            )
        }
        matieres = []
        for r in conn.execute(
            "SELECT m.id, m.nom FROM exam_matieres em JOIN matieres m ON m.id = em.matiere_id"
            " WHERE em.exam_id = ?",
            (exam_id,),
        ):
            matieres.append(r["nom"])
            cours |= {
                t["nom"]
                for t in conn.execute(
                    "SELECT nom FROM cours WHERE matiere_id = ? AND archive = 0", (r["id"],)
                )
            }
        exams.append(
            Exam(
                id=exam_id,
                cours=frozenset(cours),
                date=date.fromisoformat(row["date"]),
                intitule=row["intitule"],
                matieres=frozenset(matieres),
            )
        )
    return exams


# --- Gestion des tests -----------------------------------------------------

def _liste(valeur: str | list[str] | None) -> list[str]:
    if valeur is None:
        return []
    return [valeur] if isinstance(valeur, str) else list(valeur)


def add_test(
    conn: sqlite3.Connection,
    *,
    cours: str | list[str] | None = None,
    jour: date,
    intitule: str,
    matieres: list[str] | None = None,
) -> int:
    """Crée un test lié à un ou plusieurs cours, et/ou à des matières entières.

    Lier une matière couvre aussi les thèmes qu'on y ajoutera plus tard.
    """
    noms, noms_matieres = _liste(cours), _liste(matieres)
    if not noms and not noms_matieres:
        raise RuntimeError("Un test doit être lié à au moins un cours ou une matière.")
    for nom in noms:
        _verifier_cours_liable(conn, nom)
    for matiere in noms_matieres:
        if conn.execute("SELECT 1 FROM matieres WHERE nom = ?", (matiere,)).fetchone() is None:
            raise RuntimeError(f"Matière inconnue: {matiere}")
    cur = conn.execute(
        "INSERT INTO exams (date, intitule) VALUES (?,?)", (jour.isoformat(), intitule)
    )
    exam_id = int(cur.lastrowid)
    conn.commit()
    link_cours(conn, exam_id, noms)
    for matiere in noms_matieres:
        link_matiere(conn, exam_id, matiere)
    return exam_id


def _verifier_cours_liable(conn: sqlite3.Connection, nom: str) -> None:
    ligne = conn.execute("SELECT archive FROM cours WHERE nom = ?", (nom,)).fetchone()
    if ligne is None:
        # Comme avant: lier un cours inconnu le crée (import, scripts).
        ensure_cours(conn, nom)
    elif ligne["archive"]:
        raise RuntimeError(
            f"Le cours « {nom} » est archivé: désarchive-le avant de le lier à un test."
        )


def _exam_existe(conn: sqlite3.Connection, exam_id: int) -> None:
    if conn.execute("SELECT 1 FROM exams WHERE id = ?", (exam_id,)).fetchone() is None:
        raise RuntimeError(f"Aucun test avec l'id {exam_id}.")


def link_cours(conn: sqlite3.Connection, exam_id: int, cours: str | list[str]) -> None:
    """Lie des cours (thèmes) à un test existant."""
    _exam_existe(conn, exam_id)
    noms = _liste(cours)
    for nom in noms:
        _verifier_cours_liable(conn, nom)
    conn.executemany(
        "INSERT OR IGNORE INTO exam_cours (exam_id, cours) VALUES (?,?)",
        [(exam_id, nom) for nom in noms],
    )
    conn.commit()


def link_matiere(conn: sqlite3.Connection, exam_id: int, matiere: str) -> int:
    """Lie TOUS les cours d'une matière à un test (et ceux qu'on y ajoutera).

    Renvoie le nombre de thèmes actifs actuellement couverts. Les thèmes
    archivés sont ignorés.
    """
    _exam_existe(conn, exam_id)
    ligne = conn.execute("SELECT id FROM matieres WHERE nom = ?", (matiere,)).fetchone()
    if ligne is None:
        raise RuntimeError(f"Matière inconnue: {matiere}")
    conn.execute(
        "INSERT OR IGNORE INTO exam_matieres (exam_id, matiere_id) VALUES (?,?)",
        (exam_id, ligne["id"]),
    )
    conn.commit()
    return conn.execute(
        "SELECT COUNT(*) AS n FROM cours WHERE matiere_id = ? AND archive = 0", (ligne["id"],)
    ).fetchone()["n"]


def unlink_cours(conn: sqlite3.Connection, exam_id: int, cours: str | list[str]) -> None:
    _exam_existe(conn, exam_id)
    conn.executemany(
        "DELETE FROM exam_cours WHERE exam_id = ? AND cours = ?",
        [(exam_id, nom) for nom in _liste(cours)],
    )
    conn.commit()


def unlink_matiere(conn: sqlite3.Connection, exam_id: int, matiere: str) -> None:
    _exam_existe(conn, exam_id)
    conn.execute(
        "DELETE FROM exam_matieres WHERE exam_id = ? AND matiere_id ="
        " (SELECT id FROM matieres WHERE nom = ?)",
        (exam_id, matiere),
    )
    conn.commit()


def set_test_liens(
    conn: sqlite3.Connection,
    exam_id: int,
    *,
    cours: list[str] | None = None,
    matieres: list[str] | None = None,
) -> None:
    """Remplace les liens d'un test (None = ne pas toucher à cette catégorie)."""
    _exam_existe(conn, exam_id)
    if cours is not None:
        for nom in cours:
            _verifier_cours_liable(conn, nom)
        conn.execute("DELETE FROM exam_cours WHERE exam_id = ?", (exam_id,))
        link_cours(conn, exam_id, cours)
    if matieres is not None:
        for nom in matieres:
            if conn.execute("SELECT 1 FROM matieres WHERE nom = ?", (nom,)).fetchone() is None:
                raise RuntimeError(f"Matière inconnue: {nom}")
        conn.execute("DELETE FROM exam_matieres WHERE exam_id = ?", (exam_id,))
        for nom in matieres:
            link_matiere(conn, exam_id, nom)
    conn.commit()


def list_tests(conn: sqlite3.Connection) -> list[Exam]:
    return load_exams(conn)


def move_test(conn: sqlite3.Connection, exam_id: int, nouvelle_date: date) -> None:
    cur = conn.execute(
        "UPDATE exams SET date = ? WHERE id = ?", (nouvelle_date.isoformat(), exam_id)
    )
    if not cur.rowcount:
        raise RuntimeError(f"Aucun test avec l'id {exam_id}.")
    conn.commit()


def remove_test(conn: sqlite3.Connection, exam_id: int) -> None:
    cur = conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
    if not cur.rowcount:
        raise RuntimeError(f"Aucun test avec l'id {exam_id}.")
    conn.commit()


# --- File du jour ----------------------------------------------------------

def effective_due_for(
    row: CardRow, exams: list[Exam], today: date, config: SchedulerConfig
) -> datetime:
    """Échéance d'une carte donnée (helper pour l'inspection et les tests)."""
    return effective_due(
        row.card, exams_for_card(exams, row.cours), today, config, row.id
    )


def plan_du_jour(
    conn: sqlite3.Connection,
    today: date,
    config: SchedulerConfig,
    *,
    cours: str | None = None,
) -> FileDuJour:
    """La journée complète (file, dette, entretien), sans ordre ni skip."""
    cards = load_cards(conn)
    if cours:
        cards = [c for c in cards if c.cours == cours]
    return construire_file(cards, load_exams(conn), today, config)


def get_today_queue(
    conn: sqlite3.Connection,
    today: date,
    config: SchedulerConfig,
    *,
    cours: str | None = None,
    ordre: str | None = None,
    session_id: str | None = None,
    graine: int | None = None,
) -> list[QueueItem]:
    """File de révision du jour: cartes dues, priorisées, dans le budget en points.

    Le surplus n'est pas perdu: il reste dû et remonte le lendemain avec un
    retard plus grand, donc une priorité plus forte à urgence d'examen égale.
    Les cartes d'entretien passent après celles du mode test.

    Les questions passées (`skip`) reviennent en fin de file, tant qu'elles
    n'ont pas atteint `MAX_SKIPS_PAR_SESSION`.
    """
    items = list(plan_du_jour(conn, today, config, cours=cours).items)

    if ordre:
        items = appliquer_ordre(items, ordre, graine=graine)

    if session_id:
        skips = skips_de_session(conn, session_id)
        if skips:
            epuisees = {cid for cid, n in skips.items() if n >= MAX_SKIPS_PAR_SESSION}
            passees = [i for i in items if i.card.id in skips and i.card.id not in epuisees]
            normales = [i for i in items if i.card.id not in skips]
            items = normales + passees

    return items


def record_answer(
    conn: sqlite3.Connection,
    card_id: str,
    rating: Rating,
    *,
    reviewed_at: datetime,
    config: SchedulerConfig,
    correct: bool | None = None,
    source: str = "auto",
    duration_ms: int | None = None,
) -> None:
    """Enregistre une réponse: historique + nouvel état FSRS de la carte."""
    row = conn.execute(
        "SELECT id, cours, fsrs_state, fsrs_step, fsrs_stability,"
        " fsrs_difficulty, last_review FROM cards WHERE id = ?",
        (card_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Carte inconnue: {card_id}")

    card = card_from_row(row)
    card_exams = exams_for_card(load_exams(conn), row["cours"])
    retention = retention_target(card_exams, reviewed_at.date(), config)
    updated = apply_review(card, rating, reviewed_at, retention)

    state = card_state_for_db(updated)
    conn.execute(
        "UPDATE cards SET fsrs_state=?, fsrs_step=?, fsrs_stability=?,"
        " fsrs_difficulty=?, last_review=? WHERE id=?",
        (
            state["fsrs_state"],
            state["fsrs_step"],
            state["fsrs_stability"],
            state["fsrs_difficulty"],
            state["last_review"],
            card_id,
        ),
    )
    conn.execute(
        "INSERT INTO reviews (card_id, reviewed_at, rating, duration_ms, correct, source)"
        " VALUES (?,?,?,?,?,?)",
        (
            card_id,
            reviewed_at.isoformat(),
            int(rating),
            duration_ms,
            None if correct is None else int(correct),
            source,
        ),
    )
    conn.commit()


# --- Matières et cours (thèmes) --------------------------------------------

COULEUR_DEFAUT = "#8B7CFF"
# Marqueur « ne pas toucher » pour les champs nullables de update_cours.
INCHANGE = object()


@dataclass(frozen=True)
class Cours:
    """Un cours = un thème précis (ex. « Anglais conditionnel »)."""

    nom: str
    couleur: str
    nb_cartes: int = 0
    matiere: str | None = None
    archive: bool = False
    profil: str = ""
    recap: str = ""


@dataclass(frozen=True)
class Matiere:
    nom: str
    couleur: str
    profil: str = ""
    nb_cours: int = 0
    nb_cartes: int = 0


def list_cours(conn: sqlite3.Connection, *, archives: bool = True) -> list[Cours]:
    rows = conn.execute(
        "SELECT c.nom, c.couleur, c.archive, c.profil, c.recap, m.nom AS matiere, COUNT(k.id) AS nb"
        " FROM cours c LEFT JOIN cards k ON k.cours = c.nom"
        " LEFT JOIN matieres m ON m.id = c.matiere_id"
        " GROUP BY c.nom, c.couleur, c.archive, c.profil, c.recap, m.nom ORDER BY c.nom"
    ).fetchall()
    return [
        Cours(
            nom=r["nom"], couleur=r["couleur"], nb_cartes=r["nb"], matiere=r["matiere"],
            archive=bool(r["archive"]), profil=r["profil"], recap=r["recap"],
        )
        for r in rows
        if archives or not r["archive"]
    ]


def _matiere_id(conn: sqlite3.Connection, nom: str) -> int:
    ligne = conn.execute("SELECT id FROM matieres WHERE nom = ?", (nom,)).fetchone()
    if ligne is None:
        raise RuntimeError(f"Matière inconnue: {nom}")
    return int(ligne["id"])


def add_cours(
    conn: sqlite3.Connection,
    nom: str,
    couleur: str | None = None,
    *,
    matiere: str | None = None,
    profil: str = "",
) -> None:
    """Crée un thème. Sans couleur, il prend celle de sa matière."""
    if not nom.strip():
        raise RuntimeError("Le nom du cours ne peut pas être vide.")
    existe = conn.execute("SELECT 1 FROM cours WHERE nom = ?", (nom,)).fetchone()
    if existe:
        raise RuntimeError(f"Le cours « {nom} » existe déjà.")
    matiere_id = _matiere_id(conn, matiere) if matiere else None
    if couleur is None:
        parent = (
            conn.execute("SELECT couleur FROM matieres WHERE id = ?", (matiere_id,)).fetchone()
            if matiere_id is not None else None
        )
        couleur = parent["couleur"] if parent else COULEUR_DEFAUT
    conn.execute(
        "INSERT INTO cours (nom, couleur, matiere_id, profil) VALUES (?,?,?,?)",
        (nom, couleur, matiere_id, profil),
    )
    conn.commit()


def update_cours(
    conn: sqlite3.Connection,
    nom: str,
    *,
    nouveau_nom: str | None = None,
    couleur: str | None = None,
    matiere: object = INCHANGE,
    profil: str | None = None,
    recap: str | None = None,
    archive: bool | None = None,
) -> None:
    """Met à jour un thème. `matiere=None` le détache de sa matière.

    `recap`: page de rappel théorique (Markdown) montrée avant une session; "" l'efface.
    """
    if conn.execute("SELECT 1 FROM cours WHERE nom = ?", (nom,)).fetchone() is None:
        raise RuntimeError(f"Cours inconnu: {nom}")
    if couleur is not None:
        conn.execute("UPDATE cours SET couleur = ? WHERE nom = ?", (couleur, nom))
    if profil is not None:
        conn.execute("UPDATE cours SET profil = ? WHERE nom = ?", (profil, nom))
    if recap is not None:
        conn.execute("UPDATE cours SET recap = ? WHERE nom = ?", (recap.strip(), nom))
    if matiere is not INCHANGE:
        mid = _matiere_id(conn, matiere) if matiere else None
        conn.execute("UPDATE cours SET matiere_id = ? WHERE nom = ?", (mid, nom))
    if archive is not None:
        conn.execute("UPDATE cours SET archive = ? WHERE nom = ?", (int(archive), nom))
    if nouveau_nom and nouveau_nom != nom:
        # Renommer propage aux cartes et aux tests: les FK sont en ON UPDATE
        # implicite côté application, on met donc à jour explicitement.
        conn.execute(
            "INSERT INTO cours (nom, couleur, matiere_id, profil, archive, recap)"
            " SELECT ?, couleur, matiere_id, profil, archive, recap FROM cours WHERE nom = ?",
            (nouveau_nom, nom),
        )
        conn.execute("UPDATE cards SET cours = ? WHERE cours = ?", (nouveau_nom, nom))
        conn.execute("UPDATE exam_cours SET cours = ? WHERE cours = ?", (nouveau_nom, nom))
        conn.execute("DELETE FROM cours WHERE nom = ?", (nom,))
    conn.commit()


def archive_cours(conn: sqlite3.Connection, nom: str, archive: bool = True) -> None:
    """Archive (ou désarchive) un thème, sans toucher à ses cartes.

    Un thème archivé sort du planning et de l'entretien; ses cartes restent
    visibles dans la bibliothèque et reviennent telles quelles au désarchivage.
    """
    update_cours(conn, nom, archive=archive)


def delete_cours(
    conn: sqlite3.Connection, nom: str, *, reaffecter_vers: str | None = None
) -> int:
    """Supprime un cours. Ses questions sont réaffectées ou supprimées.

    Renvoie le nombre de questions concernées, pour que l'interface puisse
    demander confirmation en connaissance de cause.
    """
    nb = conn.execute("SELECT COUNT(*) AS n FROM cards WHERE cours = ?", (nom,)).fetchone()["n"]
    if reaffecter_vers:
        cible = conn.execute("SELECT 1 FROM cours WHERE nom = ?", (reaffecter_vers,)).fetchone()
        if not cible:
            raise RuntimeError(f"Cours de destination inconnu: {reaffecter_vers}")
        conn.execute("UPDATE cards SET cours = ? WHERE cours = ?", (reaffecter_vers, nom))
        # OR IGNORE: un test déjà lié aux deux cours garde un seul lien.
        conn.execute(
            "UPDATE OR IGNORE exam_cours SET cours = ? WHERE cours = ?", (reaffecter_vers, nom)
        )
    conn.execute("DELETE FROM cours WHERE nom = ?", (nom,))
    conn.commit()
    return nb


def points_par_cours(conn: sqlite3.Connection, config: SchedulerConfig) -> dict[str, float]:
    """Total des points des cartes de chaque cours (archivés compris, désactivées exclues)."""
    from .poids import poids_carte

    total: dict[str, float] = {}
    for r in conn.execute("SELECT cours, type, categorie, poids FROM cards WHERE desactivee = 0"):
        total[r["cours"]] = total.get(r["cours"], 0.0) + poids_carte(
            r["type"], r["categorie"], r["poids"], config
        )
    return total


def cours_en_entretien(
    conn: sqlite3.Connection, today: date, exams: list[Exam] | None = None
) -> set[str]:
    """Noms des thèmes en entretien: pas archivés, aucun test à venir."""
    en_test = themes_en_test(exams if exams is not None else load_exams(conn), today)
    return {
        r["nom"] for r in conn.execute("SELECT nom FROM cours WHERE archive = 0")
        if r["nom"] not in en_test
    }


def list_matieres(conn: sqlite3.Connection) -> list[Matiere]:
    rows = conn.execute(
        "SELECT m.nom, m.couleur, m.profil,"
        " (SELECT COUNT(*) FROM cours c WHERE c.matiere_id = m.id) AS nb_cours,"
        " (SELECT COUNT(*) FROM cards k JOIN cours c ON c.nom = k.cours"
        "   WHERE c.matiere_id = m.id) AS nb_cartes"
        " FROM matieres m ORDER BY m.nom"
    ).fetchall()
    return [
        Matiere(nom=r["nom"], couleur=r["couleur"], profil=r["profil"],
                nb_cours=r["nb_cours"], nb_cartes=r["nb_cartes"])
        for r in rows
    ]


def add_matiere(
    conn: sqlite3.Connection, nom: str, couleur: str = COULEUR_DEFAUT, profil: str = ""
) -> None:
    if not nom.strip():
        raise RuntimeError("Le nom de la matière ne peut pas être vide.")
    if conn.execute("SELECT 1 FROM matieres WHERE nom = ?", (nom,)).fetchone():
        raise RuntimeError(f"La matière « {nom} » existe déjà.")
    conn.execute(
        "INSERT INTO matieres (nom, couleur, profil) VALUES (?,?,?)", (nom, couleur, profil)
    )
    conn.commit()


def update_matiere(
    conn: sqlite3.Connection,
    nom: str,
    *,
    nouveau_nom: str | None = None,
    couleur: str | None = None,
    profil: str | None = None,
) -> None:
    mid = _matiere_id(conn, nom)
    if couleur is not None:
        conn.execute("UPDATE matieres SET couleur = ? WHERE id = ?", (couleur, mid))
    if profil is not None:
        conn.execute("UPDATE matieres SET profil = ? WHERE id = ?", (profil, mid))
    if nouveau_nom and nouveau_nom != nom:
        if conn.execute("SELECT 1 FROM matieres WHERE nom = ?", (nouveau_nom,)).fetchone():
            raise RuntimeError(f"La matière « {nouveau_nom} » existe déjà.")
        conn.execute("UPDATE matieres SET nom = ? WHERE id = ?", (nouveau_nom, mid))
    conn.commit()


def delete_matiere(conn: sqlite3.Connection, nom: str) -> int:
    """Supprime une matière. Ses thèmes (et leurs cartes) sont conservés, sans parent.

    Renvoie le nombre de thèmes détachés. Les tests liés à la matière entière
    perdent ce lien (leurs liens directs vers des cours restent).
    """
    mid = _matiere_id(conn, nom)
    nb = conn.execute(
        "SELECT COUNT(*) AS n FROM cours WHERE matiere_id = ?", (mid,)
    ).fetchone()["n"]
    conn.execute("UPDATE cours SET matiere_id = NULL WHERE matiere_id = ?", (mid,))
    conn.execute("DELETE FROM matieres WHERE id = ?", (mid,))
    conn.commit()
    return nb


def set_question_desactivee(conn: sqlite3.Connection, card_id: str, desactivee: bool,
                            note: str | None = None) -> None:
    """Marque une question comme mauvaise: elle sort des révisions jusqu'à correction."""
    cur = conn.execute(
        "UPDATE cards SET desactivee = ?, note_qualite = ? WHERE id = ?",
        (int(desactivee), note, card_id),
    )
    if not cur.rowcount:
        raise RuntimeError(f"Carte inconnue: {card_id}")
    conn.commit()


# --- Skip ------------------------------------------------------------------

# Au-delà de cette limite, une question déjà passée ne revient plus dans la
# session du jour: sans ce garde-fou, passer systématiquement une question la
# ferait boucler indéfiniment en fin de file.
MAX_SKIPS_PAR_SESSION = 2


def skip_card(
    conn: sqlite3.Connection,
    session_id: str,
    card_id: str,
    *,
    jour: date,
    moment: datetime,
    raison: str | None = None,
) -> int:
    """Repousse une question en fin de file du jour.

    Un skip n'est PAS une réponse: aucune mise à jour FSRS, aucune pénalité,
    aucune ligne dans `reviews`. Si la question n'est pas traitée d'ici la fin
    de la session, elle reste simplement due.

    Renvoie le nombre de skips cumulés pour cette carte dans cette session.
    """
    existe = conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone()
    if existe is None:
        raise RuntimeError(f"Carte inconnue: {card_id}")

    conn.execute(
        "INSERT INTO session_skips (session_id, card_id, jour, nb_skips, raison, dernier_at)"
        " VALUES (?,?,?,1,?,?)"
        " ON CONFLICT(session_id, card_id) DO UPDATE SET"
        "   nb_skips = nb_skips + 1, raison = excluded.raison,"
        "   dernier_at = excluded.dernier_at",
        (session_id, card_id, jour.isoformat(), raison, moment.isoformat()),
    )
    conn.commit()
    return conn.execute(
        "SELECT nb_skips FROM session_skips WHERE session_id = ? AND card_id = ?",
        (session_id, card_id),
    ).fetchone()["nb_skips"]


def skips_de_session(conn: sqlite3.Connection, session_id: str) -> dict[str, int]:
    return {
        r["card_id"]: r["nb_skips"]
        for r in conn.execute(
            "SELECT card_id, nb_skips FROM session_skips WHERE session_id = ?", (session_id,)
        )
    }


# --- Ordre de session ------------------------------------------------------

ORDRE_MELANGE = "melange"
ORDRE_PAR_COURS = "par_cours"


def appliquer_ordre(
    items: list[QueueItem], ordre: str, *, graine: int | None = None
) -> list[QueueItem]:
    """Réordonne la file POUR L'AFFICHAGE, après priorisation et plafond.

    On ne touche pas à la sélection elle-même: quelles cartes sont dues et
    lesquelles passent le plafond restent décidées par l'heuristique. Seul
    l'ordre de présentation change.
    """
    if ordre == ORDRE_PAR_COURS:
        return sorted(items, key=lambda i: (i.card.cours, i.card.id))
    if ordre == ORDRE_MELANGE:
        melange = list(items)
        random.Random(graine).shuffle(melange)
        return melange
    raise RuntimeError(f"Ordre de session inconnu: {ordre}")


# --- Stats -----------------------------------------------------------------

@dataclass(frozen=True)
class ExamStat:
    exam: Exam
    nb_cartes: int
    nb_jamais_revisees: int
    retention_estimee: float


@dataclass(frozen=True)
class ChargeJour:
    """Charge prévue d'un jour, en cartes ET en points."""

    jour: date
    points: float
    cartes: int
    points_entretien: float
    cartes_entretien: int


@dataclass(frozen=True)
class Stats:
    today: date
    total_cartes: int
    nb_dues: int
    nb_en_retard: int
    nb_nouvelles: int
    points_dus: float
    # La file du jour, dans le budget.
    points_jour: float
    cartes_jour: int
    points_entretien_jour: float
    cartes_entretien_jour: int
    part_entretien: float
    plafond: float
    themes_entretien: list[str]
    quotas: dict
    charge_14j: list[ChargeJour]
    examens: list[ExamStat]
    alertes: list[str]


def alertes_entretien(
    themes_entretien: frozenset[str] | set[str], config: SchedulerConfig
) -> list[str]:
    """Signale un entretien impossible à tenir: quotas > capacité de la semaine."""
    demande = len(themes_entretien) * config.entretien_quota_hebdo
    capacite = 7 * config.entretien_plafond_jour
    if demande > capacite + 1e-9:
        return [
            f"Entretien: {demande:g} points demandés par semaine ({len(themes_entretien)} thème(s) "
            f"× {config.entretien_quota_hebdo:g}) pour une capacité de {capacite:g} "
            f"({config.entretien_plafond_jour:g}/jour). Les quotas ne seront pas tous tenus: "
            f"relève le plafond d'entretien ou baisse le quota."
        ]
    return []


def get_stats(
    conn: sqlite3.Connection,
    today: date,
    config: SchedulerConfig,
    horizon: int = 14,
) -> Stats:
    cards = load_cards(conn)
    exams = load_exams(conn)

    plan = construire_file(cards, exams, today, config)
    nb_en_retard = sum(1 for i in plan.dus if i.jours_retard > 0)
    nb_nouvelles = sum(1 for i in plan.dus if i.nouvelle)

    return Stats(
        today=today,
        total_cartes=len(cards),
        nb_dues=len(plan.dus),
        nb_en_retard=nb_en_retard,
        nb_nouvelles=nb_nouvelles,
        points_dus=plan.points_dus,
        points_jour=plan.points,
        cartes_jour=len(plan.items),
        points_entretien_jour=plan.points_entretien,
        cartes_entretien_jour=plan.nb_entretien,
        part_entretien=plan.part_entretien,
        plafond=config.plafond_quotidien,
        themes_entretien=sorted(plan.themes_entretien),
        quotas=plan.quotas,
        charge_14j=project_load(cards, exams, today, config, horizon),
        examens=[_exam_stat(cards, exams, e, today, config) for e in exams if e.date >= today],
        alertes=alertes_entretien(plan.themes_entretien, config),
    )


def project_load(
    cards: list[CardRow],
    exams: list[Exam],
    today: date,
    config: SchedulerConfig,
    horizon: int,
) -> list[ChargeJour]:
    """Charge quotidienne prévue, en supposant que tu réponds juste (Good).

    Simulation en mémoire (aucune écriture en base): on appelle la même
    `construire_file` que la file réelle (budget en points, entretien), pour
    que la prévision reflète le comportement réel du moteur.
    """
    etats = {row.id: row.card for row in cards}
    charge = []

    for offset in range(horizon):
        jour = today + timedelta(days=offset)
        snapshot = [
            CardRow(
                id=row.id, concept_id=row.concept_id, cours=row.cours,
                type=row.type, payload=row.payload, card=etats[row.id],
                poids=row.poids, categorie=row.categorie,
            )
            for row in cards
        ]
        plan = construire_file(snapshot, exams, jour, config)
        charge.append(
            ChargeJour(
                jour=jour, points=plan.points, cartes=len(plan.items),
                points_entretien=plan.points_entretien, cartes_entretien=plan.nb_entretien,
            )
        )

        moment = datetime.combine(jour, datetime.min.time(), tzinfo=timezone.utc)
        for item in plan.items:
            card_exams = exams_for_card(exams, item.card.cours)
            retention = retention_target(card_exams, jour, config)
            etats[item.card.id] = apply_review(etats[item.card.id], Rating.Good, moment, retention)

    return charge


def _exam_stat(
    cards: list[CardRow],
    exams: list[Exam],
    exam: Exam,
    today: date,
    config: SchedulerConfig,
) -> ExamStat:
    concernees = [c for c in cards if exam.couvre(c.cours)]
    jamais = sum(1 for c in concernees if c.card.last_review is None)
    if concernees:
        retentions = [
            project_retention_at(
                c.card,
                exams_for_card(exams, c.cours),
                exam.date,
                today,
                config,
                spread_key=c.id,
            )
            for c in concernees
        ]
        moyenne = sum(retentions) / len(retentions)
    else:
        moyenne = 0.0
    return ExamStat(
        exam=exam,
        nb_cartes=len(concernees),
        nb_jamais_revisees=jamais,
        retention_estimee=moyenne,
    )
