# ─────────────────────────────────────────────────────────────
# worker.Dockerfile — Worker Celery independiente
# ─────────────────────────────────────────────────────────────
#
# Este contenedor NO ejecuta FastAPI ni cron. Es un worker
# Celery que corre tareas asíncronas (telemetry, etc.) como
# proceso independiente, desacoplado del ciclo de vida de la
# API.
#
# Uso
# ----
#   docker build -t trackflow-worker -f infra/worker.Dockerfile .
#   docker run --rm -e REDIS_URL="$REDIS_URL" trackflow-worker
#
# O integrado en docker-compose.yml:
#   worker:
#     build:
#       context: .
#       dockerfile: infra/worker.Dockerfile
#     environment:
#       REDIS_URL: ${REDIS_URL:-redis://redis:6379/0}
#       DATABASE_URL: ${DATABASE_URL}
#     depends_on:
#       - redis
#     restart: unless-stopped
#
# Variables de entorno
# --------------------
# REDIS_URL      — obligatorio, conexión Redis (broker + backend)
#                  Ej: redis://redis:6379/0
# DATABASE_URL   — opcional, PostgreSQL (Supabase) para DLQ y
#                  reportes de telemetría
# ─────────────────────────────────────────────────────────────

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# ── Dependencias del proyecto ───────────────────────────────
RUN pip install --no-cache-dir uv && uv venv "$VIRTUAL_ENV"

# Instalar dependencias del monorepo necesarias para el worker
# (celery, redis, sqlmodel, psycopg, pandas, pydantic, etc.)
COPY pyproject.toml ./
RUN uv pip install \
    celery>=5.6.3 \
    "redis>=8.1.0" \
    "sqlmodel>=0.0.22,<0.1.0" \
    "psycopg[binary]>=3.2,<4.0" \
    "pandas>=2.2,<4.0" \
    "pydantic>=2.12" \
    "python-dotenv>=1.0.1" \
    "PyJWT>=2.9.0" \
    "fastapi>=0.115.0" \
    "tinydb>=4.8.0" \
    "passlib[bcrypt]==1.7.4" \
    "bcrypt==4.0.1"

# ── Código del monorepo ─────────────────────────────────────
# Solo se copian los directorios que necesita el worker:
COPY services/ ./services/
COPY packages/ ./packages/
COPY data/ ./data/

# ── Healthcheck ─────────────────────────────────────────────
# Verifica que el worker responde a través de Celery inspect
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s \
    CMD celery -A services.celery_app inspect ping || exit 1

# ── Entrypoint ──────────────────────────────────────────────
# El worker se inicia con:
#   celery -A services.celery_app worker --loglevel=info
# Se pueden sobreescribir args desde docker-compose o CLI:
#   docker run ... trackflow-worker celery -A services.celery_app worker --loglevel=info -c 4
CMD ["celery", "-A", "services.celery_app", "worker", "--loglevel=info"]