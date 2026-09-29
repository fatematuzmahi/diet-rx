from fastapi import FastAPI
from backend.routers.auth_router import router as auth_router

app = FastAPI(
    title="DietRx API",
    version="1.0.0"
)

app.include_router(auth_router)

@app.get("/")
def root():
    return {
        "message": "DietRx FastAPI Backend is running"
    }

@app.get("/health")
def health_check():
    return {
        "status": "ok"
    }