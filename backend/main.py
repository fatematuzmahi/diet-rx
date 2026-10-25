from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers.password_router import router as password_router
from backend.routers.auth_router import router as auth_router
from backend.routers.doctor_router import router as doctor_router
from backend.routers.protected_router import router as protected_router
from backend.routers.health_profile_router import router as health_profile_router
from backend.routers.prescription_router import router as prescription_router
from backend.routers.dish_dna_router import router as dish_dna_router
from backend.routers.plan_router import router as plan_router
from backend.routers.daily_log_router import router as daily_log_router
from backend.routers.notification_router import router as notification_router
from backend.routers.meal_plan_router import router as meal_plan_router
from backend.routers.safety_router import router as safety_router
from backend.routers.grocery_router import router as grocery_router
from backend.routers.drift_router import router as drift_router
from backend.routers.price_router import router as price_router
from backend.routers.optimizer_router import router as optimizer_router


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
app.include_router(doctor_router)
app.include_router(protected_router)
app.include_router(health_profile_router)
app.include_router(prescription_router)
app.include_router(dish_dna_router)
app.include_router(plan_router)
app.include_router(daily_log_router)
app.include_router(notification_router)
app.include_router(meal_plan_router)
app.include_router(safety_router)
app.include_router(grocery_router)
app.include_router(drift_router)
app.include_router(price_router)
app.include_router(optimizer_router)

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