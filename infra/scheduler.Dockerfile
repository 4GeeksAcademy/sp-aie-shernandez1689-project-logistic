# ─────────────────────────────────────────────────────────────
# scheduler.Dockerfile — Contenedor scheduler dedicado
# ─────────────────────────────────────────────────────────────
#
# Este contenedor NO ejecuta FastAPI. Es un worker independiente
# que corre el script services/job_runner/scheduler.py mediante
# cron del SO.
#
# Uso
# ----
#   docker build -t scheduler -f infra/scheduler.Dockerfile .
#   docker run --rm -e DATABASE_URL="$DATABASE_URL" scheduler
#
# O integrado en docker-compose.yml:
#   scheduler:
#     build:
#       context: .
#       dockerfile: infra/scheduler.Dockerfile
#     environment:
#       DATABASE_URL: ${DATABASE_URL}
#       TARGET_DATE: ${TARGET_DATE:-}
#     restart: unless-stopped
#
# Variables de entorno
# --------------------
# DATABASE_URL  — obligatorio, conexión PostgreSQL (Supabase)
# TARGET_DATE   — opcional, YYYY-MM-DD o YYYY-WNN para pruebas
# ─────────────────────────────────────────────────────────────

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# ── Dependencias del proyecto ───────────────────────────────
RUN pip install --no-cache-dir uv && uv venv "$VIRTUAL_ENV"

# Instalar dependencias del monorepo
COPY pyproject.toml ./
RUN uv pip install sqlmodel>=0.0.22 psycopg[binary]>=3.2 python-dotenv>=1.0 \
    prefect>=3 pandas>=2.2 tinydb>=4.8 pydantic>=2.12

# ── Código ──────────────────────────────────────────────────
COPY services/ ./services/
COPY data/ ./data/
COPY packages/ ./packages/

# ── Cron ────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y cron && rm -rf /var/lib/apt/lists/*

COPY infra/crontab /etc/cron.d/scheduler-cron
RUN chmod 0644 /etc/cron.d/scheduler-cron && crontab /etc/cron.d/scheduler-cron

# ── Entrypoint ──────────────────────────────────────────────
# En producción corre cron en foreground.
# Para pruebas puntuales: docker run ... python services/job_runner/scheduler.py
CMD ["cron", "-f"]