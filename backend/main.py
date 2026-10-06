from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers.password_router import router as password_router
from backend.routers.auth_router import router as auth_router


app = FastAPI(
    title="DietRx API",
    version="1.0.0"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)



app.include_router(password_router)
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