FROM node:22-slim AS builder

WORKDIR /app

# Install dependencies first (layer-cached unless lockfile changes)
COPY package.json package-lock.json ./
RUN npm ci

# Copy source and compile
COPY src/ ./src/
COPY tsconfig.json ./
RUN npm run build

# ---------------------------------------------------------------------------
FROM node:22-slim AS runner

WORKDIR /app

# Non-root user
RUN addgroup --system --gid 1001 gateway \
 && adduser --system --uid 1001 --ingroup gateway gateway

# Production deps only
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY --from=builder /app/dist ./dist

# WhatsApp multi-device auth state, Signal linking data, etc.
VOLUME ["/app/data"]

USER gateway

CMD ["node", "dist/index.js"]
