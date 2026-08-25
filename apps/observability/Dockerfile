# syntax=docker/dockerfile:1.7
#
# Built from the repository root so contracts/openapi.json is in context:
#   docker build -f apps/observability/Dockerfile .

FROM node:24-alpine AS deps
WORKDIR /app
RUN corepack enable
COPY apps/observability/package.json apps/observability/pnpm-lock.yaml* apps/observability/pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile || pnpm install

FROM deps AS build
WORKDIR /app
COPY apps/observability/ ./
# The generated client is committed, so the image build does not run codegen.
# The contract is copied anyway so `pnpm codegen` works inside the container --
# useful for reproducing a codegen drift failure exactly as CI saw it.
COPY contracts/ /contracts/
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN pnpm build

FROM nginx:1.27-alpine AS runtime
COPY apps/observability/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD wget -q --spider http://127.0.0.1/ || exit 1
