# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# see-no-evil — ui
#
# Multi-stage:
#   1. builder  — npm ci + vite build
#   2. runtime  — nginx:alpine serves /usr/share/nginx/html
#
# The proxy API URL is set at build time via VITE_API_URL (defaults to /v1).
# In production Caddy handles /v1 → api:8000 at the reverse proxy level.
# -----------------------------------------------------------------------------
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies first (layer-cached separately from source code).
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts

# Copy source and build.
COPY . .
ARG VITE_API_URL=/v1
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# nginx config — SPA routing (all paths → index.html)
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
