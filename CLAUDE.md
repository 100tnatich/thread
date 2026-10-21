# Flashcards — mémoire de travail

Appli de flashcards à répétition espacée pour Nate (apprenti informaticien CFC +
maturité pro). Il injecte la matière thème par thème, l'appli génère les
questions et les planifie avec FSRS **piloté par ses dates de test**: il s'assoit,
répond aux questions du jour, c'est tout. Détail: **`docs/CONTEXTE.md`**
(contexte + avancement), **`README.md`** (usage).
Nate: français, direct, prêt à copier-coller. Très occupé — tranche les détails
mineurs toi-même et note-les; ne questionne qu'en cas de blocage réel.

## Commandes

```bash
uv sync                                 # installer
uv run pytest                           # ~540 tests, tous en mock (pièges SDK + réseau dans conftest)
cd frontend && npx playwright test      # 10 tests E2E
export MOCK_LLM=1 SESSION_SECRET=dev    # web, 2 terminaux
uv run flashcards web create-user --identifiant nate [--role admin|standard|etendu]  # une fois (admin par défaut)
./run.sh                                # API (PORT_API=8101) + front (PORT_WEB=5201), ports lus dans .env
# (à la main: uv run flashcards web serve --port $PORT_API --reload; cd frontend && npm run dev)
uv run flashcards extract <pdf> --cours X  # puis validated:true
uv run flashcards generate <pdf> [--cours X]            # puis review
uv run flashcards today | session | stats | simulate [--scenario annee|simple]
uv run flashcards budget; flashcards llm off|on|status   # dépense, arrêt d'urgence
# Appel réel = FLASHCARDS_LLM_REEL=1 + ANTHROPIC_API_KEY (une clé seule ne suffit pas)
# Correction par IA (réponses libres, rédactions) = OpenRouter: FLASHCARDS_JUGE_REEL=1 + OPENROUTER_API_KEY
./run.sh [--real|--simule]   # --simule: tout factice; défaut: juge réel si .env l'active
./run.sh --docker            # montage de production (réseau Docker `web`); LAN=1 ./run.sh = joignable sur le réseau local
uv run flashcards matiere add --nom X; flashcards cours add --nom T --matiere X   # matière > thème
uv run flashcards test add --cours A --cours B | --matiere X --date D --intitule I
uv run flashcards cours archive|unarchive --nom T                                  # réversible
# Workflow manuel (Claude Code joue le LLM, aucune API): /generer-questions <fichier> <thème>
uv run flashcards validate sorties/X_questions.json   # lecture seule, sans LLM
uv run flashcards prompt extraction|generation --cours X   # prompt rendu
uv run flashcards import sorties/X_questions.json
# Dépôt (compte étendu, page « Dépôt »): /generer-questions depot [N°]
uv run flashcards depot list [--cours X] [--tout]; flashcards depot recuperer N; flashcards depot traite N
```

## Conventions nategroup.ch (appli n°01)

**Avant de toucher aux ports, au Docker, au domaine ou au `.env`: lire `/home/nate/infra/CONVENTIONS.md`**
et la fiche de Thread dans `/home/nate/infra/JOURNAL.md`. Tout changement de port, de montage ou de domaine s'y écrit.

- Ports de dev **par formule** (lus dans `.env`, jamais en dur): front `5201`, API `8101`, E2E `5301` / `8301`.
  `run.sh` refuse de démarrer si `.env` s'écarte de la formule; Vite est en `strictPort`.
- Production: **aucun port publié**. Conteneurs `thread-web` (Caddy 8080: frontend + `/api` → `thread-api:8000`, CSP) et `thread-api`
  (FastAPI, réseau interne seulement). Le Caddy central du gateway (`~/infra/gateway`, `sites/thread.caddy`) gère HTTPS et DNS dynamique:
  plus de Caddy, de DDNS ni de `ACME_*` dans ce projet.
- Dérogations déclarées (`.kit/DEROGATIONS`): `api-racine` (le code Python est à la racine, pas dans `backend/`) et `csp-unsafe-inline` (KaTeX).
- Conformité: `~/infra/bin/verify-kit.sh --app .`. Déploiement: `~/infra/bin/deploy.sh thread` ou `/deployer thread`.
  **Bascule de la production actuelle vers ce montage: voir `DEPLOY.md` § « Bascule »** (non faite).
- Dépôt GitHub: `100tnatich/thread`. Branche `conventions-infra` = mise aux conventions; fusion dans `main` au moment de la bascule.

## Architecture

`src/flashcards/` — moteur pur, sans I/O terminal: `models.py` (taxonomie fermée,
6 types), `llm.py` (**wrapper LLM unique**: Anthropic pour l'extraction/génération, OpenRouter pour le juge), `ingest.py` (extraction → validation
→ génération), `manuel.py` (validation/finalisation des fichiers du workflow
manuel, **sans LLM**), `judge.py` (juge manuel / `JugeLLM`), `importer.py`, `api.py` (accès base, CRUD), `planning.py`
(**`construire_file`: la seule fonction qui décide de la journée** — points, mode
test / entretien), `poids.py`, `heuristic.py`/`fsrs_engine.py`, `db.py` +
`migrations/*.sql`. `web/` expose l'API REST, **sans** logique de planification.
`frontend/` = SPA React/PWA. `prompts/` et `profiles/` sont des données.
`inbox/` (sources) et `sorties/` (JSON produits) sont ignorés par git; commande `.claude/commands/generer-questions.md`.

## Rôles (comptes)

`comptes` (migration 009): **admin** (tout, dont la page Comptes, le modèle LLM et l'arrêt
d'urgence du crédit), **standard** (l'appli de révision telle quelle), **étendu**
(standard + page Dépôt: créer des cours, déposer des fichiers dans `FLASHCARDS_DEPOT`,
traités plus tard sans API avec `/generer-questions depot`). Le rôle est relu en base à chaque
requête (jamais dans le cookie); garde-fou: toujours au moins un admin. Les **données restent
partagées** entre comptes. Une route réservée prend `Admin` ou `Etendu` (`web/app.py`), pas
seulement `User`. Logique des fichiers: `depot.py` (sans I/O réseau, sans LLM).

## Modèle de planification (à ne pas casser)

- **Cours = thème**; `matieres` = parent optionnel (profil de matière, le thème
  surcharge: `profiles.fusionner`). Un test se lie à plusieurs thèmes
  (`exam_cours`) ou à toute une matière (`exam_matieres`, dynamique).
- **Le plafond quotidien est en POINTS** (défaut 40). Poids d'une carte:
  explicite > catégorie du concept (vocabulaire 0,1) > type (config.toml).
  Une carte ne dépasse jamais le plafond (`poids_plafonne`).
- **Mode entretien** = thème non archivé sans test à venir; seules les cartes déjà
  révisées en font partie. Quota hebdomadaire = plancher réservé chaque jour avant
  le mode test (borné par le plafond d'entretien); le reste de l'entretien passe en
  dernier. Rien n'est supprimé; archivage réversible.
- **Toute vue de la journée (file, stats, prévision, simulateur) passe par
  `planning.construire_file`**: ne pas recoder la sélection ailleurs.
- Dans les tests, `make_card` pose `poids=1.0` (1 carte = 1 point); `poids=None`
  pour tester les poids.

## Conventions

Français partout (code, commentaires, UI). Pydantic strict, union discriminée sur
`type`. `sqlite3` brut, pas d'ORM. Schéma modifié → nouvelle migration
`NNN_nom.sql`, jamais d'édition en place. Dates injectées en paramètre.

## Règles impératives

1. **Aucun test ne consomme de crédit.** Mock par défaut; vraie API sur
   activation explicite seulement.
2. **Tout appel LLM passe par `llm.py`** (Anthropic comme OpenRouter; `httpx` et `anthropic` n'y sont importés que là): coût réservé avant l'appel
   (`budget.reserver`) puis soldé, plafonds jour/mois/opération, retries bornés
   (jamais sur 4xx, crédit ni délai dépassé), limites d'entrée, anti-doublon,
   arrêt d'urgence. Une route qui lance une tâche ne le fait que si
   `_creer_job` renvoie `nouveau=True`. Détail: `AUDIT.md`.
3. **Clés API (Anthropic, OpenRouter) côté serveur, en variable d'env.** Jamais dans le code/logs/navigateur.
   Hors `FLASHCARDS_ENV=development` explicite, on est en production
   (`SESSION_SECRET` ≥ 32 car., cookie Secure). Toute route `/api/*` est
   protégée par défaut (`app._garde`); une route publique s'ajoute à `ROUTES_PUBLIQUES`.
4. **Sortie LLM = donnée non fiable**: validée par schéma; jamais de
   `dangerouslySetInnerHTML` (le SVG s'affiche en `<img>`; un test le vérifie).
5. **Jamais `datetime.now()` dans la logique.** La date est un paramètre.
6. **Logique métier séparée de l'affichage.** Code simple: pas de framework
   lourd, pas d'abstraction prématurée.
7. **Workflow manuel = zéro appel API depuis le code.** `validate`, `prompt` et
   `import` ne passent pas par `llm.py` et n'en ont pas besoin; `validate` ne
   modifie rien. Dans `/generer-questions`, jamais `extract`/`generate`, et aucune
   question avant la validation explicite des concepts par Nate.
8. **Ne pas réécrire `prompts/*.md` ni la taxonomie** sans demande de Nate, ni
   réimplémenter FSRS — la bibliothèque `fsrs` fait foi.

## Hors scope (sauf demande explicite)

Données par utilisateur (les comptes à rôles existent, les données restent partagées), app native, push, synchro Notion, paiement, analytics.

**À la fin de chaque session de travail importante, mets à jour la section
État d'avancement de `docs/CONTEXTE.md`.**
