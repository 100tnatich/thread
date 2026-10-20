# Déploiement sur ton domaine

Cible : un petit VPS Linux (Ubuntu 22.04 ou 24.04), 1 vCPU / 1 Go de RAM
suffisent. Tout tourne derrière Caddy, qui obtient et renouvelle le
certificat HTTPS tout seul.

---

## 1. DNS — faire pointer le sous-domaine vers le serveur

Chez ton registrar (Infomaniak, OVH, Hostpoint…), dans la zone DNS de
`mondomaine.ch`, ajoute **un enregistrement A** :

| Type | Nom      | Valeur              | TTL  |
| ---- | -------- | ------------------- | ---- |
| A    | `cartes` | `203.0.113.42`      | 3600 |

`203.0.113.42` = l'adresse IPv4 de ton VPS. Le nom `cartes` donne
`cartes.mondomaine.ch`.

Si ton VPS a aussi une IPv6, ajoute un enregistrement `AAAA` avec la même
logique.

**Vérifie que ça a pris** (peut prendre de quelques minutes à 1 h) :

```bash
dig +short cartes.mondomaine.ch
# doit afficher l'IP de ton VPS
```

Ne lance pas le déploiement avant que cette commande réponde : Let's Encrypt
a besoin que le domaine pointe déjà vers le serveur pour délivrer le
certificat.

---

## 2. Préparer le serveur

Connecte-toi en SSH, puis :

```bash
# Docker + compose
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# déconnecte-toi et reconnecte-toi pour que le groupe prenne effet

# Pare-feu: n'ouvrir que SSH et le web
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp   # HTTP/3
sudo ufw enable
```

---

## 3. Récupérer le code et configurer

```bash
git clone <url-de-ton-depot> flashcards
cd flashcards

cp .env.example .env
chmod 600 .env        # lisible par toi seul: il contient tes secrets
nano .env
```

Remplis au minimum :

```ini
DOMAIN=cartes.mondomaine.ch
ACME_EMAIL=moi@mondomaine.ch
SESSION_SECRET=<colle ici le résultat de: openssl rand -hex 32>
ADMIN_PASSWORD=<un mot de passe fort, 12 caractères minimum>
```

`SESSION_SECRET` doit faire au moins 32 caractères: le serveur refuse de
démarrer sinon.

`ANTHROPIC_API_KEY` peut rester vide : l'app démarre en **mode simulé** et
tout fonctionne sauf la vraie génération de questions. Tu la rempliras plus
tard (section 9). Tant que `FLASHCARDS_LLM_REEL` ne vaut pas `1`, aucune
dépense n'est possible, même avec une clé.

> `.env` est dans `.gitignore`. Ne le commite jamais.

---

## 4. Premier lancement

```bash
docker compose up -d --build
```

Le premier build prend quelques minutes (image Python + build du frontend).
Ensuite :

```bash
docker compose logs -f caddy      # doit montrer l'obtention du certificat
docker compose logs -f backend    # doit montrer "Compte web créé depuis ADMIN_PASSWORD"
```

Ouvre `https://cartes.mondomaine.ch` : l'écran de connexion s'affiche.
Connecte-toi avec `ADMIN_USER` / `ADMIN_PASSWORD`.

**Juste après la première connexion :**

1. Va dans **Réglages → Mot de passe** et change-le.
2. Vide `ADMIN_PASSWORD` dans `.env` (il n'est utilisé que pour créer le
   compte initial), puis `docker compose up -d` pour appliquer.

### Remplir l'app avec des données de démonstration (optionnel)

```bash
docker compose exec backend flashcards web seed-demo
```

Crée 5 cours, 17 questions et 4 tests fictifs pour voir l'interface remplie.

### Créer le compte à la main (si tu préfères)

```bash
docker compose exec backend flashcards web create-user --identifiant nate
# le mot de passe est demandé de façon interactive, jamais en clair dans l'historique
```

---

## 5. Installer sur le téléphone

Ouvre `https://cartes.mondomaine.ch` dans le navigateur du téléphone, puis :

- **Android / Chrome** : menu ⋮ → « Ajouter à l'écran d'accueil ».
- **iPhone / Safari** : bouton Partager → « Sur l'écran d'accueil ».

L'app s'ouvre alors en plein écran, sans barre d'adresse.

---

## 6. Mettre à jour

```bash
cd flashcards
git pull
docker compose up -d --build
```

Les migrations SQLite s'appliquent automatiquement au démarrage du backend.
Tes données sont dans un volume Docker, elles survivent au rebuild.

---

## 7. Sauvegardes

### Automatiques

Le backend fait une **copie cohérente** de la base chaque jour (API `backup`
de SQLite, pas un simple `cp`), dans `/data/backups` du volume, avec une
rotation sur 14 jours. Rien à configurer.

### Manuelle

```bash
docker compose exec backend flashcards web backup
```

### Copier les sauvegardes hors du serveur

C'est **la seule étape vraiment importante** : une sauvegarde sur la même
machine ne protège pas d'une perte du VPS.

```bash
# Depuis ta machine locale
docker compose exec backend sh -c 'ls -1 /data/backups'
scp -r user@203.0.113.42:/var/lib/docker/volumes/flashcards_donnees/_data/backups ./sauvegardes-flashcards
```

Mets ça dans un cron local hebdomadaire, ou pointe un rsync vers ton NAS.

---

## 8. Restaurer une sauvegarde

```bash
# 1. Arrêter le backend pour qu'il n'écrive plus
docker compose stop backend

# 2. Lister les sauvegardes disponibles
docker compose run --rm backend ls -1 /data/backups

# 3. Mettre de côté la base actuelle (ne jamais écraser directement)
docker compose run --rm backend sh -c \
  'mv /data/flashcards.db /data/flashcards.db.avant-restauration'

# 4. Restaurer celle que tu veux
docker compose run --rm backend sh -c \
  'cp /data/backups/flashcards-20261005-030000.db /data/flashcards.db'

# 5. Redémarrer
docker compose start backend
```

Vérifie que tout est là, puis supprime `flashcards.db.avant-restauration`.

---

## 9. Activer la vraie génération de questions

Quand tu auras une clé API :

**Avant tout**, sur la console du fournisseur de l'API: désactive le
rechargement automatique et fixe une limite de dépense mensuelle. C'est le
seul plafond qui tient même si le code a un bug.

```bash
nano .env
# ANTHROPIC_API_KEY=sk-ant-...
# FLASHCARDS_LLM_REEL=1     <- l'activation explicite; sans elle, mode simulé
# MOCK_LLM=0

docker compose up -d
```

Dans **Réglages → Crédit API**, l'étiquette passe à « Mode réel ». La carte
affiche la dépense du jour et du mois, les alertes à 80 % et un bouton d'arrêt
d'urgence.

### Variables de protection du crédit

Toutes optionnelles; à poser dans `.env`. Vides = valeurs de `config.toml`.

| Variable | Défaut | Rôle |
|---|---|---|
| `FLASHCARDS_LLM_REEL` | `0` | `1` = autorise les appels réels (avec une clé) |
| `MOCK_LLM` | `0` | `1` = force le mode simulé |
| `LLM_DISABLED` | `0` | `1` = interrupteur d'urgence, tout appel est refusé |
| `FLASHCARDS_COUT_MAX_JOUR` | `1.00` | USD par jour (UTC) |
| `FLASHCARDS_COUT_MAX_MOIS` | `5.00` | USD par mois |
| `FLASHCARDS_COUT_MAX_OPERATION` | `0.50` | USD par extraction ou par génération entière |
| `FLASHCARDS_APPELS_MAX_MINUTE` | `10` | cadence |
| `FLASHCARDS_PLAFONDS_ELEVES` | — | `1` requis au-delà de 5 / 20 / 2 USD |

Les quatre dernières lignes du tableau et les limites d'entrée
(`FLASHCARDS_MAX_PAGES_PDF`, etc.) sont détaillées dans le README. Seules les
variables listées dans `docker-compose.yml` atteignent le conteneur: ajoute-y
celles qui manquent si tu en as besoin.

### Couper les appels en urgence

```bash
docker compose exec backend flashcards llm off      # immédiat, sans redémarrage
docker compose exec backend flashcards budget       # où en est la dépense
docker compose exec backend flashcards llm on       # rétablir
```

Ou le bouton « Arrêt d'urgence » dans Réglages. Pour un arrêt que l'interface
ne peut pas lever: `LLM_DISABLED=1` dans `.env`, puis `docker compose up -d`.

---

## Développement local

Deux terminaux, rechargement à chaud des deux côtés :

```bash
# Terminal 1 — API sur :8000
export MOCK_LLM=1 SESSION_SECRET=dev   # ou simplement ./run.sh
uv run flashcards web create-user --identifiant nate   # une seule fois
uv run flashcards web seed-demo                        # données de démo
uv run flashcards web serve --reload

# Terminal 2 — frontend sur :5173 (proxy /api vers :8000)
cd frontend && npm run dev
```

Tests :

```bash
uv run pytest                    # moteur + API
cd frontend && npx playwright test   # parcours de bout en bout
```

---

## Checklist de sécurité avant publication

La liste complète, avec les commandes de vérification, est à la fin de
[`AUDIT.md`](AUDIT.md). Les points qui ne dépendent que de toi:

- [ ] Limite de dépense fixée et rechargement automatique désactivé chez le
      fournisseur de l'API.
- [ ] `.env` en `600`, jamais commité; `SESSION_SECRET` généré par
      `openssl rand -hex 32`; `ADMIN_PASSWORD` vidé après le premier démarrage.
- [ ] `docker compose exec backend id` affiche `uid=10001(app)`.
- [ ] `docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile`.
- [ ] Pare-feu: seuls 22, 80 et 443 ouverts.
- [ ] `curl -sI https://ton-domaine/` montre `content-security-policy` et
      `strict-transport-security`; `curl -s https://ton-domaine/api/me` répond 401.
- [ ] Sauvegardes copiées hors du serveur, et une restauration testée.

> Le conteneur backend tourne sous l'utilisateur `app` (uid 10001). Si tu as
> déjà un volume `donnees` créé par une ancienne image en root:
> `docker compose run --rm --user root backend chown -R app:app /data`.

---

## Dépannage

**Le certificat n'est pas délivré.** Vérifie `dig +short cartes.mondomaine.ch`
et que les ports 80/443 sont ouverts. Let's Encrypt doit pouvoir joindre le
serveur sur le port 80. `docker compose logs caddy` donne la raison exacte.

**« Trop de tentatives » au login.** Protection anti-force brute : 5 échecs
par IP bloquent 15 minutes. Attends, ou redémarre le backend
(`docker compose restart backend`) — le compteur est en mémoire.

**L'app ne se met pas à jour sur le téléphone.** Le service worker garde
l'ancienne version. Ferme complètement l'app et rouvre-la, ou désinstalle et
réinstalle le raccourci.

**Je ne sais plus mon mot de passe.** Recrée le compte :
`docker compose exec backend flashcards web create-user --identifiant nate`.
Les données ne sont pas touchées.
