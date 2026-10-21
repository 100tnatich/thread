# Conteneur d'entrée de Thread: build du frontend, puis Caddy statique sans privilèges (8080).
# Le contexte de build est la racine du projet (voir docker-compose.yml); ce qui y entre est limité
# par web/Dockerfile.dockerignore (le .dockerignore racine sert à l'image du backend).

FROM node:24-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM caddy:2.11.7-alpine

COPY web/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv

# Le binaire Caddy de l'image officielle porte la capability de fichier
# cap_net_bind_service (ports < 1024). Avec `cap_drop: ALL` + `no-new-privileges`
# le noyau refuse alors de l'exécuter (« exec /usr/bin/caddy: operation not
# permitted »). On écoute en 8080: on la retire.
RUN apk add --no-cache libcap && setcap -r /usr/bin/caddy && apk del libcap

# /data et /config: seuls dossiers inscriptibles de Caddy (en plus de tmpfs).
RUN addgroup -S -g 10001 app && adduser -S -u 10001 -G app app \
    && mkdir -p /data /config && chown -R app:app /data /config
USER app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
