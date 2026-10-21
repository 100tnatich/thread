#!/usr/bin/env bash
# Lance l'appli web complète (API sur PORT_API + interface sur PORT_WEB, lus dans .env). Ctrl+C arrête tout.
# Conventions: ~/infra/CONVENTIONS.md §3.2 (ports) et §7 (run.sh).
#   ./run.sh           extraction/génération simulées (aucun crédit Anthropic).
#                      Correction par IA des réponses libres et rédactions: RÉELLE
#                      (OpenRouter) si .env a OPENROUTER_API_KEY et FLASHCARDS_JUGE_REEL=1.
#   ./run.sh --real    + vraie API Claude pour l'extraction/génération (ANTHROPIC_API_KEY)
#   ./run.sh --simule  tout simulé, correction par IA comprise (aucune dépense possible)
#   ./run.sh --docker  le montage de production (docker compose up --build, réseau Docker `web`)
#   LAN=1 ./run.sh     idem dev, mais joignable depuis le réseau local (test téléphone); sinon 127.0.0.1
# Lancé avec `. run.sh` / `source run.sh`, le script s'exécuterait dans ton shell
# (set -e, exit, trap, kill 0 s'appliqueraient au terminal). On le relance donc
# dans un bash séparé, et on rend la main proprement.
if [[ "${BASH_SOURCE[0]:-$0}" != "$0" ]]; then
  bash "${BASH_SOURCE[0]}" "$@"
  return $?
fi

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [[ "${1:-}" == "--docker" ]]; then
  command -v docker >/dev/null || { echo "docker introuvable."; exit 1; }
  docker network inspect web >/dev/null 2>&1 || { echo "Réseau absent: docker network create web"; exit 1; }
  exec docker compose up --build
fi

export PATH="$HOME/.local/bin:$PATH"
export FLASHCARDS_ENV=development
# Secret de session aléatoire à chaque lancement: l'interface est ouverte au
# réseau local (--host), un secret connu comme « dev » permettrait à n'importe
# quel appareil du réseau de fabriquer un cookie valide.
export SESSION_SECRET="${SESSION_SECRET:-$(head -c 32 /dev/urandom | base64 | tr -d '/+=\n')}"
# Valeur d'une variable de .env (nom insensible à la casse: `OpenRouter_API_KEY`
# convient), sans sourcer le fichier ni rien afficher. Vide si absente.
lire_env() {
  [[ -f .env ]] || return 0
  { grep -iE "^$1=" .env || true; } | tail -1 | cut -d= -f2- | sed -E "s/^[\"']//; s/[\"']\$//"
}

# --- Ports: viennent de .env et doivent suivre la formule du numéro d'appli (conventions §3.2) ----
NN="$(lire_env APP_NUMBER)"
[[ "$NN" =~ ^[0-9]{2}$ ]] || { echo "APP_NUMBER invalide dans .env (2 chiffres, alloué dans ~/infra/JOURNAL.md)."; exit 1; }
nn=$((10#$NN))
# nom_variable:base — le port attendu est base + NN (ex. PORT_API=8100+01=8101)
declare -A BASES=([PORT_API]=8100 [PORT_WEB]=5200 [PORT_E2E_WEB]=5300 [PORT_E2E_API]=8300 [PORT_TOOL]=9100)
for var in "${!BASES[@]}"; do
  val="$(lire_env "$var")"
  [[ -z "$val" ]] && continue
  attendu=$(( ${BASES[$var]} + nn ))
  if [[ "$val" != "$attendu" ]]; then
    echo "$var=$val ne respecte pas la formule (attendu $attendu pour l'appli $NN). Voir ~/infra/CONVENTIONS.md §3.2."
    exit 1
  fi
done
PORT_API="$(lire_env PORT_API)"
PORT_WEB="$(lire_env PORT_WEB)"
[[ -n "$PORT_API" && -n "$PORT_WEB" ]] || { echo "PORT_API / PORT_WEB manquants dans .env (voir .env.example)."; exit 1; }
export PORT_API PORT_WEB   # PORT_WEB sert au CORS de développement du backend
HOST=127.0.0.1
[[ "${LAN:-0}" == "1" ]] && HOST=0.0.0.0

if [[ "${1:-}" == "--real" ]]; then
  unset MOCK_LLM
  # La clé n'est lue dans .env que sur demande explicite du mode réel.
  if [[ -z "${ANTHROPIC_API_KEY:-}" && -f .env ]]; then
    ANTHROPIC_API_KEY="$(grep -E '^ANTHROPIC_API_KEY=' .env | tail -1 | cut -d= -f2-)"
    export ANTHROPIC_API_KEY
  fi
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "Mode réel demandé mais ANTHROPIC_API_KEY est vide (ni exportée, ni dans .env)."
    exit 1
  fi
  export FLASHCARDS_LLM_REEL=1
  echo "⚠ Mode RÉEL: les appels LLM consomment des crédits (plafonds: flashcards budget)."
elif [[ "${1:-}" == "--simule" ]]; then
  export MOCK_LLM=1
  echo "Mode simulé partout: aucune dépense possible, corrections factices."
else
  # Extraction/génération: simulées sauf `--real`. Épinglé ici, sinon un .env qui
  # contiendrait FLASHCARDS_LLM_REEL=1 activerait la vraie API sans qu'on l'ait demandé.
  unset MOCK_LLM
  export FLASHCARDS_LLM_REEL=0
fi

# Correction par IA (OpenRouter): l'appli ne lit pas .env d'elle-même, c'est ce
# script qui transmet les trois variables du juge. Ni en `--simule`, ni sans les DEUX
# conditions (clé ET FLASHCARDS_JUGE_REEL=1): une clé seule ne dépense rien.
if [[ "${1:-}" != "--simule" ]]; then
  cle_openrouter="$(lire_env OPENROUTER_API_KEY)"
  if [[ -n "$cle_openrouter" && "$(lire_env FLASHCARDS_JUGE_REEL)" == "1" ]]; then
    export OPENROUTER_API_KEY="$cle_openrouter"
    export FLASHCARDS_JUGE_REEL=1
    modele_juge="$(lire_env FLASHCARDS_JUGE_MODELE)"
    [[ -z "$modele_juge" ]] || export FLASHCARDS_JUGE_MODELE="$modele_juge"
    echo "Correction par IA: ACTIVE (OpenRouter, payante, plafonnée: voir Réglages)."
  else
    echo "Correction par IA: inactive (auto-évaluation). Pour l'activer: OPENROUTER_API_KEY et FLASHCARDS_JUGE_REEL=1 dans .env."
  fi
fi
unset cle_openrouter

command -v uv >/dev/null || { echo "uv introuvable (attendu dans ~/.local/bin)."; exit 1; }
command -v npm >/dev/null || { echo "npm introuvable."; exit 1; }

# Dépendances (rapide si déjà à jour)
uv sync --quiet
[[ -d frontend/node_modules ]] || (cd frontend && npm install)

# Ports déjà occupés ? Cause habituelle: un ancien ./run.sh dont le terminal a été fermé
# (ou tué) a laissé uvicorn/vite tourner en arrière-plan. Si le processus vient de CE
# projet, on l'arrête tout seul; sinon on dit qui l'occupe et on s'arrête.
pids_du_port() {
  lsof -t -iTCP:"$1" -sTCP:LISTEN 2>/dev/null \
    || ss -ltnpH "sport = :$1" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 \
    || true
}
est_a_nous() {  # le processus tourne-t-il depuis ce dossier ?
  local cwd
  cwd="$(readlink "/proc/$1/cwd" 2>/dev/null || true)"
  [[ "$cwd" == "$PWD" || "$cwd" == "$PWD"/* ]]
}
liberer_port() {
  local port="$1" pids pid
  pids="$(pids_du_port "$port" | sort -u | tr '\n' ' ')"
  if [[ -z "${pids// /}" ]]; then
    # Rien côté Linux. Le port répond pourtant ? Alors c'est Windows qui le tient
    # (WSL2 partage « localhost » avec Windows): règle `netsh portproxy` oubliée.
    if (echo >/dev/tcp/127.0.0.1/"$port") 2>/dev/null; then
      echo "Le port $port est tenu par Windows (pas par un processus Linux): impossible de continuer."
      echo "Voir: netsh interface portproxy show all   (PowerShell en administrateur pour supprimer la règle)."
      exit 1
    fi
    return 0
  fi
  for pid in $pids; do
    if est_a_nous "$pid"; then
      echo "Port $port occupé par un ancien lancement ($(ps -o comm= -p "$pid"), pid $pid): arrêt."
      kill "$pid" 2>/dev/null || true
    else
      echo "Le port $port est utilisé par un programme étranger au projet:"
      ps -o pid=,args= -p "$pid" | cut -c1-120
      echo "Arrête-le (kill $pid) ou ferme l'application concernée, puis relance."
      exit 1
    fi
  done
  for _ in $(seq 1 25); do   # attend la libération (5 s), puis force
    [[ -z "$(pids_du_port "$port")" ]] && break
    sleep 0.2
  done
  for pid in $(pids_du_port "$port"); do kill -9 "$pid" 2>/dev/null || true; done
  sleep 0.3
  [[ -z "$(pids_du_port "$port")" ]] || { echo "Impossible de libérer le port $port."; exit 1; }
}
for port in $PORT_API $PORT_WEB; do liberer_port "$port"; done

# Compte web: création interactive s'il n'existe pas encore
a_un_compte=$(uv run python - <<'PY'
import sqlite3
from flashcards.config import load_scheduler_config
try:
    c = sqlite3.connect(load_scheduler_config().db_path)
    print(c.execute("SELECT COUNT(*) FROM web_user").fetchone()[0])
except sqlite3.Error:
    print(0)
PY
)
if [[ "$a_un_compte" == "0" ]]; then
  echo "Aucun compte: création (identifiant nate)."
  uv run flashcards web create-user --identifiant nate
  uv run flashcards web seed-demo
fi

# Ctrl+C / fermeture: tue API et interface (et leurs processus enfants)
# HUP inclus: fermer le terminal laissait sinon uvicorn et vite orphelins (cause du « port occupé »).
trap 'trap - INT TERM HUP EXIT; kill 0' INT TERM HUP EXIT

uv run flashcards web serve --host "$HOST" --port "$PORT_API" --reload &
(cd frontend && HOST="$HOST" npm run dev) &   # port (PORT_WEB) et strictPort: voir frontend/vite.config.ts

echo
echo "→ Ouvre http://localhost:$PORT_WEB   (Ctrl+C pour tout arrêter)"
wait
