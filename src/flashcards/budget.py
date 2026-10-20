"""Protection du crédit API: plafonds, registre, disjoncteur, idempotence.

Toute dépense possible passe par ce module. Le principe est l'inverse d'un
quota « best effort »: **par défaut on refuse**. Un appel n'est autorisé que
si son coût estimé tient sous les trois plafonds (opération, jour, mois), que
la cadence est respectée, que le disjoncteur est fermé et que l'interrupteur
d'urgence n'est pas tiré.

Les plafonds sont évalués sur le registre SQLite, pas sur un compteur en
mémoire: un redémarrage du serveur ne remet donc rien à zéro.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Tarifs USD par million de tokens. Sert à estimer AVANT l'appel et à
# comptabiliser APRÈS. Un modèle absent de cette table est refusé: on ne peut
# pas protéger un budget dont on ignore le prix.
TARIFS: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# Modèles autorisés comme réglage. Verrouiller cette liste empêche qu'un
# réglage (ou une injection) pointe vers un modèle plus cher que prévu.
MODELES_AUTORISES = frozenset(TARIFS)

# Juge des réponses libres, servi par OpenRouter (identifiants « fournisseur/
# modèle », tarifs de https://openrouter.ai/api/v1/models, USD par million de
# tokens). Liste fermée, comme ci-dessus. Seuls des modèles dont le raisonnement
# est OPTIONNEL y figurent: le juge le coupe, sinon il consommerait le budget de
# sortie avant d'écrire son verdict. Ils restent séparés de `TARIFS` pour ne
# jamais devenir, par erreur, le modèle d'extraction/génération.
TARIFS_JUGE: dict[str, tuple[float, float]] = {
    "anthropic/claude-haiku-5.5": (0.10, 0.50),
    "google/gemini-2.5-flash": (0.30, 2.50),
}
MODELES_JUGE_AUTORISES = frozenset(TARIFS_JUGE)

# ~3 caractères par token: approximation locale, volontairement pessimiste
# (les tokenizers récents produisent jusqu'à ~1,35x plus de tokens que
# l'ancienne règle des 4 caractères). On n'appelle pas `count_tokens` pour
# estimer, car ce serait déjà une requête réseau — donc un risque — avant
# d'avoir vérifié le budget.
CARACTERES_PAR_TOKEN = 3
# Un PDF ou une image coûte bien plus que son poids en texte: chaque page de
# PDF est envoyée en texte (1 500 à 3 000 tokens) ET en image. Majorations
# calées sur les ordres de grandeur documentés (jusqu'à ~4 800 tokens/image).
TOKENS_PAR_PAGE_PDF = 3_000
TOKENS_PAR_IMAGE = 5_000
# Schéma de sortie structurée + enrobage du message: coût fixe par appel.
TOKENS_FIXES_PAR_APPEL = 1_500
# Marge de sécurité sur l'estimation: mieux vaut refuser à tort que dépasser.
MARGE_ESTIMATION = 1.25


class BudgetDepasse(RuntimeError):
    """Un plafond est atteint: l'appel est refusé avant toute dépense."""


class LLMDesactive(RuntimeError):
    """`LLM_DISABLED=1`: interrupteur d'urgence tiré."""


class DisjoncteurOuvert(RuntimeError):
    """Trop d'échecs consécutifs: on arrête de taper sur l'API."""


class EntreeTropGrande(RuntimeError):
    """L'entrée dépasse une limite dure (taille, pages, nombre d'images)."""


@dataclass(frozen=True)
class Plafonds:
    cout_max_jour_usd: float
    cout_max_mois_usd: float
    cout_max_operation_usd: float
    appels_max_par_minute: int
    echecs_avant_disjoncteur: int
    duree_disjoncteur_minutes: int
    max_fichier_mo: float
    max_pages_pdf: int
    max_images: int
    max_caracteres_texte: int
    # Somme des fichiers d'un même envoi (la requête API plafonne à 32 Mo
    # une fois encodée en base64, soit ~24 Mo de fichiers).
    max_total_mo: float = 24.0

    @property
    def max_total_octets(self) -> int:
        return int(self.max_total_mo * 1024 * 1024)

    @property
    def max_fichier_octets(self) -> int:
        return int(self.max_fichier_mo * 1024 * 1024)


def _env_float(nom: str, defaut: float) -> float:
    valeur = os.environ.get(nom)
    if valeur is None or not valeur.strip():
        return defaut
    try:
        return float(valeur)
    except ValueError as e:
        raise RuntimeError(f"{nom} doit être un nombre, reçu {valeur!r}") from e


# Garde-fou de saisie: un plafond n'est pas un plafond s'il vaut `inf`, `nan`
# (toute comparaison avec `nan` est fausse: rien ne serait jamais refusé) ou
# 100 au lieu de 1.00. Au-delà de ces valeurs il faut le demander deux fois,
# avec FLASHCARDS_PLAFONDS_ELEVES=1.
PLAFONDS_MAXIMUM_USD = {
    "cout_max_jour_usd": 5.0,
    "cout_max_mois_usd": 20.0,
    "cout_max_operation_usd": 2.0,
}


def _valider(plafonds: "Plafonds") -> "Plafonds":
    for champ in (
        "cout_max_jour_usd", "cout_max_mois_usd", "cout_max_operation_usd",
        "appels_max_par_minute", "echecs_avant_disjoncteur",
        "duree_disjoncteur_minutes", "max_fichier_mo", "max_pages_pdf",
        "max_images", "max_caracteres_texte", "max_total_mo",
    ):
        valeur = getattr(plafonds, champ)
        if not math.isfinite(valeur) or valeur < 0:
            raise RuntimeError(
                f"Plafond « {champ} » invalide ({valeur!r}): un nombre fini et "
                f"positif est exigé. Aucun appel LLM tant que ce n'est pas corrigé."
            )
    if os.environ.get("FLASHCARDS_PLAFONDS_ELEVES") != "1":
        for champ, maximum in PLAFONDS_MAXIMUM_USD.items():
            if getattr(plafonds, champ) > maximum:
                raise RuntimeError(
                    f"Plafond « {champ} » = {getattr(plafonds, champ)} USD, au-dessus "
                    f"du garde-fou de {maximum:.2f} USD. Si c'est voulu, ajoute "
                    f"FLASHCARDS_PLAFONDS_ELEVES=1; sinon corrige la valeur."
                )
    return plafonds


def _env_int(nom: str, defaut: int) -> int:
    return int(_env_float(nom, defaut))


def charger_plafonds(brut: dict | None = None) -> Plafonds:
    """Plafonds depuis `config.toml`, surchargeables par l'environnement.

    Les variables d'environnement priment: sur un serveur, on doit pouvoir
    resserrer les plafonds sans redéployer d'image.
    """
    brut = brut or {}
    return _valider(Plafonds(
        cout_max_jour_usd=_env_float(
            "FLASHCARDS_COUT_MAX_JOUR", brut.get("cout_max_jour_usd", 1.00)
        ),
        cout_max_mois_usd=_env_float(
            "FLASHCARDS_COUT_MAX_MOIS", brut.get("cout_max_mois_usd", 5.00)
        ),
        cout_max_operation_usd=_env_float(
            "FLASHCARDS_COUT_MAX_OPERATION", brut.get("cout_max_operation_usd", 0.50)
        ),
        appels_max_par_minute=_env_int(
            "FLASHCARDS_APPELS_MAX_MINUTE", brut.get("appels_max_par_minute", 10)
        ),
        echecs_avant_disjoncteur=_env_int(
            "FLASHCARDS_ECHECS_DISJONCTEUR", brut.get("echecs_avant_disjoncteur", 3)
        ),
        duree_disjoncteur_minutes=_env_int(
            "FLASHCARDS_DUREE_DISJONCTEUR", brut.get("duree_disjoncteur_minutes", 10)
        ),
        max_fichier_mo=_env_float(
            "FLASHCARDS_MAX_FICHIER_MO", brut.get("max_fichier_mo", 20)
        ),
        max_pages_pdf=_env_int("FLASHCARDS_MAX_PAGES_PDF", brut.get("max_pages_pdf", 60)),
        max_images=_env_int("FLASHCARDS_MAX_IMAGES", brut.get("max_images", 10)),
        max_caracteres_texte=_env_int(
            "FLASHCARDS_MAX_CARACTERES_TEXTE", brut.get("max_caracteres_texte", 50_000)
        ),
        max_total_mo=_env_float(
            "FLASHCARDS_MAX_TOTAL_MO", brut.get("max_total_mo", 24)
        ),
    ))


# --- Interrupteur d'urgence ------------------------------------------------

CLE_ARRET = "llm_desactive"


def arret_par_env() -> bool:
    return os.environ.get("LLM_DISABLED") == "1"


def arret_en_base(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT valeur FROM settings WHERE cle = ?", (CLE_ARRET,)
    ).fetchone()
    return row is not None and row["valeur"] == "1"


def llm_desactive(conn: sqlite3.Connection | None = None) -> bool:
    """Interrupteur tiré par l'environnement OU par la commande/route d'arrêt.

    L'un ou l'autre suffit à couper; il faut lever les deux pour réactiver.
    """
    return arret_par_env() or (conn is not None and arret_en_base(conn))


def basculer_arret(conn: sqlite3.Connection, coupe: bool) -> None:
    """Coupe (ou rétablit) les appels LLM de façon durable, sans redémarrage.

    Stocké en base: vu aussitôt par le serveur web ET par le CLI. Ne peut
    pas lever un `LLM_DISABLED=1` posé dans l'environnement.
    """
    conn.execute(
        "INSERT INTO settings (cle, valeur) VALUES (?, ?)"
        " ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
        (CLE_ARRET, "1" if coupe else "0"),
    )
    conn.commit()


# --- Estimation ------------------------------------------------------------

def tarif(modele: str) -> tuple[float, float]:
    if modele in TARIFS:
        return TARIFS[modele]
    if modele in TARIFS_JUGE:
        return TARIFS_JUGE[modele]
    raise BudgetDepasse(
        f"Modèle « {modele} » absent de la table de tarifs: appel refusé. "
        f"Modèles autorisés: {', '.join(sorted(MODELES_AUTORISES | MODELES_JUGE_AUTORISES))}."
    )


def cout_usd(modele: str, tokens_entree: int, tokens_sortie: int) -> float:
    entree, sortie = tarif(modele)
    return tokens_entree / 1_000_000 * entree + tokens_sortie / 1_000_000 * sortie


def estimer_tokens_entree(
    *, texte: str = "", pages_pdf: int = 0, nb_images: int = 0
) -> int:
    """Estimation locale et majorée des tokens d'entrée (aucun appel réseau)."""
    tokens = len(texte) / CARACTERES_PAR_TOKEN
    tokens += pages_pdf * TOKENS_PAR_PAGE_PDF
    tokens += nb_images * TOKENS_PAR_IMAGE
    tokens += TOKENS_FIXES_PAR_APPEL
    return int(tokens * MARGE_ESTIMATION)


def estimer_cout(
    modele: str, tokens_entree: int, max_tokens_sortie: int
) -> float:
    """Coût estimé en supposant que la sortie remplit `max_tokens`.

    Majorer la sortie est volontaire: c'est le pire cas réel, et c'est lui
    qu'il faut comparer au plafond par opération.
    """
    return cout_usd(modele, tokens_entree, max_tokens_sortie)


# --- Limites d'entrée ------------------------------------------------------

def compter_pages_pdf(chemin: Path) -> int:
    """Nombre de pages d'un PDF. Un PDF illisible est refusé, pas deviné."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        return len(PdfReader(str(chemin)).pages)
    except (PdfReadError, ValueError, OSError) as e:
        raise EntreeTropGrande(f"PDF illisible ou corrompu ({chemin.name}): {e}") from e


def verifier_entree(
    plafonds: Plafonds,
    *,
    fichiers: tuple[Path, ...] = (),
    texte: str | None = None,
    voie: str = "texte",
) -> None:
    """Refuse une entrée hors limites AVANT toute dépense."""
    if texte is not None and len(texte) > plafonds.max_caracteres_texte:
        raise EntreeTropGrande(
            f"Texte trop long: {len(texte)} caractères, maximum "
            f"{plafonds.max_caracteres_texte}."
        )

    if voie == "image" and len(fichiers) > plafonds.max_images:
        raise EntreeTropGrande(
            f"Trop d'images: {len(fichiers)}, maximum {plafonds.max_images}."
        )
    if voie == "pdf" and len(fichiers) > 1:
        raise EntreeTropGrande("Un seul PDF à la fois.")

    total = 0
    for fichier in fichiers:
        taille = fichier.stat().st_size
        total += taille
        if total > plafonds.max_total_octets:
            raise EntreeTropGrande(
                f"Envoi trop lourd: plus de {plafonds.max_total_mo:.0f} Mo au total. "
                f"Envoie moins de fichiers à la fois."
            )
        if taille > plafonds.max_fichier_octets:
            raise EntreeTropGrande(
                f"Fichier trop lourd ({fichier.name}): "
                f"{taille / 1024 / 1024:.1f} Mo, maximum {plafonds.max_fichier_mo} Mo."
            )
        if fichier.suffix.lower() == ".pdf":
            pages = compter_pages_pdf(fichier)
            if pages > plafonds.max_pages_pdf:
                raise EntreeTropGrande(
                    f"PDF trop long ({fichier.name}): {pages} pages, "
                    f"maximum {plafonds.max_pages_pdf}."
                )


# --- Registre --------------------------------------------------------------

def _debut_jour(maintenant: datetime) -> str:
    return maintenant.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _debut_mois(maintenant: datetime) -> str:
    return maintenant.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ).isoformat()


def consommation(
    conn: sqlite3.Connection, maintenant: datetime | None = None
) -> dict[str, float | int]:
    """Dépense du jour et du mois, lue dans le registre.

    Compte tout ce qui a pu être facturé: les succès (coût réel), les appels
    en cours (coût réservé) et les échecs dont on ne peut pas exclure la
    facturation (délai dépassé, réponse reçue mais inexploitable: coût
    réservé conservé). Seuls les refus, jamais partis, valent zéro.
    """
    maintenant = maintenant or datetime.now(timezone.utc)
    jour = conn.execute(
        "SELECT COALESCE(SUM(cout_usd), 0) AS c, COUNT(*) AS n FROM llm_appels"
        " WHERE horodatage >= ? AND statut != 'refuse'",
        (_debut_jour(maintenant),),
    ).fetchone()
    mois = conn.execute(
        "SELECT COALESCE(SUM(cout_usd), 0) AS c, COUNT(*) AS n FROM llm_appels"
        " WHERE horodatage >= ? AND statut != 'refuse'",
        (_debut_mois(maintenant),),
    ).fetchone()
    return {
        "cout_jour_usd": round(jour["c"], 6),
        "appels_jour": jour["n"],
        "cout_mois_usd": round(mois["c"], 6),
        "appels_mois": mois["n"],
    }


def enregistrer(
    conn: sqlite3.Connection,
    *,
    operation: str,
    modele: str,
    statut: str,
    tokens_entree: int = 0,
    tokens_sortie: int = 0,
    cout: float = 0.0,
    cout_estime: float = 0.0,
    detail: str | None = None,
    empreinte: str | None = None,
    duree_ms: int | None = None,
    maintenant: datetime | None = None,
) -> int:
    maintenant = maintenant or datetime.now(timezone.utc)
    curseur = conn.execute(
        "INSERT INTO llm_appels (horodatage, operation, modele, tokens_entree,"
        " tokens_sortie, cout_usd, cout_estime_usd, statut, detail, empreinte, duree_ms)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            maintenant.isoformat(), operation, modele, tokens_entree, tokens_sortie,
            cout, cout_estime, statut, detail, empreinte, duree_ms,
        ),
    )
    conn.commit()
    return curseur.lastrowid


# --- Disjoncteur et cadence ------------------------------------------------

def disjoncteur_ouvert(
    conn: sqlite3.Connection, plafonds: Plafonds, maintenant: datetime | None = None
) -> bool:
    """Ouvert si les N derniers appels sont tous des échecs, et récents.

    Lu depuis le registre: l'état survit à un redémarrage, contrairement à un
    compteur en mémoire qu'un crash-loop remettrait à zéro.
    """
    maintenant = maintenant or datetime.now(timezone.utc)
    limite = maintenant - timedelta(minutes=plafonds.duree_disjoncteur_minutes)

    derniers = conn.execute(
        "SELECT statut, horodatage FROM llm_appels"
        " WHERE statut IN ('succes', 'echec')"
        " ORDER BY id DESC LIMIT ?",
        (plafonds.echecs_avant_disjoncteur,),
    ).fetchall()

    if len(derniers) < plafonds.echecs_avant_disjoncteur:
        return False
    if any(r["statut"] != "echec" for r in derniers):
        return False
    # Le plus récent des échecs doit être dans la fenêtre de blocage.
    return datetime.fromisoformat(derniers[0]["horodatage"]) >= limite


def appels_derniere_minute(
    conn: sqlite3.Connection, maintenant: datetime | None = None
) -> int:
    maintenant = maintenant or datetime.now(timezone.utc)
    depuis = (maintenant - timedelta(minutes=1)).isoformat()
    return conn.execute(
        "SELECT COUNT(*) AS n FROM llm_appels WHERE horodatage >= ?"
        " AND statut != 'refuse'",
        (depuis,),
    ).fetchone()["n"]


# --- Autorisation ----------------------------------------------------------

def _motif_refus(
    conn: sqlite3.Connection,
    plafonds: Plafonds,
    cout_estime: float,
    maintenant: datetime,
) -> Exception | None:
    """La raison de refuser cet appel, ou None s'il peut partir."""
    if llm_desactive(conn):
        return LLMDesactive(
            "Appels au modèle coupés (interrupteur d'urgence). Pour réactiver: "
            "retire LLM_DISABLED=1 de l'environnement et lance `flashcards llm on`."
        )

    if disjoncteur_ouvert(conn, plafonds, maintenant):
        return DisjoncteurOuvert(
            f"Disjoncteur ouvert: {plafonds.echecs_avant_disjoncteur} échecs "
            f"consécutifs. Nouvelle tentative possible dans "
            f"{plafonds.duree_disjoncteur_minutes} minutes."
        )

    cadence = appels_derniere_minute(conn, maintenant)
    if cadence >= plafonds.appels_max_par_minute:
        return BudgetDepasse(
            f"Cadence dépassée: {cadence} appels sur la dernière minute, "
            f"maximum {plafonds.appels_max_par_minute}."
        )

    if cout_estime > plafonds.cout_max_operation_usd:
        return BudgetDepasse(
            f"Coût estimé de l'opération ({cout_estime:.4f} USD) au-dessus du "
            f"plafond par opération ({plafonds.cout_max_operation_usd:.2f} USD). "
            f"Réduis la taille du document."
        )

    conso = consommation(conn, maintenant)
    if conso["cout_jour_usd"] + cout_estime > plafonds.cout_max_jour_usd:
        return BudgetDepasse(
            f"Plafond journalier atteint: {conso['cout_jour_usd']:.4f} USD déjà "
            f"dépensés + {cout_estime:.4f} estimés > "
            f"{plafonds.cout_max_jour_usd:.2f} USD."
        )
    if conso["cout_mois_usd"] + cout_estime > plafonds.cout_max_mois_usd:
        return BudgetDepasse(
            f"Plafond mensuel atteint: {conso['cout_mois_usd']:.4f} USD déjà "
            f"dépensés + {cout_estime:.4f} estimés > "
            f"{plafonds.cout_max_mois_usd:.2f} USD."
        )
    return None


def autoriser(
    conn: sqlite3.Connection,
    plafonds: Plafonds,
    *,
    operation: str,
    modele: str,
    cout_estime: float,
    maintenant: datetime | None = None,
) -> None:
    """Lève une exception si l'appel ne doit PAS avoir lieu.

    Chaque refus est inscrit au registre: un plafond atteint doit être
    visible après coup, pas seulement affiché une fois à l'écran.
    """
    maintenant = maintenant or datetime.now(timezone.utc)
    refus = _motif_refus(conn, plafonds, cout_estime, maintenant)
    if refus is not None:
        enregistrer(
            conn, operation=operation, modele=modele, statut="refuse",
            cout_estime=cout_estime, detail=str(refus), maintenant=maintenant,
        )
        raise refus


def reserver(
    conn: sqlite3.Connection,
    plafonds: Plafonds,
    *,
    operation: str,
    modele: str,
    cout_estime: float,
    empreinte: str | None = None,
    maintenant: datetime | None = None,
) -> int:
    """Autorise l'appel ET réserve son coût estimé, en une seule transaction.

    La réservation compte tout de suite dans la consommation: deux appels
    lancés en même temps (CLI + web, deux processus) ne peuvent pas passer
    tous les deux sous le plafond, et un processus tué en plein appel laisse
    son coût au registre au lieu de le faire disparaître. `solder` remplace
    ensuite l'estimation par le coût réel.
    """
    maintenant = maintenant or datetime.now(timezone.utc)
    if not conn.in_transaction:
        # Verrou d'écriture pris AVANT la lecture des plafonds.
        conn.execute("BEGIN IMMEDIATE")
    refus = _motif_refus(conn, plafonds, cout_estime, maintenant)
    if refus is not None:
        enregistrer(
            conn, operation=operation, modele=modele, statut="refuse",
            cout_estime=cout_estime, detail=str(refus), maintenant=maintenant,
        )
        raise refus
    return enregistrer(
        conn, operation=operation, modele=modele, statut="en_cours",
        cout=cout_estime, cout_estime=cout_estime, empreinte=empreinte,
        maintenant=maintenant,
    )


def solder(
    conn: sqlite3.Connection,
    appel_id: int,
    *,
    statut: str,
    cout: float,
    tokens_entree: int = 0,
    tokens_sortie: int = 0,
    detail: str | None = None,
    duree_ms: int | None = None,
) -> None:
    """Clôt une réservation avec le coût réellement retenu."""
    conn.execute(
        "UPDATE llm_appels SET statut = ?, cout_usd = ?, tokens_entree = ?,"
        " tokens_sortie = ?, detail = ?, duree_ms = ? WHERE id = ?",
        (statut, cout, tokens_entree, tokens_sortie, detail, duree_ms, appel_id),
    )
    conn.commit()


def clore_reservations_orphelines(conn: sqlite3.Connection) -> int:
    """Au démarrage: un appel resté « en cours » a été coupé en plein vol.

    Son coût réservé est CONSERVÉ (la requête a pu partir et être facturée).
    """
    nb = conn.execute(
        "UPDATE llm_appels SET statut = 'echec',"
        " detail = 'Interrompu en cours d''appel: coût réservé conservé.'"
        " WHERE statut = 'en_cours'"
    ).rowcount
    conn.commit()
    return nb


def alerte_80_pourcent(
    conn: sqlite3.Connection, plafonds: Plafonds, maintenant: datetime | None = None
) -> list[str]:
    conso = consommation(conn, maintenant)
    alertes = []
    for libelle, consomme, plafond in [
        ("journalier", conso["cout_jour_usd"], plafonds.cout_max_jour_usd),
        ("mensuel", conso["cout_mois_usd"], plafonds.cout_max_mois_usd),
    ]:
        if plafond > 0 and consomme >= 0.8 * plafond:
            alertes.append(
                f"Plafond {libelle} à {consomme / plafond:.0%} "
                f"({consomme:.4f} / {plafond:.2f} USD)."
            )
    return alertes


# --- Idempotence -----------------------------------------------------------

def empreinte_entree(
    *, operation: str, modele: str, texte: str | None, fichiers: tuple[Path, ...],
    extra: str = "",
) -> str:
    """Hash stable du travail demandé: même entrée = même empreinte.

    Les fichiers sont hashés par leur contenu, pas leur nom: réenvoyer le
    même PDF sous un autre nom ne doit pas repayer l'appel.
    """
    h = hashlib.sha256()
    h.update(operation.encode())
    h.update(b"\0")
    h.update(modele.encode())
    h.update(b"\0")
    h.update((texte or "").encode())
    h.update(b"\0")
    h.update(extra.encode())
    for fichier in sorted(fichiers, key=lambda p: p.name):
        h.update(b"\0")
        h.update(hashlib.sha256(fichier.read_bytes()).digest())
    return h.hexdigest()


def cache_existe(conn: sqlite3.Connection, empreinte: str) -> bool:
    """Lecture seule (ne touche pas au compteur d'économies)."""
    return conn.execute(
        "SELECT 1 FROM llm_cache WHERE empreinte = ?", (empreinte,)
    ).fetchone() is not None


def lire_cache(
    conn: sqlite3.Connection, empreinte: str
) -> dict | None:
    row = conn.execute(
        "SELECT resultat FROM llm_cache WHERE empreinte = ?", (empreinte,)
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE llm_cache SET reutilise = reutilise + 1 WHERE empreinte = ?",
        (empreinte,),
    )
    conn.commit()
    return json.loads(row["resultat"])


def ecrire_cache(
    conn: sqlite3.Connection, empreinte: str, operation: str, modele: str,
    resultat: dict, maintenant: datetime | None = None,
) -> None:
    maintenant = maintenant or datetime.now(timezone.utc)
    conn.execute(
        "INSERT OR REPLACE INTO llm_cache (empreinte, operation, modele, resultat, cree_le)"
        " VALUES (?,?,?,?,?)",
        (empreinte, operation, modele, json.dumps(resultat, ensure_ascii=False),
         maintenant.isoformat()),
    )
    conn.commit()
