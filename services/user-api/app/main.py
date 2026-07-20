from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.profiles import router as profiles_router
from app.api.users import router as users_router


app = FastAPI(title="TrackFlow User API", version="0.1.0")
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(profiles_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
