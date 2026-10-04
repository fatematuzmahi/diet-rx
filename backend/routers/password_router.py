from fastapi import APIRouter, HTTPException
from backend.database.connection import get_connection
from pydantic import BaseModel
import secrets
from werkzeug.security import generate_password_hash

router = APIRouter(
    prefix="/auth",
    tags=["Password Recovery"]
)


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    reset_token: str
    new_password: str


@router.get("/password-ping")
def password_ping():
    return {
        "message": "Password recovery router is working"
    }


@router.post("/forgot-password")
def forgot_password(data: ForgotPasswordRequest):
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
            SELECT patient_id
            FROM patient
            WHERE LOWER(email) = :email
            """,
            email=data.email.lower()
        )

        patient = cursor.fetchone()

        if patient is None:
            raise HTTPException(
                status_code=404,
                detail="Email not found"
            )

        reset_token = secrets.token_urlsafe(32)

        cursor.execute(
            """
            UPDATE patient
            SET reset_token = :reset_token
            WHERE LOWER(email) = :email
            """,
            reset_token=reset_token,
            email=data.email.lower()
        )

        connection.commit()

        return {
            "message": "Password reset token generated successfully.",
            "reset_token": reset_token
        }

    finally:
        cursor.close()
        connection.close()


@router.post("/reset-password")
def reset_password(data: ResetPasswordRequest):
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
            SELECT patient_id
            FROM patient
            WHERE reset_token = :reset_token
            """,
            reset_token=data.reset_token
        )

        patient = cursor.fetchone()

        if patient is None:
            raise HTTPException(
                status_code=400,
                detail="Invalid reset token"
            )

        hashed_password = generate_password_hash(data.new_password)

        cursor.execute(
            """
            UPDATE patient
            SET password = :password,
                reset_token = NULL
            WHERE reset_token = :reset_token
            """,
            password=hashed_password,
            reset_token=data.reset_token
        )

        connection.commit()

        return {
            "message": "Password reset successful."
        }

    finally:
        cursor.close()
        connection.close()