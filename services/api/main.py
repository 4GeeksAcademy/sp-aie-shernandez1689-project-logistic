import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlmodel import SQLModel

from db_telemetry import engine as telemetry_engine
from models import CeleryTaskFailure, TelemetryEventRecord
from routes.incidents import router as incidents_router
from routes.suppliers import router as suppliers_router
from routes.tasks import router as tasks_router
from routes.telemetry import router as telemetry_router

# Router de reporting — separado de telemetry (Brifing-pipeline.md §6-§7).
from services.reporting import router as reporting_router  # noqa: E402

# RFP Intake router — tramo de recepción de RFPs (Hito 9, Parte 1).
#  POST /rfp/upload              — sube PDF y procesa: ticket analizado/descartado
#  GET  /rfp/tickets             — lista tickets de intake
#  GET  /rfp/tickets/{ticket_id} — detalle con metadatos + departamentos
from routes.rfp import rfp_recovery_loop, router as rfp_router  # noqa: E402

# RAG router — base de conocimiento de TrackFlow.
from routes.rag import router as rag_router  # noqa: E402

# Knowledge query endpoint — endpoint público mínimo (Fase 3).
#  POST /knowledge/query  — pregunta → respuesta generada, sin crudos Qdrant.
from routes.knowledge import router as knowledge_router  # noqa: E402

# Agent endpoint — grafo LangGraph (services/rag_agent/graph.py).
#  POST /agent/query — invoca el grafo compilado (sin lógica de negocio).
from routes.agent import router as agent_router  # noqa: E402


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Inicializa la tabla telemetry_events en Supabase al arrancar."""
    if telemetry_engine is not None:
        SQLModel.metadata.create_all(telemetry_engine)
        logger = logging.getLogger(__name__)
        logger.info("telemetry_events table initialized (if not exists)")
    else:
        logging.getLogger(__name__).warning(
            "DATABASE_URL is not configured; telemetry table creation skipped"
        )
    # Barrido de tickets RFP atascados en "analizando" (proceso caído a mitad).
    recovery = asyncio.create_task(rfp_recovery_loop())
    yield
    recovery.cancel()
    with suppress(asyncio.CancelledError):
        await recovery


app = FastAPI(title="TrackFlow Operations API", lifespan=lifespan)

# CORS: las páginas de uis/backoffice se sirven desde otro origen. Mismo
# contrato que services/user-api (variable CORS_ALLOW_ORIGINS, mismos defaults).
raw_cors_origins = os.getenv(
    "CORS_ALLOW_ORIGINS",
    "http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:3000,http://localhost:3000",
)
allowed_origins = [origin.strip() for origin in raw_cors_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(incidents_router)
app.include_router(suppliers_router)
app.include_router(telemetry_router)
app.include_router(tasks_router)
app.include_router(rfp_router)

# Endpoints de reporting del pipeline (weekly warehouse client performance).
# Contrato exacto definido en data/pipelines/PIPELINE_DESIGN.md §5.2.
#  GET  /reporting/weekly-warehouse-client-performance  — KPIs semanales
#  GET  /reporting/pipeline-runs/latest                  — metadata última corrida
#  POST /reporting/pipeline-runs                         — disparo manual
app.include_router(reporting_router)

# Endpoint RAG — base de conocimiento.
#  POST /api/rag/query  — responder pregunta con contexto recuperado
app.include_router(rag_router)

# Endpoint Knowledge — capa pública simplificada.
#  POST /knowledge/query  — pregunta -> solo respuesta generada
app.include_router(knowledge_router)

# Endpoint Agente — grafo LangGraph sobre el mismo knowledge base.
#  POST /agent/query      — pregunta -> grafo (retrieve condicional + generate)
#  GET  /agent/health
app.include_router(agent_router)


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    errors = []
    for issue in error.errors():
        location = issue.get("loc", ())
        field_parts = (
            location[1:]
            if location and location[0] in {"body", "query", "path", "header"}
            else location
        )
        field = ".".join(str(part) for part in field_parts) or "body"
        error_type = issue.get("type")
        context = issue.get("ctx") or {}
        if error_type == "missing":
            message = "Este campo es obligatorio."
        elif error_type in {"enum", "literal_error"}:
            message = "El valor no está permitido."
        elif error_type == "string_too_short":
            message = f"Debe tener al menos {context.get('min_length')} caracteres."
        elif error_type == "string_too_long":
            message = f"No puede superar {context.get('max_length')} caracteres."
        elif error_type == "value_error":
            message = "El campo no puede estar vacío."
        elif error_type == "string_type":
            message = "Debe ser un texto."
        elif error_type in {"int_type", "int_parsing"}:
            message = "Debe ser un número entero."
        elif error_type in {"float_type", "float_parsing"}:
            message = "Debe ser un número."
        elif error_type == "extra_forbidden":
            message = "Este campo no está permitido."
        elif error_type == "json_invalid":
            message = "El cuerpo JSON no tiene un formato válido."
        else:
            message = "El valor no es válido."
        errors.append({"field": field, "message": message})

    return JSONResponse(status_code=422, content={"errors": errors})


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
    logging.getLogger(__name__).exception(
        "Unhandled API exception for %s %s",
        request.method,
        request.url.path,
        exc_info=error,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor."},
    )
