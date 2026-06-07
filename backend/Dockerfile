# syntax=docker/dockerfile:1

###############################################################################
# Stage 1 — builder (uv + Python 3.13 on Bookworm)
###############################################################################
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install deps first (layer is cached as long as lock file doesn't change)
RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Copy the full source, then install the project itself
COPY . .
RUN uv sync --locked --no-dev

###############################################################################
# Stage 2 — runtime
###############################################################################
FROM python:3.13-slim-bookworm

# Non-root user
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --no-create-home app

WORKDIR /app

# Copy virtualenv and source from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE="config.settings.production" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# collectstatic at build time. The real SECRET_KEY is injected at runtime via
# env; a throwaway placeholder is set inline only for this step (no ARG/ENV so
# nothing sensitive is baked into image metadata).
RUN SECRET_KEY=build-placeholder python manage.py collectstatic --noinput

USER app
EXPOSE 8000

CMD ["gunicorn", "config.asgi:application", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-w", "4", \
     "-b", "0.0.0.0:8000", \
     "--timeout", "0", \
     "--graceful-timeout", "30", \
     "--keep-alive", "75", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
