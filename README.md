# flashcards — prototype 1 (génération de questions)

Prototype en ligne de commande pour valider la **qualité de génération des
questions** à partir d'un PDF de cours. Pas d'interface, pas de planification
FSRS — voir les hors-scope dans la consigne d'origine.

## Installation

```bash
uv sync
cp .env.example .env && chmod 600 .env
```

Le modèle est dans `config.toml`; les tarifs qui font foi sont dans
`src/flashcards/budget.py`. **Par défaut tout tourne en mode simulé, sans
aucune dépense** — voir [Protection du crédit API](#protection-du-crédit-api).

## Protection du crédit API

Rapport complet: [`AUDIT.md`](AUDIT.md). L'essentiel:

**La vraie API n'est appelée que sur activation explicite.** Il faut
`FLASHCARDS_LLM_REEL=1` **et** `ANTHROPIC_API_KEY`. Une clé seule ne suffit pas;
`MOCK_LLM=1` force le mode simulé; `LLM_DISABLED=1` coupe tout.

**Correction par IA des réponses libres et des rédactions** (types `libre_llm` et
`redaction`): c'est le seul endroit où une *réponse* coûte. Elle passe par
OpenRouter (`anthropic/claude-haiku-5.5` par défaut, environ 0,0002 USD par
correction) et par le même budget que le reste: plafonds jour/mois/opération,
cadence, disjoncteur, interrupteur d'urgence (les corrections comptent dans
Réglages → Crédit API). Elle exige `FLASHCARDS_JUGE_REEL=1` **et**
`OPENROUTER_API_KEY` (écrite `OpenRouter_API_KEY` dans `.env`: la casse n'importe
pas). L'appli ne lit pas `.env` elle-même: `./run.sh` le lit pour le juge et transmet
les variables (Docker les reçoit par `docker-compose.yml`). Elle ne part que sur clic de l'élève (« Corriger avec l'IA »); « Voir la
réponse attendue » reste gratuit. Si elle est inactive, plafonnée ou en panne,
l'interface retombe sur l'auto-évaluation. L'élève voit le verdict et peut changer
la note avant de valider. `./run.sh --simule` rend tout factice, corrections
comprises.

```bash
# Un appel réel, volontaire (le CLI ne lit pas .env: les variables se passent ici)
FLASHCARDS_LLM_REEL=1 ANTHROPIC_API_KEY=sk-ant-... \
  uv run flashcards extract samples/cours.pdf --cours "Loi d'Ohm"

uv run flashcards extract <pdf> --cours X --dry-run   # coût estimé, 0 appel
uv run flashcards budget          # dépensé aujourd'hui / ce mois, plafonds, alertes
uv run flashcards llm off         # ARRÊT D'URGENCE (serveur + CLI, sans redémarrage)
uv run flashcards llm on | status
```

**Plafonds durs** (`[budget]` de `config.toml`, surchargeables par variable
d'environnement). Un plafond atteint = appel refusé avec un message explicite,
inscrit au registre.

| Garde-fou | Défaut | Variable |
|---|---|---|
| Coût par jour (UTC) | 1,00 USD | `FLASHCARDS_COUT_MAX_JOUR` |
| Coût par mois | 5,00 USD | `FLASHCARDS_COUT_MAX_MOIS` |
| Coût par opération (tous les lots d'une génération) | 0,50 USD | `FLASHCARDS_COUT_MAX_OPERATION` |
| Appels par minute | 10 | `FLASHCARDS_APPELS_MAX_MINUTE` |
| Disjoncteur | 3 échecs → 10 min | `FLASHCARDS_ECHECS_DISJONCTEUR`, `FLASHCARDS_DUREE_DISJONCTEUR` |
| Fichier | 20 Mo, 24 Mo par envoi | `FLASHCARDS_MAX_FICHIER_MO`, `FLASHCARDS_MAX_TOTAL_MO` |
| PDF | 60 pages | `FLASHCARDS_MAX_PAGES_PDF` |
| Images par ajout | 10 | `FLASHCARDS_MAX_IMAGES` |
| Texte collé | 50 000 caractères | `FLASHCARDS_MAX_CARACTERES_TEXTE` |
| Concepts par génération | 40 (5 appels) | — |

Au-delà de 5 USD/jour, 20 USD/mois ou 2 USD/opération, il faut en plus
`FLASHCARDS_PLAFONDS_ELEVES=1` (garde-fou contre `100` tapé pour `1.00`).

Ce qui se passe autour d'un appel: le coût est **estimé et réservé avant**
(sortie comptée à `max_tokens`), soldé au coût réel après; un échec qui a pu
être facturé (délai dépassé, réponse inexploitable) reste compté; au plus 2
nouvelles tentatives, uniquement sur 429/5xx/connexion, jamais sur 4xx ni
erreur de crédit; le même contenu n'est jamais payé deux fois (cache par
empreinte, `--forcer` pour passer outre); une seule tâche à la fois, un double
clic n'en lance pas deux; une tâche interrompue par un redémarrage n'est jamais
relancée. Dans l'interface: **Réglages → Crédit API**.

À savoir: le plafond par opération prime sur la limite de pages. Avec les
défauts, un PDF est refusé en extraction au-delà d'environ 55 pages (60 pages
= 0,501 USD estimés), et plus tôt en génération (un appel par lot de 8
concepts, matière rejointe à chaque fois). Découpe le thème, ou relève
`FLASHCARDS_COUT_MAX_OPERATION` en connaissance de cause.

## Workflow en 3 étapes

1. **`extract`** — envoie le PDF à Claude, qui extrait un plan de concepts
   clés. Écrit `<pdf>.concepts.json` avec `"validated": false`.
2. **Tu valides** — relis `<pdf>.concepts.json`, corrige les concepts si
   besoin, puis passe `"validated"` à `true`. Sans ce flag, `generate`
   refuse de continuer.
3. **`generate`** — génère les questions **uniquement à partir des concepts
   que tu as validés**. La matière est rejointe à cette étape pour que le
   modèle reprenne les valeurs et la notation exactes du document, mais
   aucun concept hors de ta liste ne peut produire de question (le
   `concept_id` renvoyé est revérifié, sinon la question est écartée avec un
   avertissement). Écrit `<pdf>.questions.json` et `<pdf>.coverage.json`.
4. **`review`** — parcourt les questions une à une dans le terminal, tu notes
   chacune (bonne/moyenne/mauvaise + commentaire). Écrit `<pdf>.review.json`,
   sauvegarde incrémentale (tu peux interrompre avec `q` et reprendre plus
   tard).

### Exemple

```bash
uv run flashcards extract samples/cours.pdf --cours "Loi d'Ohm"
#  -> édite samples/cours.concepts.json, mets "validated": true

uv run flashcards generate samples/cours.pdf                  # profil du fichier de concepts
uv run flashcards generate samples/cours.pdf --cours physique # ou forcé
#  -> samples/cours.questions.json, samples/cours.coverage.json

uv run flashcards review samples/cours.pdf
```

## Workflow manuel (sans API): Claude Code joue le LLM

Quand tu n'as pas de crédit API, ou pour tester la qualité sans rien dépenser,
c'est **Claude Code, dans une session, via ton abonnement**, qui fait le travail
du LLM. Le code du projet n'appelle jamais l'API Anthropic dans ce workflow
(aucune clé requise).

```
inbox/            tes fichiers source (PDF, images, texte, docx) — ignoré par git
sorties/          les fichiers produits: <nom>_concepts.json, <nom>_questions.json
```

1. Dépose la matière dans `inbox/`.
2. Dans Claude Code: `/generer-questions <fichier> <thème>`
   (commande définie dans `.claude/commands/generer-questions.md`).
3. Claude lit la matière, suit `prompts/extraction.md` à la lettre et écrit
   `sorties/<nom>_concepts.json` (`"validated": false`), qu'il contrôle avec
   `validate`. **Puis il s'arrête**: tu relis la liste (corrige, ou édite le
   fichier) et tu réponds « validé » — ou tu passes `"validated": true` toi-même.
   Rien n'est généré avant.
4. Claude suit `prompts/generation.md` sur les concepts validés, écrit
   `sorties/<nom>_questions.json`, le corrige jusqu'à ce que `validate` passe.
5. Il importe avec `import`, puis te donne le rapport de couverture et les
   questions de confiance basse.

### `validate` — contrôler un fichier, sans rien modifier

```bash
uv run flashcards validate sorties/loi-d-ohm_questions.json
uv run flashcards validate sorties/loi-d-ohm_concepts.json
uv run flashcards validate <questions.json> --concepts <concepts.json>
```

Valide avec les modèles Pydantic stricts (aucun appel LLM, aucune écriture) et
affiche: erreurs **par question** (champs manquants, type inconnu, QCM dont la
bonne réponse sort des propositions, trous de cloze incohérents…), avertissements
(champs inconnus, doublons), confiance basse, et — si la liste de concepts est
là (`<nom>_concepts.json` à côté est trouvé tout seul) — la couverture et les
concepts sans question. Code de sortie 1 si le fichier est invalide.

### Formats acceptés à l'import

- **complet** (produit par `generate`): chaque question porte `id`, `cours`,
  `source`;
- **brut** (produit par Claude Code): `concept_id`, `type`, les champs du type,
  `source_ref`, `confiance`. `cours` et `source` viennent de l'en-tête
  du fichier; l'`id` est dérivé du contenu, donc réimporter ne crée rien de plus.

`flashcards import` accepte les deux. **Anti-doublon**: une question dont le
contenu (énoncé, réponses…) existe déjà dans le même cours est ignorée, même sous
un autre `id`. L'import est strict: une seule question invalide et rien n'est
importé, avec le détail des erreurs.

### Importer depuis l'interface web

Page **Ajouter de la matière → Importer**: choisis le thème, envoie le fichier de questions (et
facultativement la liste de concepts pour la couverture), relis le rapport, puis
confirme. Aucun LLM n'est appelé; la route est protégée par la session et
limitée en taille (2 Mo, 2000 questions).

### `prompt` — voir le prompt exact

```bash
uv run flashcards prompt extraction --cours physique
uv run flashcards prompt generation --cours physique \
    --concepts sorties/loi-d-ohm_concepts.json   # doit être validé
```

Affiche `prompts/*.md` rendu avec le profil du cours, exactement comme il serait
envoyé à l'API (sans l'envoyer). C'est ce que `/generer-questions` suit.

## Profils de cours (`profiles/`)

`extract` et `generate` injectent `profiles/<cours>.md` dans les prompts à la
place de `{profil_cours}`: notation et méthode du prof, types de questions
préférés, choses à éviter, et **tes exemples de bonnes questions** — qui
priment sur les exemples génériques du prompt et fixent le niveau attendu.

```bash
cp profiles/_modele.md profiles/chimie.md   # puis remplis-le
```

Le gabarit couvre: Cours / Niveau / Langue / Notation et méthode du prof /
Types de questions préférés / À éviter / Particularités / Exemples de bonnes
questions.

**Si le profil est absent, rien ne casse**: la génération continue sans profil
et t'avertit explicitement (en clair dans le terminal, et dans les
`avertissements` de la page web). Profils livrés: `physique`, `maths`,
`informatique`.

## Récap théorique d'un thème

Page de rappel (formules, règles, pièges) lue **avant les questions d'un thème**, en
session « par cours » (Aujourd'hui → *Par cours* → *Commencer*). Un récap par thème qui en
a un, au moment d'y entrer; le bouton **Passer** (ou espace) enchaîne sur les questions. Une
question passée qui revient ne le réaffiche pas. En mode *Mélangé*, aucun récap.

```bash
uv run flashcards cours recap --nom english                          # afficher
uv run flashcards cours recap --nom english --fichier sorties/recap_english.md   # enregistrer
uv run flashcards cours recap --nom english --efface
```

Aussi éditable dans l'interface (page Cours, section du thème). Markdown simple: `##`
titres, `-` listes, `1.` listes numérotées, `> piège` (encadré), `**gras**`, `` `code` ``,
`$formule$`. Aucun HTML. Aucun LLM: je le rédige à partir des concepts validés.

## Taxonomie des questions

Six types, schémas Pydantic stricts dans `src/flashcards/models.py`:

| Type | Correction | Pour quoi |
| --- | --- | --- |
| `qcm` | automatique | reconnaissance rapide, confusions classiques |
| `exact` | automatique (stricte) | vocabulaire, formule à restituer |
| `cloze` | automatique | 1 à 2 termes clés dans une phrase du cours |
| `probleme` | auto-évaluation | tout ce qui se calcule (+ dessin via `schema_svg`) |
| `libre_llm` | auto-évaluation (interface `Juge`) | connaissance ouverte |
| `redaction` | auto-évaluation par **critères cochés** | production écrite |

Chaque question porte aussi `source_ref` (où l'information se trouve dans la
matière) et `confiance` (`haute` / `moyenne` / `basse`). `review` met en
évidence les questions de confiance basse — typiquement un calcul dont le LLM
n'est pas sûr, et que tu dois vérifier en priorité.

### Le type `redaction`

Pour les productions écrites (introduction, problématique, plan, résumé,
lettre). La carte porte une `consigne` autonome, des `contraintes` (longueur,
structure, registre), 4 à 6 `criteres` **observables** que tu peux vérifier
toi-même sur ta copie, et un `exemple_reference` complet.

Pas de correction automatique: tu rédiges, tu compares à l'exemple, puis tu
coches les critères remplis. Le barème vers FSRS est déterministe
(`grading.rating_depuis_criteres`):

| Critères cochés | Note FSRS |
| --- | --- |
| tous | Bien (ou Facile si tu le précises) |
| au moins la moitié | Difficile |
| moins de la moitié | Raté |

Cocher les critères rend l'auto-évaluation objective: c'est ce qui évite de
se noter à l'humeur sur une production écrite.

## Fiabilité des sorties du modèle

- **Sortie structurée.** Les deux appels passent par `messages.parse()` avec
  le schéma Pydantic (`output_format`). Le JSON ne peut donc pas être
  malformé ni sortir de la taxonomie — il n'y a plus de parsing manuel ni de
  boucle de retry sur du JSON cassé.
- **Génération par lots.** Les concepts validés partent par paquets de 8
  (`ingest.TAILLE_LOT`): un thème de 30 concepts tiendrait difficilement
  dans une seule réponse sans risquer la troncature à `max_tokens`.
- **`concept_id` revérifié.** Il vient du modèle, donc il est contrôlé contre
  la liste validée; une question rattachée à un concept inconnu est écartée
  avec un avertissement, jamais enregistrée.
- **Troncature et refus détectés.** `stop_reason` à `max_tokens` ou `refusal`
  lève une erreur explicite au lieu de produire une liste silencieusement
  incomplète.

## Décisions prises (non bloquantes, à ajuster si besoin)

- **Modèle par défaut** : `claude-sonnet-5`, configurable dans
  `config.toml`.
- **Tarifs dans `config.toml`** (`[pricing]`) sont des valeurs indicatives
  pour estimer le coût dans `logs/usage.jsonl` — à vérifier/ajuster, pas une
  source de vérité tarifaire.
- **`generate` ne relit pas le PDF** : un appel par concept, texte seul. Pour
  que `probleme` puisse reproduire la méthode/notation du prof, le prompt
  d'`extract` demande explicitement de capturer cette notation dans la
  `description` du concept — c'est donc là qu'il faut vérifier si tu
  corriges la liste.
- **Retry** : 1 retry avec le message d'erreur si la sortie JSON du LLM ne
  valide pas le schéma Pydantic ; au 2e échec, le concept est marqué comme
  échec dans `<pdf>.questions.json` (`echecs[]`) plutôt que de planter tout
  le run.
- **`review` montre la réponse immédiatement** avec la question : l'outil
  sert à juger la qualité de génération, pas à s'entraîner en rappel actif.
- **Nommage des fichiers** : toujours `<nom-du-pdf>.<étape>.json` à côté du
  PDF (`concepts`, `questions`, `coverage`, `review`).
- **Prompts** dans `prompts/*.md`, à itérer librement — ils ne sont pas dans
  le code Python.

## Structure

```
src/flashcards/   cli.py, extract.py, generate.py, review.py, models.py,
                  llm.py, ingest.py, profiles.py, config.py,
                  planning.py (file du jour: points, entretien), poids.py
prompts/          extraction.md, generation.md
profiles/         _modele.md + un fichier par cours
samples/          tes PDF de cours
logs/usage.jsonl  tokens + coût estimé par appel
config.toml       modèle + tarifs
```

---

# Prototype 2 — moteur de planification (FSRS)

Importe les questions du prototype 1 dans SQLite, les planifie avec FSRS en
fonction de tes dates de test, et te donne une file de révision quotidienne.

## Workflow

```bash
uv run flashcards import samples/physique-loi-ohm.questions.json

# Matières (parents optionnels) et thèmes (= les « cours »)
uv run flashcards matiere add --nom Anglais
uv run flashcards cours add --nom "Anglais conditionnel" --matiere Anglais
uv run flashcards cours list                      # mode: test / entretien / archivé

# Un test se lie à plusieurs thèmes, ou à toute une matière
uv run flashcards test add --cours "Anglais conditionnel" --cours "English present perfect" \
    --date 2026-10-20 --intitule "TE anglais"
uv run flashcards test add --matiere Anglais --date 2027-06-20 --intitule "Examen de fin d'année"
uv run flashcards test move 3 --ajouter-matiere Maths --date 2026-10-22

uv run flashcards today      # questions et points du jour, entretien, prochain test
uv run flashcards session    # la session du jour ('q' pour arrêter), progression en points
uv run flashcards stats      # charge 14j en points et cartes, entretien, rétention par test
uv run flashcards simulate   # 120 jours simulés (scénario « année ») + graphique PNG
```

`today`, `session`, `stats` acceptent `--jour YYYY-MM-DD` pour forcer la date.

## Heuristique de pilotage par les dates de test

Trois mécanismes au-dessus de FSRS (détail et justification dans
`src/flashcards/heuristic.py`):

1. **Rampe de rétention.** Hors examen proche: 0.90 (régime FSRS normal). Dès
   qu'un test couvrant la carte entre dans la fenêtre `jours_phase_finale`
   (10j), la cible monte linéairement jusqu'à 0.97 le jour J → intervalles
   plus courts, donc plus de passages. Progressive pour éviter qu'un seul
   jour bascule tout un thème dans la file.
2. **Clamp étalé.** FSRS peut planifier une carte stable 60 jours plus loin,
   par-dessus la date du test. On force donc une échéance limite avant
   l'examen — mais répartie sur la fenêtre finale via un hash stable de l'id
   de la carte, et non à une date unique (voir «ce que le simulateur a
   révélé» ci-dessous).
3. **Priorité à la couverture.** Dans la file, une carte du programme jamais
   vue dans la phase finale passe devant une carte déjà repassée.

Après le test, celui-ci n'est plus «à venir»: les cartes retombent seules sur
0.90, sans traitement spécial.

## Gestion de la dette

File triée par: urgence du test le plus proche, puis couverture, puis retard.
Le plafond `plafond_quotidien` (défaut **40 points**) se compte en **points**,
pas en nombre de questions (voir la section suivante). Le surplus n'est jamais
perdu: il reste dû et remonte le lendemain avec un retard plus grand, donc une
priorité plus forte. Une carte qui ne tient plus dans le budget est sautée au
profit d'une plus légère; une carte plus lourde que le plafond compte pour le
plafond entier (elle passe seule, jamais bloquée). Un saut de 3 jours se
résorbe en 3 à 6 jours dans la simulation.

## Matières, thèmes, poids et mode entretien

### Matières et thèmes

Un « cours » est un **thème** précis (« Anglais conditionnel »). Il peut être
rangé dans une **matière** (« Anglais »), qui est optionnelle. Le profil
(`{profil_cours}` des prompts) se définit sur la matière; le thème en hérite et
peut le surcharger. La fusion se fait **rubrique par rubrique**
(`profiles.fusionner`): une rubrique non vide du thème remplace celle de la
matière, une rubrique vide ou absente reprend celle de la matière, les
rubriques propres à un seul niveau sont gardées, dans l'ordre du gabarit
`_modele.md`. À chaque niveau, le texte saisi dans l'interface prime sur le
fichier `profiles/<nom>.md` (qui continue de fonctionner tel quel).

Un **test** se lie à plusieurs thèmes (`test add --cours A --cours B`) et/ou à
**toute une matière** (`--matiere M`, ou le bouton « toute la matière » de la
page Tests): la liaison à la matière couvre aussi les thèmes qu'on y ajoutera
plus tard. Un test couvre ses thèmes en entier. Un thème **archivé** (réversible) sort du planning et de
l'entretien sans toucher à ses cartes, et ne peut pas être lié à un test.

### Poids des questions (points)

Poids par défaut (`config.toml`, modifiables dans Réglages): vocabulaire 0,1;
`exact` 0,5; `cloze` 0,5; `qcm` 0,5; `libre_llm` 1; `probleme` 2; `redaction` 4.
Résolution, par priorité: **poids explicite** de la carte (nullable, champ
`poids` d'une question importée ou édité dans la bibliothèque) → **catégorie du
concept** (`vocabulaire` => 0,1; la catégorie est enregistrée à
l'import/génération quand on connaît la liste de concepts) → **poids du type**.
Le plafond, la dette, la priorité aux tests proches, `today`, `stats` (charge
14 jours en points ET en cartes), `session` (progression en cartes et en
points) et le simulateur raisonnent tous en points.

### Mode entretien

Un thème est **en entretien** s'il n'est ni archivé ni lié à un test à venir
(tous ses tests sont passés, ou il n'en a aucun). Dès qu'un test futur lui est
lié, il repasse en mode test (pilotage par la date, rampe et clamp inchangés).
Seules les cartes **déjà révisées** sont des cartes d'entretien: une carte neuve
est un apprentissage et suit le circuit normal.

- **Quota hebdomadaire** (`entretien_quota_hebdo`, 3 pts/semaine et par thème,
  lundi à dimanche): c'est un **plancher**. Ce qui manque est étalé sur les
  jours restants de la semaine; la part du jour se sert d'abord avec les
  cartes du thème déjà dues selon FSRS, puis, si besoin, avec les cartes de
  **plus faible rétention estimée** (retrievability FSRS), de préférence de
  poids <= `entretien_poids_max` (2), les plus lourdes seulement s'il n'existe
  rien d'autre. Si FSRS a déjà planifié assez de points du thème dans la
  semaine, **aucun complément** n'est ajouté.
- **Plafond global d'entretien** (`entretien_plafond_jour`, 8 pts/jour), en
  tourniquet: une carte par thème et par tour, le thème dont la dernière
  révision est la plus ancienne en premier. Il limite l'étalement, pas le
  montant hebdomadaire.
- **Priorité**: les cartes d'entretien (hors part du plancher) passent après
  celles du mode test. Le plafond quotidien total n'est jamais dépassé.
- Les réponses en entretien mettent à jour FSRS comme n'importe quelle
  révision; aucune carte n'est supprimée ni perdue.
- `today` et `stats` affichent la part d'entretien; l'interface (Aujourd'hui,
  Tableau de bord) aussi, avec la charge d'entretien des 14 prochains jours.

### Choix mineurs (notés, à ajuster si besoin)

- **Le plancher réserve sa part du jour avant le mode test.** La consigne dit
  « priorité la plus basse » ET « plancher ». Le simulateur a montré que la
  priorité la plus basse seule laisse l'entretien à 0 point pendant des
  semaines dès que les tests saturent le budget (quota tenu 28 %). On réserve
  donc chaque jour la part du plancher (limitée par le plafond d'entretien),
  le reste de l'entretien restant à la priorité la plus basse.
- **Le plancher compte le fait, pas le planifié**: une carte « planifiée par
  FSRS » mais jamais servie ne tient pas le quota. La règle « si FSRS a déjà
  planifié assez, ne rien rajouter » s'applique aux **compléments** (cartes non
  dues), pas aux cartes dues, qui sont servies en premier.
- **Les révisions du jour sont débitées** de la part du jour et du plafond
  d'entretien: rouvrir la file après une session ne fait pas réapparaître de
  compléments.
- **Test lié à une matière = lien dynamique** (thèmes futurs inclus, archivés
  exclus). Un thème détaché de la matière sort du test.
- **Matière > thème**, rien d'autre: un test couvre ses thèmes en entier. Ex.:
  matière Physique › thèmes « Statique des fluides », « MRU / MRUA »…
- **Un cours existant devient un thème sans matière**; ses tests restent liés
  à lui seul (migration `005`).
- **Archivé + test**: un thème archivé ne peut pas être lié à un test; il est
  exclu des matières liées.
- **Dans les tests pytest**, `make_card` pose `poids=1.0` par défaut pour que
  « plafond = N points » se lise « N cartes » dans les tests de mécanique de
  file; les tests de poids passent `poids=None`.
- **Anti-doublon par contenu et poids**: le poids n'est pas du contenu (deux
  questions identiques à des poids différents restent des doublons).

## Ce que le simulateur a révélé

Le simulateur a trouvé un vrai bug de conception, maintenant corrigé. Avec un
clamp à **date unique** (`date_examen - 1`), un gros thème rendait toutes
ses cartes dues le même jour: sur 150 cartes avec un plafond de 40,
**110 n'étaient jamais révisées avant l'examen** — la garantie cassait
précisément sur les gros thèmes. Après étalement des échéances sur la
fenêtre finale + priorité à la couverture: **0 carte oubliée** à 150 et 300
cartes (à 400 cartes il en reste 8, mais c'est la limite physique du plafond).
Régression couverte par `tests/test_queue.py`.

### Simulateur « année » (points et entretien)

`flashcards simulate` (scénario `annee`, 120 jours, graine 42): 4 matières,
**20 thèmes dont 12 en entretien**, un **examen de fin d'année lié à toute la
matière Anglais** (jour 100), **792 cartes dont 300 de vocabulaire**, 72
problèmes, 24 rédactions, des lots de cartes injectés au fil de l'eau, et un test
annoncé tardivement qui sort « Info linux » de l'entretien (jour 40). Chiffres
(graine 42; les graines 1 à 5 donnent les mêmes ordres de grandeur):

| | |
| --- | --- |
| Charge quotidienne (jours actifs) | moyenne **32 pts**, médiane 40, p95 40, **max 40** (jamais dépassé); ~53 cartes/jour, 0,61 pt/carte |
| Entretien | **23 % de la charge**, 7,4 pts/jour (plafond 8, jamais dépassé) |
| Quota hebdomadaire (3 pts/thème) | tenu **96 %** des semaines sans jour sauté (76 % toutes semaines confondues) |
| Rotation | chaque thème révisé entre 31 % et 68 % de ses jours d'entretien |
| Examens | 0 carte jamais vue avant chaque examen, rétention moyenne 97,5 à 99,6 % (examen d'Anglais: 288 cartes, 98,2 %) |
| Saut de 3 jours | résorbé en 3 jours, aucune carte perdue |

Comportements bizarres signalés par le rapport (non corrigés, à décider):

1. **Capacité d'entretien insuffisante**: 20 thèmes × 3 pts = 60 pts/semaine
   pour 8 × 7 = 56 de capacité. Quand les 8 thèmes en test rejoignent
   l'entretien, les quotas ne peuvent plus tous être tenus. Remède: `entretien_plafond_jour`
   à 12, ou `entretien_quota_hebdo` à 2 (les deux donnent 100 % de quotas
   tenus sur le scénario). `today` et `stats` affichent aussi cette alerte.
2. **Dette d'entretien persistante** (~150 pts, ~19 jours de plafond
   d'entretien en fin de simulation): après un gros examen (288 cartes
   d'Anglais), tout le lot retombe en entretien avec des intervalles courts
   (rétention 0,97 de la phase finale) et le plafond de 8 pts/jour met des
   semaines à l'absorber. Rien n'est perdu, mais ça coûte 8 pts/jour pendant
   ce temps.
3. **Le plafond en points ne borne pas le nombre de cartes**: à 0,1 pt, une
   carte de vocabulaire permet 400 cartes dans les 40 pts. 15 jours dépassent
   100 cartes (max 157), surtout dans la phase finale avant l'examen
   d'Anglais (200 cartes de vocabulaire). Un plafond de cartes par jour serait
   le garde-fou si c'est gênant.
4. **Plafond atteint 75 jours sur 108**: le scénario injecte beaucoup (792
   cartes en 30 jours) et 8 thèmes en phase de test; la charge est soutenue au
   maximum, pas explosive.

## Décisions prises

- **`due` n'est jamais stocké en base.** L'échéance est toujours recalculée à
  partir de `(stability, last_review)` + des examens courants. Conséquence:
  ajouter, déplacer ou supprimer un test replanifie tout instantanément, sans
  migration de données ni job de recalcul.
- **`learning_steps` et `relearning_steps` vides**: pas de re-présentation à
  1 min / 10 min le jour même. Une carte passe directement en `Review` avec
  des intervalles en jours entiers — adapté à une seule session par jour.
- **Fuzzing FSRS désactivé**: charge prévisible et clamp exact (le fuzz
  pourrait repousser une carte après la date du test).
- **L'algorithme FSRS n'est jamais réimplémenté**: toutes les transitions
  passent par `Scheduler.review_card` de la bibliothèque `fsrs` (v6). Seule
  la formule d'intervalle publique est reprise dans
  `fsrs_engine.interval_days_for_retention`, pour projeter une échéance sans
  déclencher de révision.
- **Logique et affichage séparés**: `api.py` ne fait aucun I/O terminal et
  prend toujours la date en paramètre (jamais `datetime.now()` caché), pour
  que le futur front-end réutilise le moteur tel quel.
- **Réimporter un fichier n'écrase pas la progression** (dédoublonnage par id).
- **`libre_llm`**: interface `Juge` + `JugeManuel` (auto-évaluation).
  `JugeLLM` existe comme emplacement explicite, non implémenté.

## Tests

```bash
uv run pytest        # mapping des notes, heuristique, dette, import, profils,
                     # confiance, rédaction, poids, entretien, matières,
                     # migrations, E2E simulateur (scénarios simple et année)
```

---

# Prototype 3 — interface web (PWA)

Application web complète : ajouter de la matière (PDF / photo / texte),
réviser chaque jour, suivre ses tests et sa charge, gérer ses cours.
Installable sur l'écran d'accueil du téléphone.

**Déploiement sur ton domaine : voir [`DEPLOY.md`](DEPLOY.md).**

## Démarrage local

```bash
# Le plus simple: ./run.sh (extraction/génération simulées; correction par IA réelle
# si .env l'active; `--simule` pour tout simuler) — ou à la main:
# Terminal 1 — API (mode simulé, aucune clé API requise)
export MOCK_LLM=1 SESSION_SECRET=dev   # `serve` en écoute locale = mode développement
uv run flashcards web create-user --identifiant nate
uv run flashcards web seed-demo        # 5 cours, 17 questions, 4 tests fictifs
uv run flashcards web serve --reload

# Terminal 2 — frontend
cd frontend && npm install && npm run dev
```

Puis `http://localhost:5173`.

### Comptes et rôles

Le compte créé par `create-user` est **admin** (tous les droits). L'admin crée les autres
depuis la page **Comptes**:

| Rôle | Accès |
| --- | --- |
| admin | tout: comptes, modèle LLM, arrêt d'urgence du crédit, dépôt |
| standard | l'appli de révision telle quelle |
| étendu | standard + page **Dépôt** (créer des cours, déposer des fichiers) |

Les données (cours, questions, historique) sont **partagées** entre les comptes: le rôle
limite ce qu'on peut faire, pas ce qu'on voit.

**Dépôt (à l'école → à la maison).** Un compte étendu dépose ses PDF / photos / textes / .docx
dans un cours depuis la page Dépôt (téléphone compris). Rien n'est envoyé à une IA: les fichiers
attendent dans `FLASHCARDS_DEPOT` (`inbox/depot` en local, `/data/depot` en Docker). À la
maison, dans Claude Code: `/generer-questions depot` liste les fichiers en attente, en copie un
dans `inbox/`, puis le flux habituel (concepts → ta validation → questions → import) et le
fichier est marqué « traité ». À la main: `flashcards depot list`, `depot recuperer N`,
`depot traite N`. Si le serveur est distant, télécharge le fichier depuis la page Dépôt: la
commande ne lit que la base locale.

## Architecture

```
src/flashcards/
  api.py heuristic.py fsrs_engine.py    moteur (prototypes 1 et 2, inchangé)
  llm.py ingest.py                      3 voies d'entrée + flux en 2 temps
  web/app.py                            API REST (couche mince)
  web/auth.py                           argon2 + cookie signé + anti-force brute
  web/settings_store.py                 réglages en base, surchargent config.toml
frontend/src/
  api.ts                                client typé
  composants/Gabarits.tsx               un gabarit par type de question (6)
  pages/                                7 pages
```

**L'interface ne contient aucune logique de planification.** Quelles cartes
sont dues, dans quel ordre, avec quelle échéance : tout vient de
`flashcards.api` et `flashcards.heuristic`, réutilisés tels quels.

## Ce qui a changé dans le moteur

- **`branche` → `cours`** partout (code, base, JSON). Les fichiers
  `questions.json` du prototype 1 restent lisibles : un alias Pydantic
  accepte encore `branche` à la lecture.
- **Cours** : entité à part entière (nom + couleur), avec renommage qui
  propage aux cartes et aux tests, et suppression avec réaffectation.
- **Skip** : `api.skip_card()`. Un skip n'est **pas** une réponse — aucune
  mise à jour FSRS, aucune ligne dans `reviews`, aucune pénalité. La question
  repart en fin de file du jour, avec une limite de 2 repasses par session
  pour qu'elle ne puisse pas boucler indéfiniment. Non traitée, elle reste
  simplement due.
- **Ordre de session** (`melange` / `par_cours`) : appliqué **après** la
  priorisation et le plafond, pour ne pas altérer la sélection des cartes.
- **Question mauvaise** : `cards.desactivee` exclut la carte des révisions
  jusqu'à correction.

## Mode simulé

C'est le mode par défaut: sans `FLASHCARDS_LLM_REEL=1` **et** une clé (ou avec
`MOCK_LLM=1`), l'extraction et la génération renvoient des données
déterministes et plausibles, sans aucun appel réseau. L'étiquette « Mode
simulé » apparaît dans Réglages. Toute la suite de tests tourne dans ce mode,
et `tests/conftest.py` remplace en plus le client du SDK par un piège: un test
ne peut pas atteindre l'API, même avec une clé exportée dans le shell.

## Sécurité

Détail et checklist avant publication dans [`AUDIT.md`](AUDIT.md). En bref:
toute route `/api/*` exige une session sauf `/login`, `/logout`, `/health`
(refus par défaut, vérifié avant la lecture du corps de la requête); cookie
`HttpOnly` + `SameSite=Lax` (+ `Secure` hors développement) lié au mot de passe
— le changer déconnecte les autres appareils; `SESSION_SECRET` obligatoire (32
caractères minimum) dès que `FLASHCARDS_ENV` n'est pas `development`; mot de
passe de 12 caractères minimum; contrôle d'origine sur les écritures; CSP
stricte; le SVG généré est affiché comme image, jamais injecté dans la page.

## Décisions prises

- **Frontend : React + Vite + TypeScript + Tailwind 4.** C'est l'écosystème
  avec le meilleur rapport outillage/documentation pour une SPA installable,
  et Vite produit un build statique trivial à servir derrière Caddy.
- **Polices auto-hébergées** (`@fontsource-variable`) : aucune requête vers
  Google Fonts, la PWA fonctionne hors ligne et rien ne fuit vers un tiers.
- **Service worker en `NetworkOnly` sur `/api`** : les questions du jour et
  l'état FSRS ne doivent jamais venir du cache.
- **Tâches longues** : `BackgroundTasks` + table `jobs` interrogée par le
  front. Pas de Celery ni de Redis — surdimensionné pour un seul utilisateur.
- **SQLite en WAL**, une connexion par requête : un lecteur ne bloque plus un
  écrivain pendant qu'une génération écrit des cartes.
- **Un seul worker uvicorn** : SQLite et l'état des tâches vivent dans le
  process.
- **Réglages en base** (table `settings`), qui surchargent `config.toml` :
  tu peux changer le plafond ou la rétention depuis le téléphone.
- **Sauvegarde quotidienne par thread interne** plutôt qu'un cron ou un
  service séparé : pour une instance mono-utilisateur, un thread qui dort
  24 h suffit et reste dans le conteneur de l'app.

## Tests

```bash
uv run pytest                        # 452 tests: moteur, API, budget, sécurité
cd frontend && npx playwright test   # 6 parcours de bout en bout
```

Côté API : accès refusé sans session, anti-force brute, CRUD cours et
migration, skip (sans effet FSRS, limite de repasses), les trois voies
d'ajout en mode simulé, les deux ordres de session, réglages, export.

Côté navigateur : login exigé, ajout de matière par texte, validation des
concepts (avec un concept décoché qui ne doit produire aucune question),
session avec un skip et une notation.
