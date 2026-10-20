"""Authentification: argon2 + session par cookie signé, comptes à rôles.

Trois rôles (`ROLES`): admin, standard, étendu. Le premier compte (admin) vient de
la variable d'environnement `ADMIN_PASSWORD` ou de la commande
`flashcards web create-user` — jamais écrit en dur. Les autres se créent depuis
la page Comptes (admin). Les données de l'appli restent partagées: le rôle
limite ce qu'un compte peut faire, pas ce qu'il voit.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from passlib.context import CryptContext

COOKIE_NAME = "flashcards_session"
SESSION_MAX_AGE = int(timedelta(days=30).total_seconds())

# Limitation de la force brute: au-delà de MAX_TENTATIVES échecs dans la
# fenêtre, on refuse même un mot de passe correct le temps du blocage.
MAX_TENTATIVES = 5
FENETRE_SECONDES = 15 * 60
# Second seuil, toutes adresses confondues: borne une attaque répartie sur
# beaucoup d'adresses (au plus ~4 800 essais par jour).
MAX_TENTATIVES_GLOBAL = 50
CLE_GLOBALE = "*"

LONGUEUR_MIN_MDP = 12
# Argon2 hache tout ce qu'on lui donne: un « mot de passe » de 50 Mo est un
# déni de service gratuit sur une route publique.
LONGUEUR_MAX_MDP = 256
LONGUEUR_MIN_SECRET = 32

journal = logging.getLogger("flashcards.securite")


def en_developpement() -> bool:
    """Vrai seulement si FLASHCARDS_ENV=development est posé explicitement.

    Sans la variable, on est en production: cookie Secure, pas de CORS,
    secret obligatoire. Oublier la variable ne doit jamais ouvrir le
    serveur; `flashcards web serve` la pose lui-même en écoute locale.
    """
    return os.environ.get("FLASHCARDS_ENV") == "development"

_pwd = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_mdp(mot_de_passe: str) -> str:
    return _pwd.hash(mot_de_passe)


def verifier_mdp(mot_de_passe: str, hash_stocke: str) -> bool:
    return _pwd.verify(mot_de_passe, hash_stocke)


_secret_ephemere: str | None = None


def secret_session() -> str:
    """Secret de signature des cookies. Aucune valeur par défaut connue.

    Un secret devinable permet de fabriquer un cookie valide, donc d'entrer
    sans mot de passe. En production il DOIT venir de l'environnement et
    être long; en développement, à défaut, il est tiré au hasard pour la
    durée du processus (un redémarrage déconnecte).
    """
    global _secret_ephemere
    secret = os.environ.get("SESSION_SECRET")
    if en_developpement():
        if secret:
            return secret
        if _secret_ephemere is None:
            _secret_ephemere = secrets.token_urlsafe(32)
        return _secret_ephemere
    if not secret or len(secret) < LONGUEUR_MIN_SECRET:
        raise RuntimeError(
            f"SESSION_SECRET est obligatoire (au moins {LONGUEUR_MIN_SECRET} "
            "caractères aléatoires: `openssl rand -hex 32`). Pour un serveur de "
            "développement local, pose FLASHCARDS_ENV=development."
        )
    return secret


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_session(), salt="flashcards-session")


def version_session(hash_stocke: str) -> str:
    """Empreinte du hash du mot de passe, embarquée dans le cookie.

    Changer le mot de passe (ou recréer le compte) change cette empreinte:
    toutes les sessions émises avant deviennent invalides. C'est le moyen de
    « déconnecter partout » si un cookie a fuité.
    """
    return hashlib.sha256(hash_stocke.encode()).hexdigest()[:16]


def creer_cookie(identifiant: str, version: str = "") -> str:
    return _serializer().dumps({"u": identifiant, "v": version})


def lire_cookie(valeur: str) -> dict | None:
    try:
        data = _serializer().loads(valeur, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None
    return data if isinstance(data, dict) and data.get("u") else None


# --- Comptes et rôles ------------------------------------------------------

ROLES = ("admin", "standard", "etendu")
LIBELLE_ROLE = {"admin": "Administrateur", "standard": "Standard", "etendu": "Étendu"}
LONGUEUR_MAX_IDENTIFIANT = 40
_MOTIF_IDENTIFIANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class Compte:
    identifiant: str
    role: str

    @property
    def admin(self) -> bool:
        return self.role == "admin"

    @property
    def etendu(self) -> bool:
        """Droit à la page Dépôt: le rôle étendu, et l'admin qui peut tout."""
        return self.role in ("admin", "etendu")


def cookie_pour(conn: sqlite3.Connection, identifiant: str) -> str:
    """Cookie de session d'un compte, lié à son mot de passe actuel."""
    row = conn.execute(
        "SELECT identifiant, hash_mdp FROM comptes WHERE identifiant = ?", (identifiant,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Compte inconnu.")
    return creer_cookie(row["identifiant"], version_session(row["hash_mdp"]))


def session_valide(conn: sqlite3.Connection, valeur: str) -> Compte | None:
    """Le compte si le cookie est signé, non expiré ET toujours d'actualité.

    Le rôle n'est PAS dans le cookie: il est relu en base à chaque requête,
    donc une rétrogradation ou une suppression prend effet immédiatement.
    """
    data = lire_cookie(valeur)
    if data is None:
        return None
    row = conn.execute(
        "SELECT identifiant, hash_mdp, role FROM comptes WHERE identifiant = ?",
        (str(data["u"]),),
    ).fetchone()
    if row is None:
        return None
    if not secrets.compare_digest(
        str(data.get("v", "")).encode(), version_session(row["hash_mdp"]).encode()
    ):
        return None
    return Compte(row["identifiant"], row["role"])


def compte_existe(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM comptes LIMIT 1").fetchone() is not None


def _verifier_force(mot_de_passe: str) -> None:
    if len(mot_de_passe) < LONGUEUR_MIN_MDP:
        raise RuntimeError(
            f"Le mot de passe doit faire au moins {LONGUEUR_MIN_MDP} caractères."
        )
    if len(mot_de_passe) > LONGUEUR_MAX_MDP:
        raise RuntimeError(
            f"Le mot de passe doit faire au plus {LONGUEUR_MAX_MDP} caractères."
        )


def _verifier_identifiant(identifiant: str) -> None:
    if not identifiant or len(identifiant) > LONGUEUR_MAX_IDENTIFIANT:
        raise RuntimeError(
            f"L'identifiant doit faire de 1 à {LONGUEUR_MAX_IDENTIFIANT} caractères."
        )
    if not _MOTIF_IDENTIFIANT.match(identifiant):
        raise RuntimeError(
            "L'identifiant ne peut contenir que des lettres, chiffres, « . », « _ » et « - »."
        )


def _verifier_role(role: str) -> None:
    if role not in ROLES:
        raise RuntimeError(f"Rôle inconnu: {role}. Rôles: {', '.join(ROLES)}.")


def creer_compte(
    conn: sqlite3.Connection, identifiant: str, mot_de_passe: str, role: str = "admin"
) -> None:
    """Crée le compte, ou le REMPLACE (mot de passe et rôle) s'il existe déjà.

    Pour la ligne de commande (`web create-user`). L'interface, elle, passe par
    `ajouter_compte`, qui refuse d'écraser un compte.
    """
    _verifier_identifiant(identifiant)
    _verifier_role(role)
    _verifier_force(mot_de_passe)
    existant = conn.execute(
        "SELECT role FROM comptes WHERE identifiant = ?", (identifiant,)
    ).fetchone()
    if existant and existant["role"] == "admin" and role != "admin":
        _refuser_si_dernier_admin(conn, identifiant)
    conn.execute(
        "INSERT INTO comptes (identifiant, hash_mdp, role, cree_le) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(identifiant) DO UPDATE SET hash_mdp = excluded.hash_mdp,"
        " role = excluded.role",
        (identifiant, hash_mdp(mot_de_passe), role, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def ajouter_compte(
    conn: sqlite3.Connection, identifiant: str, mot_de_passe: str, role: str
) -> None:
    if conn.execute("SELECT 1 FROM comptes WHERE identifiant = ?", (identifiant,)).fetchone():
        raise RuntimeError(f"Le compte « {identifiant} » existe déjà.")
    creer_compte(conn, identifiant, mot_de_passe, role)


def lister_comptes(conn: sqlite3.Connection) -> list[dict]:
    return [
        {"identifiant": r["identifiant"], "role": r["role"], "cree_le": r["cree_le"]}
        for r in conn.execute("SELECT identifiant, role, cree_le FROM comptes ORDER BY id")
    ]


def _refuser_si_dernier_admin(conn: sqlite3.Connection, identifiant: str) -> None:
    """Garde-fou: l'application ne doit jamais se retrouver sans administrateur."""
    autres = conn.execute(
        "SELECT COUNT(*) FROM comptes WHERE role = 'admin' AND identifiant != ?",
        (identifiant,),
    ).fetchone()[0]
    if autres == 0:
        raise RuntimeError("C'est le dernier administrateur: il en faut toujours un.")


def changer_role(conn: sqlite3.Connection, identifiant: str, role: str) -> None:
    _verifier_role(role)
    row = conn.execute(
        "SELECT role FROM comptes WHERE identifiant = ?", (identifiant,)
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Compte inconnu: {identifiant}.")
    if row["role"] == "admin" and role != "admin":
        _refuser_si_dernier_admin(conn, identifiant)
    conn.execute("UPDATE comptes SET role = ? WHERE identifiant = ?", (role, identifiant))
    conn.commit()


def supprimer_compte(conn: sqlite3.Connection, identifiant: str) -> None:
    row = conn.execute(
        "SELECT role FROM comptes WHERE identifiant = ?", (identifiant,)
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Compte inconnu: {identifiant}.")
    if row["role"] == "admin":
        _refuser_si_dernier_admin(conn, identifiant)
    conn.execute("DELETE FROM comptes WHERE identifiant = ?", (identifiant,))
    conn.commit()


def definir_mdp(conn: sqlite3.Connection, identifiant: str, nouveau: str) -> None:
    """Nouveau mot de passe sans l'ancien (réservé à l'administrateur).

    Invalide les sessions de ce compte (le cookie est lié au mot de passe)."""
    _verifier_force(nouveau)
    n = conn.execute(
        "UPDATE comptes SET hash_mdp = ? WHERE identifiant = ?",
        (hash_mdp(nouveau), identifiant),
    ).rowcount
    if n == 0:
        raise RuntimeError(f"Compte inconnu: {identifiant}.")
    conn.commit()


def changer_mdp(conn: sqlite3.Connection, identifiant: str, ancien: str, nouveau: str) -> None:
    row = conn.execute(
        "SELECT hash_mdp FROM comptes WHERE identifiant = ?", (identifiant,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Aucun compte configuré.")
    if not verifier_mdp(ancien, row["hash_mdp"]):
        raise RuntimeError("Ancien mot de passe incorrect.")
    definir_mdp(conn, identifiant, nouveau)


def init_compte_depuis_env(conn: sqlite3.Connection) -> bool:
    """Crée le compte administrateur au premier démarrage si ADMIN_PASSWORD est fourni."""
    if compte_existe(conn):
        return False
    mdp = os.environ.get("ADMIN_PASSWORD")
    if not mdp:
        return False
    creer_compte(conn, os.environ.get("ADMIN_USER", "nate"), mdp, "admin")
    return True


# Hash d'un mot de passe inconnu: sert à dépenser le même temps de calcul quand
# l'identifiant n'existe pas, pour ne pas révéler quels comptes existent.
_HASH_LEURRE = hash_mdp(secrets.token_urlsafe(16))


def authentifier(
    conn: sqlite3.Connection, identifiant: str, mot_de_passe: str
) -> Compte | None:
    row = conn.execute(
        "SELECT identifiant, hash_mdp, role FROM comptes WHERE identifiant = ?",
        (identifiant,),
    ).fetchone()
    hash_stocke = row["hash_mdp"] if row else _HASH_LEURRE
    mdp_ok = len(mot_de_passe) <= LONGUEUR_MAX_MDP and verifier_mdp(mot_de_passe, hash_stocke)
    if row is None or not mdp_ok:
        return None
    return Compte(row["identifiant"], row["role"])


# --- Limitation des tentatives ---------------------------------------------

@dataclass
class LimiteurTentatives:
    """Compteur en mémoire par IP. Suffisant pour une instance mono-utilisateur."""

    echecs: dict[str, list[float]] = field(default_factory=dict)

    def bloque(
        self, cle: str, maintenant: float | None = None, maximum: int = MAX_TENTATIVES
    ) -> bool:
        maintenant = maintenant if maintenant is not None else time.monotonic()
        recents = self._recents(cle, maintenant)
        return len(recents) >= maximum

    def enregistrer_echec(self, cle: str, maintenant: float | None = None) -> None:
        maintenant = maintenant if maintenant is not None else time.monotonic()
        self.echecs.setdefault(cle, []).append(maintenant)

    def reinitialiser(self, cle: str) -> None:
        self.echecs.pop(cle, None)

    def secondes_restantes(self, cle: str, maintenant: float | None = None) -> int:
        maintenant = maintenant if maintenant is not None else time.monotonic()
        recents = self._recents(cle, maintenant)
        if len(recents) < MAX_TENTATIVES:
            return 0
        return max(0, int(FENETRE_SECONDES - (maintenant - recents[0])))

    def _recents(self, cle: str, maintenant: float) -> list[float]:
        recents = [t for t in self.echecs.get(cle, []) if maintenant - t < FENETRE_SECONDES]
        self.echecs[cle] = recents
        return recents


limiteur = LimiteurTentatives()


# --- Dépendance FastAPI ----------------------------------------------------

def compte_courant(request: Request) -> Compte:
    """Exige une session valide. Toutes les routes API en dépendent sauf /login.

    Le garde global (`app._garde`) a normalement déjà fait la vérification
    avant même la lecture du corps de la requête: on réutilise son résultat.
    """
    deja = getattr(request.state, "compte", None)
    if deja:
        return deja

    brut = request.cookies.get(COOKIE_NAME)
    if not brut:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Non authentifié")

    from ..db import open_db
    from .settings_store import db_path

    conn = open_db(db_path())
    try:
        compte = session_valide(conn, brut)
    finally:
        conn.close()
    if not compte:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session invalide ou expirée")
    request.state.compte = compte
    return compte


def utilisateur_courant(request: Request) -> str:
    """Identifiant du compte connecté (les routes qui n'ont pas besoin du rôle)."""
    return compte_courant(request).identifiant


def exiger_admin(request: Request) -> Compte:
    compte = compte_courant(request)
    if not compte.admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Réservé à l'administrateur."
        )
    return compte


def exiger_etendu(request: Request) -> Compte:
    compte = compte_courant(request)
    if not compte.etendu:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Réservé aux comptes étendus et à l'administrateur."
        )
    return compte


def cookie_securise() -> bool:
    """Secure=True dès qu'on n'est pas en développement local (HTTPS requis)."""
    return not en_developpement()
