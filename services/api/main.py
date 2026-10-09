from fastapi import FastAPI

from routes.incidents import router as incidents_router
from routes.suppliers import router as suppliers_router


app = FastAPI(title="TrackFlow Operations API")
app.include_router(incidents_router)
app.include_router(suppliers_router)
