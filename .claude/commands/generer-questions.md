---
description: Génère les questions d'un thème SANS API (Claude Code joue le LLM): extraction → validation par Nate → génération → import
argument-hint: <fichier dans inbox/> <thème>  |  depot [numéro]
---

Tu vas faire, dans cette session, le travail que le code confie d'habitude à l'API
Anthropic: lire la matière, extraire les concepts, puis générer les questions. Tout
passe par l'abonnement: **aucun appel à l'API Anthropic, aucune clé.**

Arguments reçus: `$ARGUMENTS`
- 1er mot = le fichier source (dans `inbox/`, ou un chemin explicite);
- tout le reste = le thème (le « cours » dans les commandes; peut contenir des espaces;
  choisit `profiles/<cours>.md`).
S'il en manque un, demande-le et rien d'autre. `<nom>` ci-dessous = le nom du fichier
sans extension, passé par `flashcards.profiles.slug` (ex. `Loi d'Ohm.pdf` → `loi-d-ohm`).

## Source = dépôt (`/generer-questions depot [numéro]`)

Si le 1er mot est `depot`, la matière a été déposée depuis l'interface web (compte étendu,
page « Dépôt ») et attend sur le serveur. Aucune API: `depot` ne fait que lire la base et
copier un fichier.

1. `uv run flashcards depot list` (ou `--cours "<thème>"`). Montre la liste à Nate; si un
   numéro est donné dans les arguments, prends celui-là; sinon demande lequel traiter.
2. `uv run flashcards depot recuperer <numéro>` copie le fichier dans `inbox/` et affiche
   `fichier:` et `cours:`. Ce fichier et ce cours remplacent les deux arguments habituels,
   et tout le reste du flux (étapes 1 à 5) est inchangé, y compris l'arrêt de l'étape 3.
3. Après l'import réussi (étape 5), `uv run flashcards depot traite <numéro>` pour que le
   fichier ne soit plus proposé. Ne le fais jamais avant l'import.
4. Si la base est sur un serveur distant (Docker), `depot list` ne verra rien en local: dis-le
   à Nate, qui peut télécharger le fichier depuis la page « Dépôt » dans `inbox/`.

## Règles absolues

- N'appelle JAMAIS l'API Anthropic. N'utilise PAS `flashcards extract`, `generate`
  ni `web serve` avec un vrai LLM: ils appellent l'API. Seuls `prompt`, `validate` et
  `import` sont utilisés ici — aucun n'appelle de LLM.
- Suis À LA LETTRE `prompts/extraction.md` et `prompts/generation.md`. Ne les réécris
  pas, ne les résume pas, ne les « améliore » pas, et ne touche ni à `prompts/`, ni à
  `profiles/`, ni à la taxonomie (`src/flashcards/models.py`).
- N'invente RIEN hors de la matière: aucune connaissance extérieure, aucune valeur, formule
  ou méthode que le document ne contient pas. Un doute = pas de question + un
  `avertissements`.
- Tout passage illisible, coupé, ambigu ou incompris (schéma, écriture manuscrite…) est signalé
  dans `avertissements`, avec précisément quoi et où. Tu ne devines pas.
- Si `uv` est introuvable: `export PATH="$HOME/.local/bin:$PATH"` d'abord.
- Si `sorties/<nom>_concepts.json` ou `_questions.json` existe déjà, ne l'écrase pas en
  silence: demande si on reprend (concepts → étape 3) ou si on repart de zéro.

## Étape 1 — Préparer

1. Vérifie que le fichier existe. Types acceptés: `.pdf`, images (`.png`, `.jpg`, `.jpeg`,
   `.webp`, `.gif`), texte (`.txt`, `.md`), `.docx`. Autre chose: dis-le et arrête-toi.
2. Charge le prompt exact, déjà rendu avec le profil du cours (même rendu que celui
   envoyé à l'API):
   `uv run flashcards prompt extraction --cours "<cours>"`
   Si la commande écrit **« Aucun profil pour le cours… »** (stderr): **dis-le à Nate
   tout de suite**, en une ligne, et continue sans profil. Ajoute alors cet avertissement en
   premier dans le champ `avertissements` des deux fichiers de sortie.
3. Lis la matière **en entier**:
   - **PDF**: outil Read avec `pages` (20 pages max par appel; obligatoire au-delà de 10
     pages). Enchaîne les plages jusqu'à la fin: ne lis pas « un échantillon ».
   - **Image / texte**: outil Read directement.
   - **.docx**: le projet n'a pas de lecteur docx. Extrais le texte brut avec la bibliothèque
     standard, puis lis le résultat:
     ```bash
     uv run python - <<'PY'
     import html, re, zipfile
     from pathlib import Path
     src = Path("inbox/<fichier>.docx")
     xml = zipfile.ZipFile(src).read("word/document.xml").decode("utf-8")
     xml = re.sub(r"</w:p>|<w:br[^>]*/>", "\n", xml).replace("<w:tab/>", "\t")
     Path("sorties/<nom>_source.txt").write_text(html.unescape(re.sub(r"<[^>]+>", "", xml)))
     PY
     ```
     Ce texte perd les images, les tableaux (mis à plat) et les formules: ajoute un
     avertissement qui le dit, et propose un export PDF si la matière en dépend.

## Étape 2 — Extraire les concepts (prompts/extraction.md)

Applique le prompt chargé à l'étape 1: ENTRÉES, TÂCHE, GARDE-FOUS, tout. Puis écris
`sorties/<nom>_concepts.json`, au format exact du projet:

```json
{
  "cours": "<cours>",
  "source": "<nom du fichier source, avec extension>",
  "concepts": [
    {"id": "c1", "titre": "…", "categorie": "definition", "resume": "…",
     "source": "page 3", "importance": "essentiel", "adapte_calcul": false}
  ],
  "avertissements": [],
  "validated": false
}
```

`categorie`: definition | propriete | formule | methode | fait | vocabulaire | exemple_type.
`importance`: essentiel | important | secondaire. `validated` reste à **false**.

Lance `uv run flashcards validate sorties/<nom>_concepts.json` et corrige jusqu'à ce que
la sortie soit **VALIDE** (code 0). Lis aussi les avertissements du rapport.

## Étape 3 — STOP: Nate relit et valide les concepts

Affiche un tableau compact (id, titre, catégorie, importance, source) + les avertissements,
puis demande: *« Relis la liste (tu peux aussi éditer `sorties/<nom>_concepts.json`). Dis-moi
ce que je corrige, ou « validé » pour que je génère les questions. »*

**Ne génère AUCUNE question avant une validation explicite.** Elle est explicite si:
- Nate répond clairement dans le chat (« validé », « ok, génère », « go »…), ou
- `"validated": true` est dans le fichier (relis-le: Nate a pu l'éditer à la main).

Une demande de correction n'est PAS une validation: applique-la, relance `validate`,
montre ce qui a changé, et redemande. Quand Nate valide dans le chat, passe toi-même
`"validated"` à `true` dans le fichier (c'est le seul champ que tu modifies alors).
Tu ne passes jamais `validated` à `true` de ta propre initiative.

## Étape 4 — Générer les questions (prompts/generation.md)

1. Relis `sorties/<nom>_concepts.json` depuis le disque (Nate a pu le corriger): ce sont CES
   concepts, et eux seuls, qui comptent. Puis charge le prompt exact:
   `uv run flashcards prompt generation --cours "<cours>" --concepts sorties/<nom>_concepts.json`
   (la commande refuse un fichier non validé).
2. **Réflexion sur les types, avant la moindre question** (section « RÉFLEXION SUR LES
   TYPES » du prompt). Écris dans ta réponse, en quelques lignes, le plan: pour chaque groupe
   de concepts, le ou les types retenus et pourquoi, puis les types écartés. Un type absent est
   normal. `libre_llm` et `redaction` sont les seuls types à correction subjective (un juge LLM
   coûterait à chaque réponse): ne les retiens que si la leçon l'exige (production écrite
   enseignée: essay, introduction de dissertation, lettre…; explication qu'aucun `cloze`,
   `exact` ni `qcm` ne teste). Une règle de forme ou une définition se teste en `cloze` / `exact`.
   Ne demande pas de validation: affiche le plan et enchaîne.
3. Applique ce prompt À LA LETTRE: PRINCIPES, RÉFLEXION SUR LES TYPES, CHOIX DU TYPE, RÈGLES
   PAR TYPE, EXERCICES D'APPLICATION, VÉRIFICATION. Reviens à la matière source pour les valeurs et la notation
   exactes (relis les pages utiles). Travaille par lots de 8 concepts maximum, comme le
   code. **Chaque résultat numérique d'un `probleme` est recalculé deux fois, de façon
   indépendante**; un doute = `confiance` « basse » et une explication.
4. Écris `sorties/<nom>_questions.json`. N'écris PAS `id`, `cours` ni `source`
   par question: ils sont ajoutés à l'import (l'`id` est dérivé du contenu).

```json
{
  "source": "<nom du fichier source>",
  "cours": "<cours>",
  "questions": [
    {"concept_id": "c1", "type": "exact", "enonce": "…", "reponse_exacte": "…",
     "source_ref": "page 3", "confiance": "haute"}
  ],
  "avertissements": []
}
```

Champs par type (source de vérité: les classes `*Content` de `src/flashcards/models.py`),
tous accompagnés de `concept_id`, `source_ref` et `confiance` (haute | moyenne | basse):

| type | champs |
| --- | --- |
| `qcm` | `enonce`, `propositions` (liste), `bonnes_reponses` (indices à partir de 0) |
| `libre_llm` | `enonce`, `reponse_attendue` |
| `cloze` | `texte` avec `{{c1::valeur}}`, `valeurs_attendues` (`{"c1": "valeur"}`) |
| `probleme` | `enonce`, `solution_etapes` (liste), `reponse_finale`, `necessite_dessin`, `schema_svg` (ou `null`) |
| `exact` | `enonce`, `reponse_exacte` |
| `redaction` | `consigne`, `contraintes`, `criteres` (4 à 6), `exemple_reference` |

5. Lance `uv run flashcards validate sorties/<nom>_questions.json` (il trouve tout seul
   `<nom>_concepts.json` à côté) et **corrige jusqu'à ce que tout passe** (code 0). Les erreurs
   sont données question par question. Vérifie dans le rapport:
   - **Concepts sans question**: chacun est traité (question ajoutée) ou expliqué dans
     `avertissements`;
   - **Confiance basse**: relis chaque cas, et corrige ou garde-le signalé.

## Étape 5 — Importer et rendre compte

1. `uv run flashcards import sorties/<nom>_questions.json`
2. `uv run flashcards validate sorties/<nom>_questions.json` pour réafficher couverture et
   confiances basses.
3. Réponds à Nate en quelques lignes: cartes importées / déjà présentes / doublons ignorés, le
   **rapport de couverture** (concepts sans question, s'il y en a), la liste des **questions de
   confiance basse** (à vérifier en priorité, surtout les calculs) et les `avertissements`.
   Rappelle que les cartes sont dans la base et qu'il peut les voir avec `./run.sh`.
4. Si la matière venait du dépôt: `uv run flashcards depot traite <numéro>`.
5. **Récap théorique du thème** (lu avant les questions en session « par cours »). Propose à Nate
   d'écrire ou de compléter le récap du cours, **uniquement à partir des concepts validés** (rien
   d'extérieur): une page courte, Markdown simple (`##` titres, `-` listes, `> piège`, `**gras**`,
   `$formule$`), avec formules, règles, conventions du prof et pièges. Écris-le dans
   `sorties/recap_<cours>.md` (s'il en existe déjà un: `uv run flashcards cours recap --nom "<cours>"`,
   puis complète-le au lieu de l'écraser), montre-le à Nate, puis, **seulement s'il valide**:
   `uv run flashcards cours recap --nom "<cours>" --fichier sorties/recap_<cours>.md`.
