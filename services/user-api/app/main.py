import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlmodel import SQLModel

from app import db
from app.api.applications import router as applications_router
from app.api.auth import router as auth_router
from app.api.profiles import router as profiles_router
from app.api.users import router as users_router
from app.models import HealthResponse, RootResponse
from app.routers.inventory import router as inventory_router


@asynccontextmanager
async def lifespan(application: FastAPI):
    if db.engine is not None:
        SQLModel.metadata.create_all(db.engine)
    else:
        logging.getLogger(__name__).warning(
            "DATABASE_URL is not configured; inventory schema initialization skipped"
        )
    yield


app = FastAPI(title="TrackFlow User API", version="0.1.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    details = []
    for issue in error.errors():
        location = issue.get("loc", ())
        if location and location[0] in {"body", "query", "path", "header"}:
            source = location[0]
            field_parts = location[1:]
        else:
            source = "body"
            field_parts = location
        safe_parts = [str(part)[:60] for part in field_parts if isinstance(part, (str, int))]
        error_type = issue.get("type")
        context = issue.get("ctx") or {}

        if error_type == "missing":
            message = "Este campo es obligatorio."
        elif error_type in {"enum", "literal_error"}:
            message = "El valor no está permitido."
        elif error_type == "string_too_short" and isinstance(context.get("min_length"), int):
            message = f"Debe tener al menos {context.get('min_length')} caracteres."
        elif error_type == "string_too_long" and isinstance(context.get("max_length"), int):
            message = f"No puede superar {context.get('max_length')} caracteres."
        elif error_type == "string_type":
            message = "Debe ser un texto."
        elif error_type in {"int_type", "int_parsing"}:
            message = "Debe ser un número entero."
        elif error_type in {"float_type", "float_parsing"}:
            message = "Debe ser un número."
        elif error_type == "json_invalid":
            message = "El cuerpo JSON no tiene un formato válido."
        else:
            message = "El valor no es válido."

        details.append({"loc": [source, *safe_parts], "msg": message})

    return JSONResponse(status_code=422, content={"detail": details})


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
    logging.getLogger(__name__).error(
        "Unhandled User API exception (%s)",
        type(error).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor."},
    )


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

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(profiles_router)
app.include_router(applications_router)
app.include_router(inventory_router)


@app.get("/", response_model=RootResponse)
def root() -> RootResponse:
    return RootResponse(
        message="TrackFlow User API running",
        docs="/docs",
        health="/health",
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")
