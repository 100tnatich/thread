"""Correction des réponses et mapping vers les notes FSRS (1..4).

Fonctions pures, sans I/O: le terminal (ou un futur front-end) collecte la
saisie, ce module décide seulement si c'est juste et quelle note FSRS en
découle.
"""
from __future__ import annotations

from fsrs import Rating

# Types corrigeables automatiquement. `probleme` et `redaction` sont
# auto-évalués par l'utilisateur, `libre_llm` passe par un Juge.
AUTO_TYPES = frozenset({"qcm", "exact", "cloze"})

# Types sans correction automatique: l'élève produit sur feuille, puis compare
# à la solution / à l'exemple de référence et se note lui-même.
AUTO_EVALUES = frozenset({"probleme", "redaction"})

MANUAL_RATINGS = {
    "1": Rating.Again,
    "2": Rating.Hard,
    "3": Rating.Good,
    "4": Rating.Easy,
}


def corrige_qcm(reponse: str, bonnes_reponses: list[int]) -> bool:
    """`reponse` est une liste d'indices saisie libre, ex. "0,2" ou "0 2"."""
    try:
        choisis = {int(x) for x in reponse.replace(",", " ").split()}
    except ValueError:
        return False
    return choisis == set(bonnes_reponses)


def corrige_exact(reponse: str, reponse_exacte: str) -> bool:
    """Comparaison stricte, sans tolérance (hors espaces de bord)."""
    return reponse.strip() == reponse_exacte.strip()


def corrige_cloze(reponses: dict[str, str], valeurs_attendues: dict[str, str]) -> bool:
    if set(reponses) != set(valeurs_attendues):
        return False
    return all(reponses[cle].strip() == valeurs_attendues[cle].strip() for cle in valeurs_attendues)


def rating_depuis_criteres(
    coches: int, total: int, facile: bool = False
) -> Rating:
    """Note FSRS d'une `redaction` à partir des critères que l'élève a cochés.

    Tous les critères remplis -> Bien; la plupart -> Difficile; peu ou aucun
    -> Raté. Facile reste un choix explicite de l'élève: le barème ne peut pas
    deviner qu'une rédaction était confortable, seulement qu'elle était juste.
    """
    if total <= 0:
        raise RuntimeError("Une rédaction doit avoir au moins un critère.")
    if coches < 0 or coches > total:
        raise RuntimeError(f"Critères cochés hors bornes: {coches}/{total}.")

    if coches == total:
        return Rating.Easy if facile else Rating.Good
    if coches * 2 >= total:
        return Rating.Hard
    return Rating.Again


def rating_depuis_correction(correct: bool, nuance: str | None = None) -> Rating:
    """Juste -> Good, faux -> Again.

    `nuance` permet de préciser à la main "c'était dur" ("hard") ou "c'était
    facile" ("easy") sur une réponse juste; sans nuance on reste sur Good.
    Une réponse fausse est toujours Again: FSRS attend un échec franc.
    """
    if not correct:
        return Rating.Again
    if nuance == "hard":
        return Rating.Hard
    if nuance == "easy":
        return Rating.Easy
    return Rating.Good
