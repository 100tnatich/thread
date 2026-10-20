#!/usr/bin/env bash
# Lance l'appli web complète (API :8000 + interface :5173). Ctrl+C arrête tout.
#   ./run.sh           extraction/génération simulées (aucun crédit Anthropic).
#                      Correction par IA des réponses libres et rédactions: RÉELLE
#                      (OpenRouter) si .env a OPENROUTER_API_KEY et FLASHCARDS_JUGE_REEL=1.
#   ./run.sh --real    + vraie API Claude pour l'extraction/génération (ANTHROPIC_API_KEY)
#   ./run.sh --simule  tout simulé, correction par IA comprise (aucune dépense possible)
# Lancé avec `. run.sh` / `source run.sh`, le script s'exécuterait dans ton shell
# (set -e, exit, trap, kill 0 s'appliqueraient au terminal). On le relance donc
# dans un bash séparé, et on rend la main proprement.
if [[ "${BASH_SOURCE[0]:-$0}" != "$0" ]]; then
  bash "${BASH_SOURCE[0]}" "$@"
  return $?
fi

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

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

# Ports déjà occupés ? Cause habituelle: un ancien ./run.sh dont le terminal a été
# fermé (ou tué) a laissé uvicorn/vite tourner en arrière-plan. Si le processus
# vient de CE projet, on l'arrête tout seul; sinon on dit qui l'occupe et on s'arrête.
# (Docker n'est pas en cause: compose n'expose que 80/443, jamais 5173 ni 8000.)
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
port_front=5173
for port in 8000 5173; do
  pids="$(pids_du_port "$port" | sort -u | tr '\n' ' ')"
  if [[ -z "${pids// /}" ]]; then
    # Rien côté Linux. Le port répond pourtant ? Alors c'est Windows qui le tient
    # (WSL2 partage « localhost » avec Windows): typiquement une règle
    # `netsh interface portproxy` oubliée, que ss/lsof ne voient pas.
    (echo >/dev/tcp/127.0.0.1/"$port") 2>/dev/null || continue
    if [[ "$port" == 5173 ]]; then
      echo "⚠ Le port 5173 est tenu par Windows (probablement une règle portproxy oubliée)."
      echo "  Pour le libérer (PowerShell en administrateur):  netsh interface portproxy delete v4tov4 listenport=5173 listenaddress=0.0.0.0"
      echo "  En attendant, l'interface prendra le port suivant (regarde l'URL « Local » que Vite affiche juste après)."
      port_front=""
      continue
    fi
    echo "Le port $port est tenu par Windows (pas par un processus Linux): impossible de continuer."
    echo "Voir: netsh interface portproxy show all   (PowerShell en administrateur pour supprimer la règle)."
    exit 1
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
  if [[ -n "$(pids_du_port "$port")" ]]; then
    echo "Impossible de libérer le port $port."
    exit 1
  fi
done

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

uv run flashcards web serve --reload &
(cd frontend && npm run dev -- --host) &   # --host: accessible depuis tout le réseau local

echo
if [[ -n "$port_front" ]]; then
  echo "→ Ouvre http://localhost:5173   (Ctrl+C pour tout arrêter)"
else
  echo "→ Ouvre l'adresse « Local » affichée par Vite ci-dessous (≠ 5173)   (Ctrl+C pour tout arrêter)"
fi
wait
