"""Construction de la file du jour: budget en points, mode entretien.

Une seule fonction, `construire_file`, décide de la journée. La file réelle
(`api.get_today_queue`), les prévisions (`project_load`, `stats`) et le
simulateur l'appellent toutes: elles ne peuvent donc pas diverger.

Deux régimes par thème (un thème = un cours):

- **mode test** — au moins un test futur lui est lié: pilotage normal par la
  date du test (rampe de rétention, clamp, priorité aux tests proches);
- **mode entretien** — tous ses tests sont passés, ou il n'en a aucun (et il
  n'est pas archivé): plus de date à viser, un quota hebdomadaire en points.

Budget. Le plafond quotidien est en POINTS. Les cartes se prennent par ordre
de priorité tant qu'elles tiennent; une carte qui ne tient pas est sautée au
profit d'une plus légère. Une carte plus lourde que le plafond compte pour le
plafond entier (`poids.poids_plafonne`): elle reste toujours planifiable.

Répartition entre thèmes (mode test). Sans garde-fou, le thème au test le plus
proche (ou celui qui vient de recevoir plein de cartes neuves) avalerait tout
le plafond. Le budget qui reste après le plancher d'entretien se partage donc à
parts égales entre les thèmes qui ont des cartes à faire; ce qu'un thème
n'utilise pas retourne aux autres (seconde passe, par priorité). Exception: un
thème dont le prochain test est dans au plus `jours_focus_test` jours passe
d'abord, sans limite de part.

Entretien. Seules les cartes DÉJÀ révisées d'un thème en entretien sont des
cartes d'entretien: une carte neuve est un apprentissage, pas un entretien,
et suit le circuit normal. Deux catégories de cartes d'entretien:

- le **plancher** (quota hebdomadaire): chaque thème en entretien doit avoir
  `entretien_quota_hebdo` points révisés dans la semaine (lundi à dimanche).
  Ce qui manque est étalé sur les jours restants de la semaine, et la part du
  jour est RÉSERVÉE: elle passe avant les cartes du mode test, sinon une
  semaine chargée en tests laisserait l'entretien à zéro. La part du jour se
  sert d'abord avec les cartes du thème déjà dues selon FSRS (plus faible
  rétention d'abord); si elles ne suffisent pas, on complète avec des cartes
  non dues, de plus faible rétention estimée, de préférence de poids <=
  `entretien_poids_max` (les plus lourdes seulement s'il n'existe rien
  d'autre). Plancher, pas objectif: si FSRS a déjà planifié assez de points
  du thème dans la semaine, aucun complément n'est ajouté;
- le **reste**: les autres cartes d'entretien dues selon FSRS, à la priorité la
  plus basse, dans ce qui reste du plafond d'entretien et du budget.

Le plafond d'entretien du jour (`entretien_plafond_jour`) limite la somme des
deux, tous thèmes confondus, en tourniquet: un passage donne une carte à chaque
thème, le thème dont la dernière révision est la plus ancienne en premier. Il
limite donc l'étalement du plancher, pas son montant hebdomadaire. Le plafond
quotidien total n'est jamais dépassé: ce qui ne tient pas reste dû (dette).

Rien n'est mémorisé d'un appel à l'autre: les révisions déjà faites
aujourd'hui sont débitées des parts du jour, donc rouvrir la file après une
session ne fait pas réapparaître de nouveaux compléments.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone

from fsrs import Card
from pydantic import TypeAdapter

from .config import SchedulerConfig
from .fsrs_engine import retrievability_at
from .heuristic import (
    Exam,
    deja_couvert_pour,
    effective_due,
    exams_for_card,
    next_exam,
    priority_key,
)
from .models import Question
from .poids import poids_carte, poids_plafonne

_QUESTION_ADAPTER = TypeAdapter(Question)
EPS = 1e-9


@dataclass
class CardRow:
    """Une carte: ses métadonnées, sa question (modèle du prototype 1) et son état FSRS."""

    id: str
    concept_id: str
    cours: str
    type: str
    payload: str
    card: Card
    # Poids explicite (nullable) et catégorie du concept (si connue): voir poids.py.
    poids: float | None = None
    categorie: str | None = None

    def question(self):
        return _QUESTION_ADAPTER.validate_json(self.payload)

    def points(self, config: SchedulerConfig) -> float:
        return poids_carte(self.type, self.categorie, self.poids, config)


@dataclass(frozen=True)
class QueueItem:
    card: CardRow
    due: datetime
    jours_retard: int
    prochain_examen: Exam | None
    nouvelle: bool = False
    # Poids effectif en points (déjà plafonné au plafond du jour).
    points: float = 0.0
    # Carte d'un thème en entretien (déjà révisée au moins une fois).
    entretien: bool = False
    # Carte ajoutée pour atteindre le quota hebdomadaire (pas due selon FSRS).
    complement: bool = False
    # Part du plancher hebdomadaire réservée aujourd'hui (passe avant le mode test).
    plancher: bool = False


@dataclass(frozen=True)
class QuotaTheme:
    """Où en est un thème en entretien cette semaine (pour l'affichage)."""

    theme: str
    quota: float
    fait_avant_aujourdhui: float
    planifie_fsrs: float
    manque: float
    allocation_jour: float


@dataclass
class FileDuJour:
    jour: date
    plafond: float
    items: list[QueueItem]  # la file du jour, dans l'ordre de présentation
    dus: list[QueueItem]  # toutes les cartes dues selon FSRS (hors compléments)
    complements: list[QueueItem]  # compléments de quota planifiés aujourd'hui
    themes_entretien: frozenset[str]
    quotas: dict[str, QuotaTheme] = field(default_factory=dict)
    entretien_plafond_restant: float = 0.0

    @property
    def points(self) -> float:
        return sum(i.points for i in self.items)

    @property
    def points_entretien(self) -> float:
        return sum(i.points for i in self.items if i.entretien)

    @property
    def points_dus(self) -> float:
        return sum(i.points for i in self.dus)

    @property
    def nb_entretien(self) -> int:
        return sum(1 for i in self.items if i.entretien)

    @property
    def part_entretien(self) -> float:
        """Part des POINTS du jour consacrée à l'entretien (0 à 1)."""
        total = self.points
        return self.points_entretien / total if total > EPS else 0.0


# --- Modes -----------------------------------------------------------------

def themes_en_test(exams: list[Exam], today: date) -> frozenset[str]:
    """Thèmes liés à au moins un test à venir (aujourd'hui compris)."""
    return frozenset(c for e in exams if e.date >= today for c in e.cours)


def themes_en_entretien(
    noms: set[str] | frozenset[str], exams: list[Exam], today: date
) -> frozenset[str]:
    """Thèmes dont aucun test lié n'est à venir (aujourd'hui compris).

    Un thème archivé n'arrive jamais ici: ses cartes ne sont pas chargées.
    """
    en_test = themes_en_test(exams, today)
    return frozenset(n for n in noms if n not in en_test)


def bornes_semaine(jour: date) -> tuple[date, date]:
    """Lundi et dimanche de la semaine de `jour`."""
    lundi = jour - timedelta(days=jour.weekday())
    return lundi, lundi + timedelta(days=6)


# --- Cartes dues -----------------------------------------------------------

def _debut_du_jour(jour: date) -> datetime:
    return datetime.combine(jour, datetime.min.time(), tzinfo=timezone.utc)


def _items_dus(
    cards: list[CardRow],
    exams: list[Exam],
    today: date,
    config: SchedulerConfig,
    themes_ent: frozenset[str] = frozenset(),
) -> list[QueueItem]:
    today_dt = _debut_du_jour(today)
    items = []
    for row in cards:
        card_exams = exams_for_card(exams, row.cours)
        due = effective_due(row.card, card_exams, today, config, row.id)
        if due.date() > today:
            continue
        # Une carte jamais révisée n'est pas "en retard": elle est neuve.
        nouvelle = row.card.last_review is None
        items.append(
            QueueItem(
                card=row,
                due=due,
                jours_retard=0 if nouvelle else max(0, (today_dt - due).days),
                prochain_examen=next_exam(card_exams, today),
                nouvelle=nouvelle,
                points=poids_plafonne(row.points(config), config.plafond_quotidien),
                entretien=(not nouvelle) and row.cours in themes_ent,
            )
        )
    return items


def _tri_priorite(items: list[QueueItem], today: date, config: SchedulerConfig) -> None:
    items.sort(
        key=lambda i: priority_key(
            i.card.id,
            [i.prochain_examen] if i.prochain_examen is not None else [],
            i.due,
            today,
            deja_couvert_pour(i.card.card, i.prochain_examen, config),
        )
    )


def due_items(
    cards: list[CardRow],
    exams: list[Exam],
    today: date,
    config: SchedulerConfig,
    themes_ent: frozenset[str] = frozenset(),
) -> list[QueueItem]:
    """Toutes les cartes dues à `today` (sans plafond), triées par priorité."""
    items = _items_dus(cards, exams, today, config, themes_ent)
    _tri_priorite(items, today, config)
    return items


# --- Plancher d'entretien (quota hebdomadaire) -------------------------------

def _derniere_revue(cs: list[CardRow]) -> datetime:
    revues = [c.card.last_review for c in cs if c.card.last_review is not None]
    return max(revues) if revues else datetime.min.replace(tzinfo=timezone.utc)


def _reserver_plancher(
    cards: list[CardRow],
    dus: list[QueueItem],
    themes_ent: frozenset[str],
    today: date,
    config: SchedulerConfig,
) -> tuple[dict[str, list[QueueItem]], dict[str, QuotaTheme]]:
    """Part du plancher hebdomadaire de chaque thème en entretien, pour aujourd'hui."""
    quota = config.entretien_quota_hebdo
    if quota <= 0 or not themes_ent:
        return {}, {}

    lundi, dimanche = bornes_semaine(today)
    jours_restants = (dimanche - today).days + 1
    today_dt = _debut_du_jour(today)
    reserves: dict[str, list[QueueItem]] = {}
    quotas: dict[str, QuotaTheme] = {}

    par_theme: dict[str, list[CardRow]] = {}
    for row in cards:
        if row.cours in themes_ent:
            par_theme.setdefault(row.cours, []).append(row)
    dus_par_theme: dict[str, list[QueueItem]] = {}
    for item in dus:
        if item.entretien:
            dus_par_theme.setdefault(item.card.cours, []).append(item)

    for theme, cs in par_theme.items():
        fait_avant = fait_aujourdhui = planifie = 0.0
        sans_echeance_semaine: list[CardRow] = []
        for row in cs:
            revue = row.card.last_review
            if revue is None:
                continue  # carte neuve: apprentissage, hors quota
            pts = row.points(config)
            if revue.date() == today:
                fait_aujourdhui += pts
                continue
            if lundi <= revue.date() < today:
                fait_avant += pts
            # Thème en entretien: aucun test à venir, l'échéance suit FSRS pur.
            echeance = effective_due(row.card, [], today, config, row.id)
            if echeance.date() <= dimanche:
                planifie += pts  # FSRS l'a déjà planifiée cette semaine
            elif revue.date() < lundi:
                sans_echeance_semaine.append(row)

        # Plancher: ce qui est RÉELLEMENT fait compte, pas ce qui est seulement
        # planifié (une carte planifiée mais jamais servie ne tient pas le quota).
        manque = quota - fait_avant
        # Étalement: le manque se répartit sur les jours restants; ce qui a déjà
        # été révisé aujourd'hui est débité de la part du jour.
        cible = max(0.0, manque / jours_restants - fait_aujourdhui) if manque > EPS else 0.0
        # Complément: seulement si FSRS n'a pas déjà planifié assez dans la semaine.
        manque_planifie = quota - (fait_avant + planifie)
        complement_max = (
            max(0.0, manque_planifie / jours_restants - fait_aujourdhui)
            if manque_planifie > EPS else 0.0
        )
        quotas[theme] = QuotaTheme(
            theme=theme, quota=quota, fait_avant_aujourdhui=fait_avant,
            planifie_fsrs=planifie, manque=max(0.0, manque), allocation_jour=cible,
        )
        if cible <= EPS:
            continue

        choisis: list[QueueItem] = []
        accumule = 0.0
        # 1. Les cartes du thème déjà dues selon FSRS, plus faible rétention d'abord.
        dues = sorted(
            dus_par_theme.get(theme, []),
            key=lambda i: (retrievability_at(i.card.card, today_dt), i.card.id),
        )
        for item in dues:
            if accumule + EPS >= cible:
                break
            accumule += item.card.points(config)
            choisis.append(replace(item, plancher=True))

        # 2. Compléments: cartes non dues, plus faible rétention, poids légers
        #    de préférence (les lourds seulement s'il n'existe rien d'autre).
        if accumule + EPS < cible and complement_max > EPS:
            legers = [
                r for r in sans_echeance_semaine
                if r.points(config) <= config.entretien_poids_max + EPS
            ]
            candidats = legers or sans_echeance_semaine
            candidats.sort(key=lambda r: (retrievability_at(r.card, today_dt), r.id))
            ajoute = 0.0
            for row in candidats:
                if accumule + EPS >= cible or ajoute + EPS >= complement_max:
                    break
                pts = row.points(config)
                accumule += pts
                ajoute += pts
                choisis.append(
                    QueueItem(
                        card=row, due=today_dt, jours_retard=0, prochain_examen=None,
                        nouvelle=False,
                        points=poids_plafonne(pts, config.plafond_quotidien),
                        entretien=True, complement=True, plancher=True,
                    )
                )
        if choisis:
            reserves[theme] = choisis
    return reserves, quotas


# --- Assemblage de la journée ----------------------------------------------

def _tourniquet(
    files: dict[str, deque[QueueItem]],
    rotation: list[str],
    choisis: list[QueueItem],
    budget: float,
    cap: float,
    cap_total: float,
) -> tuple[float, float]:
    """Une carte par thème et par tour, dans l'ordre de rotation, tant que le
    budget du jour et le plafond d'entretien le permettent. Renvoie ce qui reste."""
    progres = True
    while progres and cap > EPS and budget > EPS:
        progres = False
        for theme in rotation:
            file_theme = files.get(theme)
            while file_theme:
                item = file_theme.popleft()
                pts_cap = poids_plafonne(item.points, cap_total)
                if item.points <= budget + EPS and pts_cap <= cap + EPS:
                    choisis.append(item)
                    budget -= item.points
                    cap -= pts_cap
                    progres = True
                    break  # un seul par thème et par tour
                # Ne tient pas aujourd'hui: sautée (reste due), on essaie la suivante.
            if cap <= EPS or budget <= EPS:
                break
    return budget, cap


def _repartir_themes(
    candidates: list[QueueItem],
    budget: float,
    today: date,
    config: SchedulerConfig,
) -> tuple[list[QueueItem], float]:
    """Choisit les cartes du mode test en répartissant le budget entre thèmes.

    `candidates` est déjà trié par priorité. Trois temps:

    1. thèmes « focus » (test dans au plus `jours_focus_test` jours): leurs
       cartes passent d'abord, sans limite de part;
    2. le budget restant se partage à parts égales entre les autres thèmes
       (glouton dans chaque part: une carte qui ne tient pas est sautée), en
       plusieurs tours pour redonner équitablement ce qu'un thème n'utilise pas;
    3. les cartes qui ne tenaient dans aucune part passent ensuite, par priorité.

    Renvoie les cartes retenues et le budget restant.
    """
    def focus(item: QueueItem) -> bool:
        ex = item.prochain_examen
        return ex is not None and (ex.date - today).days <= config.jours_focus_test

    pris: list[QueueItem] = []
    ids: set[str] = set()

    def prendre(item: QueueItem) -> None:
        nonlocal budget
        pris.append(item)
        ids.add(item.card.id)
        budget -= item.points

    # 1. Focus: tout ce qui tient, par priorité.
    for item in candidates:
        if focus(item) and item.points <= budget + EPS:
            prendre(item)

    # 2. Parts égales entre les thèmes restants, en tours: ce qu'un thème n'utilise
    #    pas est redonné, à parts égales encore, à ceux qui ont des cartes en plus.
    #    Une carte plus lourde que la part reste servie si c'est la première du
    #    thème (sinon un thème aux cartes lourdes ne passerait jamais).
    restantes: dict[str, deque[QueueItem]] = {}
    for item in candidates:
        if item.card.id not in ids:
            restantes.setdefault(item.card.cours, deque()).append(item)
    servis: set[str] = set()
    while budget > EPS:
        actifs = [t for t, q in restantes.items() if q]
        if not actifs:
            break
        part = budget / len(actifs)
        progres = False
        for theme in actifs:
            reste = part
            file_theme = restantes[theme]
            for _ in range(len(file_theme)):
                item = file_theme.popleft()
                tient = item.points <= reste + EPS or theme not in servis
                if tient and item.points <= budget + EPS:
                    prendre(item)
                    reste -= item.points
                    servis.add(theme)
                    progres = True
                else:
                    file_theme.append(item)  # reste due, on essaie la suivante
        if not progres:
            break

    # 3. Ce qui ne tenait dans aucune part (cartes lourdes): par priorité.
    for item in candidates:
        if item.card.id not in ids and item.points <= budget + EPS:
            prendre(item)
    return pris, budget


def construire_file(
    cards: list[CardRow],
    exams: list[Exam],
    today: date,
    config: SchedulerConfig,
) -> FileDuJour:
    """La journée: cartes dues + compléments d'entretien, dans le budget en points."""
    plafond = config.plafond_quotidien
    themes_ent = themes_en_entretien({c.cours for c in cards}, exams, today)
    dus = _items_dus(cards, exams, today, config, themes_ent)
    _tri_priorite(dus, today, config)

    reserves, quotas = _reserver_plancher(cards, dus, themes_ent, today, config)
    complements = [i for items in reserves.values() for i in items if i.complement]

    # Rotation: le thème dont la dernière révision est la plus ancienne d'abord.
    derniere = {
        t: _derniere_revue([c for c in cards if c.cours == t]) for t in themes_ent
    }
    rotation = sorted(themes_ent, key=lambda t: (derniere[t], t))

    # Plafond d'entretien du jour, débité de ce qui a déjà été révisé aujourd'hui.
    deja_fait = sum(
        c.points(config) for c in cards
        if c.cours in themes_ent and c.card.last_review is not None
        and c.card.last_review.date() == today
    )
    cap_total = config.entretien_plafond_jour
    cap = max(0.0, cap_total - deja_fait)

    budget = plafond
    choisis: list[QueueItem] = []

    # 1. Plancher d'entretien: la part du jour réservée, avant le mode test.
    budget, cap = _tourniquet(
        {t: deque(items) for t, items in reserves.items()}, rotation, choisis, budget, cap, cap_total,
    )
    deja_pris = {i.card.id for i in choisis}

    # 2. Mode test et cartes neuves: répartition entre thèmes (voir l'en-tête),
    #    puis présentation dans l'ordre de priorité.
    candidates = [i for i in dus if not i.entretien and i.card.id not in deja_pris]
    pris, budget = _repartir_themes(candidates, budget, today, config)
    rang = {i.card.id: n for n, i in enumerate(dus)}
    choisis.extend(sorted(pris, key=lambda i: rang[i.card.id]))

    # 3. Le reste de l'entretien (dû selon FSRS), priorité la plus basse, dans
    #    ce qui reste du plafond d'entretien et du budget.
    reste: dict[str, deque[QueueItem]] = {}
    for theme in rotation:
        propres = [
            i for i in dus
            if i.entretien and i.card.cours == theme and i.card.id not in deja_pris
        ]
        propres.sort(key=lambda i: (retrievability_at(i.card.card, _debut_du_jour(today)), i.card.id))
        reste[theme] = deque(propres)
    budget, cap = _tourniquet(reste, rotation, choisis, budget, cap, cap_total)

    return FileDuJour(
        jour=today, plafond=plafond, items=choisis, dus=dus, complements=complements,
        themes_entretien=themes_ent, quotas=quotas, entretien_plafond_restant=max(0.0, cap),
    )
