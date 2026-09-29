from fastapi import APIRouter

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)


@router.get("/ping")
def auth_ping():
    return {
        "message": "Authentication router is working"
    }