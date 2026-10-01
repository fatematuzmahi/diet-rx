import logging
import os
from dotenv import load_dotenv

load_dotenv()

import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import oracledb
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from werkzeug.security import check_password_hash, generate_password_hash

from backend.database.connection import get_connection


VERIFICATION_CODE_TTL = timedelta(minutes=10)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str


class VerificationRequest(BaseModel):
    email: str
    code: str


def generate_verification_code() -> str:
    """Return a cryptographically secure six-digit verification code."""
    return str(secrets.randbelow(900000) + 100000)


def hash_verification_code(code: str) -> str:
    return generate_password_hash(code)


def send_verification_email(recipient: str, code: str) -> None:
    """Send a verification code using the configured SMTP service."""
    smtp_host = os.getenv("SMTP_HOST")
    sender = os.getenv("SMTP_FROM_EMAIL")
    if not smtp_host or not sender:
        raise RuntimeError("Email delivery is not configured.")

    message = EmailMessage()
    message["Subject"] = "Verify your DietRx email address"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "Your DietRx verification code is "
        f"{code}. It expires in {int(VERIFICATION_CODE_TTL.total_seconds() // 60)} minutes."
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


def _normalise_email(email: str) -> str:
    return email.strip().lower()


def _utc_now() -> datetime:
    """Return a UTC value compatible with Oracle TIMESTAMP (without time zone)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_duplicate_email(error: oracledb.Error) -> bool:
    error_info = error.args[0] if error.args else None
    return getattr(error_info, "code", None) == 1  # ORA-00001


@router.get("/ping")
def auth_ping():
    return {"message": "Authentication router is working"}


@router.post("/register", status_code=201)
def register_user(data: RegisterRequest):
    logger.info("registration started")
    print("DEBUG REG: registration started", flush=True)
    email = _normalise_email(data.email)
    name = data.name.strip()
    if not name or not email or not data.password:
        raise HTTPException(status_code=400, detail="Name, email, and password are required.")

    verification_code = generate_verification_code()
    connection = get_connection()
    if connection is None:
        raise HTTPException(status_code=500, detail="Database connection failed")
    logger.info("Oracle connection obtained")
    print("DEBUG REG: Oracle connection obtained", flush=True)

    cursor = connection.cursor()
    try:
        password_hash = generate_password_hash(data.password)
        verification_code_hash = hash_verification_code(verification_code)
        logger.info("password/code hashes generated")
        print("DEBUG REG: password/code hashes generated", flush=True)
        cursor.execute(
            """
            INSERT INTO patient
                (name, email, password, verification_code_hash,
                 verification_code_expires_at, is_verified)
            VALUES
                (:name, :email, :password, :verification_code_hash,
                 :verification_code_expires_at, 0)
            """,
            name=name,
            email=email,
            password=password_hash,
            verification_code_hash=verification_code_hash,
            verification_code_expires_at=_utc_now() + VERIFICATION_CODE_TTL,
        )
        logger.info("INSERT executed")
        print("DEBUG REG: INSERT executed", flush=True)
        logger.info("duplicate-email check completed")
        print("DEBUG REG: duplicate-email check completed", flush=True)
        try:
            logger.info("verification email sending started")
            print("DEBUG REG: verification email sending started", flush=True)
            send_verification_email(email, verification_code)
            logger.info("verification email sending completed")
            print("DEBUG REG: verification email sending completed", flush=True)
        except Exception as error:
            logger.exception("Verification email sending failed")
            print("DEBUG REG: SMTP/generic exception reached", flush=True)
            connection.rollback()
            raise HTTPException(
                status_code=503,
                detail="Unable to send the verification email. Please try again later.",
            ) from error

        connection.commit()
        logger.info("commit completed")
        print("DEBUG REG: commit completed", flush=True)
        return {"message": "Registration successful. Check your email for a verification code."}
    except oracledb.Error as error:
        connection.rollback()
        print("DEBUG REG: Oracle exception reached", flush=True)
        if _is_duplicate_email(error):
            raise HTTPException(status_code=409, detail="An account with this email already exists.") from error
        logger.exception("Registration failed")
        raise HTTPException(status_code=500, detail="Unable to register account.") from error
    finally:
        cursor.close()
        connection.close()


@router.post("/verify-email")
def verify_email(data: VerificationRequest):
    email = _normalise_email(data.email)
    code = data.code.strip()
    if not email or not code:
        raise HTTPException(status_code=400, detail="Email and verification code are required.")

    connection = get_connection()
    if connection is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT patient_id, is_verified, verification_code_hash, verification_code_expires_at
            FROM patient
            WHERE email = :email
            """,
            email=email,
        )
        patient = cursor.fetchone()
        if patient is None:
            raise HTTPException(status_code=404, detail="Account not found.")

        patient_id, is_verified, code_hash, expires_at = patient
        if is_verified == 1:
            raise HTTPException(status_code=409, detail="This email address is already verified.")
        if not code_hash or not expires_at or expires_at <= _utc_now():
            raise HTTPException(status_code=400, detail="The verification code has expired. Request a new code.")

        try:
            code_matches = check_password_hash(code_hash, code)
        except (TypeError, ValueError):
            code_matches = False
        if not code_matches:
            raise HTTPException(status_code=400, detail="Invalid verification code.")

        cursor.execute(
            """
            UPDATE patient
            SET is_verified = 1,
                verified_at = CURRENT_TIMESTAMP,
                verification_code_hash = NULL,
                verification_code_expires_at = NULL
            WHERE patient_id = :patient_id
            """,
            patient_id=patient_id,
        )
        connection.commit()
        return {"message": "Email verified successfully."}
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        raise HTTPException(status_code=500, detail="Unable to verify email.") from error
    finally:
        cursor.close()
        connection.close()

class LoginRequest(BaseModel):
    email: str
    password: str

@router.post("/login")
def login_user(data: LoginRequest):
    email = _normalise_email(data.email)

    if not email or not data.password:
        raise HTTPException(
            status_code=400,
            detail="Email and password are required."
        )

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
            SELECT patient_id, name, email, password, is_verified
            FROM patient
            WHERE LOWER(email) = :email
            """,
            email=email,
        )

        patient = cursor.fetchone()

        if patient is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid email or password."
            )

        patient_id, name, patient_email, password_hash, is_verified = patient

        if not check_password_hash(password_hash, data.password):
            raise HTTPException(
                status_code=401,
                detail="Invalid email or password."
            )

        if not is_verified:
            raise HTTPException(
                status_code=403,
                detail="Please verify your email before logging in."
            )

        return {
            "message": "Login successful.",
            "user": {
                "patient_id": patient_id,
                "name": name,
                "email": patient_email,
            }
        }

    finally:
        cursor.close()
        connection.close()