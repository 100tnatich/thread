"""API REST exposant le moteur de planification.

Couche mince: aucune logique de planification ici. Tout ce qui décide
«quelles cartes, dans quel ordre, quand» vit dans `flashcards.api` et
`flashcards.heuristic`, réutilisés tels quels.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, get_args

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import api as engine
from .. import budget, depot, importer, ingest, llm, manuel, profiles
from ..judge import JugeLLM
from ..config import POIDS_TYPE_DEFAUT, load_config
from ..db import open_db
from ..poids import poids_carte
from ..models import Categorie, Concept
from . import auth, schemas, settings_store

UPLOADS = Path(os.environ.get("FLASHCARDS_UPLOADS", "uploads"))

# Taxonomie fermée: types de questions et catégories de concepts connus.
CATEGORIES_CONNUES = frozenset(get_args(Categorie))


def engine_config_types() -> frozenset[str]:
    return frozenset(POIDS_TYPE_DEFAUT)

journal = logging.getLogger("flashcards.securite")

# Ni documentation interactive ni schéma OpenAPI: rien à offrir à un
# visiteur non authentifié.
app = FastAPI(title="Flashcards", docs_url=None, redoc_url=None, openapi_url=None)

# Seules routes atteignables sans session. Tout le reste de /api est refusé
# par défaut: une route ajoutée demain sans dépendance `User` reste protégée.
ROUTES_PUBLIQUES = frozenset({"/api/login", "/api/logout", "/api/health"})
METHODES_SURES = frozenset({"GET", "HEAD", "OPTIONS"})
# Plafond de taille d'une requête: la somme des fichiers autorisés + marge.
MAX_REQUETE_OCTETS = 32 * 1024 * 1024

ENTETES_SECURITE = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    # L'API ne renvoie que du JSON: rien ne doit s'y exécuter ni s'y charger.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    # Réponses authentifiées (cours, base): jamais dans un cache partagé.
    "Cache-Control": "no-store",
}


def _refus(code: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=code, headers=ENTETES_SECURITE)


def _origine_etrangere(request: Request) -> bool:
    """Requête d'écriture venue d'un autre site (CSRF) ?

    SameSite=Lax couvre déjà le cas général; ce contrôle ajoute les
    sous-domaines voisins et les navigateurs anciens. En développement le
    front tourne sur un autre port (Vite): on ne vérifie pas.
    """
    if auth.en_developpement():
        return False
    if request.headers.get("sec-fetch-site") == "cross-site":
        return True
    origine = request.headers.get("origin")
    if not origine:
        return False  # pas un navigateur (curl, script): pas de CSRF possible
    hote = request.headers.get("host", "")
    return origine.split("://", 1)[-1].lower() != hote.lower()


@app.middleware("http")
async def _garde(request: Request, call_next):
    """Contrôles faits AVANT que le corps de la requête soit lu.

    FastAPI lit et met en tampon le corps (y compris un envoi de fichiers)
    avant de résoudre les dépendances: sans ce garde, un visiteur anonyme
    pouvait faire écrire des gigaoctets sur le disque du serveur avant de
    recevoir son 401.
    """
    chemin = request.url.path
    if request.method not in METHODES_SURES:
        if _origine_etrangere(request):
            journal.warning("requête d'écriture d'origine étrangère refusée: %s", chemin)
            return _refus(status.HTTP_403_FORBIDDEN, "Origine de la requête refusée.")
        longueur = request.headers.get("content-length")
        if longueur is not None and (
            not longueur.isdigit() or int(longueur) > MAX_REQUETE_OCTETS
        ):
            return _refus(413, "Requête trop volumineuse.")

    if (
        chemin.startswith("/api/")
        and chemin not in ROUTES_PUBLIQUES
        and request.method != "OPTIONS"
    ):
        try:
            compte = auth.compte_courant(request)
            request.state.compte = compte
            request.state.utilisateur = compte.identifiant
        except HTTPException as exc:
            return _refus(exc.status_code, exc.detail)

    reponse = await call_next(request)
    for nom, valeur in ENTETES_SECURITE.items():
        reponse.headers.setdefault(nom, valeur)
    return reponse


# En développement le front (Vite, port 5173) appelle l'API sur un autre port.
# En production tout passe par Caddy sur la même origine: pas de CORS.
# (Ajouté après le garde: CORS reste donc la couche la plus externe.)
if auth.en_developpement():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def get_conn():
    conn = open_db(Path(settings_store.db_path()))
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
User = Annotated[str, Depends(auth.utilisateur_courant)]
# Rôles: `Admin` = administrateur seul; `Etendu` = compte étendu ou administrateur.
Admin = Annotated[auth.Compte, Depends(auth.exiger_admin)]
Etendu = Annotated[auth.Compte, Depends(auth.exiger_etendu)]
CompteCourant = Annotated[auth.Compte, Depends(auth.compte_courant)]


@app.on_event("startup")
def _demarrage() -> None:
    conn = open_db(Path(settings_store.db_path()))
    try:
        if auth.init_compte_depuis_env(conn):
            print("Compte web créé depuis ADMIN_PASSWORD.")

        # Un job coupé par un redémarrage passe en « interrompu », JAMAIS en
        # « à relancer »: une relance automatique est exactement la boucle
        # qui vide un crédit API pendant un crash-loop.
        coupes = conn.execute(
            "UPDATE jobs SET statut = 'interrompu',"
            " message = 'Interrompu par un redémarrage du serveur.',"
            " fini_le = ? WHERE statut = 'en_cours'",
            (datetime.now(timezone.utc).isoformat(),),
        ).rowcount
        conn.commit()
        if coupes:
            print(f"{coupes} tâche(s) marquée(s) interrompue(s) — aucune relance.")
        # Un appel coupé en plein vol garde son coût réservé au registre.
        budget.clore_reservations_orphelines(conn)
    finally:
        conn.close()

    # Échoue au démarrage, pas à la première connexion, si le secret manque.
    auth.secret_session()

    UPLOADS.mkdir(parents=True, exist_ok=True)
    _restreindre(UPLOADS, 0o700)
    _restreindre(Path(settings_store.db_path()), 0o600)
    _purger_uploads()

    # Sauvegarde quotidienne, sauf en développement et en test (où elle
    # polluerait tmp_path).
    if not auth.en_developpement():
        from ..backup import demarrer_planificateur

        demarrer_planificateur(Path(settings_store.db_path()))


# Les fichiers de cours envoyés servent entre l'extraction et la génération,
# pas au-delà: on ne garde pas indéfiniment des copies de cours sur le serveur.
RETENTION_UPLOADS_JOURS = 7


def _restreindre(chemin: Path, mode: int) -> None:
    try:
        chemin.chmod(mode)
    except OSError:
        pass


def _purger_uploads() -> int:
    limite = time.time() - RETENTION_UPLOADS_JOURS * 86400
    supprimes = 0
    for fichier in UPLOADS.glob("*"):
        try:
            if fichier.is_file() and fichier.stat().st_mtime < limite:
                fichier.unlink()
                supprimes += 1
        except OSError:
            pass
    return supprimes


def _ip(request: Request) -> str:
    return request.client.host if request.client else "inconnu"


def _jour(valeur: date | None) -> date:
    return valeur or datetime.now(timezone.utc).date()


def _erreur(e: Exception) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


# Cadence par session sur les routes qui peuvent déclencher une dépense.
# Complète (sans remplacer) les plafonds du module budget, qui eux sont la
# garantie financière.
limiteur_llm = auth.LimiteurTentatives()
MAX_INGESTIONS_PAR_FENETRE = 20


def _verifier_cadence_llm(request: Request) -> None:
    cle = f"llm:{_ip(request)}"
    recents = limiteur_llm.echecs.get(cle, [])
    maintenant = time.monotonic()
    recents = [t for t in recents if maintenant - t < auth.FENETRE_SECONDES]
    if len(recents) >= MAX_INGESTIONS_PAR_FENETRE:
        limiteur_llm.echecs[cle] = recents
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Trop de demandes d'ingestion ({MAX_INGESTIONS_PAR_FENETRE} par "
            f"{auth.FENETRE_SECONDES // 60} minutes). Réessaie plus tard.",
        )
    recents.append(maintenant)
    limiteur_llm.echecs[cle] = recents


# --- Auth ------------------------------------------------------------------

@app.post("/api/login")
def login(data: schemas.LoginIn, request: Request, response: Response, conn: Conn):
    cle = _ip(request)
    if auth.limiteur.bloque(cle):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Trop de tentatives. Réessaie dans {auth.limiteur.secondes_restantes(cle)} s.",
        )
    if auth.limiteur.bloque(auth.CLE_GLOBALE, maximum=auth.MAX_TENTATIVES_GLOBAL):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives. Réessaie plus tard."
        )

    if not auth.compte_existe(conn):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Aucun compte configuré.")

    compte = auth.authentifier(conn, data.identifiant, data.mot_de_passe)
    if compte is None:
        auth.limiteur.enregistrer_echec(cle)
        auth.limiteur.enregistrer_echec(auth.CLE_GLOBALE)
        # Ni l'identifiant ni le mot de passe saisis ne sont journalisés: un
        # mot de passe tapé dans le mauvais champ finirait dans les logs.
        journal.warning("connexion échouée depuis %s", cle)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants incorrects.")

    auth.limiteur.reinitialiser(cle)
    journal.info("connexion réussie depuis %s", cle)
    _poser_cookie(response, conn, compte.identifiant)
    return {"identifiant": compte.identifiant, "role": compte.role}


def _poser_cookie(response: Response, conn: sqlite3.Connection, identifiant: str) -> None:
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.cookie_pour(conn, identifiant),
        max_age=auth.SESSION_MAX_AGE,
        httponly=True,
        secure=auth.cookie_securise(),
        samesite="lax",
        path="/",
    )


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(compte: CompteCourant):
    return {"identifiant": compte.identifiant, "role": compte.role}


@app.post("/api/mot-de-passe")
def changer_mot_de_passe(
    data: schemas.ChangerMdpIn, request: Request, response: Response,
    user: User, conn: Conn,
):
    # Même frein que le login: une session volée ne doit pas servir à
    # deviner l'ancien mot de passe à volonté.
    cle = f"mdp:{_ip(request)}"
    if auth.limiteur.bloque(cle):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives. Réessaie plus tard."
        )
    try:
        auth.changer_mdp(conn, user, data.ancien, data.nouveau)
    except RuntimeError as e:
        auth.limiteur.enregistrer_echec(cle)
        journal.warning("changement de mot de passe refusé depuis %s", _ip(request))
        raise _erreur(e)
    journal.info("mot de passe changé depuis %s", _ip(request))
    # Toutes les sessions existantes viennent d'être invalidées (le cookie
    # est lié au mot de passe): on en redonne une à l'appareil courant.
    _poser_cookie(response, conn, user)
    return {"ok": True}


# --- Comptes (administrateur) ------------------------------------------------

@app.get("/api/comptes", response_model=list[schemas.CompteOut])
def liste_comptes(admin: Admin, conn: Conn):
    return auth.lister_comptes(conn)


@app.post("/api/comptes", response_model=schemas.CompteOut, status_code=201)
def creer_compte_web(data: schemas.CompteIn, admin: Admin, conn: Conn):
    try:
        auth.ajouter_compte(conn, data.identifiant, data.mot_de_passe, data.role)
    except RuntimeError as e:
        raise _erreur(e)
    journal.info("compte « %s » (%s) créé par %s", data.identifiant, data.role, admin.identifiant)
    return next(c for c in auth.lister_comptes(conn) if c["identifiant"] == data.identifiant)


@app.patch("/api/comptes/{identifiant}")
def modifier_compte(identifiant: str, data: schemas.ComptePatch, admin: Admin, conn: Conn):
    try:
        if data.role is not None:
            auth.changer_role(conn, identifiant, data.role)
        if data.mot_de_passe is not None:
            auth.definir_mdp(conn, identifiant, data.mot_de_passe)
    except RuntimeError as e:
        raise _erreur(e)
    journal.info("compte « %s » modifié par %s", identifiant, admin.identifiant)
    return {"ok": True}


@app.delete("/api/comptes/{identifiant}")
def supprimer_compte_web(identifiant: str, admin: Admin, conn: Conn):
    if identifiant == admin.identifiant:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Tu ne peux pas supprimer ton propre compte."
        )
    try:
        auth.supprimer_compte(conn, identifiant)
    except RuntimeError as e:
        raise _erreur(e)
    journal.info("compte « %s » supprimé par %s", identifiant, admin.identifiant)
    return {"ok": True}


# --- Dépôt de fichiers (compte étendu et administrateur) ---------------------

def _depot_out(d: depot.Depot) -> schemas.DepotOut:
    return schemas.DepotOut(
        id=d.id, cours=d.cours, nom=d.nom_original, taille=d.taille,
        depose_par=d.depose_par, depose_le=d.depose_le, traite_le=d.traite_le,
    )


@app.get("/api/depot", response_model=list[schemas.DepotOut])
def liste_depot(compte: Etendu, conn: Conn, cours: str | None = None):
    return [_depot_out(d) for d in depot.lister(conn, cours=cours)]


@app.post("/api/depot", response_model=schemas.DepotOut, status_code=201)
async def deposer_fichier(
    compte: Etendu,
    conn: Conn,
    cours: str = Form(..., min_length=1, max_length=200),
    fichier: UploadFile = File(...),
):
    """Un fichier par requête: la limite de taille d'une requête reste respectée."""
    donnees = await _lire_borne(fichier, depot.MAX_OCTETS_FICHIER)
    try:
        enregistre = depot.enregistrer(
            conn, cours, fichier.filename or "fichier", donnees, compte.identifiant
        )
    except depot.DepotRefuse as e:
        raise _erreur(e)
    journal.info("dépôt %s (%s) par %s", enregistre.id, enregistre.cours, compte.identifiant)
    return _depot_out(enregistre)


@app.get("/api/depot/{depot_id}/fichier")
def telecharger_depot(depot_id: int, compte: Etendu, conn: Conn):
    from fastapi.responses import FileResponse

    try:
        d = depot.lire(conn, depot_id)
        chemin = depot.chemin_complet(d)
    except depot.DepotRefuse as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    if not chemin.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fichier introuvable sur le disque.")
    # `attachment` + nosniff: le navigateur télécharge, il n'affiche ni n'exécute.
    return FileResponse(
        chemin, filename=depot.nom_sur(d.nom_original),
        content_disposition_type="attachment", media_type="application/octet-stream",
    )


@app.delete("/api/depot/{depot_id}")
def supprimer_depot(depot_id: int, compte: Etendu, conn: Conn):
    try:
        depot.supprimer(conn, depot_id)
    except depot.DepotRefuse as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return {"ok": True}


# --- Cours -----------------------------------------------------------------

def _themes_resume(conn: sqlite3.Connection, jour: date) -> list[schemas.CoursOut]:
    config = settings_store.config_effective(conn)
    points = engine.points_par_cours(conn, config)
    entretien = engine.cours_en_entretien(conn, jour)
    return [
        schemas.CoursOut(
            nom=c.nom, couleur=c.couleur, nb_cartes=c.nb_cartes,
            points=round(points.get(c.nom, 0.0), 2), matiere=c.matiere,
            archive=c.archive, profil=c.profil, recap=c.recap,
            en_entretien=c.nom in entretien,
        )
        for c in engine.list_cours(conn)
    ]


@app.get("/api/cours", response_model=list[schemas.CoursOut])
def liste_cours(user: User, conn: Conn, jour: date | None = None):
    return _themes_resume(conn, _jour(jour))


@app.post("/api/cours", response_model=schemas.CoursOut, status_code=201)
def creer_cours(data: schemas.CoursIn, user: User, conn: Conn):
    try:
        engine.add_cours(
            conn, data.nom, data.couleur, matiere=data.matiere, profil=data.profil
        )
    except (RuntimeError, sqlite3.IntegrityError) as e:
        raise _erreur(e)
    return next(c for c in _themes_resume(conn, _jour(None)) if c.nom == data.nom)


@app.patch("/api/cours/{nom}")
def modifier_cours(nom: str, data: schemas.CoursPatch, user: User, conn: Conn):
    try:
        engine.update_cours(
            conn, nom, nouveau_nom=data.nouveau_nom, couleur=data.couleur,
            # `matiere: null` détache; absent = inchangé.
            matiere=data.matiere if "matiere" in data.model_fields_set else engine.INCHANGE,
            profil=data.profil, recap=data.recap, archive=data.archive,
        )
    except (RuntimeError, sqlite3.IntegrityError) as e:
        raise _erreur(e)
    return {"ok": True}


@app.delete("/api/cours/{nom}")
def supprimer_cours(nom: str, data: schemas.CoursSuppression, user: User, conn: Conn):
    try:
        nb = engine.delete_cours(conn, nom, reaffecter_vers=data.reaffecter_vers)
    except RuntimeError as e:
        raise _erreur(e)
    return {"questions_concernees": nb}


@app.get("/api/matieres", response_model=list[schemas.MatiereOut])
def liste_matieres(user: User, conn: Conn, jour: date | None = None):
    entretien = engine.cours_en_entretien(conn, _jour(jour))
    themes = engine.list_cours(conn)
    return [
        schemas.MatiereOut(
            nom=m.nom, couleur=m.couleur, profil=m.profil, nb_cours=m.nb_cours,
            nb_cartes=m.nb_cartes,
            nb_cours_entretien=sum(
                1 for t in themes if t.matiere == m.nom and t.nom in entretien
            ),
        )
        for m in engine.list_matieres(conn)
    ]


@app.post("/api/matieres", response_model=schemas.MatiereOut, status_code=201)
def creer_matiere(data: schemas.MatiereIn, user: User, conn: Conn):
    try:
        engine.add_matiere(conn, data.nom, data.couleur, data.profil)
    except RuntimeError as e:
        raise _erreur(e)
    return schemas.MatiereOut(
        nom=data.nom, couleur=data.couleur, profil=data.profil, nb_cours=0, nb_cartes=0,
    )


@app.patch("/api/matieres/{nom}")
def modifier_matiere(nom: str, data: schemas.MatierePatch, user: User, conn: Conn):
    try:
        engine.update_matiere(
            conn, nom, nouveau_nom=data.nouveau_nom, couleur=data.couleur, profil=data.profil,
        )
    except RuntimeError as e:
        raise _erreur(e)
    return {"ok": True}


@app.delete("/api/matieres/{nom}")
def supprimer_matiere(nom: str, user: User, conn: Conn):
    try:
        nb = engine.delete_matiere(conn, nom)
    except RuntimeError as e:
        raise _erreur(e)
    return {"cours_detaches": nb}


# --- Tests -----------------------------------------------------------------

def _test_out(exam) -> schemas.TestOut:
    return schemas.TestOut(
        id=exam.id, cours=sorted(exam.cours), matieres=sorted(exam.matieres),
        date=exam.date, intitule=exam.intitule,
    )


def _test_depuis_base(conn: sqlite3.Connection, exam_id: int) -> schemas.TestOut:
    return _test_out(next(e for e in engine.load_exams(conn) if e.id == exam_id))


@app.get("/api/tests", response_model=list[schemas.TestOut])
def liste_tests(user: User, conn: Conn):
    return [_test_out(e) for e in engine.list_tests(conn)]


@app.post("/api/tests", response_model=schemas.TestOut, status_code=201)
def creer_test(data: schemas.TestIn, user: User, conn: Conn):
    try:
        exam_id = engine.add_test(
            conn, cours=data.cours, matieres=data.matieres, jour=data.date,
            intitule=data.intitule,
        )
    except (RuntimeError, sqlite3.IntegrityError) as e:
        raise _erreur(e)
    return _test_depuis_base(conn, exam_id)


@app.patch("/api/tests/{test_id}")
def deplacer_test(test_id: int, data: schemas.TestPatch, user: User, conn: Conn):
    try:
        if data.date is not None:
            engine.move_test(conn, test_id, data.date)
        if data.cours is not None or data.matieres is not None:
            engine.set_test_liens(conn, test_id, cours=data.cours, matieres=data.matieres)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return {"ok": True}


@app.delete("/api/tests/{test_id}")
def supprimer_test(test_id: int, user: User, conn: Conn):
    try:
        engine.remove_test(conn, test_id)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return {"ok": True}


# --- Aujourd'hui / session -------------------------------------------------

def _couleurs(conn: sqlite3.Connection) -> dict[str, str]:
    return {c.nom: c.couleur for c in engine.list_cours(conn)}


@app.get("/api/today", response_model=schemas.TodayOut)
def aujourdhui(user: User, conn: Conn, jour: date | None = None, cours: str | None = None):
    config = settings_store.config_effective(conn)
    j = _jour(jour)
    couleurs = _couleurs(conn)

    plan = engine.plan_du_jour(conn, j, config, cours=cours)
    queue = plan.items

    par_cours: dict[str, list] = {}
    for item in queue:
        par_cours.setdefault(item.card.cours, []).append(item)

    tous_tests = engine.load_exams(conn)
    exams = [e for e in tous_tests if e.date >= j and (not cours or cours in e.cours)]
    prochain = min(exams, key=lambda e: e.date) if exams else None

    return schemas.TodayOut(
        jour=j,
        total=len(queue),
        total_dues=len(plan.dus),
        nb_nouvelles=sum(1 for i in queue if i.nouvelle),
        nb_en_retard=sum(1 for i in queue if i.jours_retard > 0),
        par_cours=[
            schemas.CoursCharge(
                cours=c, couleur=couleurs.get(c, "#8B7CFF"), nb=len(items),
                points=round(sum(i.points for i in items), 2),
                entretien=c in plan.themes_entretien,
            )
            for c, items in sorted(par_cours.items())
        ],
        prochain_test=_test_out(prochain) if prochain else None,
        jours_avant_test=(prochain.date - j).days if prochain else None,
        points=round(plan.points, 2),
        points_dus=round(plan.points_dus, 2),
        plafond=config.plafond_quotidien,
        nb_entretien=plan.nb_entretien,
        points_entretien=round(plan.points_entretien, 2),
        part_entretien=round(plan.part_entretien, 3),
        themes_entretien=sorted(plan.themes_entretien),
        alertes=engine.alertes_entretien(plan.themes_entretien, config),
    )


@app.get("/api/session", response_model=schemas.QueueOut)
def ouvrir_session(
    user: User,
    conn: Conn,
    jour: date | None = None,
    cours: str | None = None,
    ordre: str | None = None,
    session_id: str | None = None,
):
    config = settings_store.config_effective(conn)
    j = _jour(jour)
    sid = session_id or str(uuid.uuid4())
    ordre_final = ordre or settings_store.ordre_effectif(conn)
    couleurs = _couleurs(conn)

    try:
        queue = engine.get_today_queue(
            conn, j, config, cours=cours, ordre=ordre_final, session_id=sid,
        )
    except RuntimeError as e:
        raise _erreur(e)

    total_dues = len(engine.plan_du_jour(conn, j, config, cours=cours).dus)

    # Le récap théorique n'a de sens qu'en mode « par cours »: les questions d'un
    # thème se suivent, on le lit avant d'y entrer. Mélangé: aucun récap.
    recaps: list[schemas.RecapOut] = []
    if ordre_final == engine.ORDRE_PAR_COURS:
        themes = {c.nom: c for c in engine.list_cours(conn)}
        for nom in dict.fromkeys(i.card.cours for i in queue):
            if nom in themes and themes[nom].recap.strip():
                recaps.append(schemas.RecapOut(
                    cours=nom, couleur=themes[nom].couleur, texte=themes[nom].recap,
                ))

    return schemas.QueueOut(
        jour=j,
        session_id=sid,
        total=len(queue),
        total_dues=total_dues,
        plafond=config.plafond_quotidien,
        points=round(sum(i.points for i in queue), 2),
        points_entretien=round(sum(i.points for i in queue if i.entretien), 2),
        juge=llm.etat_juge(),
        ordre=ordre_final,
        recaps=recaps,
        questions=[
            schemas.QuestionOut(
                card_id=i.card.id,
                type=i.card.type,
                cours=i.card.cours,
                couleur_cours=couleurs.get(i.card.cours, "#8B7CFF"),
                payload=json.loads(i.card.payload),
                jours_retard=i.jours_retard,
                nouvelle=i.nouvelle,
                poids=round(i.points, 3),
                entretien=i.entretien,
            )
            for i in queue
        ],
    )


@app.post("/api/reponse")
def enregistrer_reponse(data: schemas.ReponseIn, user: User, conn: Conn):
    from fsrs import Rating

    config = settings_store.config_effective(conn)
    moment = datetime.now(timezone.utc)
    if data.jour:
        moment = datetime.combine(data.jour, datetime.min.time(), tzinfo=timezone.utc)
    try:
        engine.record_answer(
            conn, data.card_id, Rating(data.rating),
            reviewed_at=moment, config=config,
            correct=data.correct, source=data.source, duration_ms=data.duration_ms,
        )
    except RuntimeError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return {"ok": True}


def _modele_juge_affiche() -> str:
    try:
        return llm.juge_modele()
    except RuntimeError:
        return ""


@app.post("/api/juger", response_model=schemas.JugementOut)
def juger_reponse(data: schemas.JugerIn, user: User, conn: Conn):
    """Fait corriger par le juge LLM une réponse libre ou une rédaction.

    Un refus attendu (juge non activé, plafond atteint, interrupteur d'urgence,
    panne du fournisseur) n'est PAS une erreur HTTP: la réponse dit
    `disponible=false` et pourquoi, et l'interface propose l'auto-évaluation.
    L'énoncé et la réponse attendue viennent de la base, jamais du client.
    """
    ligne = conn.execute("SELECT payload FROM cards WHERE id = ?", (data.card_id,)).fetchone()
    if not ligne:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Carte inconnue")
    payload = json.loads(ligne["payload"])
    if payload.get("type") not in ("libre_llm", "redaction"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Seules les réponses libres et les rédactions sont corrigées par le juge.",
        )

    juge = JugeLLM(conn, langue=data.langue)
    try:
        if payload["type"] == "libre_llm":
            verdict = juge.juge(
                enonce=payload["enonce"],
                reponse_attendue=payload["reponse_attendue"],
                reponse_donnee=data.reponse,
            )
        else:
            verdict = juge.juge_redaction(
                consigne=payload["consigne"], contraintes=payload["contraintes"],
                criteres=payload["criteres"],
                exemple_reference=payload["exemple_reference"], texte=data.reponse,
            )
    except RuntimeError as e:
        # BudgetDepasse, LLMDesactive, DisjoncteurOuvert, EntreeTropGrande,
        # JugeIndisponible, ErreurOpenRouter: tous des RuntimeError, au message
        # lisible. Jamais la clé API là-dedans (voir llm._message_erreur_http).
        journal.warning("correction par IA indisponible: %s", e)
        return schemas.JugementOut(disponible=False, etat=llm.etat_juge(), raison=str(e))

    return schemas.JugementOut(
        disponible=True,
        etat=llm.etat_juge(),
        note_suggeree=int(verdict.rating),
        verdict=verdict.verdict,
        commentaire=verdict.commentaire,
        criteres=list(verdict.criteres) if verdict.criteres is not None else None,
    )


@app.post("/api/skip", response_model=schemas.SkipOut)
def passer_question(data: schemas.SkipIn, user: User, conn: Conn):
    j = _jour(data.jour)
    moment = datetime.now(timezone.utc)
    try:
        nb = engine.skip_card(
            conn, data.session_id, data.card_id,
            jour=j, moment=moment, raison=data.raison,
        )
    except RuntimeError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return schemas.SkipOut(nb_skips=nb, reviendra=nb < engine.MAX_SKIPS_PAR_SESSION)


# --- Stats -----------------------------------------------------------------

@app.get("/api/stats", response_model=schemas.StatsOut)
def stats(user: User, conn: Conn, jour: date | None = None, cours: str | None = None):
    config = settings_store.config_effective(conn)
    j = _jour(jour)
    s = engine.get_stats(conn, j, config)

    tests = [
        schemas.ExamStatOut(
            test=_test_out(es.exam), nb_cartes=es.nb_cartes,
            nb_jamais_revisees=es.nb_jamais_revisees,
            retention_estimee=es.retention_estimee,
        )
        for es in s.examens
        if not cours or cours in es.exam.cours
    ]
    return schemas.StatsOut(
        jour=s.today,
        total_cartes=s.total_cartes,
        nb_dues=s.nb_dues,
        nb_nouvelles=s.nb_nouvelles,
        nb_en_retard=s.nb_en_retard,
        charge_14j=[
            schemas.ChargeJour(
                jour=c.jour, nb=c.cartes, points=round(c.points, 2),
                nb_entretien=c.cartes_entretien, points_entretien=round(c.points_entretien, 2),
            )
            for c in s.charge_14j
        ],
        tests=tests,
        points_dus=round(s.points_dus, 2),
        points_jour=round(s.points_jour, 2),
        plafond=s.plafond,
        part_entretien=round(s.part_entretien, 3),
        points_entretien_jour=round(s.points_entretien_jour, 2),
        nb_themes=len(engine.list_cours(conn, archives=False)),
        themes_entretien=s.themes_entretien,
        quotas=[
            schemas.QuotaOut(
                theme=q.theme, quota=q.quota,
                fait_avant_aujourdhui=round(q.fait_avant_aujourdhui, 2),
                planifie_fsrs=round(q.planifie_fsrs, 2), manque=round(q.manque, 2),
            )
            for q in s.quotas.values()
        ],
        alertes=s.alertes,
    )


# --- Bibliothèque ----------------------------------------------------------

@app.get("/api/cartes", response_model=list[schemas.CarteOut])
def liste_cartes(
    user: User, conn: Conn,
    cours: str | None = None,
    recherche: str | None = None, desactivees: bool | None = None,
    limite: int = 200,
):
    clauses, params = [], []
    if cours:
        clauses.append("c.cours = ?")
        params.append(cours)
    if desactivees is not None:
        clauses.append("c.desactivee = ?")
        params.append(int(desactivees))
    if recherche:
        clauses.append("c.payload LIKE ?")
        params.append(f"%{recherche}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    config = settings_store.config_effective(conn)

    rows = conn.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM reviews r WHERE r.card_id = c.id) AS nb_revisions"
        f" FROM cards c {where} ORDER BY c.cours, c.imported_at DESC LIMIT ?",
        (*params, limite),
    ).fetchall()

    return [
        schemas.CarteOut(
            id=r["id"], concept_id=r["concept_id"], cours=r["cours"],
            type=r["type"], payload=json.loads(r["payload"]),
            desactivee=bool(r["desactivee"]), note_qualite=r["note_qualite"],
            nb_revisions=r["nb_revisions"], stabilite=r["fsrs_stability"],
            source_ref=json.loads(r["payload"]).get("source_ref"),
            confiance=json.loads(r["payload"]).get("confiance", "moyenne"),
            poids=r["poids"], categorie=r["categorie"],
            points=poids_carte(r["type"], r["categorie"], r["poids"], config),
        )
        for r in rows
    ]


@app.patch("/api/cartes/{card_id}")
def modifier_carte(card_id: str, data: schemas.CartePatch, user: User, conn: Conn):
    existe = conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone()
    if not existe:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Carte inconnue")

    if data.payload is not None:
        from pydantic import TypeAdapter, ValidationError

        from ..models import Question

        try:
            # On revalide avec la taxonomie fermée: l'édition manuelle ne doit
            # pas pouvoir introduire une question hors schéma.
            valide = TypeAdapter(Question).validate_python(data.payload)
        except ValidationError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Question invalide: {e}")
        conn.execute(
            "UPDATE cards SET payload = ?, type = ?, poids = ? WHERE id = ?",
            (valide.model_dump_json(), valide.type, valide.poids, card_id),
        )
    if data.desactivee is not None:
        engine.set_question_desactivee(conn, card_id, data.desactivee, data.note_qualite)
    if data.cours is not None:
        conn.execute("UPDATE cards SET cours = ? WHERE id = ?", (data.cours, card_id))
    if "poids" in data.model_fields_set:
        # La colonne pilote le planning; le payload garde le même `poids`.
        ligne = conn.execute("SELECT payload FROM cards WHERE id = ?", (card_id,)).fetchone()
        if ligne is not None:
            contenu = json.loads(ligne["payload"])
            contenu["poids"] = data.poids
            conn.execute(
                "UPDATE cards SET poids = ?, payload = ? WHERE id = ?",
                (data.poids, json.dumps(contenu, ensure_ascii=False), card_id),
            )
    conn.commit()
    return {"ok": True}


@app.delete("/api/cartes/{card_id}")
def supprimer_carte(card_id: str, user: User, conn: Conn):
    cur = conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
    conn.commit()
    if not cur.rowcount:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Carte inconnue")
    return {"ok": True}


# --- Ingestion (tâches longues) --------------------------------------------

# Au-delà, un job « en cours » est forcément mort (le pire cas réel est de
# 3 tentatives x 5 minutes): il ne doit pas bloquer l'unique créneau à vie.
DUREE_MAX_JOB_MINUTES = 30


def _creer_job(
    conn: sqlite3.Connection,
    type_: str,
    contexte: dict | None = None,
    empreinte: str | None = None,
) -> tuple[str, bool]:
    """Crée un job, ou refuse si un travail identique / concurrent existe.

    Renvoie `(job_id, nouveau)`. **La tâche de fond ne doit être lancée que
    si `nouveau` est vrai**: sur un double clic, le second appel reçoit l'id
    du job déjà en cours et ne doit surtout pas démarrer un second travail.

    Trois protections cumulées contre la double dépense:
    - un job identique (même empreinte) encore en cours est réutilisé plutôt
      que dupliqué: double clic, rechargement, deux onglets;
    - un index unique partiel en base interdit deux jobs `en_cours`, même si
      deux requêtes passent la vérification applicative en même temps;
    - l'empreinte couvre le contenu, pas le nom du fichier.
    """
    maintenant = datetime.now(timezone.utc)
    conn.execute(
        "UPDATE jobs SET statut = 'interrompu', fini_le = ?,"
        " message = 'Tâche abandonnée: durée maximale dépassée.'"
        " WHERE statut = 'en_cours' AND cree_le < ?",
        (
            maintenant.isoformat(),
            (maintenant - timedelta(minutes=DUREE_MAX_JOB_MINUTES)).isoformat(),
        ),
    )
    conn.commit()

    if empreinte:
        existant = conn.execute(
            "SELECT id FROM jobs WHERE empreinte = ? AND statut = 'en_cours'",
            (empreinte,),
        ).fetchone()
        if existant is not None:
            return existant["id"], False

    job_id = str(uuid.uuid4())
    try:
        conn.execute(
            "INSERT INTO jobs (id, type, statut, progression, cree_le, contexte, empreinte)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                job_id, type_, "en_cours", 0, maintenant.isoformat(),
                json.dumps(contexte, ensure_ascii=False) if contexte else None,
                empreinte,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Une tâche est déjà en cours. Attends qu'elle se termine avant d'en "
            "lancer une autre (c'est ce qui évite de payer deux fois le même appel).",
        )
    return job_id, True


# Erreurs dont le message est destiné à l'utilisateur (budget, limites).
# Tout le reste est remplacé par un message générique: un traceback ou un
# chemin de fichier dans l'interface renseigne un attaquant.
ERREURS_EXPLICITES = (
    budget.BudgetDepasse,
    budget.LLMDesactive,
    budget.DisjoncteurOuvert,
    budget.EntreeTropGrande,
)


def _message_utilisateur(exc: Exception) -> str:
    if isinstance(exc, ERREURS_EXPLICITES):
        return str(exc)
    if isinstance(exc, RuntimeError):
        return str(exc)
    logging.getLogger("flashcards.taches").error(
        "échec non prévu: %s: %s", type(exc).__name__, exc
    )
    return "La tâche a échoué. Consulte les journaux du serveur."


def _empreinte_travail(
    operation: str, cours: str, *,
    texte: str | None = None, fichiers: tuple[Path, ...] = (),
) -> str:
    """Identifie un travail par son contenu, pas par l'instant où il arrive."""
    return budget.empreinte_entree(
        operation=operation, modele="job", texte=texte, fichiers=fichiers,
        extra=cours,
    )


def _entree_depuis_contexte(
    conn: sqlite3.Connection, job_id: str | None, cours: str
) -> llm.Entree:
    """Reconstruit la matière d'une extraction pour la rejoindre à la génération.

    Le contexte est relu côté serveur à partir de l'id du job: le navigateur
    n'a jamais connaissance des chemins de fichiers.
    """
    if not job_id:
        return llm.Entree(voie="texte", cours=cours, texte=None)

    row = conn.execute("SELECT contexte FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None or not row["contexte"]:
        return llm.Entree(voie="texte", cours=cours, texte=None)

    ctx = json.loads(row["contexte"])
    fichiers = tuple(Path(c) for c in ctx.get("fichiers", []) if Path(c).exists())
    return llm.Entree(
        voie=ctx.get("voie", "texte"),
        cours=cours,
        texte=ctx.get("texte"),
        fichiers=fichiers,
    )


def _ecrire_job(conn: sqlite3.Connection, job_id: str, **champs) -> None:
    sets = ", ".join(f"{k} = ?" for k in champs)
    conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*champs.values(), job_id))
    conn.commit()


def _maj_job(job_id: str, **champs) -> None:
    """Ouvre sa propre connexion: la tâche tourne hors du cycle de la requête."""
    conn = open_db(Path(settings_store.db_path()))
    try:
        _ecrire_job(conn, job_id, **champs)
    finally:
        conn.close()


def _tache_extraction(job_id: str, entree: llm.Entree, modele: str | None) -> None:
    conn = open_db(Path(settings_store.db_path()))
    try:
        resultat = ingest.extraire(entree, conn=conn, modele=modele)
        _ecrire_job(
            conn, job_id, statut="termine", progression=100,
            resultat=json.dumps({
                "concepts": [c.model_dump(by_alias=True) for c in resultat.concepts],
                "avertissements": resultat.avertissements,
                "source": resultat.source,
            }, ensure_ascii=False),
            fini_le=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:
        # Le message d'un refus de budget est utile à l'utilisateur; celui
        # d'une erreur interne ne doit pas fuiter de détail d'implémentation.
        _ecrire_job(
            conn, job_id, statut="echoue", message=_message_utilisateur(e),
            fini_le=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        conn.close()


def _tache_generation(job_id: str, entree: llm.Entree, source: str,
                      concepts: list[Concept], modele: str | None) -> None:
    conn = open_db(Path(settings_store.db_path()))
    try:
        # Progression écrite sur LA MÊME connexion que la génération: ouvrir
        # une seconde connexion ici bloquerait, la génération tenant déjà une
        # transaction en écriture.
        def progression(fait: int, total: int) -> None:
            _ecrire_job(conn, job_id, progression=int(fait / total * 100))

        resume = ingest.generer(
            conn, entree, concepts, source=source, modele=modele,
            progression=progression,
        )
        _ecrire_job(
            conn, job_id, statut="termine", progression=100,
            resultat=json.dumps({
                "questions_creees": resume.questions_creees,
                "echecs": [e.model_dump() for e in resume.echecs],
                "avertissements": resume.avertissements,
                "concepts_sans_question": resume.couverture.concepts_sans_question,
                "details": [d.model_dump() for d in resume.couverture.details],
            }, ensure_ascii=False),
            fini_le=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:
        _ecrire_job(
            conn, job_id, statut="echoue", message=_message_utilisateur(e),
            fini_le=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        conn.close()


@app.post("/api/ingest/texte")
def ingest_texte(
    data: schemas.ExtractionTexteIn, request: Request, taches: BackgroundTasks,
    user: User, conn: Conn,
):
    _verifier_cadence_llm(request)
    plafonds = budget.charger_plafonds(load_config().budget_brut)
    try:
        budget.verifier_entree(plafonds, texte=data.texte, voie="texte")
    except budget.EntreeTropGrande as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    entree = llm.Entree(voie="texte", cours=data.cours, texte=data.texte)
    job_id, nouveau = _creer_job(
        conn, "extraction", {"voie": "texte", "texte": data.texte},
        empreinte=_empreinte_travail("extraction", data.cours, texte=data.texte),
    )
    if nouveau:
        taches.add_task(
            _tache_extraction, job_id, entree, settings_store.modele_effectif(conn)
        )
    return {"job_id": job_id}


# Signatures réelles des formats acceptés. L'extension est déclarative et
# ne prouve rien: un `.png` peut être un exécutable.
SIGNATURES = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
    ".gif": (b"GIF87a", b"GIF89a"),
}


async def _lire_borne(fichier: UploadFile, maximum: int) -> bytes:
    """Lit au plus `maximum` octets + 1, pour détecter le dépassement.

    `await fichier.read()` sans borne chargerait un fichier de plusieurs Go
    en mémoire avant même de vérifier sa taille.
    """
    donnees = await fichier.read(maximum + 1)
    if len(donnees) > maximum:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Fichier trop lourd ({fichier.filename}): maximum "
            f"{maximum / 1024 / 1024:.0f} Mo.",
        )
    return donnees


@app.post("/api/ingest/fichiers")
async def ingest_fichiers(
    request: Request,
    taches: BackgroundTasks,
    user: User,
    conn: Conn,
    cours: str = Form(..., min_length=1, max_length=200),
    voie: str = Form("pdf"),
    fichiers: list[UploadFile] = File(...),
):
    _verifier_cadence_llm(request)
    if voie not in ("pdf", "image"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Voie inconnue")

    plafonds = budget.charger_plafonds(load_config().budget_brut)
    if voie == "image" and len(fichiers) > plafonds.max_images:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Trop d'images: {len(fichiers)}, maximum {plafonds.max_images}.",
        )
    if voie == "pdf" and len(fichiers) > 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Un seul PDF à la fois.")

    UPLOADS.mkdir(parents=True, exist_ok=True)
    chemins: list[Path] = []
    try:
        for fichier in fichiers:
            # Le nom fourni par le client n'est jamais utilisé comme chemin:
            # seul son suffixe est lu, et le fichier est écrit sous un UUID.
            suffixe = Path(fichier.filename or "fichier").suffix.lower()
            attendu = (".pdf",) if voie == "pdf" else tuple(llm.MEDIA_TYPES)
            if suffixe not in attendu:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Format non accepté pour la voie {voie}: {suffixe or '(aucun)'}",
                )

            donnees = await _lire_borne(fichier, plafonds.max_fichier_octets)
            signatures = SIGNATURES.get(suffixe, ())
            if signatures and not any(donnees.startswith(sig) for sig in signatures):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Le contenu de {fichier.filename} ne correspond pas à un "
                    f"fichier {suffixe}.",
                )

            cible = UPLOADS / f"{uuid.uuid4()}{suffixe}"
            cible.write_bytes(donnees)
            _restreindre(cible, 0o600)
            chemins.append(cible)

        entree = llm.Entree(voie=voie, cours=cours, fichiers=tuple(chemins))
        # Limites métier (pages PDF, etc.) avant toute dépense.
        budget.verifier_entree(
            plafonds, fichiers=entree.fichiers, texte=None, voie=voie
        )
    except (HTTPException, budget.EntreeTropGrande) as exc:
        for chemin in chemins:
            chemin.unlink(missing_ok=True)
        if isinstance(exc, budget.EntreeTropGrande):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
        raise

    empreinte = _empreinte_travail("extraction", cours, fichiers=tuple(chemins))
    nouveau = False
    try:
        job_id, nouveau = _creer_job(
            conn, "extraction",
            {"voie": voie, "fichiers": [str(c) for c in chemins]},
            empreinte=empreinte,
        )
    finally:
        # Doublon ou refus: ces copies ne serviront à aucun job.
        if not nouveau:
            for chemin in chemins:
                chemin.unlink(missing_ok=True)
    if nouveau:
        taches.add_task(
            _tache_extraction, job_id, entree, settings_store.modele_effectif(conn)
        )
    return {"job_id": job_id}


# --- Import de questions déjà générées (sans aucun appel LLM) ---------------
# Les fichiers viennent du workflow manuel (Claude Code, voir `manuel.py`).
# Contenu non fiable: validé par les modèles stricts, borné en taille, et
# l'import est refusé en bloc à la première question invalide.

MAX_IMPORT_OCTETS = 2 * 1024 * 1024
MAX_IMPORT_CONCEPTS_OCTETS = 1024 * 1024


async def _analyser_import(
    fichier: UploadFile, concepts: UploadFile | None, cours: str,
) -> manuel.Analyse:
    cours = cours.strip()
    if not cours or len(cours) > 100:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choisis un cours (100 caractères max).")

    donnees = await _lire_borne(fichier, MAX_IMPORT_OCTETS)
    donnees_concepts = nom_concepts = None
    if concepts is not None and concepts.filename:
        donnees_concepts = await _lire_borne(concepts, MAX_IMPORT_CONCEPTS_OCTETS)
        nom_concepts = Path(concepts.filename).name

    # Le nom sert uniquement à l'affichage (jamais comme chemin).
    nom = Path(fichier.filename or "questions.json").name
    analyse = manuel.analyser(
        donnees, nom=nom, concepts_contenu=donnees_concepts, concepts_nom=nom_concepts,
        cours=cours,
    )
    if analyse.rapport.nature == "concepts":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Ce fichier est une liste de concepts, pas un fichier de questions.",
        )
    return analyse


@app.post("/api/import/apercu", response_model=schemas.ImportApercuOut)
async def apercu_import(
    user: User, conn: Conn,
    fichier: UploadFile = File(...),
    concepts: UploadFile | None = File(None),
    cours: str = Form(...),
):
    """Valide le fichier et dit ce que l'import ferait. N'écrit rien."""
    analyse = await _analyser_import(fichier, concepts, cours)
    await run_in_threadpool(importer.marquer_deja_en_base, conn, analyse)
    apercu = analyse.rapport.apercu
    return schemas.ImportApercuOut(
        rapport=analyse.rapport,
        importable=analyse.resultat is not None,
        nouvelles=sum(1 for a in apercu if a.statut == "nouvelle"),
        deja_en_base=sum(1 for a in apercu if a.statut == "deja_en_base"),
        doublons_fichier=sum(1 for a in apercu if a.statut == "doublon_fichier"),
    )


@app.post("/api/import/confirmer", response_model=schemas.ImportOut)
async def confirmer_import(
    user: User, conn: Conn,
    fichier: UploadFile = File(...),
    concepts: UploadFile | None = File(None),
    cours: str = Form(...),
):
    """Revalide le fichier (rien n'est gardé entre l'aperçu et l'import), puis l'importe."""
    analyse = await _analyser_import(fichier, concepts, cours)
    if analyse.resultat is None:
        raise HTTPException(
            422,
            f"Fichier invalide ({analyse.rapport.nb_erreurs} erreur(s)): rien n'a été importé.",
        )
    rapport = await run_in_threadpool(
        importer.importer_resultat, conn, analyse.resultat, fichier=analyse.rapport.fichier,
        categories=importer.categories_de(analyse.concepts),
    )
    return schemas.ImportOut(
        importees=rapport.importees, deja_presentes=rapport.deja_presentes,
        doublons_contenu=rapport.doublons_contenu, cours=rapport.cours,
    )


@app.post("/api/ingest/generer")
def ingest_generer(
    data: schemas.GenerationIn, request: Request, taches: BackgroundTasks,
    user: User, conn: Conn,
):
    _verifier_cadence_llm(request)
    if not data.concepts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Aucun concept validé.")
    concepts = [Concept(**c.model_dump()) for c in data.concepts]
    job_id, nouveau = _creer_job(
        conn, "generation",
        empreinte=_empreinte_travail(
            "generation", data.cours,
            texte=json.dumps(
                [c.model_dump() for c in data.concepts], sort_keys=True, ensure_ascii=False
            ),
        ),
    )
    if nouveau:
        entree = _entree_depuis_contexte(conn, data.extraction_job_id, data.cours)
        taches.add_task(
            _tache_generation, job_id, entree, data.source,
            concepts, settings_store.modele_effectif(conn),
        )
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}", response_model=schemas.JobOut)
def etat_job(job_id: str, user: User, conn: Conn):
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tâche inconnue")
    return schemas.JobOut(
        id=row["id"], type=row["type"], statut=row["statut"],
        progression=row["progression"], message=row["message"],
        resultat=json.loads(row["resultat"]) if row["resultat"] else None,
    )


# --- Réglages / export -----------------------------------------------------

@app.get("/api/reglages", response_model=schemas.ReglagesOut)
def lire_reglages(user: User, conn: Conn):
    config = settings_store.config_effective(conn)
    return schemas.ReglagesOut(
        plafond_quotidien=config.plafond_quotidien,
        retention_normale=config.retention_normale,
        retention_test=config.retention_test,
        jours_phase_finale=config.jours_phase_finale,
        buffer_jours_avant_test=config.buffer_jours_avant_test,
        entretien_quota_hebdo=config.entretien_quota_hebdo,
        entretien_plafond_jour=config.entretien_plafond_jour,
        entretien_poids_max=config.entretien_poids_max,
        poids_type=config.poids_type,
        poids_categorie=config.poids_categorie,
        modele=settings_store.modele_effectif(conn),
        ordre_session=settings_store.ordre_effectif(conn),
        mock_llm=llm.mock_actif(),
        juge_etat=llm.etat_juge(),
        juge_modele=_modele_juge_affiche(),
        profils=profiles.lister(),
    )


@app.patch("/api/reglages")
def modifier_reglages(data: schemas.ReglagesPatch, compte: CompteCourant, conn: Conn):
    if data.modele is not None and not compte.admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Le modèle LLM est réservé à l'administrateur."
        )
    types_connus = set(engine_config_types())
    for cle, valeur in data.model_dump(exclude_none=True).items():
        if cle == "poids_type":
            for type_, poids in valeur.items():
                if type_ not in types_connus:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Type inconnu: {type_}")
                if poids <= 0:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Un poids doit être > 0.")
                settings_store.ecrire(conn, settings_store.PREFIXE_POIDS_TYPE + type_, poids)
        elif cle == "poids_categorie":
            for categorie, poids in valeur.items():
                if categorie not in CATEGORIES_CONNUES:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, f"Catégorie inconnue: {categorie}"
                    )
                if poids <= 0:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Un poids doit être > 0.")
                settings_store.ecrire(
                    conn, settings_store.PREFIXE_POIDS_CATEGORIE + categorie, poids
                )
        elif cle == "modele" and valeur not in budget.MODELES_AUTORISES:
            # Liste fermée: le modèle fixe le prix de chaque appel.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Modèle non autorisé. Modèles permis: "
                f"{', '.join(sorted(budget.MODELES_AUTORISES))}.",
            )
        else:
            settings_store.ecrire(conn, cle, valeur)
    return {"ok": True}


@app.get("/api/export")
def export_complet(user: User, conn: Conn):
    """Export JSON de toutes les données (cours, cartes, tests, historique)."""
    def lignes(requete: str) -> list[dict]:
        return [dict(r) for r in conn.execute(requete)]

    return {
        "exporte_le": datetime.now(timezone.utc).isoformat(),
        "matieres": lignes("SELECT * FROM matieres"),
        "cours": lignes("SELECT * FROM cours"),
        "cartes": lignes("SELECT * FROM cards"),
        "tests": lignes("SELECT * FROM exams"),
        "test_cours": lignes("SELECT * FROM exam_cours"),
        "test_matieres": lignes("SELECT * FROM exam_matieres"),
        "revisions": lignes("SELECT * FROM reviews"),
    }


@app.get("/api/budget", response_model=schemas.BudgetOut)
def lire_budget(user: User, conn: Conn):
    """Consommation, plafonds et alertes — visible dans Réglages."""
    plafonds = budget.charger_plafonds(load_config().budget_brut)
    conso = budget.consommation(conn)
    economies = conn.execute(
        "SELECT COALESCE(SUM(reutilise), 0) AS n FROM llm_cache"
    ).fetchone()["n"]
    refus = conn.execute(
        "SELECT COUNT(*) AS n FROM llm_appels WHERE statut = 'refuse'"
    ).fetchone()["n"]
    return schemas.BudgetOut(
        cout_jour_usd=conso["cout_jour_usd"],
        cout_mois_usd=conso["cout_mois_usd"],
        appels_jour=conso["appels_jour"],
        appels_mois=conso["appels_mois"],
        plafond_jour_usd=plafonds.cout_max_jour_usd,
        plafond_mois_usd=plafonds.cout_max_mois_usd,
        plafond_operation_usd=plafonds.cout_max_operation_usd,
        llm_desactive=budget.llm_desactive(conn),
        arret_par_env=budget.arret_par_env(),
        mode_reel=not llm.mock_actif(),
        disjoncteur_ouvert=budget.disjoncteur_ouvert(conn, plafonds),
        alertes=budget.alerte_80_pourcent(conn, plafonds),
        appels_evites=economies,
        refus=refus,
    )


@app.post("/api/budget/arret")
def arret_urgence(data: schemas.ArretIn, request: Request, admin: Admin, conn: Conn):
    """Interrupteur d'urgence: coupe (ou rétablit) tous les appels LLM.

    Effet immédiat, sans redémarrage, pour le serveur comme pour le CLI.
    Ne peut pas lever un `LLM_DISABLED=1` posé dans l'environnement.
    """
    budget.basculer_arret(conn, data.coupe)
    journal.warning(
        "appels LLM %s depuis %s", "coupés" if data.coupe else "rétablis", _ip(request)
    )
    return {
        "llm_desactive": budget.llm_desactive(conn),
        "arret_par_env": budget.arret_par_env(),
    }


@app.post("/api/ingest/estimer", response_model=schemas.EstimationOut)
def estimer_ingestion(data: schemas.ExtractionTexteIn, user: User, conn: Conn):
    """Coût estimé d'une extraction par texte, sans aucun appel."""
    try:
        estimation = llm.estimer(
            llm.Entree(voie="texte", cours=data.cours, texte=data.texte),
            operation="extraction",
            modele=settings_store.modele_effectif(conn),
        )
    except (budget.EntreeTropGrande, budget.BudgetDepasse) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return schemas.EstimationOut(
        operation=estimation.operation, modele=estimation.modele,
        tokens_entree=estimation.tokens_entree,
        max_tokens_sortie=estimation.max_tokens_sortie,
        cout_estime_usd=estimation.cout_estime_usd,
    )


@app.get("/api/health")
def health():
    return {"ok": True}
