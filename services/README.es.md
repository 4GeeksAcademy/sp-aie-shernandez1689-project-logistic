# `services/` — Servicios Backend

Esta carpeta contiene todos los servicios backend del monorepo TrackFlow:
**APIs REST**, **workers en segundo plano** (Celery) y **paquetes de análisis**.

> _English version: [README.md](./README.md)_

---

## Resumen de servicios

| Directorio             | Tipo               | Descripción                                                |
| ---------------------- | ------------------ | ---------------------------------------------------------- |
| `api/`                 | API REST FastAPI   | API del monorepo: incidencias, proveedores, telemetría     |
| `user-api/`            | API REST FastAPI   | API de autenticación (registro, login, recuperación pwd)   |
| `celery_app.py`        | App Celery         | Definición de la app Celery (broker, backend, config)      |
| `celery_tasks.py`      | Tareas Celery      | Implementación de tareas asíncronas (reporte, ping)        |
| `telemetry/`           | Paquete Python     | Pipeline de análisis de telemetría (métricas con Pandas)   |
| `reporting/`           | Router FastAPI     | Reportes semanales de rendimiento (pipeline runner)        |
| `job_runner/`          | Scripts            | Planificador basado en cron del SO (no Celery)             |

---

## 1. Worker de Celery (procesador de tareas asíncronas)

El worker de Celery es un **proceso independiente** que ejecuta tareas
en segundo plano encoladas por la API (o cualquier otro productor).
**No comparte** el proceso de FastAPI — corre como su propio proceso del
sistema operativo, desacoplado del ciclo de vida de la API.

### Arquitectura

```text
┌──────────────┐     send_task()     ┌──────────────┐     ┌─────────────────┐
│  FastAPI API │ ──────────────────▶ │   Redis      │ ───│  Worker Celery  │
│  (productor) │   202 + task_id     │  (broker)    │    │  (consumidor)   │
└──────────────┘                     └──────────────┘    └─────────────────┘
                                          │                    │
                                    ┌─────▼──────┐       ┌────▼────┐
                                    │  Flower    │       │ Redis   │
                                    │ (monitor)  │       │(backend)│
                                    └────────────┘       └─────────┘
```

- **Productor**: Los endpoints de FastAPI encolan tareas mediante
  `celery_app.send_task()` y devuelven inmediatamente `202 Accepted`
  con `{"task_id": "..."}`.
- **Broker**: Redis almacena la cola de tareas hasta que el worker las recoge.
- **Worker**: El proceso worker de Celery consume las tareas, las ejecuta
  y guarda el resultado en Redis (el backend de Celery).
- **Monitor**: Flower proporciona una interfaz web para inspeccionar workers,
  colas y tareas.

### Requisitos previos

- **Python** >= 3.12
- **Redis** funcionando y accesible vía `REDIS_URL` (por defecto:
  `redis://redis:6379/0`)
- **Dependencias** instaladas (ver [pyproject.toml](../pyproject.toml) o
  ejecutar `pip install -e .` desde la raíz del repositorio)

### Cómo iniciar el worker

#### Opción A: Docker Compose (recomendada)

```bash
# Iniciar todo el stack (API + worker + Redis + Flower + scheduler)
docker compose up -d

# Iniciar solo el worker (si el stack ya está arriba)
docker compose up -d worker

# Escalar a múltiples workers
docker compose up -d --scale worker=4 worker
```

El worker se ejecuta mediante [infra/worker.Dockerfile](../infra/worker.Dockerfile),
que instala todas las dependencias del monorepo e inicia Celery con:

```text
celery -A services.celery_app worker --loglevel=info
```

#### Opción B: CLI local (desarrollo)

```bash
# Desde la raíz del repositorio, con dependencias instaladas:
celery -A services.celery_app worker --loglevel=info

# Con concurrencia personalizada (número de procesos worker):
celery -A services.celery_app worker --loglevel=info -c 4

# Con una cola específica (si usas enrutamiento personalizado):
celery -A services.celery_app worker --loglevel=info -Q default,telemetry
```

> **Consejo**: Si tienes errores de importación, asegúrate de que la raíz
> del repo esté en tu `PYTHONPATH` o ejecuta `pip install -e .` primero.

#### Opción C: Docker CLI (contenedor independiente)

```bash
docker build -t trackflow-worker -f infra/worker.Dockerfile .
docker run --rm -e REDIS_URL=redis://redis:6379/0 trackflow-worker
```

### Cómo detener el worker

#### Apagado graceful

```bash
# Si se ejecuta en una terminal: presiona Ctrl+C
# El worker terminará la tarea actual y se apagará.

# Si se ejecuta con Docker Compose:
docker compose stop worker

# O eliminar el contenedor por completo:
docker compose down worker
```

#### Apagado remoto (desde otra terminal)

```bash
# Enviar señal de apagado a todos los workers
celery -A services.celery_app control shutdown

# Revocar una tarea específica (detenerla si no ha empezado)
celery -A services.celery_app revoke <task_id>
```

### Cómo verificar que el worker está funcionando

#### Hacer ping al worker vía Celery inspect

```bash
celery -A services.celery_app inspect ping
# Salida esperada: {"celery@<hostname>": {"ok": "pong"}}
```

#### Encolar una tarea ping manualmente

```bash
celery -A services.celery_app call ping
# Devuelve el UUID de la tarea. Ver resultado:
celery -A services.celery_app result <task_id>
# Esperado: {"status": "pong", "timestamp": "2026-..."}
```

#### Verificar el estado del worker

```bash
# Listar colas activas
celery -A services.celery_app inspect active_queues

# Listar tareas registradas
celery -A services.celery_app inspect registered

# Ver estadísticas del worker (concurrencia, carga, etc.)
celery -A services.celery_app inspect stats
```

#### A través de la API

```bash
# Encolar un reporte de telemetría
curl -X GET http://localhost:8000/telemetry/report \
  -H "Authorization: Bearer <tu-token>"
# → 202 {"task_id": "uuid-..."}

# Consultar el estado de la tarea
curl http://localhost:8000/tasks/<task_id> \
  -H "Authorization: Bearer <tu-token>"
# → {"task_id": "...", "status": "success", "result": {...}}
```

### Monitorización con Flower

Flower está incluido en `docker-compose.yml` y se inicia automáticamente.
Accede al panel en **http://localhost:5555**.

Flower muestra:
- Workers activos y su estado
- Colas de tareas y tasas de mensajes
- Historial de tareas (éxito/fallo, tiempos)
- Posibilidad de revocar o reintentar tareas

```bash
# Iniciar Flower manualmente (si no usas docker-compose)
celery -A services.celery_app flower \
  --broker=redis://redis:6379/0 \
  --port=5555
```

### Tareas disponibles

| Nombre de tarea              | Descripción                                           |
| ---------------------------- | ----------------------------------------------------- |
| `generate_telemetry_report`  | Genera el reporte completo de telemetría para una ventana |
| `ping`                       | Tarea de verificación, devuelve `{"status": "pong"}`  |

### Dead Letter Queue (DLQ)

Cuando una tarea agota todos los reintentos (`max_retries=3` para el
reporte de telemetría), la clase base `DatabaseTask` persiste los
detalles del fallo en la tabla `celery_task_failures` (PostgreSQL).
Esto permite a los operadores revisar y reintentar tareas fallidas
sin perder el contexto del error.

---

## 2. API — API REST del Monorepo (`services/api/`)

La API principal para las operaciones de TrackFlow. Construida con **FastAPI**.

### Endpoints

| Endpoint                          | Método | Descripción                                |
| --------------------------------- | ------ | ------------------------------------------ |
| `/incidents/...`                  | GET    | Gestión de incidencias                     |
| `/suppliers/...`                  | GET    | Directorio de proveedores                  |
| `/telemetry/events`               | POST   | Ingesta de eventos de telemetría           |
| `/telemetry/report`               | GET    | Encola reporte asíncrono → 202             |
| `/tasks/{task_id}`                | GET    | Consulta estado/resultado de tarea Celery  |
| `/reporting/weekly-warehouse-...` | GET    | KPIs semanales de almacén/cliente          |
| `/reporting/pipeline-runs/latest` | GET    | Metadatos de la última ejecución           |
| `/reporting/pipeline-runs`        | POST   | Disparo manual de ejecución de pipeline    |

### Cómo ejecutar (desarrollo)

```bash
cd services/api
uvicorn main:app --reload --port 8000
```

### Flujo de tareas asíncronas

1. `GET /telemetry/report` resuelve la ventana temporal, llama a
   `celery_app.send_task("generate_telemetry_report", args=[...])`,
   y devuelve **202 Accepted** con `{"task_id": "..."}`.
2. El **worker de Celery** (ejecutándose independientemente) recoge la
   tarea, ejecuta `generate_telemetry_report` y guarda el resultado en Redis.
3. El cliente consulta `GET /tasks/{task_id}` para obtener el resultado
   cuando `status` sea `"success"` o `"failure"`.

---

## 3. User API (`services/user-api/`)

Servicio de autenticación construido con FastAPI. Gestiona registro de
usuarios, login, recuperación de contraseña. Usa tokens JWT y TinyDB
(almacenamiento en archivo).

```bash
cd services/user-api
uvicorn app.main:app --reload --port 8001
```

---

## 4. Paquete de Análisis de Telemetría (`services/telemetry/`)

Funciones de métrica puras con Pandas para el pipeline de reportes de
telemetría. Cada función carga eventos desde PostgreSQL, los transforma
con Pandas y devuelve diccionarios serializables a JSON.

Ver [`services/telemetry/__init__.py`](./telemetry/__init__.py) para la
API pública (`build_telemetry_report`, `events_per_day`, etc.).

---

## 5. Router de Reporting (`services/reporting/`)

Reportes semanales de rendimiento de almacén/cliente. Se ejecuta como
un router montado dentro de la API principal. Ver
`data/pipelines/PIPELINE_DESIGN.md` para el contrato exacto.

---

## 6. Job Runner / Scheduler (`services/job_runner/`)

Planificador de pipelines basado en cron. **Esto no es Celery** — usa el
demonio cron del SO para ejecutar pipelines semanales. Ver
[infra/scheduler.Dockerfile](../infra/scheduler.Dockerfile) y
[infra/crontab](../infra/crontab).

---

## Referencia de variables de entorno

| Variable         | Por defecto                  | Descripción                                 |
| ---------------- | ---------------------------- | ------------------------------------------- |
| `REDIS_URL`      | `redis://redis:6379/0`       | Conexión Redis para Celery (broker/backend) |
| `DATABASE_URL`   | *(ninguno)*                  | Conexión PostgreSQL (Supabase)              |
| `FLOWER_PORT`    | `5555`                       | Puerto de la interfaz web de Flower         |
| `REDIS_PORT`     | `6379`                       | Puerto del servidor Redis (docker-compose)  |
| `JWT_SECRET_KEY` | *(requerido)*                | Secreto para firmar JWT                     |
| `CORS_ALLOW_ORIGINS` | `*`                     | Orígenes permitidos para CORS               |
