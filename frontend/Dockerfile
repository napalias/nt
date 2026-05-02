### Stage 1: Build
FROM node:20-alpine AS builder

RUN corepack enable

WORKDIR /app

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

### Stage 2: Production runtime
FROM node:20-alpine

WORKDIR /app

# Copy only the built output (adapter-node output goes to build/)
COPY --from=builder /app/build ./build
COPY --from=builder /app/package.json ./

ENV NODE_ENV=production
ENV PORT=3000

EXPOSE 3000

CMD ["node", "build"]
