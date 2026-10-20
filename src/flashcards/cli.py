"""CLI du projet.

Prototype 1 (génération): extract -> validation manuelle -> generate -> review
Prototype 2 (planification): import -> test add -> today / session / stats
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import typer

from . import api, manuel, profiles, session as session_mod
from .config import load_scheduler_config
from .db import open_db
from .extract import estimer_extract, run_extract
from .generate import estimer_generate, run_generate
from .importer import concepts_voisin, import_questions
from .models import ConceptList
from .review import run_review

app = typer.Typer(add_completion=False, no_args_is_help=True)
test_app = typer.Typer(add_completion=False, no_args_is_help=True, help="Gère les dates de test.")
app.add_typer(test_app, name="test")
web_app = typer.Typer(add_completion=False, no_args_is_help=True,
                      help="Interface web: compte, serveur, démo, sauvegardes.")
app.add_typer(web_app, name="web")
matiere_app = typer.Typer(add_completion=False, no_args_is_help=True,
                          help="Gère les matières (parents optionnels des cours).")
app.add_typer(matiere_app, name="matiere")
cours_app = typer.Typer(add_completion=False, no_args_is_help=True,
                        help="Gère les cours (thèmes): matière, archivage.")
app.add_typer(cours_app, name="cours")
depot_app = typer.Typer(add_completion=False, no_args_is_help=True,
                        help="Fichiers déposés depuis l'interface web (comptes étendus).")
app.add_typer(depot_app, name="depot")


def _guard(fn: Callable[[], None]) -> None:
    try:
        fn()
    except (FileNotFoundError, RuntimeError) as e:
        typer.echo(f"Erreur: {e}", err=True)
        raise typer.Exit(1)


def _open() -> tuple:
    config = load_scheduler_config()
    return open_db(config.db_path), config


def _parse_date(valeur: str | None) -> date:
    """`--jour` permet de figer la date (tests, rattrapage d'un jour passé)."""
    return date.fromisoformat(valeur) if valeur else datetime.now(timezone.utc).date()


# --- Prototype 1 -----------------------------------------------------------

@app.command()
def extract(
    pdf: Path = typer.Argument(..., exists=True, readable=True, help="PDF du cours."),
    cours: str = typer.Option(..., help="Thème (ex: Statique des fluides)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Estime le coût sans appeler l'API."),
    forcer: bool = typer.Option(False, "--forcer", help="Ignore le cache anti-doublon."),
) -> None:
    """Étape A: extrait le plan de concepts du PDF dans <pdf>.concepts.json."""

    def _run() -> None:
        if dry_run:
            _afficher_estimation(estimer_extract(pdf, cours=cours))
            return
        out_path, avertissements = run_extract(
            pdf, cours=cours, forcer=forcer
        )
        for avertissement in avertissements:
            typer.echo(f"Avertissement: {avertissement}", err=True)
        typer.echo(f"Concepts extraits -> {out_path}")
        typer.echo(
            "Relis et corrige ce fichier, puis mets \"validated\": true avant de "
            "lancer `generate`."
        )

    _guard(_run)


@app.command()
def generate(
    pdf: Path = typer.Argument(..., exists=True, readable=True, help="PDF du cours."),
    cours: str = typer.Option(
        None,
        help="Cours dont appliquer le profil (défaut: celui du fichier de concepts).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Estime le coût sans appeler l'API."),
    forcer: bool = typer.Option(False, "--forcer", help="Ignore le cache anti-doublon."),
) -> None:
    """Étape B: génère les questions depuis <pdf>.concepts.json (doit être validé)."""

    def _run() -> None:
        if dry_run:
            _afficher_estimation(estimer_generate(pdf, cours=cours))
            return
        questions_out, coverage_out, avertissements = run_generate(
            pdf, cours=cours, forcer=forcer
        )
        for avertissement in avertissements:
            typer.echo(f"Avertissement: {avertissement}", err=True)
        typer.echo(f"Questions générées -> {questions_out}")
        typer.echo(f"Rapport de couverture -> {coverage_out}")

    _guard(_run)


@app.command()
def review(
    pdf: Path = typer.Argument(..., exists=True, readable=True, help="PDF du cours."),
) -> None:
    """Relit les questions générées une à une pour juger leur qualité."""

    def _run() -> None:
        out_path = run_review(pdf)
        typer.echo(f"Notes sauvegardées -> {out_path}")

    _guard(_run)


# --- Workflow manuel (Claude Code joue le rôle du LLM, sans API) ------------

@app.command()
def validate(
    fichier: Path = typer.Argument(
        ..., exists=True, readable=True, help="Fichier de concepts ou de questions (.json)."
    ),
    concepts: Path = typer.Option(
        None, "--concepts", exists=True, readable=True,
        help="Liste de concepts pour contrôler les concept_id et la couverture "
             "(défaut: <nom>_concepts.json à côté du fichier, s'il existe).",
    ),
) -> None:
    """Valide un fichier de concepts ou de questions. Ne modifie rien, aucun appel LLM.

    Code de sortie 1 si le fichier est invalide.
    """

    valide = False

    def _run() -> None:
        nonlocal valide
        voisin = concepts or concepts_voisin(fichier)
        analyse = manuel.analyser(
            fichier.read_bytes(),
            nom=str(fichier),
            concepts_contenu=voisin.read_bytes() if voisin else None,
            concepts_nom=str(voisin) if voisin else None,
        )
        typer.echo(manuel.rapport_en_texte(analyse.rapport))
        valide = analyse.rapport.valide

    _guard(_run)
    # Hors de `_guard`: `typer.Exit` est un RuntimeError, il serait avalé.
    if not valide:
        raise typer.Exit(1)


@app.command("prompt")
def prompt_cmd(
    etape: str = typer.Argument(..., help="extraction ou generation."),
    cours: str = typer.Option(..., help="Thème (choisit le profil)."),
    concepts: Path = typer.Option(
        None, "--concepts", exists=True, readable=True,
        help="Fichier de concepts VALIDÉ (obligatoire pour generation).",
    ),
) -> None:
    """Affiche le prompt exact (prompts/*.md + profil du cours), prêt à suivre.

    Sert au workflow manuel: c'est le même rendu que celui envoyé à l'API,
    sans aucun appel. Les avertissements (profil absent) vont sur stderr.
    """

    def _run() -> None:
        from .llm import PROMPT_EXTRACTION, PROMPT_GENERATION, charger_prompt

        if etape not in ("extraction", "generation"):
            raise RuntimeError("L'étape doit être « extraction » ou « generation ».")

        conn, _ = _open()
        profil = profiles.charger(cours, conn=conn)
        if profil.avertissement:
            typer.echo(f"Avertissement: {profil.avertissement}", err=True)

        variables = {"cours": cours, "profil_cours": profil.texte}
        if etape == "generation":
            if concepts is None:
                raise RuntimeError("`prompt generation` demande --concepts <fichier validé>.")
            liste = ConceptList.model_validate_json(concepts.read_text())
            if not liste.validated:
                raise RuntimeError(
                    f"{concepts} n'est pas validé: passe `validated` à `true` après relecture."
                )
            variables["concepts_valides"] = json.dumps(
                [c.model_dump(by_alias=True) for c in liste.concepts],
                ensure_ascii=False, indent=2,
            )
        gabarit = PROMPT_EXTRACTION if etape == "extraction" else PROMPT_GENERATION
        typer.echo(profiles.rendre(charger_prompt(gabarit), **variables))

    _guard(_run)


# --- Prototype 2: import ---------------------------------------------------

@app.command("import")
def import_cmd(
    fichier: Path = typer.Argument(
        ..., exists=True, readable=True,
        help="<nom>.questions.json (API) ou sorties/<nom>_questions.json (manuel).",
    ),
) -> None:
    """Importe des questions générées dans la base de planification."""

    def _run() -> None:
        conn, _ = _open()
        rapport = import_questions(conn, fichier)
        typer.echo(
            f"{rapport.importees} carte(s) importée(s), "
            f"{rapport.deja_presentes} déjà présente(s), "
            f"{rapport.doublons_contenu} doublon(s) de contenu ignoré(s)."
        )
        if rapport.cours:
            typer.echo(f"Cours: {chr(44).join(rapport.cours)}")

    _guard(_run)


# --- Prototype 2: tests ----------------------------------------------------

@test_app.command("add")
def test_add(
    cours: list[str] = typer.Option(None, "--cours", help="Cours (thème) lié (répétable)."),
    matiere: list[str] = typer.Option(
        None, "--matiere", help="Lie TOUS les cours de cette matière (répétable)."
    ),
    jour: str = typer.Option(..., "--date", help="Date du test (YYYY-MM-DD)."),
    intitule: str = typer.Option(..., help="Intitulé du test."),
) -> None:
    """Ajoute un test lié à un ou plusieurs cours, ou à toute une matière.

    Le planning des cartes concernées est recalculé aussitôt.
    """

    def _run() -> None:
        conn, _ = _open()
        exam_id = api.add_test(
            conn,
            cours=list(cours or []),
            matieres=list(matiere or []),
            jour=date.fromisoformat(jour),
            intitule=intitule,
        )
        exam = next(e for e in api.list_tests(conn) if e.id == exam_id)
        typer.echo(f"Test #{exam_id} ajouté: {intitule} le {jour}")
        typer.echo(f"  couvre: {_couverture_texte(exam)}")

    _guard(_run)


def _couverture_texte(exam) -> str:
    morceaux = []
    if exam.matieres:
        morceaux.append("matière(s) " + ", ".join(sorted(exam.matieres)) + " (entière)")
    morceaux.append(f"{len(exam.cours)} cours: {exam.cours_txt}" if exam.cours else "aucun cours actif")
    return " · ".join(morceaux)


@test_app.command("list")
def test_list() -> None:
    """Liste les tests enregistrés."""

    def _run() -> None:
        conn, _ = _open()
        exams = api.list_tests(conn)
        if not exams:
            typer.echo("Aucun test enregistré.")
            return
        for exam in exams:
            typer.echo(f"#{exam.id}  {exam.date}  {exam.intitule}")
            typer.echo(f"      {_couverture_texte(exam)}")

    _guard(_run)


@test_app.command("move")
def test_move(
    exam_id: int = typer.Argument(..., help="Id du test (voir `test list`)."),
    jour: str = typer.Option(None, "--date", help="Nouvelle date (YYYY-MM-DD)."),
    ajouter_cours: list[str] = typer.Option(None, "--ajouter-cours", help="Lie un cours de plus."),
    retirer_cours: list[str] = typer.Option(None, "--retirer-cours", help="Délie un cours."),
    ajouter_matiere: list[str] = typer.Option(
        None, "--ajouter-matiere", help="Lie toute une matière de plus."
    ),
    retirer_matiere: list[str] = typer.Option(None, "--retirer-matiere", help="Délie une matière."),
) -> None:
    """Déplace un test et/ou change ses liens: le planning se recalcule automatiquement."""

    def _run() -> None:
        conn, _ = _open()
        if not any([jour, ajouter_cours, retirer_cours, ajouter_matiere, retirer_matiere]):
            raise RuntimeError("Rien à faire: donne --date et/ou des liens à ajouter/retirer.")
        if jour:
            api.move_test(conn, exam_id, date.fromisoformat(jour))
            typer.echo(f"Test #{exam_id} déplacé au {jour}.")
        if ajouter_cours:
            api.link_cours(conn, exam_id, list(ajouter_cours))
        for m in ajouter_matiere or []:
            nb = api.link_matiere(conn, exam_id, m)
            typer.echo(f"Matière « {m} » liée: {nb} cours actif(s) couvert(s).")
        if retirer_cours:
            api.unlink_cours(conn, exam_id, list(retirer_cours))
        for m in retirer_matiere or []:
            api.unlink_matiere(conn, exam_id, m)
        exam = next(e for e in api.list_tests(conn) if e.id == exam_id)
        typer.echo(f"Test #{exam_id}: {_couverture_texte(exam)}")

    _guard(_run)


@test_app.command("remove")
def test_remove(
    exam_id: int = typer.Argument(..., help="Id du test (voir `test list`)."),
) -> None:
    """Supprime un test: les cartes retombent sur le rythme FSRS normal."""

    def _run() -> None:
        conn, _ = _open()
        api.remove_test(conn, exam_id)
        typer.echo(f"Test #{exam_id} supprimé.")

    _guard(_run)


# --- Prototype 2: today / session / stats ----------------------------------

def _pts(valeur: float) -> str:
    """1 décimale, sans « .0 » inutile."""
    return f"{valeur:.1f}".rstrip("0").rstrip(".") if abs(valeur - round(valeur)) > 1e-9 else f"{valeur:.0f}"


@app.command()
def today(
    jour: str = typer.Option(None, "--jour", help="Forcer la date (YYYY-MM-DD)."),
) -> None:
    """Affiche la charge du jour (cartes et points) par cours, l'entretien et le prochain test."""

    def _run() -> None:
        conn, config = _open()
        aujourdhui = _parse_date(jour)
        plan = api.plan_du_jour(conn, aujourdhui, config)
        queue = plan.items

        typer.echo(
            f"{aujourdhui} — {len(queue)} question(s), environ {_pts(plan.points)} point(s) "
            f"à réviser aujourd'hui (plafond {_pts(config.plafond_quotidien)} pts)."
        )
        reportees = len(plan.dus) - sum(1 for i in queue if not i.complement)
        if reportees > 0:
            typer.echo(
                f"({len(plan.dus)} dues au total, {_pts(plan.points_dus)} pts; "
                f"{reportees} reportée(s) à demain)"
            )

        par_cours: dict[str, list] = {}
        for item in queue:
            par_cours.setdefault(item.card.cours, []).append(item)
        for cours, items in sorted(par_cours.items()):
            marque = "  (entretien)" if cours in plan.themes_entretien else ""
            typer.echo(
                f"  {cours:22} {len(items):3} q  {_pts(sum(i.points for i in items)):>5} pts{marque}"
            )

        neuves = sum(1 for item in queue if item.nouvelle)
        retard = sum(1 for item in queue if item.jours_retard > 0)
        if neuves:
            typer.echo(f"  dont {neuves} nouvelle(s)")
        if retard:
            typer.echo(f"  dont {retard} en retard")

        if plan.themes_entretien:
            typer.echo(
                f"\nEntretien: {len(plan.themes_entretien)} thème(s) sans test à venir — "
                f"{plan.nb_entretien} question(s), {_pts(plan.points_entretien)} pts aujourd'hui "
                f"({plan.part_entretien:.0%} de la charge; plafond d'entretien "
                f"{_pts(config.entretien_plafond_jour)} pts/jour)."
            )
        for alerte in api.alertes_entretien(plan.themes_entretien, config):
            typer.echo(f"⚠ {alerte}")

        exams = [e for e in api.load_exams(conn) if e.date >= aujourdhui]
        if exams:
            prochain = min(exams, key=lambda e: e.date)
            jours = (prochain.date - aujourdhui).days
            typer.echo(
                f"\nProchain test: {prochain.intitule} ({prochain.cours_txt}) "
                f"le {prochain.date} — dans {jours} jour(s)."
            )
        else:
            typer.echo("\nAucun test à venir.")

    _guard(_run)


@app.command()
def session(
    jour: str = typer.Option(None, "--jour", help="Forcer la date (YYYY-MM-DD)."),
) -> None:
    """Lance la session de révision du jour. `q` à tout moment pour arrêter."""

    def _run() -> None:
        conn, config = _open()
        aujourdhui = _parse_date(jour)
        queue = api.get_today_queue(conn, aujourdhui, config)
        if not queue:
            typer.echo("Rien à réviser aujourd'hui.")
            return

        total_pts = sum(i.points for i in queue)
        typer.echo(
            f"{len(queue)} question(s), environ {_pts(total_pts)} point(s) — "
            f"'q' pour arrêter à tout moment.\n"
        )
        faites = 0
        points_faits = 0.0
        for i, item in enumerate(queue, 1):
            marque = f" (en retard de {item.jours_retard}j)" if item.jours_retard else ""
            if item.entretien:
                marque += " (entretien)"
            typer.echo(
                f"--- {i}/{len(queue)} · {_pts(points_faits)}/{_pts(total_pts)} pts — "
                f"{item.card.cours} — {item.card.type}{marque} ---"
            )
            try:
                reponse = session_mod.pose_question(item.card.question())
            except (session_mod.QuitSession, EOFError, KeyboardInterrupt):
                typer.echo("\nSession interrompue. Progression enregistrée.")
                break
            api.record_answer(
                conn,
                item.card.id,
                reponse.rating,
                reviewed_at=datetime.now(timezone.utc),
                config=config,
                correct=reponse.correct,
                source=reponse.source,
            )
            faites += 1
            points_faits += item.points
            typer.echo("")

        typer.echo(f"{faites} question(s) enregistrée(s), {_pts(points_faits)} point(s).")

    _guard(_run)


@app.command()
def stats(
    jour: str = typer.Option(None, "--jour", help="Forcer la date (YYYY-MM-DD)."),
) -> None:
    """Charge prévue sur 14 jours (points et cartes), entretien, rétention par test."""

    def _run() -> None:
        conn, config = _open()
        aujourdhui = _parse_date(jour)
        s = api.get_stats(conn, aujourdhui, config)

        typer.echo(f"{s.total_cartes} carte(s) actives en base.")
        typer.echo(
            f"Dues aujourd'hui: {s.nb_dues} carte(s), {_pts(s.points_dus)} pts "
            f"({s.nb_nouvelles} nouvelle(s), {s.nb_en_retard} en retard)"
        )
        typer.echo(
            f"File du jour: {s.cartes_jour} carte(s), {_pts(s.points_jour)} pts "
            f"sur {_pts(s.plafond)} de plafond."
        )
        if s.themes_entretien:
            typer.echo(
                f"Entretien: {len(s.themes_entretien)} thème(s) — aujourd'hui "
                f"{s.cartes_entretien_jour} carte(s), {_pts(s.points_entretien_jour)} pts "
                f"({s.part_entretien:.0%} de la charge)."
            )
        for alerte in s.alertes:
            typer.echo(f"⚠ {alerte}")

        typer.echo("\nCharge prévue (si tu réponds juste), points / cartes (dont entretien):")
        for c in s.charge_14j:
            barre = "#" * min(round(c.points), 60)
            typer.echo(
                f"  {c.jour}  {_pts(c.points):>5} pts  {c.cartes:3} q"
                f"  (entretien {_pts(c.points_entretien):>4} pts, {c.cartes_entretien:2} q)  {barre}"
            )

        if s.examens:
            typer.echo("\nTests à venir:")
            for es in s.examens:
                typer.echo(
                    f"  {es.exam.date}  {es.exam.intitule} ({es.exam.cours_txt})\n"
                    f"      {es.nb_cartes} carte(s), {es.nb_jamais_revisees} jamais révisée(s),"
                    f" rétention estimée le jour J: {es.retention_estimee:.1%}"
                )
        else:
            typer.echo("\nAucun test à venir.")

    _guard(_run)


@app.command()
def simulate(
    jours: int = typer.Option(120, help="Durée de la simulation en jours."),
    scenario: str = typer.Option(
        "annee", help="« annee » (20 thèmes, entretien, examen de matière) ou « simple »."
    ),
    cartes: int = typer.Option(320, help="Nombre de cartes factices (scénario simple)."),
    graine: int = typer.Option(42, help="Graine aléatoire (reproductibilité)."),
    p_saut: float = typer.Option(0.12, help="Probabilité de sauter un jour."),
    sortie: Path = typer.Option(Path("logs/simulation_charge.png"), help="PNG de la charge."),
) -> None:
    """Simule N jours avec un élève virtuel: rapport en points et en cartes + graphique."""

    def _run() -> None:
        from .config import PROJECT_ROOT
        from .simulate import SimConfig, plot_charge, simulate as run_sim

        if scenario not in ("annee", "simple"):
            raise RuntimeError("Scénario inconnu: « annee » ou « simple ».")
        config = load_scheduler_config()
        sim = SimConfig(
            jours=jours, nb_cartes=cartes, graine=graine, p_saut=p_saut, scenario=scenario,
        )
        db_path = PROJECT_ROOT / "logs" / "simulation.db"
        start = date(2026, 1, 5)

        result = run_sim(db_path, config, sim, start)
        r, c, e = result.resume_scenario, result.rapport_charge, result.rapport_entretien

        typer.echo(f"Simulation « {scenario} »: {jours} jours, graine {graine}")
        typer.echo(
            f"Base simulée: {r['themes']} thème(s), {r['matieres']} matière(s), {r['cartes']} cartes "
            f"({r['vocabulaire']} de vocabulaire), {_pts(r['points_total_cartes'])} pts au total."
        )
        typer.echo("  par type: " + ", ".join(f"{t} {n}" for t, n in sorted(r["par_type"].items())))
        typer.echo(f"Jours sautés: {len(result.jours_sautes)}")
        typer.echo(
            f"\nCharge quotidienne (jours actifs, plafond {_pts(result.plafond)} pts):\n"
            f"  points  — moyenne {c['points_moyenne']:.1f}, médiane {c['points_mediane']:.1f}, "
            f"p95 {c['points_p95']:.1f}, max {c['points_max']:.1f}\n"
            f"  cartes  — moyenne {c['cartes_moyenne']:.1f}, max {c['cartes_max']}  "
            f"({c['points_par_carte_moyen']:.2f} pt/carte en moyenne)\n"
            f"  plafond atteint {c['jours_au_plafond']} jour(s) sur {c['jours_actifs']}"
        )
        typer.echo(
            f"Révisions totales: {result.total_revisions} ({_pts(result.points_total)} pts), "
            f"taux de réussite de l'élève: {result.taux_reussite:.1%}"
        )

        if e:
            typer.echo(
                f"\nEntretien ({e['themes_entretien_min']} à {e['themes_entretien_max']} thème(s) "
                f"en entretien selon les jours):\n"
                f"  part de la charge en points: {e['part_moyenne']:.0%} — "
                f"{e['points_par_jour_moyenne']:.1f} pts/jour (max {e['points_par_jour_max']:.1f}, "
                f"plafond {_pts(e['plafond_jour'])}; dépassé {e['jours_plafond_entretien_depasse']} jour(s))\n"
                f"  quota hebdo {_pts(e['quota_hebdo'])} pts/thème: tenu "
                + (f"{e['quota_tenu_pct']:.0%}" if e["quota_tenu_pct"] is not None else "n/a")
                + " des semaines de thème"
                + (f" ({e['quota_tenu_sans_saut_pct']:.0%} hors semaines avec jour sauté)"
                   if e["quota_tenu_sans_saut_pct"] is not None else "")
                + f"\n  points par semaine et par thème: moyenne "
                + (f"{e['points_semaine_theme_moyenne']:.1f}" if e["points_semaine_theme_moyenne"] is not None else "n/a")
                + f", min {e['points_semaine_theme_min']}, max {e['points_semaine_theme_max']}\n"
                f"  rotation: chaque thème révisé entre {e['rotation_min']:.0%} et "
                f"{e['rotation_max']:.0%} de ses jours d'entretien"
            )

        typer.echo("\nÉtat aux examens (à venir au départ):")
        for exam in result.examens:
            typer.echo(
                f"  J+{exam['jour_sim']:3} {exam['date']} {exam['intitule']} ({exam['cours'][:40]})\n"
                f"       {exam['nb_cartes']} cartes, "
                f"{exam['nb_jamais_revisees_avant']} jamais vue(s) avant le test, "
                f"rétention moy. {exam['retention_moyenne']:.1%} "
                f"(min {exam['retention_min']:.1%})"
            )

        if result.effet_saut_force:
            ef = result.effet_saut_force
            typer.echo(f"\nEffet d'un saut forcé de 3 jours (à J+{ef['jour_debut_saut']}):")
            typer.echo(
                f"  dette (pts) avant: {_pts(ef['dette_avant'])} -> après 3 jours: "
                f"{_pts(ef['dette_apres_3j'])}"
            )
            typer.echo(f"  charge (pts) des 5 jours suivants: {[round(n) for n in ef['charge_5j_suivants']]}")
            typer.echo(f"  jours pour revenir au niveau d'avant: {ef['jours_pour_resorber']}")

        dettes = [n for _, n in result.dette_points_par_jour]
        typer.echo(
            f"\nDette (points dus) tous les 10 jours: {[round(n) for n in dettes[::10]]}"
        )
        if result.dette_entretien_points_par_jour:
            ent = [n for _, n in result.dette_entretien_points_par_jour]
            typer.echo(f"  dont entretien, tous les 10 jours:   {[round(n) for n in ent[::10]]}")

        if result.anomalies:
            typer.echo("\n⚠ Comportements à examiner:")
            for a in result.anomalies:
                typer.echo(f"  - {a}")
        else:
            typer.echo("\nAucune anomalie détectée.")

        png = plot_charge(result, PROJECT_ROOT / sortie)
        typer.echo(f"\nGraphique -> {png}")

    _guard(_run)


# --- Matières et cours (thèmes) ---------------------------------------------

@matiere_app.command("list")
def matiere_list() -> None:
    """Liste les matières et leurs thèmes."""

    def _run() -> None:
        conn, _ = _open()
        matieres = api.list_matieres(conn)
        themes = api.list_cours(conn)
        if not matieres:
            typer.echo("Aucune matière. Crée-en une: flashcards matiere add --nom Anglais")
        for m in matieres:
            typer.echo(f"{m.nom}  ({m.nb_cours} cours, {m.nb_cartes} cartes)")
            for t in (t for t in themes if t.matiere == m.nom):
                typer.echo(f"    - {t.nom}{'  [archivé]' if t.archive else ''} ({t.nb_cartes} cartes)")
        seuls = [t for t in themes if t.matiere is None]
        if seuls:
            typer.echo("\nSans matière:")
            for t in seuls:
                typer.echo(f"    - {t.nom}{'  [archivé]' if t.archive else ''} ({t.nb_cartes} cartes)")

    _guard(_run)


@matiere_app.command("add")
def matiere_add(
    nom: str = typer.Option(..., help="Nom de la matière (ex. Anglais)."),
    couleur: str = typer.Option("#8B7CFF", help="Couleur d'affichage."),
) -> None:
    """Crée une matière. Le profil se règle ensuite dans l'interface (ou profiles/<nom>.md)."""

    def _run() -> None:
        conn, _ = _open()
        api.add_matiere(conn, nom, couleur)
        typer.echo(f"Matière « {nom} » créée.")

    _guard(_run)


@matiere_app.command("delete")
def matiere_delete(nom: str = typer.Option(..., help="Matière à supprimer.")) -> None:
    """Supprime une matière. Ses cours et leurs cartes sont conservés, sans parent."""

    def _run() -> None:
        conn, _ = _open()
        nb = api.delete_matiere(conn, nom)
        typer.echo(f"Matière « {nom} » supprimée ({nb} cours détaché(s), rien d'autre touché).")

    _guard(_run)


@cours_app.command("add")
def cours_add(
    nom: str = typer.Option(..., help="Nom du thème (ex. Anglais conditionnel)."),
    matiere: str = typer.Option(None, help="Matière parente (facultative)."),
) -> None:
    """Crée un cours (thème), éventuellement rangé dans une matière."""

    def _run() -> None:
        conn, _ = _open()
        api.add_cours(conn, nom, matiere=matiere)
        typer.echo(f"Cours « {nom} » créé" + (f" dans « {matiere} »." if matiere else "."))

    _guard(_run)


@cours_app.command("list")
def cours_list(
    jour: str = typer.Option(None, "--jour", help="Forcer la date (YYYY-MM-DD)."),
) -> None:
    """Liste les cours avec leur mode (test / entretien / archivé) et leurs points."""

    def _run() -> None:
        conn, config = _open()
        entretien = api.cours_en_entretien(conn, _parse_date(jour))
        points = api.points_par_cours(conn, config)
        for c in api.list_cours(conn):
            mode = "archivé" if c.archive else ("entretien" if c.nom in entretien else "test")
            parent = f"{c.matiere} › " if c.matiere else ""
            typer.echo(
                f"{parent}{c.nom:28} {mode:10} {c.nb_cartes:4} cartes  "
                f"{_pts(points.get(c.nom, 0.0)):>6} pts"
            )

    _guard(_run)


@cours_app.command("set-matiere")
def cours_set_matiere(
    nom: str = typer.Option(..., help="Cours (thème)."),
    matiere: str = typer.Option(None, help="Matière (omets pour détacher)."),
) -> None:
    """Range un cours dans une matière (ou le détache)."""

    def _run() -> None:
        conn, _ = _open()
        api.update_cours(conn, nom, matiere=matiere)
        typer.echo(f"« {nom} » → " + (f"« {matiere} »." if matiere else "sans matière."))

    _guard(_run)


@cours_app.command("recap")
def cours_recap(
    nom: str = typer.Option(..., help="Cours (thème)."),
    fichier: Path = typer.Option(
        None, help="Fichier Markdown à enregistrer comme récap (omets pour afficher l'actuel)."
    ),
    efface: bool = typer.Option(False, "--efface", help="Supprime le récap du thème."),
) -> None:
    """Affiche, enregistre (--fichier) ou efface (--efface) le récap théorique d'un thème.

    Le récap est lu avant les questions du thème en session « par cours ». Aucun LLM.
    """

    def _run() -> None:
        conn, _ = _open()
        theme = next((c for c in api.list_cours(conn) if c.nom == nom), None)
        if theme is None:
            raise RuntimeError(f"Cours inconnu: {nom}")
        if efface:
            api.update_cours(conn, nom, recap="")
            typer.echo(f"Récap de « {nom} » effacé.")
        elif fichier is not None:
            texte = fichier.read_text(encoding="utf-8")
            api.update_cours(conn, nom, recap=texte)
            typer.echo(f"Récap de « {nom} » enregistré ({len(texte.strip())} caractères).")
        else:
            typer.echo(theme.recap or f"(Pas de récap pour « {nom} ».)")

    _guard(_run)


@depot_app.command("list")
def depot_list(
    cours: str = typer.Option(None, help="Un seul cours."),
    tout: bool = typer.Option(False, "--tout", help="Aussi les fichiers déjà traités."),
) -> None:
    """Liste les fichiers déposés (par défaut: ceux qui attendent encore)."""

    def _run() -> None:
        from . import depot

        conn, _ = _open()
        lignes = depot.lister(conn, cours=cours, a_traiter=not tout)
        if not lignes:
            typer.echo("Aucun fichier en attente." if not tout else "Aucun fichier déposé.")
            return
        for d in lignes:
            etat = "traité" if d.traite_le else "à traiter"
            typer.echo(
                f"#{d.id}  [{d.cours}]  {d.nom_original}  "
                f"({d.taille / 1024:.0f} Ko, par {d.depose_par}, {d.depose_le[:10]}, {etat})"
            )

    _guard(_run)


@depot_app.command("recuperer")
def depot_recuperer(
    depot_id: int = typer.Argument(..., help="Numéro affiché par `depot list`."),
    vers: Path = typer.Option(Path("inbox"), help="Dossier de destination."),
) -> None:
    """Copie un fichier déposé dans inbox/ et affiche son chemin et son cours."""

    def _run() -> None:
        from . import depot

        conn, _ = _open()
        d = depot.lire(conn, depot_id)
        cible = depot.recuperer(conn, depot_id, vers)
        typer.echo(f"fichier: {cible}")
        typer.echo(f"cours: {d.cours}")

    _guard(_run)


@depot_app.command("traite")
def depot_traite(depot_id: int = typer.Argument(..., help="Numéro affiché par `depot list`.")) -> None:
    """Marque un fichier comme utilisé (les questions ont été importées)."""

    def _run() -> None:
        from . import depot

        conn, _ = _open()
        depot.marquer_traite(conn, depot_id)
        typer.echo(f"Dépôt #{depot_id} marqué comme traité.")

    _guard(_run)


@cours_app.command("archive")
def cours_archive(nom: str = typer.Option(..., help="Cours à archiver.")) -> None:
    """Archive un cours (réversible): il sort du planning et de l'entretien, ses cartes restent."""

    def _run() -> None:
        conn, _ = _open()
        api.archive_cours(conn, nom, True)
        typer.echo(f"« {nom} » archivé. Ses cartes sont conservées (`cours unarchive` pour revenir).")

    _guard(_run)


@cours_app.command("unarchive")
def cours_unarchive(nom: str = typer.Option(..., help="Cours à désarchiver.")) -> None:
    """Désarchive un cours: il revient dans le planning (entretien s'il n'a plus de test à venir)."""

    def _run() -> None:
        conn, _ = _open()
        api.archive_cours(conn, nom, False)
        typer.echo(f"« {nom} » désarchivé.")

    _guard(_run)


# --- Prototype 3: interface web --------------------------------------------

@web_app.command("create-user")
def web_create_user(
    identifiant: str = typer.Option("nate", help="Nom d'utilisateur."),
    mot_de_passe: str = typer.Option(
        ..., prompt=True, hide_input=True, confirmation_prompt=True,
        help="Mot de passe (12 caractères minimum).",
    ),
    role: str = typer.Option(
        "admin", help="admin | standard | etendu (admin par défaut: le premier compte)."
    ),
) -> None:
    """Crée ou remplace un compte de l'interface web."""

    def _run() -> None:
        from .web import auth

        conn, _ = _open()
        auth.creer_compte(conn, identifiant, mot_de_passe, role)
        typer.echo(f"Compte « {identifiant} » ({role}) créé.")

    _guard(_run)


@web_app.command("seed-demo")
def web_seed_demo() -> None:
    """Remplit la base avec des cours, questions et tests de démonstration."""

    def _run() -> None:
        from .seed import seed_demo

        conn, _ = _open()
        resultat = seed_demo(conn)
        if resultat["deja_rempli"]:
            typer.echo("La base contient déjà des questions: rien à faire.")
            return
        typer.echo(
            f"{resultat['cours']} cours, {resultat['questions']} questions et "
            f"{resultat['tests']} tests de démonstration créés."
        )

    _guard(_run)


@web_app.command("backup")
def web_backup(
    vers: Path = typer.Option(None, help="Dossier de destination."),
) -> None:
    """Sauvegarde cohérente de la base (export manuel)."""

    def _run() -> None:
        from .backup import purger, sauvegarder

        config = load_scheduler_config()
        chemin = sauvegarder(config.db_path, vers)
        supprimees = purger(vers)
        typer.echo(f"Sauvegarde -> {chemin}")
        if supprimees:
            typer.echo(f"{len(supprimees)} ancienne(s) sauvegarde(s) purgée(s).")

    _guard(_run)


@web_app.command("serve")
def web_serve(
    host: str = typer.Option("127.0.0.1", help="Adresse d'écoute."),
    port: int = typer.Option(8000, help="Port."),
    reload: bool = typer.Option(False, help="Rechargement à chaud (développement)."),
) -> None:
    """Démarre l'API web (serveur de développement, écoute locale).

    En production, c'est le conteneur Docker qui lance uvicorn derrière Caddy.
    """
    import os

    import uvicorn

    local = host in ("127.0.0.1", "localhost", "::1")
    if "FLASHCARDS_ENV" not in os.environ:
        if not local:
            typer.echo(
                "Erreur: écoute sur une adresse non locale sans FLASHCARDS_ENV. "
                "Pose FLASHCARDS_ENV=production (cookie Secure, SESSION_SECRET "
                "obligatoire) ou reste sur 127.0.0.1.",
                err=True,
            )
            raise typer.Exit(1)
        # Écoute locale: mode développement (cookie non Secure, CORS Vite).
        os.environ["FLASHCARDS_ENV"] = "development"

    uvicorn.run("flashcards.web.app:app", host=host, port=port, reload=reload)


def _afficher_estimation(estimation) -> None:
    typer.echo(f"Opération      : {estimation.operation}")
    typer.echo(f"Modèle         : {estimation.modele}")
    typer.echo(f"Tokens entrée  : ~{estimation.tokens_entree:,}".replace(",", " "))
    typer.echo(f"Tokens sortie  : {estimation.max_tokens_sortie:,} (max)".replace(",", " "))
    typer.echo(f"Coût estimé    : {estimation.cout_estime_usd:.4f} USD")
    typer.echo("Aucun appel n'a été fait.")


llm_app = typer.Typer(add_completion=False, no_args_is_help=True,
                      help="Interrupteur d'urgence des appels au modèle.")
app.add_typer(llm_app, name="llm")


def _etat_llm(conn) -> str:
    from . import budget as budget_mod
    from . import llm as llm_mod

    if budget_mod.arret_par_env():
        return "COUPÉ par LLM_DISABLED=1 (variable d'environnement)."
    if budget_mod.arret_en_base(conn):
        return "COUPÉ par l'interrupteur d'urgence (`flashcards llm on` pour rétablir)."
    if llm_mod.mock_actif():
        return (
            "mode simulé: aucun appel réel possible "
            "(il faut FLASHCARDS_LLM_REEL=1 et une clé ANTHROPIC_API_KEY)."
        )
    return "ACTIF: les appels réels sont possibles, dans la limite des plafonds."


@llm_app.command("off")
def llm_off() -> None:
    """ARRÊT D'URGENCE: coupe tous les appels LLM, tout de suite, durablement."""

    def _run() -> None:
        from . import budget as budget_mod

        conn, _ = _open()
        budget_mod.basculer_arret(conn, True)
        typer.echo("Appels LLM coupés (serveur web et CLI). `flashcards llm on` pour rétablir.")

    _guard(_run)


@llm_app.command("on")
def llm_on() -> None:
    """Rétablit les appels LLM coupés par `llm off` (ou par l'interface)."""

    def _run() -> None:
        from . import budget as budget_mod

        conn, _ = _open()
        budget_mod.basculer_arret(conn, False)
        typer.echo(f"Appels LLM: {_etat_llm(conn)}")

    _guard(_run)


@llm_app.command("status")
def llm_status() -> None:
    """Dit si un appel réel peut partir, et sinon pourquoi."""

    def _run() -> None:
        conn, _ = _open()
        typer.echo(f"Appels LLM: {_etat_llm(conn)}")

    _guard(_run)


@app.command("budget")
def budget(
    desactiver: bool = typer.Option(
        False, "--desactiver", help="Coupe tous les appels LLM (comme `flashcards llm off`)."
    ),
) -> None:
    """Consommation du jour et du mois, plafonds et alertes."""

    def _run() -> None:
        from . import budget as budget_mod
        from .config import load_config

        conn, _ = _open()
        if desactiver:
            budget_mod.basculer_arret(conn, True)
            typer.echo("Appels LLM coupés. `flashcards llm on` pour rétablir.")
            return

        plafonds = budget_mod.charger_plafonds(load_config().budget_brut)
        conso = budget_mod.consommation(conn)

        typer.echo(f"Appels LLM: {_etat_llm(conn)}\n")

        typer.echo(
            f"Jour  : {conso['cout_jour_usd']:.4f} / "
            f"{plafonds.cout_max_jour_usd:.2f} USD  ({conso['appels_jour']} appels)"
        )
        typer.echo(
            f"Mois  : {conso['cout_mois_usd']:.4f} / "
            f"{plafonds.cout_max_mois_usd:.2f} USD  ({conso['appels_mois']} appels)"
        )
        typer.echo(f"Par opération: max {plafonds.cout_max_operation_usd:.2f} USD")
        typer.echo(f"Cadence      : max {plafonds.appels_max_par_minute} appels/minute")

        if budget_mod.disjoncteur_ouvert(conn, plafonds):
            typer.echo("\nDisjoncteur OUVERT: appels bloqués après échecs répétés.")
        for alerte in budget_mod.alerte_80_pourcent(conn, plafonds):
            typer.echo(f"\nALERTE: {alerte}")

        economies = conn.execute(
            "SELECT COALESCE(SUM(reutilise), 0) AS n FROM llm_cache"
        ).fetchone()["n"]
        if economies:
            typer.echo(f"\nAnti-doublon: {economies} appel(s) évité(s) par le cache.")

        refus = conn.execute(
            "SELECT COUNT(*) AS n FROM llm_appels WHERE statut = 'refuse'"
        ).fetchone()["n"]
        if refus:
            typer.echo(f"Refus enregistrés: {refus}")

    _guard(_run)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
