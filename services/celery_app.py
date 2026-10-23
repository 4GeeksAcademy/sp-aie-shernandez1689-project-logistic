"""Configuración de Celery para el monorepo TrackFlow.

Usa Redis como broker y backend de resultados. La conexión se configura
vía variable de entorno con la siguiente precedencia:

1. ``REDIS_URL`` — variable estándar del proyecto (usada por API, workers
   y Flower). Ejemplo: ``redis://redis:6379/0`` (Docker Compose).
2. ``CELERY_BROKER_URL`` — variable legacy de Celery.
3. Valor por defecto: ``redis://redis:6379/0``.

Uso desde el worker:
    celery -A services.celery_app worker --loglevel=info

Uso desde la API (FastAPI):
    from services.celery_app import celery_app
    celery_app.send_task("ping")
"""

from __future__ import annotations

import os

from celery import Celery

# Cadena de resolución: REDIS_URL > CELERY_BROKER_URL > default Docker
_REDIS_URL = (
    os.getenv("REDIS_URL")
    or os.getenv("CELERY_BROKER_URL")
    or "redis://redis:6379/0"
)

celery_app = Celery(
    "trackflow",
    broker=_REDIS_URL,
    backend=_REDIS_URL,
)

# Configuración adicional
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # TTL por defecto para resultados (1 hora)
    result_expires=3600,
    # Transicionar a STARTED cuando el worker empieza a ejecutar
    # (necesario para Flower y GET /tasks/{task_id} → status="started")
    task_track_started=True,
    # Tareas importadas automáticamente
    include=["services.celery_tasks"],
)

# Para depuración: si se ejecuta directamente, muestra la configuración
if __name__ == "__main__":
    celery_app.start()