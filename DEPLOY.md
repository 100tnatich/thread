# Déploiement de Thread

Thread suit les conventions de **`~/infra/CONVENTIONS.md`** (appli n°01): aucun Caddy public, DDNS ni `ACME_*` ici, aucun port publié.
Il fournit deux conteneurs: **`thread-web`** (Caddy 8080: frontend + `/api` → `thread-api`, CSP, non-root) et **`thread-api`** (FastAPI,
réseau interne seulement). Le **Caddy central** (`~/infra/gateway/`) joint `thread-web:8080` par le réseau Docker `web` et gère HTTPS + DNS dynamique.

| Fichier | Rôle |
|---|---|
| `docker-compose.yml` | `web` (alias `thread-web`) + `api` (alias `thread-api`, réseau `interne`), volumes `donnees` et `logs` |
| `web/Dockerfile`, `web/Caddyfile` | build du frontend + Caddy statique; CSP, taille des requêtes, `sw.js` jamais en cache |
| `Dockerfile` | image du backend (Python 3.12, uv, utilisateur 10001) |
| `.env` / `.env.example` | identité, ports de dev, secrets, plafonds LLM |
| `~/infra/gateway/sites/thread.caddy` | branche `thread.nategroup.ch` sur `thread-web:8080` |

## Développement

```bash
./run.sh              # http://localhost:5201 (API 8101), ports lus dans .env
./run.sh --docker     # le vrai montage (réseau `web` requis: docker network create web)
```

## Déployer (une fois la bascule faite)

```bash
~/infra/bin/deploy.sh thread          # pull, build, up, healthcheck, sondes (/ et /api/me → 401), conformité
```

## Bascule de la production actuelle vers ce montage — À FAIRE (coupure de quelques minutes)

État actuel: `thread-caddy-1` tient 80/443 et `thread-ddns-1` met à jour `test.nategroup.ch` avec l'**ancien** compose.
Tant que la bascule n'est pas faite, ces conteneurs continuent de tourner (le nouveau compose n'y touche pas).

1. **Sauvegarde** (ancien montage encore en place): `docker compose exec backend flashcards web backup`, et copie du dossier de sauvegardes hors machine.
2. **Fusionner** la branche `conventions-infra` dans `main` (le nouveau compose doit être celui qu'on déploie).
3. **Arrêter l'ancien montage en entier** (libère 80/443 et évite deux backends sur la même base SQLite; le volume `thread_donnees` est conservé,
   c'est le même nom dans le nouveau compose): `docker stop thread-caddy-1 thread-ddns-1 thread-backend-1 thread-frontend-1`,
   puis `docker compose up -d --remove-orphans` (démarre `web` et `api`, retire les anciens conteneurs).
4. **Gateway**: dans `~/infra/gateway/.env`, activer `COMPOSE_PROFILES=ddns` (les identifiants DDNS y sont déjà), puis
   `cd ~/infra/gateway && docker compose up -d`. Le réseau `web` doit exister.
5. **Thread**: `~/infra/bin/deploy.sh thread`.
6. **DNS**: l'enregistrement dynamique s'appelle aujourd'hui `test.nategroup.ch` (ancien `DOMAIN`). Pour servir `thread.nategroup.ch`:
   CNAME `thread` → l'enregistrement DDNS du gateway (ou renommer l'enregistrement). Point ouvert dans `~/infra/JOURNAL.md`.
7. **Vérifier**: `curl -sI https://thread.nategroup.ch/ | grep -iE 'content-security-policy|strict-transport'` et `curl -s -o /dev/null -w '%{http_code}' https://thread.nategroup.ch/api/me` → 401.
8. **Hub**: `~/infra/bin/publish-app.sh thread --tagline … --description …` (le hub a déjà une entrée `thread`; elle sera mise à jour).

Retour arrière: arrêter le gateway (`docker compose -f ~/infra/gateway/docker-compose.yml down`), `git switch --detach 164bcca` (dernier commit de l'ancien montage),
`docker compose up -d --build --remove-orphans` (rend 80/443 à l'ancien Caddy), puis `git switch main`. Les données (`thread_donnees`) restent intactes.

## Sauvegardes et données

La base SQLite est dans le volume `donnees` (`/data` dans `thread-api`). Sauvegarde quotidienne automatique (14 jours) dans `/data/backups`,
et à la demande: `docker compose exec api flashcards web backup`. **La seule étape vraiment importante**: copier les sauvegardes hors de la machine
et tester une restauration. Voir l'ancienne procédure détaillée dans l'historique git (`git show main:DEPLOY.md`), toujours valable pour la restauration
en remplaçant `backend` par `api`.

## Vérifier la conformité

```bash
~/infra/bin/verify-kit.sh --app .       # doit finir sans ✗ (2 ⚠ = dérogations déclarées)
docker compose ps                       # aucun port publié (pas de « -> »)
```
