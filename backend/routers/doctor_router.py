"""SCRUM-44: Professional (doctor) authentication for role-based access control.

Doctors are PRE-EXISTING / PROVISIONED accounts. There is deliberately no
public doctor registration and no doctor email-verification endpoint: the SRS
does not define either, and provisioning happens out-of-band (an administrator
or DBA sets doctor.email and doctor.password_hash directly).

A doctor row whose ``password_hash`` is NULL cannot authenticate at all, so the
many existing DOCTOR rows that exist purely as prescription references remain
unusable as login accounts.

Authentication is kept entirely separate from the patient flow:
  * patient login lives in backend.routers.auth_router (queries PATIENT)
  * doctor login lives here (queries DOCTOR)
No query ever spans both tables, so a principal can never be resolved against
the wrong table.

The issued token carries ``role = "DOCTOR"``. Because PATIENT_ID and DOCTOR_ID
share the same numeric space, the role claim is what disambiguates the subject
during authorization.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from jose import JWTError, jwt
from pydantic import BaseModel
from werkzeug.security import check_password_hash

from backend.database.connection import get_connection
from backend.routers.auth_router import ALGORITHM, SECRET_KEY, create_access_token

router = APIRouter(prefix="/auth/doctor", tags=["Doctor Authentication"])


class DoctorLoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login")
def doctor_login(data: DoctorLoginRequest):
    email = data.email.strip().lower()

    connection = get_connection()
    if connection is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = connection.cursor()
    try:
        # Never selects the hash into application logic beyond comparison.
        cursor.execute(
            """
            SELECT doctor_id, name, email, password_hash
            FROM doctor
            WHERE LOWER(email) = :email
            """,
            email=email,
        )
        doctor = cursor.fetchone()

        # Uniform 401 for unknown account AND wrong password (no user enumeration).
        if doctor is None:
            raise HTTPException(status_code=401, detail="Invalid email or password.")

        doctor_id, name, doctor_email, password_hash = doctor

        # A provisioned doctor without a credential can never log in.
        if not password_hash:
            raise HTTPException(status_code=401, detail="Invalid email or password.")

        if not check_password_hash(password_hash, data.password):
            raise HTTPException(status_code=401, detail="Invalid email or password.")

        access_token = create_access_token(
            {"sub": str(doctor_id), "role": "DOCTOR", "typ": "access"}
        )

        return {
            "message": "Login successful.",
            "access_token": access_token,
            "user": {
                "doctor_id": doctor_id,
                "name": name,
                "email": doctor_email,
                "role": "DOCTOR",
            },
        }
    finally:
        cursor.close()
        connection.close()
