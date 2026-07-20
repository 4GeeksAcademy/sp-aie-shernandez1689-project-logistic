from fastapi import FastAPI

from routes.suppliers import router as suppliers_router


app = FastAPI(title="TrackFlow Supplier Directory API")
app.include_router(suppliers_router)
