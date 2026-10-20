# Projet: appli de flashcards à répétition espacée

## Qui et pourquoi
Nate est apprenti informaticien CFC (exploitation et infrastructure) et suit la maturité professionnelle technique en parallèle, avec un travail à temps plein: 15 leçons de 45 min par semaine, 8 branches. Il veut une appli qui absorbe toute la planification de ses révisions: il injecte la matière au fil des cours (jamais en bloc avant un test), l'appli génère des flashcards et les planifie avec FSRS, piloté par ses dates de test. Objectif final: il s'assoit, répond aux questions du jour, c'est tout.

## Contrainte d'usage critique
La matière doit être injectée thème par thème dès le début de chaque sujet. En bloc juste avant un test, l'espacement perd son intérêt.

## Découpage en 3 prototypes
1. Qualité de génération (CLI): PDF → liste de concepts → validation par Nate → questions typées → JSON + rapport de couverture + commande `review` pour noter les questions.
2. Moteur de planification (CLI, sans clé API): SQLite, FSRS, tests d'examen, dette de révision, simulateur sur 90 jours, tests pytest.
3. Interface web publiée sur le domaine de Nate (FastAPI + SPA/PWA, Docker + Caddy, login mono-utilisateur).
Principe: ne pas sur-ingénierier avant d'avoir validé la qualité de génération sur 2-3 vrais cours.

## Stack
Python 3.12+, `uv`, WSL2 Ubuntu. SDK `anthropic`. Modèles Pydantic stricts (union discriminée sur `type`). SQLite via `sqlite3` (pas d'ORM). Bibliothèque `fsrs` (ne pas réinventer l'algorithme). PDF envoyés directement à l'API. Mode `MOCK_LLM=1` pour tout tester sans API.

## Les 6 types de questions (taxonomie fermée)
Le LLM choisit un type existant et remplit ses champs, il n'improvise jamais de structure.
- `qcm`: énoncé, propositions, bonnes réponses. Correction automatique.
- `libre_llm`: énoncé, réponse attendue. Juge LLM non implémenté (interface `Juge`, auto-évaluation en attendant).
- `cloze`: texte à trous `{{c1::valeur}}`.
- `probleme`: énoncé, solution détaillée étape par étape, réponse finale, `schema_svg` optionnel. Auto-évaluation, pas de correction automatique. Type par défaut pour les matières à calcul.
- `exact`: réponse fixe, comparaison stricte (vocabulaire).
- `redaction`: consigne, contraintes, grille de 4 à 6 critères vérifiables, exemple de référence. Auto-évaluation par cases à cocher; note FSRS déduite (tous cochés = Bien, la plupart = Difficile, peu = Raté, Facile à la main).
Chaque question: `id`, `concept_id`, cours, `source_ref`, `confiance` (haute/moyenne/basse), et `avertissements` au niveau de la sortie.

## Prompts de génération
`prompts/extraction.md` et `prompts/generation.md` sont des spécifications précises, à ne pas réécrire ni résumer sans demande de Nate. Chaque appel combine: prompt fixe + profil du cours (`profiles/<cours>.md`, notation et méthode du prof, exemples de bonnes questions écrits par Nate) + matière et concepts validés. Garde-fou: si le modèle ne comprend pas un document ou une photo, il le signale au lieu d'inventer. Les exercices d'application (problèmes, rédactions) sont prévus pour les concepts essentiels.

## Règles de planification
- Heuristique par-dessus FSRS: rétention cible qui monte à l'approche d'un test, retour au rythme normal après, recalcul automatique quand un test change.
- Dette: plafond quotidien configurable en **points** (défaut 40; une carte pèse 0,1 à 4 points selon son type, voir config.toml), étalement avec priorité aux tests les plus proches, aucune carte perdue. Une carte plus lourde que le plafond compte pour le plafond entier (toujours planifiable).
- Poids d'une carte, par priorité: poids explicite (nullable) > catégorie du concept (vocabulaire 0,1) > type (exact/cloze/qcm 0,5; libre_llm 1; probleme 2; redaction 4).
- Cours = thème; matière = parent optionnel qui définit le profil (le thème l'hérite et le surcharge, fusion par rubrique, le thème prime). Un test se lie à plusieurs thèmes ou à toute une matière. Un thème archivé sort du planning sans perdre ses cartes.
- Mode entretien: un thème sans test à venir (tous passés, ou aucun) et non archivé. Quota hebdomadaire plancher par thème (3 pts/semaine), cartes de plus faible rétention de préférence légères (poids <= 2), étalé sur la semaine; plafond global d'entretien par jour (8 pts) avec rotation; priorité la plus basse, sauf la part du plancher qui est réservée chaque jour. Retour automatique en mode test dès qu'un test futur est lié.
- Skip: bouton Passer en session; la question revient plus tard, aucune mise à jour FSRS, aucune pénalité, boucle bornée.
- Ordre des questions en session (mélangé ou par cours): réglage à tester, question ouverte.

## Interface (prototype 3)
Thème sombre sobre et stylé, mobile first, PWA. Accent violet `#8B7CFF`, fond `#0B0D12`. Pages: Aujourd'hui (session plein écran), Tableau de bord, Tests (liés à plusieurs thèmes, bouton « toute la matière »), Ajouter de la matière (PDF, photo, texte, import; sélecteur matière puis thème avec création rapide d'un thème; extraction puis validation dans la page puis génération), Bibliothèque, Matières et thèmes (profil éditable par matière, surcharge par thème, archivage), Réglages (plafond en points, poids par type, quota et plafond d'entretien). Les vues affichent le nombre de questions ET les points (« 32 questions, environ 20 points »). Un seul utilisateur, mot de passe, HTTPS, sauvegarde quotidienne.

## Règles impératives
- Toute requête LLM passe par un seul wrapper, avec registre de consommation, plafonds de coût (jour, mois, opération), retries bornés (jamais sur 4xx ni erreur de crédit), limites d'entrée, anti-doublon, interrupteur `LLM_DISABLED=1`.
- Les tests et le développement utilisent le mode mock par défaut: la vraie API ne s'appelle que sur activation explicite. Aucun test ne consomme de crédit.
- La clé API reste côté serveur et en variable d'environnement, jamais dans le code, les logs ou le navigateur.
- Le contenu généré par le LLM est une donnée non fiable (SVG, Markdown, HTML à sanitiser; sortie toujours validée par schéma).
- Les dates sont injectées, jamais `datetime.now()` en dur dans la logique.
- Séparation nette entre logique métier et affichage.
- Code simple et lisible, pas de framework lourd ni d'abstraction prématurée.

## Hors scope (sauf demande explicite de Nate)
Brand guideline et logo, multi-utilisateurs, app mobile native, notifications push, jugement LLM des réponses libres, synchronisation avec Notion, paiement, analytics.

## Contexte pratique de Nate
Clé API Anthropic pas encore utilisable (chargement de crédit bloqué), donc le test de qualité réel de la génération reste à faire. Crédit à protéger: plafonds bas, pas de rechargement automatique. Questions encore ouvertes: hébergement exact du serveur, mélange ou tri par cours en session, qualité de lecture des photos manuscrites.

## Façon de travailler avec Nate
Il communique en français, style direct, veut des sorties prêtes à copier-coller. Il est très occupé: décide les détails mineurs toi-même et note-les, ne pose de question qu'en cas de blocage réel. Commence chaque gros chantier par un plan court, puis enchaîne.

---

# État d'avancement

*Rédigé d'après le code réel. Dernière mise à jour: audit sécurité + protection
du crédit API (6 octobre 2026, `AUDIT.md`), après matières/thèmes, poids en
points, mode entretien (migration 005) et le workflow manuel.*

## Ce qui est fait

**Rôles et dépôt (13 octobre 2026).** Migration 009: table `comptes` (admin / standard /
étendu; l'ancien compte unique devient admin) et table `depots`. Admin: page Comptes
(créer, changer le rôle, nouveau mot de passe, supprimer; jamais sans admin), modèle LLM et
arrêt d'urgence réservés. Étendu: page Dépôt (créer un cours, déposer PDF / photos / texte /
.docx, télécharger, supprimer; 20 Mo par fichier, 1 Go au total). Les fichiers attendent dans
`FLASHCARDS_DEPOT` (`/data/depot` en Docker), sans aucun appel API. À la maison:
`/generer-questions depot` (`flashcards depot list|recuperer|traite`). Code: `web/auth.py`,
`depot.py`, `web/app.py`; tests: `tests/test_roles_depot.py`, E2E « rôles ». Un petit
personnage à côté de « Se déconnecter » (`composants/Avatar.tsx`) montre le rôle: vert =
standard, bleu = étendu, rouge = admin.

**Menu « Cours à réviser » (13 octobre 2026).** En mode « Par cours » sur la page Aujourd'hui, un menu permet de ne réviser qu'un thème (`/session?ordre=par_cours&cours=X`, le paramètre `cours` existait déjà côté API).

**Identité « Thread » (13 octobre 2026).** L'appli s'appelle Thread. Logo = un T en
fil (barre qui finit en boucle, tige qui se défait en points de couture aux écarts
croissants = répétition espacée): `frontend/src/composants/Logo.tsx` (`Logo`, `Marque`,
`Fil`, `FilDeFond`), `public/favicon.svg`, `icon-192/512.png`. Remplace « Flashcards »
dans le rail latéral, le titre, le manifeste PWA et la page de connexion (refaite).

**Répartition entre thèmes (13 octobre 2026).** En mode test, le plafond du jour
se partage à parts égales entre les thèmes qui ont des cartes à faire (4 thèmes,
40 pts = 10 chacun), au lieu de laisser le thème au test le plus proche tout
avaler. Le non-utilisé est redonné aux autres. Exception: un thème dont le test
est dans au plus `jours_focus_test` jours (défaut 2, `config.toml`) passe d'abord,
sans limite. Code: `planning._repartir_themes`; tests: `tests/test_repartition_themes.py`.
Aussi: bouton « J'avais juste quand même » (QCM, exact, cloze) pour contester une
correction automatique et choisir sa note.

**Prototype 1 — génération (CLI).** `extract` → validation manuelle
(`validated: true`) → `generate` → `review`. Les six types de questions sont
implémentés en modèles Pydantic stricts. Les prompts vivent dans
`prompts/extraction.md` et `prompts/generation.md`, rendus avec le profil du
cours (`profiles/<cours>.md`). Profil absent = avertissement explicite, pas
une erreur. `review` affiche `source_ref` et `confiance`, et met en évidence
les confiances basses.

**Prototype 2 — planification (CLI).** SQLite avec migrations versionnées
(3 à ce jour), FSRS via la bibliothèque officielle (jamais réimplémenté),
heuristique de pilotage par les dates de test, gestion de la dette avec
plafond quotidien, skip sans effet FSRS, simulateur 90 jours avec graphique
PNG. Commandes: `import`, `test add|list|move|remove`, `today`, `session`,
`stats`, `simulate`.

**Prototype 3 — interface web.** FastAPI + React/Vite/TS/Tailwind, PWA
installable. Sept pages, session plein écran avec un gabarit par type,
raccourcis clavier, skip avec raison. Auth mono-utilisateur (argon2, cookie
HttpOnly signé, anti-force-brute). Docker + Caddy + `DEPLOY.md`. Sauvegarde
quotidienne par thread interne avec rotation 14 jours. `seed-demo` pour une
interface remplie au premier lancement.

**Matières, poids, entretien (migration 005).** Tables `matieres`, `exam_cours`,
`exam_matieres`; colonnes `cours.matiere_id/profil/archive` et
`cards.poids/categorie`. Données existantes conservées (chaque cours devient un
thème sans matière, chaque test reste lié à son cours; `exams.cours` est
remplacée par la table de liaison). `planning.construire_file` est l'unique
fonction qui décide de la journée (file réelle, prévisions, stats, simulateur):
budget en points, mode test / mode entretien, plancher hebdomadaire réservé,
plafond d'entretien avec rotation. CLI: `matiere add|list|delete`,
`cours add|list|set-matiere|archive|unarchive`, `test add|move|list` multi-cours
ou par matière, `today|session|stats|simulate` en points avec la part
d'entretien. Interface: pages Matières et thèmes, Tests, Réglages, Tableau de
bord, Aujourd'hui, Session, Ajouter.

**Workflow manuel (sans API).** Dossiers `inbox/` (sources) et `sorties/` (JSON),
tous deux ignorés par git. Commande `/generer-questions <fichier> <thème>`
(`.claude/commands/generer-questions.md`): Claude Code lit la matière, suit
`prompts/extraction.md` à la lettre, écrit `sorties/<nom>_concepts.json`, **s'arrête
pour que Nate valide**, puis suit `prompts/generation.md`, écrit
`sorties/<nom>_questions.json` et importe. Briques de code (aucune n'appelle de LLM,
aucune n'est dans `llm.py`): `manuel.py` (validation par les modèles stricts +
contrôles de cohérence, rapport par question, empreinte de contenu), `flashcards
validate` (lecture seule, code 1 si invalide), `flashcards prompt` (prompt rendu avec
le profil, identique à celui de l'API), `importer.py` étendu (fichiers bruts,
anti-doublon par contenu). L'interface web a un 4e mode « Importer » (aperçu puis
confirmation, routes `/api/import/apercu` et `/api/import/confirmer`).

**Interface FR/EN (2026-10-11).** Bouton FR | EN (rail latéral, haut de page sur
mobile, page Réglages, écran de connexion). `frontend/src/i18n.ts`: `tr("phrase
française")` renvoie la phrase telle quelle en FR, ou sa version de
`i18n-en.ts` en EN (repli sur le français si absente); `trn` pour les pluriels.
Langue mémorisée dans le navigateur (`localStorage`), français par défaut; un
changement remonte les pages (l'URL est conservée). Pas traduits: le contenu des
cartes, les messages d'erreur renvoyés par le serveur, les rubriques de
`profiles/*.md` (clés de format). Toute nouvelle chaîne d'interface passe par
`tr(...)` + une entrée dans `i18n-en.ts`. Bibliothèque: filtre par type de question.

**Réflexion sur les types (2026-10-11).** `prompts/generation.md` (à la demande de
Nate) a une phase « RÉFLEXION SUR LES TYPES » avant toute question: tous les types
ne sont pas obligatoires par leçon; `libre_llm` et `redaction` (correction
subjective, qui coûterait avec un juge LLM) seulement si la leçon l'exige (essay,
intro de dissertation…). `/generer-questions` affiche ce plan avant de générer.

**Correction par IA, OpenRouter (2026-10-11).** Le juge des types `libre_llm` et
`redaction` est branché (à la demande de Nate), **jamais testé en réel** (tous les
tests passent par un faux transport HTTP). `llm.py` (porte unique, comme pour
Anthropic): `juger_libre`, `juger_redaction`, `etat_juge()` (`reel` / `simule` /
`indisponible`). Réel = `FLASHCARDS_JUGE_REEL=1` + clé `OPENROUTER_API_KEY` (le
`.env` de Nate l'écrit `OpenRouter_API_KEY`: casse ignorée); `MOCK_LLM=1` a le
dernier mot. Modèle `anthropic/claude-haiku-5.5` (liste fermée `budget.TARIFS_JUGE`,
réglable par `FLASHCARDS_JUGE_MODELE`), sortie JSON stricte, raisonnement coupé,
coût réel lu dans `usage.cost`, mêmes plafonds/disjoncteur/arrêt d'urgence, cache
anti-doublon, 1 retry sur 408/429/502/503 seulement. `POST /api/juger` ne renvoie
jamais d'erreur HTTP pour un refus attendu: `disponible=false` + `raison`, et
l'interface retombe sur l'auto-évaluation. Côté interface: bouton « Corriger avec
l'IA » (réponse libre; rédaction ≥ 20 caractères, critères pré-cochés et
ajustables), verdict + commentaire, note suggérée (jamais Facile), source `juge` si
la note est gardée telle quelle, sinon `manuel`. Le CLI `flashcards session` reste
en auto-évaluation. `run.sh`: défaut = juge réel si `.env` l'active, `--simule` =
tout factice. Prompts: `prompts/juge_libre.md`, `juge_redaction.md`.

**Premier thème de physique: Mec-16 Force hydrostatique (2026-10-11).** Via
`/generer-questions` (sans API). Sources: livre scanné (19 p., lu en vision), tableau
imprimé des réponses, corrigé manuscrit du prof (3 p., lisible). Matière « Physique »,
cours `physique` créés; test du 2026-11-13 lié au cours (mode test). 39 concepts
validés par Nate (c27, c41, c42 retirés), 73 cartes importées (32 probleme, 33 cloze,
6 exact, 2 qcm; aucun libre_llm/redaction), 82,5 pts. c40 sans question (valeurs
numériques d'exemple). Choix: g = 9,81 N/kg comme le prof; ρ_acier, ρ_terre, ρ_mer
viennent du corrigé du prof et sont donnés dans les énoncés; valeurs des problèmes
changées (sauf ex. 17 et 14), recalculées deux fois. À vérifier par Nate: levier du
cric (rapport 3, repris du prof), ex. 13 (force sur paroi verticale, méthode absente
du cours) et ex. 11 (croquis ambigu) non traités; ex. 20 du livre incohérent (102 vs
104 cm). Fichiers: `sorties/mec-16-force-hydrostatique_{concepts,questions}.json`.

**Récap théorique par thème (2026-10-12).** À la demande de Nate (il ne savait plus
quoi faire après plusieurs jours sans exercer): colonne `cours.recap` (migration 006,
Markdown). En session **par cours uniquement** (pas en mélangé), une page de récap
s'intercale avant la première question de chaque thème qui en a un; bouton **Passer**
(ou espace). `GET /api/session` renvoie `ordre` et `recaps` (vide en mélangé); un thème
déjà lu n'est pas réaffiché si une question passée revient. Édition: page Cours,
`PATCH /api/cours/{nom}` (`recap`, 20 000 car. max) ou `flashcards cours recap --nom X
[--fichier f.md | --efface]`. Rendu sans HTML (`composants/Recap.tsx`, KaTeX via `Texte`).
Récaps écrits (sans API, d'après les concepts validés) pour `english` (conditionals) et
`physique` (Mec-16); sources dans `sorties/recap_*.md`. `/generer-questions` propose
désormais le récap en étape 5. À vérifier par Nate: pour Mec-16, le concept c31 se
contredit (« la pression ne varie pas si l'on incline la planche: elle diminue »), la
phrase est donc absente du récap.

**Correction par IA: premier appel réel (2026-10-12).** Le premier essai de Nate donnait
« 404 No endpoints found that can handle the requested parameters »: `anthropic/claude-haiku-5.5`
ne liste pas `temperature` dans ses `supported_parameters` (OpenRouter), et la requête
l'envoyait avec `provider.require_parameters`. Corrigé (`llm.MODELES_SANS_TEMPERATURE`, la
température 0 reste pour les autres modèles du juge). Vérifié par un appel réel (verdict
`juste`, ~0,0002 USD).

**Matière > thème, rien d'autre (2026-10-12).** À la demande de Nate: ce qu'il
appelait « chapitre » (Statique des fluides, MRU/MRUA…) est le thème. Migrations 007
(colonne des cartes et table de filtre des tests supprimées: un test couvre ses
thèmes en entier, `Exam.couvre(cours)`) et 008 (clé retirée des payloads déjà
stockés). Retiré de l'API, de l'interface (Ajouter, Import, Bibliothèque, Tests,
Session), du CLI (`extract`, `prompt`, `test add`), de `/generer-questions`
(`<fichier> <thème>`) et des prompts (`Thème: {cours}`, réécrits à sa demande).
Les noms de fichiers `007_sans_chapitre.sql` et le schéma d'avant 005 (migrations
001/002, tests de migration) gardent l'ancien mot: les migrations appliquées ne
s'éditent pas.

### Hébergement à la maison (2026-10-20)

- **Premier `docker compose up --build` réussi**, sur le PC de Nate (WSL2 en mode `mirrored`),
  exposé par DDNS Infomaniak: `https://test.nategroup.ch` (certificat Let's Encrypt obtenu,
  API protégée: 401 sans connexion). Passage en `thread.nategroup.ch` = changer `DOMAIN`
  dans `.env` puis `docker compose up -d`.
- Prérequis hors code: redirection TCP 80/443 de la box vers le PC, et
  `scripts/windows-exposer.ps1` (pare-feu Windows + Hyper-V, PowerShell administrateur).
  Piège rencontré: une règle `netsh interface portproxy` oubliée tenait le port 5173 côté
  Windows (invisible pour `ss`/`lsof`); `run.sh` le détecte maintenant et décale Vite.
- `run.sh`: libère tout seul les ports 8000/5173 tenus par un ancien lancement du projet,
  trap aussi sur HUP (fermer le terminal laissait uvicorn/vite orphelins).
- `docker-compose.yml`: l'image DDNS est `ghcr.io/qdm12/ddns-updater:v2.10.0` (le dépôt
  Docker Hub `qdm12/ddns-updater` n'existe plus).
- Base locale copiée dans le volume `donnees` (API `backup` de SQLite, base arrêtée,
  copie convertie hors WAL; sauvegarde de la prod vide dans `/data/backups/avant-import-*`).
  Les deux bases sont maintenant indépendantes: choisir où réviser. `uploads/` n'a pas été copié.

## Ce qui est testé

- **538 tests pytest**, tous en mode simulé: aucun ne consomme de crédit
  (`conftest.py` remplace le client du SDK par un piège pour chaque test; ceux du
  workflow manuel échouent au moindre appel au wrapper LLM, fixture `pas_de_llm`).
  `test_catastrophes.py` (vrai chemin d'appel contre un faux SDK qui compte les
  appels) et `test_securite.py` (sessions, accès, CSRF, entrées hostiles).
  Couvrent aussi les poids (priorité explicite > catégorie > type, plafond en
  points, carte plus lourde que le plafond), l'entretien (entrée, sortie quand un
  test futur est lié, quota hebdomadaire, plafond global, rotation, idempotence),
  les tests multi-cours / matière, l'archivage, les profils fusionnés et la
  migration 005 sur une base avec données.
  Couvrent le mapping des notes, l'heuristique de test (rampe + clamp étalé),
  le lissage de la dette, l'import (JSON invalide inclus), le skip, le CRUD
  des cours, les trois voies d'ajout, les deux ordres de session, les profils,
  `source_ref`/`confiance`, le type `redaction` et son barème.
- **10 tests Playwright** (dont le récap en session par cours) (dont l'import de questions, matières/thèmes/tests liés à une matière, réglages en points): login exigé, ajout de matière par texte, validation
  des concepts avec un concept décoché, session avec skip et notation.
- **Vérifié à la main dans un navigateur**: les six gabarits de question, le
  rendu mobile, le flux d'ajout complet, le gabarit `redaction` (4/4 → note
  `Good`, source `manuel`, aucune correction automatique).
- **Le simulateur a trouvé un vrai bug de conception** (voir Décisions).

## Ce qui est fragile ou manquant

**Audit sécurité + crédit API fait le 6 octobre 2026: voir `AUDIT.md`.** Le
wrapper LLM tient maintenant toutes ses règles (réservation du coût avant
l'appel, plafonds jour/mois/opération, retries bornés, limites d'entrée,
anti-doublon, arrêt d'urgence `flashcards llm off`, mode réel sur activation
explicite `FLASHCARDS_LLM_REEL=1`). Le `schema_svg` s'affiche en image, plus en
HTML. Restent ouverts (détail et correctifs dans `AUDIT.md`):
- **Docker et Caddy jamais exécutés**: `Dockerfile` (utilisateur non-root),
  `docker-compose.yml` et `Caddyfile` (CSP, taille des requêtes) sont relus,
  pas lancés.
- **`.env` contient une clé OpenRouter**, que le code n'utilise pas (API
  Anthropic seulement): à trancher par Nate.
- Sur Sonnet 5, la réflexion compte dans `max_tokens` (4 000 / 8 000): risque
  de réponse tronquée, à mesurer au premier appel réel.
- La matière est renvoyée à chaque lot de génération: un gros PDF + beaucoup de
  concepts dépasse le plafond par opération (0,50 USD) et est refusé. Le cache
  de prompt réglerait ça mais demande de toucher aux prompts.
- Faibles: anti-force-brute en mémoire, sauvegardes sur le même volume,
  `passlib` non maintenu, 3 erreurs oxlint (hooks conditionnels) dans
  `Gabarits.tsx`, images Docker non épinglées par empreinte.

**Entretien: capacité et dette à surveiller** (voir README, « Simulateur année »).
Avec les défauts (quota 3, plafond d'entretien 8/jour), 18 thèmes en entretien
saturent la capacité (56 pts/semaine): au-delà, les quotas ne sont plus tous
tenus. Après un gros examen, tout le lot retombe en entretien d'un coup (dette
d'entretien ~150 pts, ~19 jours de plafond). Le plafond en points ne borne pas le
nombre de cartes (jusqu'à 157 cartes/jour dans la simulation, vocabulaire à
0,1 pt). `today`, `stats` et le simulateur signalent l'alerte de capacité.

**La page Cours ne permet pas encore d'éditer le profil du cours.** Les
profils ne se modifient qu'en éditant `profiles/<cours>.md` à la main.
L'interface liste les profils disponibles dans Réglages, rien de plus.

**Aucun appel réel n'a jamais été fait.** Tout a été validé en mode simulé.
Les schémas de sortie structurée ont été vérifiés hors ligne (pas de
contrainte non supportée, pas de récursion, pas de `discriminator`), mais le
premier appel réel reste le vrai premier essai.

**Le build Docker n'a jamais tourné.** Docker n'est pas installé dans
l'environnement de développement. Les fichiers sont cohérents et le paquet
Python se construit, mais le premier `docker compose up --build` sur le VPS
sera un vrai premier essai.

**La rétention estimée affiche souvent 100 %.** C'est la projection optimiste
(elle suppose des réponses justes). Correct mais peu informatif tant qu'il n'y
a pas de vrai historique.

**L'anti-force-brute est en mémoire**: un redémarrage remet le compteur à zéro.

## Ce qui reste à faire

1. **Avant publication**: dérouler la checklist de `AUDIT.md` (limite de
   dépense chez le fournisseur, premier `docker compose up --build`,
   validation du `Caddyfile`).
2. **Trancher le fournisseur** (clé Anthropic, ou support d'OpenRouter dans `llm.py`).
3. **Premier test de qualité réel** sur 2-3 vrais cours, dès que la clé API
   fonctionne. C'est le but d'origine du prototype 1 et il n'est pas atteint.
4. **Sortir les sauvegardes du serveur** (rsync vers un NAS): une sauvegarde
   sur la même machine ne protège pas de la perte du VPS.
5. Rendre le **profil de cours éditable** depuis la page Cours.
6. Trancher les questions ouvertes: hébergement, ordre de session
   (mélangé vs par cours), qualité de lecture des photos manuscrites.

## Décisions techniques prises, et pourquoi

**Le plancher d'entretien réserve sa part du jour (et compte le fait).** Première
version: entretien à la priorité la plus basse, « plancher » seulement pour les
compléments. Le simulateur a montré que, dès que le mode test sature le budget,
l'entretien tombait à 0 point pendant des semaines (quota tenu 28 %, dette
d'entretien 253 pts), parce que les cartes « planifiées par FSRS » comptaient
pour le quota sans jamais être servies. Corrigé: la part du plancher (manque
hebdomadaire / jours restants, débitée de ce qui est déjà fait aujourd'hui) est
réservée avant le mode test, dans la limite du plafond d'entretien; le quota
compte ce qui est fait. Résultat: quota tenu à 96 % (hors semaines avec jour
sauté). Plafond total jamais dépassé.

**Poids: plafond en points, glouton avec saut.** Les cartes se prennent par
priorité tant qu'elles tiennent; une carte qui ne tient pas est sautée au profit
d'une plus légère (sinon un budget de 40 pts s'arrêterait sur une rédaction à
2 points de la fin). Une carte plus lourde que le plafond compte pour le plafond
entier. Conséquence assumée: le plafond ne borne plus le nombre de cartes
(400 cartes de vocabulaire à 0,1 pt tiennent dans 40 pts).

**Tests liés à une matière = lien dynamique.** `exam_matieres` s'ajoute à
`exam_cours`: les thèmes ajoutés plus tard à la matière sont couverts, les
archivés exclus. Un test couvre ses thèmes en entier.

**Workflow manuel: deux formes de fichier de questions.** *Complet* (`generate`:
`id`, `cours`, `source` sur chaque question) et *brut* (Claude Code:
seulement `concept_id`, `type`, champs du type, `source_ref`, `confiance`). Pour le
brut, `cours`/`source` viennent de l'en-tête et l'`id` est un UUIDv5 du
cours + contenu: un même contenu donne toujours le même `id`, donc l'import est
idempotent sans état. Claude n'invente jamais d'`id`.

**Anti-doublon par contenu, partout.** `manuel.empreinte_contenu` hache les champs de
contenu (pas `id`, `concept_id`, `source_ref`, `confiance`…), insensible aux espaces,
dans le périmètre d'un cours. Appliqué à tout import (CLI et web). Conséquence assumée:
deux cartes de contenu identique sous deux `id` ne sont plus importées toutes les deux
(la fixture `question_dict` des tests a été rendue unique par `id`).

**Import strict, en bloc.** Une question invalide et rien n'est écrit; le rapport
liste les erreurs question par question. Les incohérences qui rendraient une carte
fausse sont des erreurs (QCM hors bornes, trou de cloze sans valeur); le reste
(propositions ≠ 4, champ inconnu, doublon) n'est qu'un avertissement.

**Pas de lecteur docx dans le projet.** La consigne parlait du lecteur docx « s'il
existe »: il n'existe pas. Repli documenté dans la commande (extraction du texte brut
par `zipfile`), avec avertissement de perte des tableaux/formules/images.

**Contenu importé = non fiable.** Le `schema_svg` (importé ou généré) n'est jamais
injecté dans la page: il est affiché en `<img src="data:image/svg+xml,…">`, où un
SVG ne peut ni exécuter de script ni charger de ressource. La CSP le double.

**Coût réservé avant l'appel, soldé après.** Compter seulement les succès laissait
passer les échecs facturés (délai dépassé, réponse inexploitable) sous les plafonds.
La réservation rend aussi le contrôle atomique entre processus.

**Mode réel sur activation explicite.** Une clé dans l'environnement ne suffit
plus: il faut `FLASHCARDS_LLM_REEL=1`. Sans `FLASHCARDS_ENV=development`
explicite, le serveur se considère en production.

**`due` n'est jamais stocké en base.** L'échéance est recalculée à partir de
`(stability, last_review)` + des examens courants. Conséquence: ajouter,
déplacer ou supprimer un test replanifie tout instantanément, sans migration
de données ni job de recalcul.

**Clamp pré-examen étalé, et non à date unique.** Le simulateur a montré
qu'un clamp à `date_examen - 1` rendait tout un thème dû le même jour: sur
150 cartes avec un plafond de 40, **110 n'étaient jamais révisées avant
l'examen**. La garantie cassait précisément sur les gros thèmes. Corrigé
par un étalement déterministe (hash de l'id) sur la fenêtre finale + une
priorité à la couverture. Résultat: 0 carte oubliée à 150 et 300 cartes.
Régression couverte par un test.

**`learning_steps` et `relearning_steps` vides, fuzzing désactivé.** Une seule
session par jour: pas de re-présentation à 1 min / 10 min. Le fuzz pourrait
repousser une carte après la date du test.

**Sortie structurée via `messages.parse()`** avec le schéma Pydantic, plutôt
que du parsing JSON manuel. Le `discriminator` a été retiré du schéma envoyé à
l'API (mot-clé non supporté par les sorties structurées); un `Union` simple
suffit, chaque variante ayant son `type` littéral.

**Génération par lots de 8 concepts.** Le prompt renvoie une liste pour tous
les concepts d'un coup; un thème de 30 concepts risquait la troncature à
`max_tokens`.

**La génération rejoint la matière source.** Le prompt l'exige, contrairement
au prototype 1 où le PDF n'était pas relu. Le garde-fou est déplacé, pas
perdu: le `concept_id` renvoyé est revérifié contre la liste validée, et toute
question rattachée à un concept inconnu est écartée avec un avertissement.

**Moteur de templates maison pour les prompts.** `str.format` cassait sur le
JSON de la section SORTIE et sur `{{c1::valeur}}`. Le rendu ne remplace que
les variables connues; un test vérifie que les deux survivent.

**SQLite en WAL, une connexion par requête.** Trouvé en debuggant: la mise à
jour de progression ouvrait une 2e connexion pendant que la génération tenait
une transaction en écriture (auto-blocage). La suite de tests est passée de
211 s à 5,6 s après correction.

**Migrations avec clés étrangères désactivées.** `DROP TABLE cards` déclenchait
le CASCADE et effaçait tout l'historique de révisions. Les FK sont coupées
pendant les migrations, avec `PRAGMA foreign_key_check` ensuite.

**`FLASHCARDS_DB` centralisé dans `config.load_config`.** Le CLI et le serveur
web résolvaient le chemin différemment, ce qui aurait cassé `seed-demo` et
`backup` en production.
