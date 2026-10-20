# Backend: API FastAPI + moteur de planification.
# Pour figer l'image de base au bit près:
#   docker buildx imagetools inspect python:3.12-slim   -> FROM python:3.12-slim@sha256:...
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv

WORKDIR /app

# Dépendances d'abord: la couche est réutilisée tant que les lock/pyproject
# ne changent pas. `--frozen`: versions exactes de uv.lock, rien d'autre.
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev

COPY config.toml ./
COPY prompts ./prompts
COPY profiles ./profiles

# Utilisateur sans privilèges: une faille dans l'app (ou dans l'analyse d'un
# PDF piégé) ne donne pas root dans le conteneur. Seuls /data et /app/logs lui
# appartiennent; le code reste en lecture seule pour lui.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data/uploads /data/depot /data/backups /app/logs \
    && chown -R app:app /data /app/logs \
    && chmod 700 /data

ENV PATH="/app/.venv/bin:$PATH" \
    FLASHCARDS_ENV=production \
    FLASHCARDS_DB=/data/flashcards.db \
    FLASHCARDS_UPLOADS=/data/uploads \
    FLASHCARDS_DEPOT=/data/depot \
    FLASHCARDS_BACKUPS=/data/backups

USER app
# Fichiers créés (base, sauvegardes, cours envoyés): lisibles par `app` seul.
EXPOSE 8000

# Un seul worker: SQLite + l'état des tâches de fond vivent dans le process.
# --proxy-headers/--forwarded-allow-ips: l'adresse du visiteur vient de
# X-Forwarded-For posé par Caddy (seul à pouvoir joindre ce port), sinon la
# limitation de la force brute verrait une seule adresse pour tout le monde.
CMD ["sh", "-c", "umask 077 && exec uvicorn flashcards.web.app:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips '*' --no-server-header"]
