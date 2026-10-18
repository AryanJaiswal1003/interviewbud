# Build the SPA, then serve it (and route /api, /ws) with Caddy, which also gets the HTTPS certificates.
FROM node:22-slim AS build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM caddy:2.10-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /web/dist /srv
