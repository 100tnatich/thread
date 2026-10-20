"""Boucle terminal pour noter à la main les questions générées."""
from __future__ import annotations

from pathlib import Path

from .generate import questions_path_for
from .models import GenerationResult, ReviewEntry, ReviewFile

# Les questions de confiance basse sont celles que le LLM lui-même ne garantit
# pas (typiquement un calcul de `probleme`). Ce sont les premières à relire.
MARQUEUR_CONFIANCE = {
    "haute": "confiance haute",
    "moyenne": "confiance moyenne",
    "basse": ">>> CONFIANCE BASSE — à vérifier en priorité <<<",
}


class _Quit(Exception):
    pass


def review_path_for(pdf_path: Path) -> Path:
    return pdf_path.with_name(pdf_path.stem + ".review.json")


def run_review(pdf_path: Path) -> Path:
    questions_path = questions_path_for(pdf_path)
    if not questions_path.exists():
        raise FileNotFoundError(
            f"Fichier de questions introuvable: {questions_path}. Lance d'abord `generate`."
        )
    result = GenerationResult.model_validate_json(questions_path.read_text())

    review_path = review_path_for(pdf_path)
    if review_path.exists():
        review_file = ReviewFile.model_validate_json(review_path.read_text())
    else:
        review_file = ReviewFile(source=pdf_path.name)

    reviewed_ids = {e.question_id for e in review_file.entries}
    remaining = [q for q in result.questions if q.id not in reviewed_ids]

    if result.avertissements:
        print("Avertissements de la génération:")
        for avertissement in result.avertissements:
            print(f"  · {avertissement}")
        print()

    if not remaining:
        print("Toutes les questions ont déjà été notées.")
        return review_path

    basses = sum(1 for q in remaining if q.confiance == "basse")
    print(f"{len(remaining)} question(s) à noter sur {len(result.questions)} générées.")
    if basses:
        print(f"Dont {basses} de confiance basse, signalée(s) ci-dessous.")
    print()

    for i, question in enumerate(remaining, 1):
        entete = (
            f"--- Question {i}/{len(remaining)} — type: {question.type} "
            f"— concept: {question.concept_id} ---"
        )
        print(entete)
        print(f"    source: {question.source_ref or 'non précisée'}")
        print(f"    {MARQUEUR_CONFIANCE.get(question.confiance, question.confiance)}")
        print(_format_question(question))
        try:
            note = _ask_note()
        except _Quit:
            print("\nInterrompu. Progression sauvegardée.")
            break
        commentaire = input("Commentaire (optionnel, Entrée pour passer): ").strip() or None
        review_file.entries.append(
            ReviewEntry(question_id=question.id, note=note, commentaire=commentaire)
        )
        review_path.write_text(review_file.model_dump_json(indent=2) + "\n")
        print()

    return review_path


def _ask_note() -> str:
    mapping = {"b": "bonne", "m": "moyenne", "w": "mauvaise"}
    while True:
        raw = input("Note [b=bonne / m=moyenne / w=mauvaise / q=quitter]: ").strip().lower()
        if raw == "q":
            raise _Quit()
        if raw in mapping:
            return mapping[raw]
        print("Entrée invalide, réessaie.")


def _format_question(q) -> str:
    if q.type == "qcm":
        lines = [f"Énoncé: {q.enonce}"]
        for i, p in enumerate(q.propositions):
            marker = "x" if i in q.bonnes_reponses else " "
            lines.append(f"  [{marker}] {i}. {p}")
        return "\n".join(lines)
    if q.type == "libre_llm":
        return f"Énoncé: {q.enonce}\nRéponse attendue: {q.reponse_attendue}"
    if q.type == "cloze":
        return f"Texte: {q.texte}\nValeurs attendues: {q.valeurs_attendues}"
    if q.type == "probleme":
        lines = [f"Énoncé: {q.enonce}", "Solution:"]
        lines += [f"  {i}. {step}" for i, step in enumerate(q.solution_etapes, 1)]
        lines.append(f"Réponse finale: {q.reponse_finale}")
        if q.necessite_dessin:
            dessin = "avec schema_svg" if q.schema_svg else "sans schema_svg"
            lines.append(f"(nécessite un dessin, {dessin})")
        return "\n".join(lines)
    if q.type == "exact":
        return f"Énoncé: {q.enonce}\nRéponse exacte: {q.reponse_exacte}"
    if q.type == "redaction":
        lines = [f"Consigne: {q.consigne}", f"Contraintes: {q.contraintes}", "Critères:"]
        lines += [f"  - {critere}" for critere in q.criteres]
        lines.append("Exemple de référence:")
        lines += [f"  {ligne}" for ligne in q.exemple_reference.splitlines() or [""]]
        return "\n".join(lines)
    return str(q)
