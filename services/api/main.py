import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlmodel import SQLModel

from db_telemetry import engine as telemetry_engine
from models import TelemetryEventRecord
from routes.incidents import router as incidents_router
from routes.suppliers import router as suppliers_router
from routes.telemetry import router as telemetry_router


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
    yield


app = FastAPI(title="TrackFlow Operations API", lifespan=lifespan)
app.include_router(incidents_router)
app.include_router(suppliers_router)
app.include_router(telemetry_router)


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
