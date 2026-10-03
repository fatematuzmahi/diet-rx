from fastapi import APIRouter

router = APIRouter(
    prefix="/auth",
    tags=["Password Recovery"]
)


@router.get("/password-ping")
def password_ping():
    return {"message": "Password recovery router is working"}