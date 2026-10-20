# Frontend: build statique servi par Caddy.
FROM node:24-slim AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# Étape finale: uniquement les fichiers statiques, récupérés par le Caddy du
# compose via un volume partagé.
FROM alpine:3.21
COPY --from=build /app/dist /dist
CMD ["sh", "-c", "cp -r /dist/. /srv/ && echo 'Frontend copié dans /srv' && tail -f /dev/null"]
