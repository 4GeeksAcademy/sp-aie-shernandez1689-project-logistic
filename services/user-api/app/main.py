import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.applications import router as applications_router
from app.api.auth import router as auth_router
from app.api.profiles import router as profiles_router
from app.api.users import router as users_router


app = FastAPI(title="TrackFlow User API", version="0.1.0")

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


@app.get("/")
def root() -> dict[str, str]:
    return {
        "message": "TrackFlow User API running",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
