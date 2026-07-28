# syntax=docker/dockerfile:1

# ---- deps: install dependencies from the lockfile only -------------------
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

# ---- builder: build the Next.js app --------------------------------------
FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# next.config.ts sets `output: "standalone"`, so this produces a pruned,
# self-contained server bundle in .next/standalone.
RUN npm run build

# ---- runner: minimal production image -------------------------------------
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
  && adduser --system --uid 1001 nextjs

# Only the standalone output, static assets, and public files are needed at
# runtime - this deliberately does NOT copy the full node_modules tree.
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

# The standalone output includes its own minimal server.js entrypoint.
CMD ["node", "server.js"]
