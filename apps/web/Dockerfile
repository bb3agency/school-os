# syntax=docker/dockerfile:1.7
# SchoolOS web (Next.js standalone). Build from the repository root:
#   docker build -f apps/web/Dockerfile -t schoolos-web .
# Run read-only:
#   docker run --read-only --tmpfs /tmp -p 3000:3000 schoolos-web

# node:24-bookworm-slim, pinned by digest (update via the weekly dependency PR).
ARG NODE_IMAGE=node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6

# ---------------------------------------------------------------- deps
FROM ${NODE_IMAGE} AS deps
WORKDIR /repo
ENV NEXT_TELEMETRY_DISABLED=1 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    npm_config_fund=false \
    npm_config_audit=false \
    npm_config_update_notifier=false
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages/api-client/package.json packages/api-client/package.json
# Optional extra CA for TLS-intercepting proxies, passed as a build secret so it never
# lands in an image layer:  --secret id=extra_ca,src=/path/to/ca-bundle.crt
RUN --mount=type=cache,target=/root/.npm \
    --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi \
 && npm ci

# ---------------------------------------------------------------- build
FROM deps AS build
COPY apps/web apps/web
COPY packages/api-client packages/api-client
RUN npm run build --workspace @schoolos/web \
 && mkdir -p apps/web/.next/standalone/apps/web/.next \
 && cp -R apps/web/.next/static apps/web/.next/standalone/apps/web/.next/static \
 # Next.js writes its runtime cache here; point it at /tmp for a read-only root fs.
 && rm -rf apps/web/.next/standalone/apps/web/.next/cache \
 && ln -s /tmp/next-cache apps/web/.next/standalone/apps/web/.next/cache

# ---------------------------------------------------------------- runtime
FROM ${NODE_IMAGE} AS runtime
LABEL org.opencontainers.image.title="schoolos-web" \
      org.opencontainers.image.description="SchoolOS web app and BFF (Next.js standalone)" \
      org.opencontainers.image.source="https://github.com/schoolos/school-os"
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /app
# Application files stay root-owned and read-only for the runtime user.
COPY --from=build --chown=root:root /repo/apps/web/.next/standalone ./
# `node` (uid 1000) ships with the official image; never run as root.
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "apps/web/server.js"]
