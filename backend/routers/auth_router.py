import logging
import os
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import oracledb
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from jose import jwt
from pydantic import BaseModel
from werkzeug.security import check_password_hash, generate_password_hash

from backend.database.connection import get_connection

load_dotenv()

VERIFICATION_CODE_TTL = timedelta(minutes=10)
def load_secret_key(env=None) -> str:
    """SCRUM-62: the JWT signing key must come from the environment (.env).

    There is no built-in fallback any more: a key written in the source code
    is public on GitHub, so anyone could forge tokens with it. If the key is
    missing the server refuses to start with a clear message.
    """
    value = (env if env is not None else os.environ).get("JWT_SECRET_KEY", "")
    value = (value or "").strip()
    if not value:
        raise RuntimeError(
            "JWT_SECRET_KEY is not set. Add JWT_SECRET_KEY=<a long random value> "
            "to the .env file in the project root, then start the server again."
        )
    if value == "dietrx-secret-key":
        raise RuntimeError(
            "JWT_SECRET_KEY still uses the old public default value. "
            "Put a new long random value in .env."
        )
    return value


SECRET_KEY = load_secret_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


class RegisterRequest(BaseModel):
    name: str
    email: str
    age: int
    gender: str
    height: float
    weight: float
    password: str
    confirm_password: str


class VerificationRequest(BaseModel):
    email: str
    code: str


class ResendVerificationCodeRequest(BaseModel):
    email: str


class LoginRequest(BaseModel):
    email: str
    password: str


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
        f"{code}. It expires in "
        f"{int(VERIFICATION_CODE_TTL.total_seconds() // 60)} minutes."
    )

    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() not in {
        "0", "false", "no"
    }

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
        if use_tls:
            smtp.starttls()
        if smtp_username and smtp_password:
            smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)


def _normalise_email(email: str) -> str:
    return email.strip().lower()


def _utc_now() -> datetime:
    """Return a UTC value compatible with Oracle TIMESTAMP."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_duplicate_email(error: oracledb.Error) -> bool:
    error_info = error.args[0] if error.args else None
    return getattr(error_info, "code", None) == 1


@router.get("/ping")
def auth_ping():
    return {"message": "Authentication router is working"}


@router.post("/register", status_code=201)
def register_user(data: RegisterRequest):
    logger.info("registration started")

    email = _normalise_email(data.email)
    name = data.name.strip()
    gender = data.gender.strip()

    if not all([
        name, email, gender, data.password, data.confirm_password
    ]):
        raise HTTPException(
            status_code=400,
            detail="All registration fields are required.",
        )

    if data.password != data.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")

    verification_code = generate_verification_code()
    connection = get_connection()

    if connection is None:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed.",
        )

    cursor = connection.cursor()

    try:
        password_hash = generate_password_hash(data.password)
        verification_code_hash = hash_verification_code(verification_code)

        cursor.execute(
            """
            INSERT INTO patient
                (
                    name, email, age, gender, height, weight, password,
                    verification_code_hash, verification_code_expires_at,
                    is_verified
                )
            VALUES
                (
                    :name, :email, :age, :gender, :height, :weight,
                    :password, :verification_code_hash,
                    :verification_code_expires_at, 0
                )
            """,
            name=name,
            email=email,
            age=data.age,
            gender=gender,
            height=data.height,
            weight=data.weight,
            password=password_hash,
            verification_code_hash=verification_code_hash,
            verification_code_expires_at=_utc_now() + VERIFICATION_CODE_TTL,
        )

        try:
            send_verification_email(email, verification_code)
        except Exception as error:
            logger.exception("Verification email sending failed")
            connection.rollback()
            raise HTTPException(
                status_code=503,
                detail=(
                    "Unable to send the verification email. "
                    "Please try again later."
                ),
            ) from error

        connection.commit()
        return {
            "message": (
                "Registration successful. "
                "Check your email for a verification code."
            )
        }

    except HTTPException:
        raise
    except oracledb.Error as error:
        connection.rollback()
        if _is_duplicate_email(error):
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists.",
            ) from error
        logger.exception("Registration failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to register account.",
        ) from error
    finally:
        cursor.close()
        connection.close()


@router.post("/verify-email")
def verify_email(data: VerificationRequest):
    email = _normalise_email(data.email)
    code = data.code.strip()

    if not email or not code:
        raise HTTPException(
            status_code=400,
            detail="Email and verification code are required.",
        )

    connection = get_connection()
    if connection is None:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed.",
        )

    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                patient_id, is_verified, verification_code_hash,
                verification_code_expires_at
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
            raise HTTPException(
                status_code=409,
                detail="This email address is already verified.",
            )

        if not code_hash or not expires_at or expires_at <= _utc_now():
            raise HTTPException(
                status_code=400,
                detail=(
                    "The verification code has expired. "
                    "Request a new code."
                ),
            )

        try:
            code_matches = check_password_hash(code_hash, code)
        except (TypeError, ValueError):
            code_matches = False

        if not code_matches:
            raise HTTPException(
                status_code=400,
                detail="Invalid verification code.",
            )

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
        raise HTTPException(
            status_code=500,
            detail="Unable to verify email.",
        ) from error
    finally:
        cursor.close()
        connection.close()


@router.post("/resend-verification-code")
def resend_verification_code(data: ResendVerificationCodeRequest):
    email = _normalise_email(data.email)

    if not email:
        raise HTTPException(status_code=400, detail="Email is required.")

    verification_code = generate_verification_code()
    connection = get_connection()

    if connection is None:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed.",
        )

    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT patient_id, is_verified
            FROM patient
            WHERE email = :email
            """,
            email=email,
        )

        patient = cursor.fetchone()
        if patient is None:
            raise HTTPException(status_code=404, detail="Account not found.")

        patient_id, is_verified = patient

        if is_verified == 1:
            raise HTTPException(
                status_code=409,
                detail="This email address is already verified.",
            )

        cursor.execute(
            """
            UPDATE patient
            SET verification_code_hash = :code_hash,
                verification_code_expires_at = :expires_at
            WHERE patient_id = :patient_id
            """,
            code_hash=hash_verification_code(verification_code),
            expires_at=_utc_now() + VERIFICATION_CODE_TTL,
            patient_id=patient_id,
        )

        try:
            send_verification_email(email, verification_code)
        except Exception as error:
            connection.rollback()
            raise HTTPException(
                status_code=503,
                detail=(
                    "Unable to send the verification email. "
                    "Please try again later."
                ),
            ) from error

        connection.commit()
        return {"message": "Verification code sent to your email."}

    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        raise HTTPException(
            status_code=500,
            detail="Unable to send the verification code.",
        ) from error
    finally:
        cursor.close()
        connection.close()


@router.post("/login")
def login_user(data: LoginRequest):
    email = _normalise_email(data.email)
    password = data.password

    if not email or not password:
        raise HTTPException(
            status_code=400,
            detail="Email and password are required.",
        )

    connection = get_connection()
    if connection is None:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed.",
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
                detail="Invalid email or password.",
            )

        patient_id, name, patient_email, password_hash, is_verified = patient

        if not check_password_hash(password_hash, password):
            raise HTTPException(
                status_code=401,
                detail="Invalid email or password.",
            )

        if not is_verified:
            raise HTTPException(
                status_code=403,
                detail="Please verify your email before logging in.",
            )

        session_token = secrets.token_urlsafe(32)
        access_token = create_access_token({
            "sub": str(patient_id),
            "role": "PATIENT",
            "typ": "access",
        })

        return {
            "message": "Login successful.",
            "session_token": session_token,
            "access_token": access_token,
            "user": {
                "patient_id": patient_id,
                "name": name,
                "email": patient_email,
                "role": "PATIENT",
            },
        }

    finally:
        cursor.close()
        connection.close()


# SCRUM-61: Admin login. Admin accounts are provisioned separately;
# there is intentionally no public admin registration endpoint.
@router.post("/admin/login")
def login_admin(data: LoginRequest):
    email = _normalise_email(data.email)
    password = data.password

    if not email or not password:
        raise HTTPException(
            status_code=400,
            detail="Email and password are required.",
        )

    connection = get_connection()
    if connection is None:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed.",
        )

    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT admin_id, email, password_hash
            FROM ADMIN_USER
            WHERE LOWER(email) = :email
            """,
            email=email,
        )

        admin = cursor.fetchone()

        # Use the same response for unknown email and incorrect password.
        if admin is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid email or password.",
            )

        admin_id, admin_email, password_hash = admin

        try:
            password_matches = check_password_hash(password_hash, password)
        except (TypeError, ValueError):
            password_matches = False

        if not password_matches:
            raise HTTPException(
                status_code=401,
                detail="Invalid email or password.",
            )

        access_token = create_access_token({
            "sub": str(admin_id),
            "role": "ADMIN",
            "typ": "access",
        })

        return {
            "message": "Admin login successful.",
            "access_token": access_token,
            "user": {
                "admin_id": admin_id,
                "email": admin_email,
                "role": "ADMIN",
            },
        }

    except HTTPException:
        raise
    except oracledb.Error as error:
        logger.exception("Admin login database operation failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to log in.",
        ) from error
    finally:
        cursor.close()
        connection.close()


@router.post("/logout")
def logout_user():
    return {"message": "Logout successful."}
