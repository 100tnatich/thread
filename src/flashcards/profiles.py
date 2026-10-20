"""Profils de cours et rendu des gabarits de prompt.

Un profil est un fichier `profiles/<cours>.md` que tu écris à la main: notation
du prof, types de questions préférés, exemples. Il est injecté tel quel dans
les prompts à la place de `{profil_cours}`.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .config import PROJECT_ROOT

PROFILES_DIR = PROJECT_ROOT / "profiles"
MODELE = "_modele.md"

PROFIL_VIDE = "(aucun profil fourni pour ce cours)"


@dataclass(frozen=True)
class Profil:
    cours: str
    texte: str
    chemin: Path | None
    avertissement: str | None = None
    # D'où vient le texte: « thème », « matière » ou « matière + thème ».
    origine: str = ""
    # Texte saisi dans l'interface (colonne `profil`), sans fichier derrière.
    depuis_base: bool = False

    @property
    def trouve(self) -> bool:
        return self.chemin is not None or self.depuis_base


def slug(valeur: str) -> str:
    """`Loi d'Ohm` -> `loi-d-ohm`. Sert à retrouver un fichier de profil."""
    sans_accents = "".join(
        c for c in unicodedata.normalize("NFD", valeur) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", "-", sans_accents.lower()).strip("-")


def chemins_candidats(cours: str) -> list[Path]:
    """Nom exact d'abord, puis version sans accents ni espaces."""
    noms = [f"{cours}.md"]
    slugifie = f"{slug(cours)}.md"
    if slugifie not in noms:
        noms.append(slugifie)
    # Le nom du cours vient de l'utilisateur (API, CLI): « ../../x » ou un
    # chemin absolu ferait lire un fichier hors du dossier des profils, puis
    # l'enverrait au modèle. Seul un nom de fichier nu est accepté.
    return [PROFILES_DIR / nom for nom in noms if Path(nom).name == nom and nom != ".md"]


def charger(cours: str, conn: sqlite3.Connection | None = None) -> Profil:
    """Charge le profil d'un cours (thème).

    Sans `conn`: fichier `profiles/<cours>.md` seul, comme avant.

    Avec `conn`: le thème hérite du profil de sa matière et peut le surcharger.
    À chaque niveau, le texte saisi dans l'interface prime sur le fichier
    `profiles/<nom>.md`. Les deux niveaux sont fusionnés section par section
    (voir `fusionner`): le thème prime.

    Profil absent: ce n'est pas une erreur — on continue avec un profil vide,
    mais on remonte un avertissement explicite jusqu'à l'utilisateur plutôt
    que de le laisser croire que son profil a été pris en compte.
    """
    if conn is not None:
        hierarchique = _charger_hierarchique(cours, conn)
        if hierarchique is not None:
            return hierarchique
    return _charger_fichier(cours)


def _charger_fichier(cours: str) -> Profil:
    for chemin in chemins_candidats(cours):
        if chemin.exists():
            texte = chemin.read_text().strip()
            if not texte:
                return Profil(
                    cours=cours,
                    texte=PROFIL_VIDE,
                    chemin=chemin,
                    avertissement=f"Le profil {chemin.name} est vide: aucun profil appliqué.",
                )
            return Profil(cours=cours, texte=texte, chemin=chemin, origine="thème")

    attendu = PROFILES_DIR / f"{slug(cours) or 'cours'}.md"
    return Profil(
        cours=cours,
        texte=PROFIL_VIDE,
        chemin=None,
        avertissement=(
            f"Aucun profil pour le cours « {cours} » (attendu: {attendu}). "
            f"Génération sans profil: la notation et la méthode du prof ne seront "
            f"pas prises en compte. Copie {PROFILES_DIR / MODELE} pour en créer un."
        ),
    )


def _texte_du_niveau(nom: str, saisi: str) -> tuple[str, Path | None]:
    """Texte d'un niveau: celui de l'interface s'il existe, sinon le fichier."""
    if saisi.strip():
        return saisi.strip(), None
    for chemin in chemins_candidats(nom):
        if chemin.exists() and chemin.read_text().strip():
            return chemin.read_text().strip(), chemin
    return "", None


def _charger_hierarchique(cours: str, conn: sqlite3.Connection) -> Profil | None:
    ligne = conn.execute(
        "SELECT c.profil AS profil_theme, m.nom AS matiere, m.profil AS profil_matiere"
        " FROM cours c LEFT JOIN matieres m ON m.id = c.matiere_id WHERE c.nom = ?",
        (cours,),
    ).fetchone()
    if ligne is None:
        return None  # thème inconnu de la base: comportement fichier seul

    theme, chemin_theme = _texte_du_niveau(cours, ligne["profil_theme"])
    matiere, chemin_matiere = ("", None)
    if ligne["matiere"]:
        matiere, chemin_matiere = _texte_du_niveau(ligne["matiere"], ligne["profil_matiere"])

    if not theme and not matiere:
        return _charger_fichier(cours)  # absent (ou vide): même avertissement qu'avant

    if theme and matiere:
        texte, origine = fusionner(matiere, theme), "matière + thème"
        chemin = chemin_theme or chemin_matiere
    elif theme:
        texte, origine, chemin = theme, "thème", chemin_theme
    else:
        texte, origine, chemin = matiere, "matière", chemin_matiere
    depuis_base = chemin is None
    return Profil(
        cours=cours, texte=texte, chemin=chemin, origine=origine, depuis_base=depuis_base,
    )


# --- Fusion matière + thème --------------------------------------------------

# Les rubriques du gabarit (`_modele.md`), dans l'ordre. Un profil est une suite
# de rubriques « Titre: contenu ».
ENTETES = (
    "Cours",
    "Niveau",
    "Langue",
    "Notation et méthode du prof",
    "Types de questions préférés",
    "À éviter",
    "Particularités",
    "Exemples de bonnes questions",
)
_ENTETE = re.compile(
    r"^(" + "|".join(re.escape(e) for e in ENTETES) + r")(\s*\([^)]*\))?\s*:", re.IGNORECASE
)


def decouper(texte: str) -> dict[str, str]:
    """Découpe un profil en rubriques: clé = titre canonique (« » = texte libre
    avant la première rubrique), valeur = bloc complet, titre compris."""
    blocs: dict[str, list[str]] = {}
    courant = ""
    for ligne in texte.splitlines():
        m = _ENTETE.match(ligne)
        if m:
            courant = next(e for e in ENTETES if e.lower() == m.group(1).lower())
        blocs.setdefault(courant, []).append(ligne)
    return {cle: "\n".join(lignes).strip() for cle, lignes in blocs.items()}


def _contenu_vide(bloc: str) -> bool:
    """Une rubrique « Niveau: » sans rien après le titre est vide."""
    m = _ENTETE.match(bloc.splitlines()[0]) if bloc else None
    reste = bloc[m.end():] if m else bloc
    return not reste.strip()


def fusionner(parent: str, enfant: str) -> str:
    """Fusionne le profil de la matière (`parent`) et celui du thème (`enfant`).

    Rubrique par rubrique: le thème prime quand sa rubrique n'est pas vide,
    sinon on garde celle de la matière. Les rubriques propres à un seul niveau
    sont conservées. Le texte libre (avant la première rubrique) des deux
    niveaux est mis bout à bout, matière d'abord.
    """
    p, e = decouper(parent), decouper(enfant)
    sections = []
    libres = [t for t in (p.get("", ""), e.get("", "")) if t]
    if libres:
        sections.append("\n\n".join(libres))
    for entete in ENTETES:
        bloc_enfant = e.get(entete)
        if bloc_enfant is not None and not _contenu_vide(bloc_enfant):
            sections.append(bloc_enfant)
        elif entete in p and not _contenu_vide(p[entete]):
            sections.append(p[entete])
    return "\n".join(sections)


def lister() -> list[str]:
    if not PROFILES_DIR.exists():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.md") if p.name != MODELE)


# --- Rendu des gabarits ----------------------------------------------------

# Les prompts contiennent du JSON (`{"concepts": [...]}`) et la syntaxe cloze
# (`{{c1::valeur}}`). `str.format` casserait dessus, donc on remplace
# uniquement les variables connues, littéralement.
VARIABLES_CONNUES = frozenset({"cours", "profil_cours", "concepts_valides"})


def rendre(gabarit: str, **variables: str) -> str:
    inconnues = set(variables) - VARIABLES_CONNUES
    if inconnues:
        raise RuntimeError(f"Variables de prompt inconnues: {sorted(inconnues)}")

    rendu = gabarit
    for nom, valeur in variables.items():
        rendu = rendu.replace("{" + nom + "}", valeur)

    restantes = [
        v for v in VARIABLES_CONNUES if "{" + v + "}" in rendu and v not in variables
    ]
    if restantes:
        raise RuntimeError(f"Variables de prompt non fournies: {sorted(restantes)}")
    return rendu
