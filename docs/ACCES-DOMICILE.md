# Accès depuis internet, depuis la maison (WSL + Docker)

> **Mis à jour 2026-10-21 (conventions v2).** Le Caddy, le HTTPS et le DNS dynamique ne sont plus dans ce projet:
> ils sont tenus par le **gateway** (`~/infra/gateway`), commun à toutes les applis. Les sections « DNS dynamique » et
> « Lancer » ci-dessous décrivent l'ancien montage; les parties **routeur (80/443), Windows (mirrored, pare-feu)**
> et **dépannage** restent valables telles quelles. Montage actuel et bascule: `DEPLOY.md` et `~/infra/CONVENTIONS.md` §4.

But: `https://thread.nategroup.ch` joint l'appli qui tourne sur ton PC, le temps
des tests. Le même montage sert ensuite pour le vrai serveur (seule la cible du
port forwarding change).

```
téléphone (4G) → thread.nategroup.ch → IP de ta box → routeur (80/443)
   → PC Windows 192.168.1.200 → WSL (mode mirrored) → Caddy → appli
```

## 0. Avant de commencer

- **Coupe ProtonVPN pendant les tests.** Avec le VPN, l'IP « publique » vue par le
  PC est celle du VPN: le DNS dynamique pointerait au mauvais endroit.
- **Vérifie que ta box a une vraie IP publique** (pas de CGNAT). Compare l'« IP WAN »
  affichée dans l'interface du routeur avec celle de https://api.ipify.org (VPN coupé).
  Si elles diffèrent, ou si la WAN est en `100.64.x.x`, `10.x` ou `192.168.x`: l'ouverture
  de ports est impossible chez ton fournisseur (demande une IP publique, souvent gratuite
  ou payante en option).
- Pas d'IPv6 vue depuis WSL: on fait de l'IPv4 seulement (`ip_version: ipv4`).

## 1. DNS dynamique chez Infomaniak (dans le Manager, à la main)

Le domaine doit utiliser la zone DNS d'Infomaniak. Manager → **Domaines** → `nategroup.ch`
→ **DNS dynamique** → **Ajouter un DNS dynamique**:

- Sous-domaine: `thread`
- Adresse IP: ton IP publique actuelle (VPN coupé)
- Identifiant / mot de passe: au choix, **lettres et chiffres seulement**

Reporte-les dans `.env` (`DDNS_USER`, `DDNS_PASSWORD`). Le conteneur `ddns` met
l'enregistrement à jour toutes les 5 minutes.

## 2. Routeur: redirection de ports

Réserve d'abord une **adresse fixe** (réservation DHCP) pour le PC: `192.168.1.200`.
Puis redirige vers cette IP:

| Protocole | Port externe | Port interne |
|---|---|---|
| TCP | 80 | 80 |
| TCP | 443 | 443 |
| UDP | 443 | 443 (facultatif, HTTP/3) |

Le port 80 doit rester ouvert: Let's Encrypt s'en sert pour valider le domaine et
renouveler le certificat. Ne redirige **rien d'autre** (pas le 8000, pas le SSH).

## 3. Windows: réseau WSL en « mirrored » + pare-feu

Crée/complète `C:\Users\<toi>\.wslconfig` (garde tes lignes existantes):

```ini
[wsl2]
networkingMode=mirrored
```

Puis dans PowerShell: `wsl --shutdown` et relance WSL. WSL partage alors l'IP et les ports
du PC: pas de `netsh portproxy` à refaire à chaque redémarrage.
Ensuite, PowerShell **administrateur**, une fois:

```powershell
powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\nate\thread\scripts\windows-exposer.ps1
```

Les ports 80/443 ne doivent pas être pris côté Windows (IIS, Skype, etc.):
`Get-NetTCPConnection -State Listen -LocalPort 80,443` ne doit rien afficher.

## 4. Lancer

Dans `.env` (déjà posé: `DOMAIN`, `ACME_EMAIL`): `COMPOSE_PROFILES=ddns`,
`DDNS_USER`, `DDNS_PASSWORD`, et **pour le premier essai** la ligne `ACME_CA=…staging…`.

```bash
docker compose up -d --build
docker compose logs -f ddns     # doit dire que l'IP est mise à jour
dig +short thread.nategroup.ch  # doit renvoyer l'IP de ta box (sudo apt install dnsutils)
docker compose logs -f caddy    # doit montrer « certificate obtained successfully »
```

Quand le staging fonctionne: retire `ACME_CA` de `.env`, puis
`docker compose up -d --force-recreate caddy` (certificat réel).

## 5. Tester

- **Depuis ton téléphone en 4G** (wifi coupé): `https://thread.nategroup.ch`.
- Depuis le PC ou le wifi de la maison, ça peut échouer même si tout est bon
  (« hairpin NAT » de certains routeurs). Ce n'est pas un signe de panne.
- `curl -s https://thread.nategroup.ch/api/me` doit répondre 401.

## Dépannage

| Symptôme | Piste |
|---|---|
| `dig` ne renvoie rien / mauvaise IP | VPN actif au moment de la mise à jour; logs `ddns`; identifiants DynDNS |
| Caddy: erreur de validation (timeout) | Port 80 non redirigé, CGNAT, ou pare-feu Windows/Hyper-V |
| Joignable en 4G, pas en wifi | Hairpin NAT, normal |
| Plus joignable après redémarrage | IP du PC changée (réservation DHCP), WSL arrêté, Docker non démarré |
| Erreur « too many failed authorizations » | Tu es passé en certificat réel trop tôt: reviens au staging |

## Sécurité pendant l'exposition

L'appli est publique: mot de passe admin **fort** (12 car. minimum, imposé), `ADMIN_PASSWORD`
vidé après le premier login, clés API absentes ou plafonnées chez le fournisseur. Les en-têtes,
l'anti-force brute et la CSP sont déjà en place (voir `AUDIT.md`). Coupe la redirection du
routeur quand tu ne testes pas.
