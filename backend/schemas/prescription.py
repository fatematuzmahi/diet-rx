"""SCRUM-46: Pydantic schemas for Prescription Management (SRS 3.1.3.x).

Scope
-----
* PATIENT may add / view / edit ONLY their OWN self-submitted prescriptions
  (``source == 'PATIENT'``).
* DOCTOR may add / view / edit ONLY prescriptions they issued
  (``doctor_id == subject``).
* Patients can NEVER modify doctor-issued prescriptions.

The owner and ``source`` are derived by the router from the verified access
token, never trusted from the request body: request models intentionally omit
``patient_id`` / ``doctor_id`` / ``source`` / ``validated``.

Data lives in the single PRESCRIPTION table:

    PRESCRIPTION_ID  NUMBER(22) PK
    PATIENT_ID       NUMBER(22)  NOT NULL  (owner / subject of care)
    DOCTOR_ID        NUMBER(22)  NULL      (NULL for patient self-submissions)
    UPLOAD_DATE      TIMESTAMP
    RESTRICTIONS     CLOB        (structured restriction text, optional)
    VALIDATED        NUMBER(1)   (0 = unvalidated, 1 = validated)
    SOURCE           VARCHAR2(10)  'DOCTOR' | 'PATIENT'
    STATUS           VARCHAR2(20)  'ACTIVE' | 'COMPLETED' | 'CANCELLED'
    UPDATED_AT       TIMESTAMP
    FILE_PATH        VARCHAR2(500)  (relative reference; bytes stored on disk)
    FILE_NAME        VARCHAR2(255)
    FILE_MIME_TYPE   VARCHAR2(100)
    FILE_SIZE_BYTES  NUMBER

Uploaded original files are stored OUTSIDE any served/static directory; the DB
holds only a reference. Every response carries the medical decision-support
disclaimer required by the SRS.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field  # noqa: F401

# Required by the SRS: surfaced on every Prescription response so the system is
# never mistaken for a replacement for professional medical advice.
DECISION_SUPPORT_DISCLAIMER = (
    "DietRx is a decision-support system and does not replace professional "
    "medical advice."
)

SOURCE_DOCTOR = "DOCTOR"
SOURCE_PATIENT = "PATIENT"

STATUS_ACTIVE = "ACTIVE"
STATUS_COMPLETED = "COMPLETED"
STATUS_CANCELLED = "CANCELLED"
ALLOWED_STATUSES = (STATUS_ACTIVE, STATUS_COMPLETED, STATUS_CANCELLED)


class PrescriptionCreate(BaseModel):
    """Patient self-submission payload (no file, no owner, no source)."""

    model_config = ConfigDict(extra="ignore")

    restrictions: Optional[str] = Field(default=None, max_length=4000)
    status: Optional[str] = Field(default=STATUS_ACTIVE)

    def validated_status(self) -> str:
        status = (self.status or STATUS_ACTIVE).strip().upper()
        if status not in ALLOWED_STATUSES:
            status = STATUS_ACTIVE
        return status

    def normalised_restrictions(self) -> Optional[str]:
        if self.restrictions is None:
            return None
        value = self.restrictions.strip()
        return value or None


class PrescriptionUpdate(BaseModel):
    """Editable fields for a prescription the caller already owns.

    ``validated`` is deliberately absent: patients cannot validate their own
    prescriptions (they stay unvalidated until an authorized review), and
    nothing in this endpoint writes the VALIDATED flag.
    """

    model_config = ConfigDict(extra="ignore")

    restrictions: Optional[str] = Field(default=None, max_length=4000)
    status: Optional[str] = None

    def validated_status(self) -> Optional[str]:
        if self.status is None:
            return None
        status = self.status.strip().upper()
        return status if status in ALLOWED_STATUSES else None

    def normalised_restrictions(self) -> Optional[str]:
        if self.restrictions is None:
            return None
        value = self.restrictions.strip()
        return value or None


class PrescriptionResponse(BaseModel):
    """A single prescription as returned to an authorized owner."""

    prescription_id: int
    patient_id: int
    doctor_id: Optional[int] = None
    source: str
    status: str
    validated: bool
    restrictions: Optional[str] = None
    upload_date: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # File reference only; the bytes are stored securely on disk.
    file_name: Optional[str] = None
    file_mime_type: Optional[str] = None
    file_size_bytes: Optional[int] = None
    has_file: bool = False
    disclaimer: str = DECISION_SUPPORT_DISCLAIMER


class PrescriptionListResponse(BaseModel):
    """A list of prescriptions plus the decision-support disclaimer."""

    prescriptions: list[PrescriptionResponse]
    disclaimer: str = DECISION_SUPPORT_DISCLAIMER
