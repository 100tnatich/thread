# Audit: protection du crédit API et sécurité

Date: 6 octobre 2026. Périmètre: tout le dépôt (moteur Python, CLI, API FastAPI,
frontend React/PWA, Docker + Caddy). Méthode: lecture du code route par route,
`pip-audit`, `npm audit`, `npm outdated`, recherche de secrets, puis correction
et tests.

## Résumé exécutif

| Risque | Avant l'audit | Après corrections |
|---|---|---|
| **Financier** (crédit API) | **7 / 10** | **2 / 10** |
| **Sécurité** | **7 / 10** | **3 / 10** |

Le projet avait déjà un wrapper LLM unique avec registre, plafonds, disjoncteur
et cache (`budget.py`, migration 004) — plus avancé que ce que disait
`docs/CONTEXTE.md`. L'audit a pourtant trouvé des trous qui rendaient ces
garde-fous contournables:

1. **Double clic = deux appels payés** (C-01). Un seul job en base, mais deux
   tâches lancées. Le test existant ne le voyait pas.
2. **Un appel échoué valait 0 USD** (C-02): délai dépassé ou réponse
   inexploitable, donc facturés, n'entamaient jamais les plafonds.
3. **Le mode réel s'activait tout seul** dès qu'une clé était dans
   l'environnement (C-03), y compris pendant `pytest`.
4. **XSS stockée** par le `schema_svg` du modèle, injecté tel quel (S-01).
5. **Cookie de session falsifiable** si `FLASHCARDS_ENV` était oublié: secret
   par défaut connu (S-02).
6. **Remplissage du disque sans être connecté** (S-03), pas de CSP (S-04),
   anti-force-brute inopérant derrière Caddy (S-05).

Tout ce qui est Critique ou Élevé est corrigé et testé, ainsi que toute la
Partie A. Suite de tests: 377 → **452 tests pytest**, 6 tests Playwright, tous
verts, tous sans réseau.

**Limites de cette vérification** (à lire avant de publier):

- **Docker et Caddy n'ont pas pu être exécutés** (Docker absent de la machine).
  `Dockerfile`, `docker-compose.yml` et `Caddyfile` sont relus, pas lancés.
  Le premier `docker compose up --build` reste un vrai premier essai.
- **Aucun appel réel à l'API n'a jamais été fait**, ni avant ni pendant
  l'audit. Les garde-fous sont prouvés contre un faux SDK qui compte les
  appels, pas contre l'API.

---

## Partie A — Crédit API

### A1. Cartographie des appels LLM

Un seul endroit appelle le SDK: `llm._appel_structure` (`src/flashcards/llm.py:219`).
Un test AST (`tests/test_budget.py::test_aucun_module_hors_llm_n_importe_le_sdk`)
échoue si un autre module importe `anthropic`.

| Déclencheur | Chemin | Appels max par action | `max_tokens` | Entrée max | Retries |
|---|---|---|---|---|---|
| `POST /api/ingest/texte` | tâche de fond → `ingest.extraire` → `llm.extraire_concepts` | 1 (+2 retries) | 4 000 | 50 000 caractères | ≤ 2, transitoires seulement |
| `POST /api/ingest/fichiers` | idem | 1 (+2 retries) | 4 000 | 1 PDF ≤ 60 pages ou ≤ 10 images, 20 Mo/fichier, 24 Mo au total | idem |
| `POST /api/ingest/generer` | tâche de fond → `ingest.generer` → `llm.generer_questions` par lot de 8 | 5 lots (40 concepts) | 8 000 par lot | matière de l'extraction + concepts | idem, par lot |
| `flashcards extract` | `extract.run_extract` | 1 (+2 retries) | 4 000 | comme ci-dessus | idem |
| `flashcards generate` | `generate.run_generate` | 5 lots | 8 000 par lot | comme ci-dessus | idem |
| `POST /api/ingest/estimer`, `--dry-run` | `llm.estimer` | **0** | — | — | — |
| `validate`, `prompt`, `import`, `/api/import/*`, `/generer-questions` | `manuel.py`, `importer.py` | **0** (aucun passage par `llm.py`) | — | — | — |
| Tests (`pytest`, Playwright) | mode simulé forcé | **0** | — | — | — |
| Démarrage, hot reload, sauvegarde quotidienne | `app._demarrage`, `backup.py` | **0** | — | — | — |

Modèle: `config.toml` → `claude-sonnet-5` (2 / 10 USD par million de tokens),
modifiable dans Réglages mais seulement parmi la liste fermée
`budget.MODELES_AUTORISES`. Aucun scheduler, cron ni tâche relancée
automatiquement: tout appel part d'une action de Nate. `judge.JugeLLM` lève
`NotImplementedError`.

### A2 / A3. Constats

#### C-01 — Double clic: un job, deux appels · **Critique** · corrigé
`src/flashcards/web/app.py` (routes `ingest_texte`, `ingest_fichiers`, `ingest_generer`).
`_creer_job` renvoyait l'id du job déjà en cours, mais la route enchaînait
quand même:

```python
job_id = _creer_job(...)                      # renvoie l'id existant
taches.add_task(_tache_extraction, job_id, …) # …et relance une tâche
```

Deux tâches partaient donc en parallèle, avant que le cache ne soit écrit: deux
appels payés. Le test `test_double_soumission_ne_cree_qu_un_job` remplaçait la
tâche par un no-op et ne comparait que les ids.
**Correctif:** `_creer_job` renvoie `(job_id, nouveau)` (`app.py:790`); la tâche
n'est lancée que si `nouveau`. Second rempart: verrou `llm._VERROU_APPEL` +
relecture du cache sous verrou (`llm.py:305`). Les copies du fichier en double
sont supprimées. Tests: `test_double_clic_ne_lance_qu_une_seule_tache`,
`…_sur_un_fichier…`, `…_sur_generer…`, `test_deux_demandes_identiques_en_meme_temps_un_seul_appel`.

#### C-02 — Un appel échoué était compté 0 USD · **Élevée** · corrigé
`budget.consommation` ne sommait que `statut = 'succes'`, et tout échec était
inscrit avec `cout_usd = 0`. Or un délai dépassé ou une réponse reçue mais
inexploitable sont facturés. Le disjoncteur laissait repasser un appel toutes
les 10 minutes: jusqu'à 6 appels/heure à 0,50 USD invisibles pour les plafonds.
**Correctif:** `budget.reserver` (`budget.py:506`) inscrit le coût estimé AVANT
l'appel; `budget.solder` le remplace par le coût réel. Sur échec, le coût
réservé est conservé sauf si l'on est certain que rien n'a été facturé (statut
4xx/5xx renvoyé par l'API, connexion jamais établie): `llm._non_facture`
(`llm.py:173`). Tests: `test_delai_depasse_pas_de_retry_et_cout_conserve`,
`test_des_echecs_facturables_finissent_par_atteindre_le_plafond`,
`test_reponse_invalide_a_chaque_fois_s_arrete_et_reste_comptee`.

#### C-03 — Le mode réel s'activait dès qu'une clé existait · **Élevée** · corrigé
`llm.mock_actif()` renvoyait `False` dès que `ANTHROPIC_API_KEY` était définie.
Un `pytest` ou un `flashcards extract` lancé dans un shell où la clé est
exportée partait en réel. Seuls 3 fichiers de tests posaient `MOCK_LLM=1`.
**Correctif:** le mode réel exige `FLASHCARDS_LLM_REEL=1` **et** une clé
(`llm.py:69`); `MOCK_LLM=1` garde le dernier mot. `tests/conftest.py` pose pour
chaque test trois verrous: mode simulé, clé retirée, client du SDK remplacé par
un piège qui fait échouer le test. Test: `test_une_cle_seule_ne_suffit_pas_a_passer_en_reel`.

#### C-04 — Contrôle des plafonds non atomique · **Élevée** · corrigé
`autoriser()` lisait le registre puis l'appel partait; rien n'était écrit entre
les deux. Deux processus (CLI + web) passaient ensemble sous le plafond, et un
processus tué en plein appel ne laissait aucune trace.
**Correctif:** `reserver` prend le verrou d'écriture SQLite (`BEGIN IMMEDIATE`),
vérifie, puis inscrit la réservation dans la même transaction. Au démarrage,
`clore_reservations_orphelines` (`budget.py:562`) garde le coût des appels
coupés. Tests: `test_la_reservation_compte_avant_la_reponse`,
`test_appel_coupe_en_plein_vol_garde_son_cout`.

#### C-05 — « Par opération » voulait dire « par appel » · **Élevée** · corrigé
Une génération envoie un appel par lot de 8 concepts, chacun avec toute la
matière. Le plafond de 0,50 USD s'appliquait à chaque lot: une génération
pouvait coûter N × 0,50 USD. Le nombre de concepts n'était pas borné
(`GenerationIn.concepts` sans limite).
**Correctif:** `llm.verifier_budget_generation` (`llm.py:636`) chiffre TOUS les
lots avant le premier appel et refuse si le total dépasse le plafond par
opération; 40 concepts maximum (schéma + moteur). Tests:
`test_generation_trop_chere_refusee_avant_le_premier_appel`,
`test_trop_de_concepts_refuse_sans_appel`.

#### C-06 — Estimation trop optimiste · **Moyenne** · corrigé
Le prompt système (plusieurs milliers de caractères + profil) et le schéma de
sortie n'étaient pas comptés; 2 000 tokens par page de PDF alors qu'une page
part en texte ET en image; 4 caractères par token.
**Correctif:** prompt inclus, 3 000 tokens/page, 3 caractères/token, 1 500
tokens fixes par appel, marge de 25 % conservée (`budget.py:45-52`).
Conséquence assumée, mesurée: avec les défauts, un PDF de 60 pages est estimé à
0,501 USD en extraction, donc refusé d'un cheveu par le plafond de 0,50 USD. La
limite utile est d'environ 55 pages, et moins encore pour une génération en
plusieurs lots. C'est le plafond par opération qui prime sur la limite de
pages; pour de gros thèmes, découper le PDF ou relever
`FLASHCARDS_COUT_MAX_OPERATION`.

#### C-07 — Plafonds `nan`, `inf` ou faute de frappe acceptés · **Moyenne** · corrigé
`FLASHCARDS_COUT_MAX_JOUR=nan` rendait toute comparaison fausse: plus aucun
refus. `=100` au lieu de `1.00` passait sans bruit.
**Correctif:** `budget._valider` (`budget.py:119`): nombres finis et positifs
exigés; au-delà de 5 USD/jour, 20 USD/mois ou 2 USD/opération il faut en plus
`FLASHCARDS_PLAFONDS_ELEVES=1`. Tests: `test_plafond_absurde_refuse`,
`test_plafond_eleve_demande_une_confirmation`.

#### C-08 — Délai dépassé rejoué, délai du SDK de 10 minutes · **Moyenne** · corrigé
`APITimeoutError` hérite de `APIConnectionError`: il était rejoué jusqu'à 2
fois, soit jusqu'à 3 générations complètes facturées pour une seule demande.
Aucun `timeout` n'était passé au client.
**Correctif:** un délai dépassé n'est plus rejoué (`llm.py:155`);
`timeout=300 s` explicite; chaque retry repasse par `reserver` (un retry est
une dépense). Tests: `test_delai_depasse…`, `test_chaque_retry_repasse_par_les_plafonds`,
`test_erreur_4xx_ou_credit_un_seul_appel_cout_nul` (400, 401, 402, 403, 404, 413, 422).

#### C-09 — Pas de commande d'arrêt d'urgence · **Moyenne** · corrigé
Seule la variable `LLM_DISABLED=1` existait (redémarrage nécessaire);
`flashcards budget --desactiver` n'affichait qu'un rappel.
**Correctif:** interrupteur persistant en base, vu aussitôt par le serveur et le
CLI: `flashcards llm off|on|status`, `POST /api/budget/arret`, bouton dans
Réglages. `LLM_DISABLED=1` reste prioritaire et ne peut pas être levé depuis
l'interface. Tests: `test_arret_d_urgence_en_base_coupe_tout`,
`test_arret_d_urgence_depuis_l_interface`, `test_commandes_d_arret_d_urgence`.

#### C-10 — Consommation invisible dans l'interface · **Moyenne** · corrigé
`/api/budget` et `/api/ingest/estimer` existaient mais rien ne les affichait.
**Correctif:** carte « Crédit API » dans Réglages (jour, mois, barres, alertes à
80 %, disjoncteur, mode réel/simulé, arrêt d'urgence); bouton « Estimer le coût »
sur l'ajout par texte.

#### C-11 — Un journal illisible faisait perdre une réponse payée · **Moyenne** · corrigé
`_log_fichier` écrivait `logs/usage.jsonl` après l'appel et avant le retour: un
dossier non inscriptible (conteneur non-root, disque plein) levait une
exception, la réponse payée était perdue et jamais mise en cache.
**Correctif:** journal « au mieux » (`llm.py:188`), le registre SQLite fait foi.

#### C-12 — Un job bloqué condamnait l'unique créneau · **Moyenne** · corrigé
Un job resté `en_cours` (thread mort) bloquait toute nouvelle tâche jusqu'au
redémarrage. **Correctif:** au-delà de 30 minutes il passe en `interrompu`
(jamais relancé). Test: `test_job_bloque_ne_condamne_pas_le_creneau`.

#### C-13 — Modèle et tarifs · **Faible** · corrigé
Le réglage `modele` n'était validé qu'au moment de l'appel; il l'est maintenant
à l'enregistrement. Tarifs de `claude-sonnet-5` corrigés (2 / 10 et non
3 / 15), `claude-sonnet-5-5` et `claude-opus-5-5` ajoutés.

#### C-14 — Lots déjà payés perdus si un lot échouait · **Faible** · corrigé
Une erreur au lot 3 faisait échouer toute la génération. Maintenant le lot est
marqué échoué (aucune nouvelle tentative), les lots réussis sont gardés, et un
refus de budget arrête la boucle (`ingest.py:113-145`).

#### Constats non corrigés (Partie A)

| ID | Gravité | Constat | Pourquoi laissé |
|---|---|---|---|
| C-15 | Info | `.env` contient `OpenRouter_API_KEY`, pas `ANTHROPIC_API_KEY`. **Mis à jour 2026-10-11:** la clé OpenRouter sert maintenant au juge des réponses libres (`llm.juger_libre` / `juger_redaction`, même budget); extraction/génération restent Anthropic. `config.load_api_key` n'est appelé nulle part: le CLI ne lit jamais `.env`. `[pricing]` de `config.toml` n'est plus lu. | Décision de Nate (voir plus bas). |
| C-16 | Info | Sur Sonnet 5 la réflexion du modèle est comptée dans `max_tokens` (4 000 / 8 000): risque de réponse tronquée. Une troncature coûte au plus 0,04 / 0,08 USD et n'est pas rejouée. | À mesurer au premier appel réel. |
| C-17 | Info | La matière est renvoyée en entier à chaque lot. Le cache de prompt la rendrait ~10 fois moins chère dès le 2e lot, mais demande de sortir les concepts du prompt système. | Interdit sans demande (`prompts/*.md`, règle 8). |
| C-18 | Info | Pas d'estimation préalable dans l'interface pour un PDF ou des photos. | Le serveur refuse de lui-même avant tout appel. |

### A4. Scénarios catastrophe testés

| Scénario demandé | Test(s) |
|---|---|
| Schéma invalide à chaque réponse | `test_reponse_invalide_a_chaque_fois_s_arrete_et_reste_comptee` (5 lots → 3 appels, disjoncteur), `test_reponse_tronquee_n_est_pas_rejouee`, `test_schema_invalide_ne_boucle_pas` |
| Double soumission / double clic / deux onglets | `test_double_clic_ne_lance_qu_une_seule_tache` (+ fichier, + générer), `test_deux_demandes_identiques_en_meme_temps_un_seul_appel`, `test_deux_jobs_differents_en_meme_temps_refuses` |
| Redémarrage pendant un job | `test_redemarrage_ne_relance_aucun_job`, `test_appel_coupe_en_plein_vol_garde_son_cout` |
| PDF et images hors limites | `test_fichier_trop_lourd_rejete`, `test_trop_d_images_rejete`, `test_pdf_illisible_rejete_sans_appel`, `test_envoi_total_trop_lourd_rejete`, `test_texte_trop_long_rejete_sans_job` |
| Plafond journalier atteint | `test_plafond_journalier_atteint_zero_appel_message_clair`, `test_plafond_atteint_bloque_l_ingestion` |
| Erreurs 4xx / crédit | `test_erreur_4xx_ou_credit_un_seul_appel_cout_nul` (7 statuts), `test_aucun_retry_sur_4xx_ni_erreur_de_credit` |
| Route LLM sans session | `test_route_llm_refusee_sans_session`, `test_toute_route_api_exige_une_session`, `test_arret_d_urgence_exige_une_session` |
| Même fichier envoyé deux fois | `test_meme_fichier_envoye_deux_fois_un_seul_appel`, `test_meme_entree_deux_fois_un_seul_appel` |

Une décision à connaître: la consigne autorisait 1 nouvelle tentative sur échec
de validation de schéma. J'ai gardé **0**. Avec les sorties structurées, une
réponse non conforme vient presque toujours d'une troncature, que rejouer ne
corrige pas.

---

## Partie B — Sécurité

### Authentification, sessions, contrôle d'accès

#### S-01 — XSS stockée par le SVG du modèle · **Critique** · corrigé
`frontend/src/composants/Gabarits.tsx:403` (avant):

```tsx
<div dangerouslySetInnerHTML={{ __html: payload.schema_svg }} />
```

`schema_svg` vient du modèle (donc d'un PDF piégé par injection de prompt) ou
d'un fichier importé. Un `<svg onload=…>` s'exécutait dans la session de Nate:
lecture de tous les cours, changement de réglages, lancement d'ingestions
payantes.
**Correctif:** rendu en `<img src="data:image/svg+xml,…">` (`Gabarits.tsx:408`):
dans une image, un SVG ne peut ni exécuter de script ni charger de ressource.
Second rempart: CSP `script-src 'self'` (S-04). Test:
`test_aucune_injection_html_brute_dans_le_frontend`.

#### S-02 — Cookie de session falsifiable · **Élevée** · corrigé
`auth.secret_session` renvoyait `"dev-secret-non-securise"` hors production, et
`FLASHCARDS_ENV` valait `development` par défaut. Un serveur lancé sans la
variable (hors Docker) signait donc ses cookies avec un secret public: n'importe
qui fabriquait `{"u": "nate"}` et entrait sans mot de passe. `run.sh` posait
`SESSION_SECRET=dev` avec l'interface ouverte au réseau local.
**Correctif:** plus aucun secret par défaut; sans `FLASHCARDS_ENV=development`
explicite on est en production (secret ≥ 32 caractères obligatoire, vérifié au
démarrage, cookie `Secure`, pas de CORS) — `auth.py:43-88`. `flashcards web serve`
pose le mode développement seulement en écoute locale et refuse `--host 0.0.0.0`
sans `FLASHCARDS_ENV`. `run.sh` tire un secret aléatoire. Tests:
`test_cookie_forge_avec_un_secret_devinable_refuse`,
`test_secret_obligatoire_et_long_hors_developpement`,
`test_serve_refuse_une_ecoute_publique_en_mode_developpement`.

#### S-03 — Écriture sur le disque sans être connecté · **Élevée** · corrigé
FastAPI lit et met en tampon le corps d'une requête avant de résoudre les
dépendances: `POST /api/ingest/fichiers` sans cookie écrivait tout l'envoi dans
`/tmp` avant de répondre 401. Aucune limite de taille côté Caddy.
**Correctif:** garde global `app._garde` (`app.py:99`), exécuté avant la lecture
du corps: session exigée sur tout `/api/*` sauf `/login`, `/logout`, `/health`
(refus par défaut: une route ajoutée sans dépendance reste protégée), et
`Content-Length` ≤ 32 Mo. Caddy: `request_body max_size 32MB`. Tests:
`test_toute_route_api_exige_une_session` (toutes les routes déclarées),
`test_route_oubliee_sans_dependance_reste_protegee`,
`test_envoi_de_fichier_anonyme_refuse_avant_lecture`.

#### S-04 — Aucune politique CSP · **Élevée** · corrigé
**Correctif:** `Caddyfile` — `default-src 'self'; script-src 'self'; style-src
'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src
'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors
'none'`, plus `Permissions-Policy`, `Cross-Origin-Opener-Policy`, `-Server`.
Vérifié sur le build: aucun script en ligne dans `dist/index.html`.
`'unsafe-inline'` ne reste que pour les styles (KaTeX). L'API pose
`default-src 'none'` et `Cache-Control: no-store` sur chaque réponse.

#### S-05 — Anti-force-brute inopérant derrière Caddy · **Élevée** · corrigé
Le compteur était indexé sur `request.client.host`, qui derrière Caddy est
l'adresse du conteneur Caddy pour tout le monde. N'importe qui pouvait
verrouiller Nate dehors en 5 essais toutes les 15 minutes, indéfiniment.
**Correctif:** uvicorn lancé avec `--proxy-headers --forwarded-allow-ips '*'`
(sûr car seul Caddy atteint le port 8000), seuil global de 50 échecs / 15 min
contre une attaque répartie, même frein sur le changement de mot de passe,
échecs journalisés sans le mot de passe saisi.

#### S-06 — Sessions non révocables · **Moyenne** · corrigé
Cookie signé valable 30 jours, sans état serveur: la déconnexion n'effaçait que
le cookie local, changer le mot de passe ne déconnectait personne.
**Correctif:** le cookie embarque une empreinte du hash du mot de passe
(`auth.version_session`, `auth.py:95`), revérifiée à chaque requête. Changer le
mot de passe invalide toutes les autres sessions. Test:
`test_changer_le_mot_de_passe_deconnecte_les_autres_appareils`.

#### S-08 — CSRF reposant sur `SameSite=Lax` seul · **Moyenne** · corrigé
Les routes `multipart` sont des « requêtes simples »: seule `SameSite=Lax` les
protégeait, ce qui ne couvre pas un sous-domaine voisin. **Correctif:** en
production, toute écriture dont l'en-tête `Origin` ne correspond pas à l'hôte,
ou marquée `Sec-Fetch-Site: cross-site`, est refusée (403).

#### S-11 — Mot de passe · **Moyenne** · corrigé
Minimum relevé de 8 à 12 caractères; maximum de 256 (argon2 hachait n'importe
quelle taille, sur une route publique); un identifiant non ASCII faisait planter
`secrets.compare_digest` (erreur 500).

### Injections, uploads, contenu généré

#### S-09 — Lecture de fichier hors du dossier des profils · **Moyenne** · corrigé
`profiles.chemins_candidats` construisait `profiles/<cours>.md` avec un nom de
cours venu de l'API. `cours = "/chemin/absolu/x"` faisait lire
`/chemin/absolu/x.md` et l'envoyait au modèle. **Correctif:** seul un nom de
fichier nu est accepté (`profiles.py:56`).

**Vérifié sans défaut:**
- **SQL**: requêtes paramétrées partout. Deux f-strings (`app.py`, `liste_cartes`
  et `_ecrire_job`) n'interpolent que des noms de colonnes écrits dans le code.
- **Commandes système / SSRF**: aucun `subprocess`, `eval`, `pickle`, ni
  récupération d'URL.
- **Uploads**: nom du client jamais utilisé comme chemin (UUID), signature réelle
  vérifiée, taille bornée, stockage hors racine web, rien d'exécutable.
- **Injection de prompt**: la sortie du modèle est validée par schéma strict
  (taxonomie fermée), `concept_id` revérifié contre la liste validée, jamais
  utilisée pour choisir un modèle, un chemin, une commande ou une requête.
  Risque résiduel: un document piégé peut faire générer des questions fausses.
  C'est la validation des concepts par Nate qui le couvre.
- **KaTeX**: option `trust` désactivée; le texte hors formule est inséré en
  nœud texte.
- **PWA**: `/api/*` en `NetworkOnly`, jamais mis en cache; rien dans
  `localStorage`.

### Secrets, configuration, déploiement

- **Historique git**: le dépôt n'a aucun commit, donc aucun secret dans
  l'historique. `.env`, `*.db`, `uploads/`, `backups/`, `inbox/`, `sorties/`
  sont ignorés. Aucune clé dans le code ni dans les logs.
- **S-12 · Moyenne · corrigé** — `.env` et la base étaient en `644`, les
  sauvegardes et les cours envoyés aussi. Maintenant `600` / `700`
  (`.env` passé en `600` sur cette machine), `umask 077` dans le conteneur.
- **S-07 · Moyenne · corrigé en partie** — le conteneur tournait en root, sans
  `.dockerignore`, avec `caddy:2-alpine` flottant. Maintenant: utilisateur `app`
  (uid 10001), `cap_drop: ALL`, `no-new-privileges`, limites mémoire et
  processus, `caddy:2.11.7-alpine`, deux `.dockerignore`, `profiles/` copié dans
  l'image (il manquait). **Reste:** épingler les images par empreinte.
- **S-10 · Moyenne · corrigé** — `/openapi.json` restait servi; désactivé.
- **S-13 · Moyenne · corrigé** — les fichiers de cours envoyés n'étaient jamais
  supprimés; purge après 7 jours.
- **S-14 · Faible · corrigé** — connexions échouées, refus d'origine, arrêt
  d'urgence et changement de mot de passe sont journalisés (`flashcards.securite`).
- **S-15 · Faible · corrigé** — `.env.example` était référencé partout mais
  absent.
- HTTPS et redirection HTTP → HTTPS: assurés par Caddy; HSTS présent. Seul
  Caddy publie des ports.

### Dépendances

`pip-audit` (sur `uv.lock`): **aucune vulnérabilité connue**. `npm audit`:
**0 vulnérabilité**. Versions figées par `uv.lock` (`uv sync --frozen`) et
`package-lock.json` (`npm ci`). En retard: `vite` 8.3.2 → 8.3.3, et deux
majeures disponibles (`typescript` 7, `@types/node` 26) sans enjeu de sécurité.

### Constats non corrigés (Partie B)

| ID | Gravité | Constat | Correctif |
|---|---|---|---|
| S-16 | Faible | Compteur anti-force-brute en mémoire: remis à zéro au redémarrage. | Table SQLite `tentatives_connexion`. |
| S-17 | Faible | Sauvegardes sur le même volume que la base, non chiffrées. | `rsync` quotidien vers une autre machine (voir `DEPLOY.md` §7). |
| S-18 | Faible | `passlib` n'est plus maintenu. | Utiliser `argon2-cffi` directement (hashes compatibles). |
| S-19 | Faible | 3 erreurs `oxlint` « hook appelé conditionnellement » dans `Gabarits.tsx:267,428,474` (robustesse, antérieures à l'audit). | Remonter les `useState` avant les retours anticipés. |
| S-20 | Faible | Images Docker épinglées par étiquette, pas par empreinte. | `docker buildx imagetools inspect <image>` puis `FROM image@sha256:…`. |
| S-21 | Info | Session de 30 jours, pas de second facteur. | Acceptable en mono-utilisateur avec un mot de passe long. |
| S-22 | Info | `@app.on_event("startup")` est déprécié par FastAPI. | Passer à `lifespan`. |

---

## Feuille de route

1. **Avant toute mise en ligne** (Nate): les actions de la section suivante.
2. **Au premier `docker compose up --build`**: vérifier que le backend démarre
   en utilisateur `app`, que `caddy validate` accepte le `Caddyfile`, et que la
   console du navigateur n'affiche aucune violation CSP.
3. **Au premier appel réel**: un texte court, puis `flashcards budget`; comparer
   le coût réel au coût estimé, et surveiller les troncatures (C-16).
4. **Ensuite, par ordre d'intérêt**: S-17 (sauvegardes hors serveur), C-17
   (cache de prompt, si les générations en plusieurs lots sont trop souvent
   refusées), S-16, S-18, S-19, S-20.

### Ce que Nate doit faire lui-même

1. **Choisir le fournisseur.** `.env` contient une clé OpenRouter, que le code
   n'utilise pas. Soit obtenir une clé Anthropic (`ANTHROPIC_API_KEY`), soit
   demander le support d'OpenRouter (changement dans `llm.py` seulement).
2. **Sur la console du fournisseur: désactiver le rechargement automatique et
   fixer une limite de dépense mensuelle** au niveau du compte ou de l'espace
   de travail. C'est le seul plafond que le code ne peut pas contourner, même
   avec un bug.
3. Générer `SESSION_SECRET` (`openssl rand -hex 32`), choisir un mot de passe de
   12 caractères ou plus, vider `ADMIN_PASSWORD` après le premier démarrage.
4. Mettre en place la copie des sauvegardes hors du serveur.
5. Faire un premier commit seulement après `git status` (rien de sensible ne
   doit apparaître).

---

## Pire scénario chiffré (A5)

Hypothèses: modèle par défaut `claude-sonnet-5` (2 USD par million de tokens en
entrée, 10 en sortie), plafonds par défaut, un PDF de 60 pages ≈ 180 000 tokens
réels en entrée.

### Avec les garde-fous

Le coût compté ne dépasse jamais un plafond: chaque appel est réservé à son
coût estimé avant de partir. Le seul dépassement possible est l'écart entre
estimation et réalité sur **le dernier appel accepté**. La sortie est bornée
exactement par `max_tokens`. L'entrée est estimée avec une marge de 25 %; dans
le pire cas (pages très denses, ~6 000 tokens réels par page contre 3 750
estimés) un appel estimé à 0,50 USD peut en coûter environ 0,80.

| Période | Maximum compté | Maximum réel, pire cas |
|---|---|---|
| 1 heure | 1,00 USD | ≈ 1,30 USD |
| 1 jour | 1,00 USD | ≈ 1,30 USD |
| 1 mois | 5,00 USD | ≈ 5,30 USD |

Sur une heure le plafond du jour s'applique déjà: 10 appels par minute au plus,
et le disjoncteur coupe après 3 échecs. Ce maximum suppose que quelqu'un
déclenche des appels: rien ne part tout seul.

### Sans aucun garde-fou (état décrit dans `CONTEXTE.md` avant `budget.py`)

Une boucle qui renvoie le même PDF de 60 pages, un appel toutes les 30
secondes: 0,36 USD d'entrée + 0,08 de sortie = **0,44 USD par appel**.

| Période | Sonnet 5 | Opus 5 (5 / 25 USD) |
|---|---|---|
| 1 heure (120 appels) | ≈ 53 USD | ≈ 132 USD |
| 1 jour | ≈ 1 270 USD | ≈ 3 170 USD |
| 1 mois | tout le solde du compte | tout le solde du compte |

En pratique la perte s'arrête au solde prépayé, d'où l'importance de ne pas
activer le rechargement automatique.

### Avec les garde-fous tels que trouvés (avant cet audit)

- C-01: chaque double clic doublait le coût de l'action.
- C-02: des appels en échec mais facturés échappaient aux plafonds, jusqu'à 6
  par heure à 0,50 USD, soit **≈ 3 USD/heure et 72 USD/jour** tant que quelque
  chose les déclenchait.
- C-05: une génération de 40 concepts sur un gros PDF pouvait atteindre le
  plafond du jour en une seule action.

---

## Checklist de sécurité avant publication

- [ ] Limite de dépense fixée et rechargement automatique désactivé chez le
      fournisseur de l'API.
- [ ] `.env` en `600`, jamais commité; `SESSION_SECRET` de 64 caractères
      hexadécimaux; `ADMIN_PASSWORD` vidé après le premier démarrage.
- [ ] `FLASHCARDS_LLM_REEL=0` tant que le premier essai n'est pas décidé.
- [ ] `docker compose config` sans erreur; `docker compose exec backend id`
      affiche `uid=10001(app)`.
- [ ] `docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile`.
- [ ] Pare-feu: seuls 22, 80 et 443 ouverts; `curl http://IP:8000` échoue
      depuis l'extérieur.
- [ ] `curl -sI https://DOMAINE/` montre `content-security-policy`,
      `strict-transport-security`, `x-frame-options`; `http://` redirige vers
      `https://`.
- [ ] `curl -s https://DOMAINE/api/me` répond 401;
      `curl -s https://DOMAINE/openapi.json` ne renvoie pas de schéma.
- [ ] Connexion, changement de mot de passe, puis vérification qu'un autre
      appareil est déconnecté.
- [ ] Réglages → Crédit API affiche « Mode simulé »; le bouton d'arrêt
      d'urgence fonctionne.
- [ ] Une sauvegarde restaurée sur une autre machine s'ouvre.
- [ ] `uv run pytest` et `npx playwright test` verts sur la version déployée.
