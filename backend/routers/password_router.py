import os
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from pydantic import BaseModel
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash

from backend.database.connection import get_connection


load_dotenv()

RESET_CODE_TTL = timedelta(minutes=10)
RESET_SESSION_COOKIE = "dietrx_reset_session"
# SCRUM-62: same key as access tokens, loaded once (no hard-coded fallback).
from backend.routers.auth_router import SECRET_KEY as RESET_SESSION_SECRET  # noqa: E402
RESET_SESSION_ALGORITHM = "HS256"

router = APIRouter(prefix="/auth", tags=["Password Recovery"])


class ForgotPasswordRequest(BaseModel):
    email: str


class VerifyResetCodeRequest(BaseModel):
    email: str
    code: str


class ResetPasswordRequest(BaseModel):
    new_password: str
    confirm_password: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _generate_reset_code() -> str:
    return str(secrets.randbelow(900000) + 100000)


def _build_reset_code_record(code: str) -> str:
    expires_at = _utc_now() + RESET_CODE_TTL
    return f"{expires_at.isoformat()}|{generate_password_hash(code)}"


def _reset_code_is_valid(record: str | None, submitted_code: str) -> bool:
    if not record:
        return False
    try:
        expires_text, code_hash = record.split("|", 1)
        expires_at = datetime.fromisoformat(expires_text)
        return expires_at >= _utc_now() and check_password_hash(code_hash, submitted_code)
    except (TypeError, ValueError):
        return False


def _send_reset_code_email(recipient: str, code: str) -> None:
    smtp_host = os.getenv("SMTP_HOST")
    sender = os.getenv("SMTP_FROM_EMAIL")
    if not smtp_host or not sender:
        raise RuntimeError("Email delivery is not configured.")

    message = EmailMessage()
    message["Subject"] = "Reset your DietRx password"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        f"Your DietRx password reset code is {code}. "
        f"It expires in {int(RESET_CODE_TTL.total_seconds() // 60)} minutes."
    )

    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() not in {"0", "false", "no"}

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
        if use_tls:
            smtp.starttls()
        if smtp_username and smtp_password:
            smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)


@router.get("/password-ping")
def password_ping():
    return {"message": "Password recovery router is working"}


@router.post("/forgot-password")
def forgot_password(data: ForgotPasswordRequest):
    email = data.email.strip().lower()
    connection = get_connection()
    if connection is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = connection.cursor()
    try:
        cursor.execute("SELECT patient_id FROM patient WHERE LOWER(email) = :email", email=email)
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Email not found")

        code = _generate_reset_code()
        cursor.execute(
            "UPDATE patient SET reset_token = :reset_token WHERE LOWER(email) = :email",
            reset_token=_build_reset_code_record(code),
            email=email,
        )
        try:
            _send_reset_code_email(email, code)
        except Exception as error:
            connection.rollback()
            raise HTTPException(
                status_code=503,
                detail="Unable to send the password reset email. Please try again later.",
            ) from error

        connection.commit()
        return {"message": "A verification code has been sent to your email."}
    finally:
        cursor.close()
        connection.close()


@router.post("/verify-reset-code")
def verify_reset_code(data: VerifyResetCodeRequest):
    email = data.email.strip().lower()
    code = data.code.strip()
    connection = get_connection()
    if connection is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT patient_id, reset_token FROM patient WHERE LOWER(email) = :email",
            email=email,
        )
        patient = cursor.fetchone()
        if patient is None or not _reset_code_is_valid(patient[1], code):
            raise HTTPException(status_code=400, detail="Invalid or expired verification code.")

        expires_at = _utc_now() + RESET_CODE_TTL
        reset_session = jwt.encode(
            {"sub": str(patient[0]), "purpose": "password_reset", "exp": expires_at},
            RESET_SESSION_SECRET,
            algorithm=RESET_SESSION_ALGORITHM,
        )
        response = JSONResponse({"message": "Code verified. You can now reset your password."})
        response.set_cookie(
            key=RESET_SESSION_COOKIE,
            value=reset_session,
            httponly=True,
            samesite="lax",
            secure=os.getenv("RESET_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"},
            max_age=int(RESET_CODE_TTL.total_seconds()),
        )
        return response
    finally:
        cursor.close()
        connection.close()


@router.post("/reset-password")
def reset_password(
    data: ResetPasswordRequest,
    reset_session: str | None = Cookie(default=None, alias=RESET_SESSION_COOKIE),
):
    if data.new_password != data.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")
    # No reset cookie (expired or never verified) -> 401, not a 500 crash.
    if not reset_session:
        raise HTTPException(status_code=401, detail="Your reset session is invalid or has expired.")
    try:
        payload = jwt.decode(reset_session, RESET_SESSION_SECRET, algorithms=[RESET_SESSION_ALGORITHM])
        patient_id = int(payload.get("sub"))
        if payload.get("purpose") != "password_reset":
            raise ValueError
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Your reset session is invalid or has expired.")

    connection = get_connection()
    if connection is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT patient_id FROM patient WHERE patient_id = :patient_id AND reset_token IS NOT NULL",
            patient_id=patient_id,
        )
        if cursor.fetchone() is None:
            raise HTTPException(status_code=401, detail="Your reset session is invalid or has expired.")

        cursor.execute(
            """
            UPDATE patient
            SET password = :password,
                reset_token = NULL
            WHERE patient_id = :patient_id
            """,
            password=generate_password_hash(data.new_password),
            patient_id=patient_id,
        )
        connection.commit()
        response = JSONResponse({"message": "Password reset successful."})
        response.delete_cookie(RESET_SESSION_COOKIE, httponly=True, samesite="lax")
        return response
    except HTTPException:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()
