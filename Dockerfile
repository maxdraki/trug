# syntax=docker/dockerfile:1

# ---- Stage 1: build the Svelte PWA ----
FROM node:22-slim AS webbuild
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ .
RUN npm run build

# ---- Stage 2: server runtime ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app

# Copy bytecode-compiled deps into a project venv (.venv) owned by root at
# build time; the app user only needs read/execute on it at runtime.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Install third-party deps first (cached across source changes). --no-install-project
# skips building trug itself, whose source isn't copied yet.
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY server/trug ./trug
COPY --from=webbuild /web/dist ./static

# License attribution for the third-party assets redistributed inside this image
# (fonts, icons, palette bundled into ./static). Travels with the build so the
# notices ship alongside the assets they cover.
COPY THIRD-PARTY-NOTICES.md ./

# Now build+install the trug package itself, which creates the `trug-doctor`
# console script in the venv. Putting the venv on PATH makes both `trug-doctor`
# and `uvicorn` invokable by bare name (so `docker compose exec trug trug-doctor`
# is short).
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"

# Non-root user with a pinned uid/gid (999). Platform volumes (Railway, docker
# named volumes, Pi bind mounts) arrive root-owned, so the entrypoint starts as
# root, chowns the data dir, and drops to this user via gosu. gosu is the only
# extra runtime package.
RUN groupadd -r -g 999 trug && useradd -r -u 999 -g trug trug && mkdir /data && chown trug:trug /data \
    && apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/*

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV TRUG_DB_PATH=/data/trug.db \
    TRUG_STATIC_DIR=/app/static

EXPOSE 8000

# httpx is a runtime dependency of the server, so the healthcheck runs from the
# same venv as the app (no `uv run`, which would need a writable cache).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import httpx; httpx.get('http://localhost:8000/healthz').raise_for_status()"]

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["/app/.venv/bin/uvicorn", "trug.app:app", "--host", "0.0.0.0", "--port", "8000"]
