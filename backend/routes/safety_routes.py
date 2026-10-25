from fastapi import APIRouter, Body
from backend.services.food_checker import check_food_safety

safety_router = APIRouter(
    prefix="/api/safety",
    tags=["Food Safety"],
)

@safety_router.post("/check")
def safety_check(payload: dict = Body(...)):
    return check_food_safety(payload)