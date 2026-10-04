from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from werkzeug.security import check_password_hash
from backend.database.connection import get_connection


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(data: LoginRequest):
    connection = get_connection()

    if connection is None:
        raise HTTPException(
            status_code=500,
            detail="Database connection failed"
        )

    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT patient_id, name, email, password
            FROM patient
            WHERE LOWER(email) = :email
            """,
            email=data.email.lower()
        )

        user = cursor.fetchone()

        if user is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid email or password"
            )

        stored_password = user[3]

        if not check_password_hash(stored_password, data.password):
            raise HTTPException(
                status_code=401,
                detail="Invalid email or password"
            )

        return {
            "message": "Login successful",
            "user": {
                "patient_id": user[0],
                "name": user[1],
                "email": user[2]
            }
        }

    finally:
        cursor.close()
        connection.close()