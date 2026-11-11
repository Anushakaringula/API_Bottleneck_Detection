# Target API image for the experiment lab (Phase 3.5C-2). Runs on the lab's Docker network
# next to PostgreSQL and Redis (infrastructure/docker-compose.yml, service "api"), so database
# and cache calls do not cross Docker Desktop's host port forwarding.
# Node 20 = the documented runtime; pinned by digest like the lab's other images.
FROM node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY instrumentation.mjs ./
COPY src ./src
COPY scripts ./scripts

ENV NODE_ENV=production \
    PORT=3000 \
    METRICS_PORT=9464 \
    ABR_DATA_DIR=/data
EXPOSE 3000 9464
# Unprivileged user (uid 1000); telemetry goes to the /data bind mount.
USER node
CMD ["node", "--import", "./instrumentation.mjs", "./src/server.js"]
