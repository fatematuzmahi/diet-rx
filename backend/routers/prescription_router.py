"""SCRUM-46: Prescription Management endpoints.

Endpoints
---------
Patient (self-submitted prescriptions only, identity from the token):
    POST   /api/prescriptions/me            add (JSON or multipart upload)
    GET    /api/prescriptions/me            list my prescriptions
    GET    /api/prescriptions/{id}          read one I own
    PUT    /api/prescriptions/{id}          edit one I own (self-submitted)
    GET    /api/prescriptions/{id}/file     download my uploaded original

Doctor (prescriptions they issued, identity from the token):
    GET    /api/prescriptions/doctor/mine   list prescriptions I issued
    GET    /api/prescriptions/{id}          read one I issued
    PUT    /api/prescriptions/{id}          edit one I issued
    GET    /api/prescriptions/{id}/file     download original of one I issued

Ownership & authorization (reusing the SCRUM-44 security layer unchanged)
------------------------------------------------------------------------
* Identity is ALWAYS the verified access token subject_id + role. No endpoint
  trusts patient_id / doctor_id / source / validated from the request.
* 401 -- missing/malformed/expired/bad-signature/wrong-purpose token.
* 403 -- authenticated but wrong role for a role-gated endpoint.
* PATIENT can only touch rows where patient_id == subject AND source == PATIENT.
  Doctor-issued rows are invisible to the patient (owner+source filter -> 404).
* DOCTOR can only touch rows where doctor_id == subject AND source == DOCTOR.
* Patient self-submissions stay validated = 0; this router never writes VALIDATED.

Uploaded originals are stored in secure on-disk storage outside the web root;
the DB holds only a reference. /file streams the bytes with an attachment
disposition after the same ownership check.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import oracledb
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from backend.core.file_storage import (
    UploadValidationError,
    delete_file,
    resolve_path,
    validate_and_store,
)
from backend.core.security import (
    Principal,
    require_authenticated,
    require_doctor,
    require_patient,
)
from backend.database.connection import get_connection
from backend.schemas.prescription import (
    SOURCE_DOCTOR,
    SOURCE_PATIENT,
    ALLOWED_STATUSES,
    STATUS_ACTIVE,
    DECISION_SUPPORT_DISCLAIMER,
    PrescriptionCreate,
    PrescriptionListResponse,
    PrescriptionResponse,
    PrescriptionUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prescriptions", tags=["Prescriptions"])

# Column order MUST match the unpacking in _row_to_response().
_COLS = (
    "prescription_id, patient_id, doctor_id, upload_date, restrictions, "
    "validated, source, status, updated_at, "
    "file_path, file_name, file_mime_type, file_size_bytes"
)


def _require_connection():
    """Return a live Oracle connection or raise 503 (distinct from 401/403)."""
    connection = get_connection()
    if connection is None:
        raise HTTPException(status_code=503, detail="Database connection failed")
    return connection


def _subject_int(principal: Principal) -> int:
    """Coerce the verified token subject to an int id or 401 if it is not."""
    try:
        return int(principal.subject)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has an invalid subject.",
        )


def _utc_now_naive() -> datetime:
    """UTC now compatible with Oracle TIMESTAMP (no tzinfo), matching SCRUM-44/45."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clob_to_str(value) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(value)
    except Exception:  # pragma: no cover - defensive
        return None

def _row_to_response(row) -> PrescriptionResponse:
    """Map a PRESCRIPTION row (in _COLS order) to the response model.

    VALIDATED (NUMBER(1) 0/1) is surfaced as a bool and is NEVER written by this
    router: patient self-submissions stay unvalidated until an authorized review.
    """
    (
        prescription_id, patient_id, doctor_id, upload_date, restrictions,
        validated, source, prescription_status, updated_at,
        file_path, file_name, file_mime_type, file_size_bytes,
    ) = row
    return PrescriptionResponse(
        prescription_id=prescription_id,
        patient_id=patient_id,
        doctor_id=doctor_id,
        source=source,
        status=prescription_status or STATUS_ACTIVE,
        validated=bool(validated),
        restrictions=_clob_to_str(restrictions),
        upload_date=upload_date,
        updated_at=updated_at,
        file_name=file_name,
        file_mime_type=file_mime_type,
        file_size_bytes=file_size_bytes,
        has_file=bool(file_path),
    )


# --------------------------------------------------------------------------- #
# Patient: POST /api/prescriptions/me  (add, JSON or multipart upload)
# --------------------------------------------------------------------------- #
@router.post("/me", response_model=PrescriptionResponse, status_code=201)
async def create_my_prescription(request: Request, principal: Principal = Depends(require_patient)):
    """Add a prescription owned by the authenticated patient (self-submission).

    Accepts either JSON (``application/json``) with a ``restrictions`` string, or
    ``multipart/form-data`` with an optional ``file`` upload plus a ``restrictions``
    field. Owner is the token subject; ``source`` is forced to PATIENT and
    ``doctor_id`` stays NULL. ``validated`` is written as 0 and never settable.
    """
    patient_id = _subject_int(principal)
    now = _utc_now_naive()

    content_type = request.headers.get("content-type", "")
    stored = None
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        restrictions_raw = form.get("restrictions")
        status_raw = form.get("status")
        upload = form.get("file")
        if upload is not None and getattr(upload, "filename", ""):
            try:
                content = await upload.read()
                stored = validate_and_store(patient_id, upload.filename, content)
            except UploadValidationError as error:
                raise HTTPException(status_code=400, detail=str(error))
    else:
        try:
            body = await request.json()
        except Exception:
            body = {}
        restrictions_raw = body.get("restrictions")
        status_raw = body.get("status")

    payload = PrescriptionCreate(
        restrictions=restrictions_raw if isinstance(restrictions_raw, str) else None,
        status=status_raw if isinstance(status_raw, str) else None,
    )
    restrictions = payload.normalised_restrictions()
    status_value = payload.validated_status()

    connection = _require_connection()
    cursor = connection.cursor()
    try:
        # PRESCRIPTION_ID is a GENERATED ALWAYS AS IDENTITY column: Oracle
        # allocates the value itself, so the column is deliberately NOT in the
        # column list and no sequence is referenced. The generated key is read
        # back with RETURNING ... INTO, and cursor.var(int).getvalue() returns a
        # one-element LIST, so the id is the first element.
        prescription_id_var = cursor.var(int)
        cursor.execute(
            """
            INSERT INTO prescription (
                patient_id, doctor_id, upload_date,
                restrictions, validated, source, status, updated_at,
                file_path, file_name, file_mime_type, file_size_bytes
            ) VALUES (
                :patient_id, NULL, :upload_date,
                :restrictions, 0, :source, :status, :updated_at,
                :file_path, :file_name, :file_mime_type, :file_size_bytes
            )
            RETURNING prescription_id INTO :new_id
            """,
            patient_id=patient_id, upload_date=now, restrictions=restrictions,
            source=SOURCE_PATIENT, status=status_value, updated_at=now,
            file_path=stored.relative_path if stored else None,
            file_name=stored.file_name if stored else None,
            file_mime_type=stored.mime_type if stored else None,
            file_size_bytes=stored.size_bytes if stored else None,
            new_id=prescription_id_var,
        )
        new_id = int(prescription_id_var.getvalue()[0])
        connection.commit()

        cursor.execute(
            f"SELECT {_COLS} FROM prescription WHERE prescription_id = :id",
            id=new_id,
        )
        row = cursor.fetchone()
        return _row_to_response(row)
    except HTTPException:
        connection.rollback()
        if stored is not None:
            delete_file(stored.relative_path)
        raise
    except oracledb.Error as error:
        connection.rollback()
        if stored is not None:
            delete_file(stored.relative_path)
        logger.error("SCRUM-46 patient create failed: %s", getattr(error.args[0] if error.args else None, "code", None))
        raise HTTPException(status_code=500, detail="Unable to save the prescription.")
    finally:
        cursor.close()
        connection.close()

# --------------------------------------------------------------------------- #
# Patient: GET /api/prescriptions/me  (list my self-submitted prescriptions)
# --------------------------------------------------------------------------- #
@router.get("/me", response_model=PrescriptionListResponse)
def list_my_prescriptions(principal: Principal = Depends(require_patient)):
    """List the authenticated patient's OWN self-submitted prescriptions.

    Doctor-issued rows are filtered out by the owner + source predicate, so a
    patient can never see prescriptions they did not submit themselves.
    """
    patient_id = _subject_int(principal)
    connection = _require_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            f"SELECT {_COLS} FROM prescription "
            "WHERE patient_id = :patient_id AND source = :source "
            "ORDER BY upload_date DESC, prescription_id DESC",
            patient_id=patient_id, source=SOURCE_PATIENT,
        )
        rows = cursor.fetchall()
        return PrescriptionListResponse(
            prescriptions=[_row_to_response(r) for r in rows]
        )
    finally:
        cursor.close()
        connection.close()


# --------------------------------------------------------------------------- #
# Doctor: GET /api/prescriptions/doctor/mine  (list prescriptions I issued)
# --------------------------------------------------------------------------- #
@router.get("/doctor/mine", response_model=PrescriptionListResponse)
def list_my_issued_prescriptions(principal: Principal = Depends(require_doctor)):
    """List prescriptions the authenticated doctor issued (doctor_id == subject)."""
    doctor_id = _subject_int(principal)
    connection = _require_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            f"SELECT {_COLS} FROM prescription "
            "WHERE doctor_id = :doctor_id AND source = :source "
            "ORDER BY upload_date DESC, prescription_id DESC",
            doctor_id=doctor_id, source=SOURCE_DOCTOR,
        )
        rows = cursor.fetchall()
        return PrescriptionListResponse(
            prescriptions=[_row_to_response(r) for r in rows]
        )
    finally:
        cursor.close()
        connection.close()


# --------------------------------------------------------------------------- #
# Shared read / update / download — ownership resolved from the token role.
# --------------------------------------------------------------------------- #
def _load_owned(principal: Principal, prescription_id: int):
    """Fetch one prescription the principal is authorized to view/own.

    Returns ``(connection, cursor, row)``. Raises 404 (not 403) when the row does
    not belong to the caller so cross-owner access cannot be probed. The caller
    MUST close both cursor and connection in a ``finally``.
    """
    role = principal.role
    subject = _subject_int(principal)
    if role == "PATIENT":
        owner_sql = "patient_id = :subject AND source = :source"
        binds = {"id": prescription_id, "subject": subject, "source": SOURCE_PATIENT}
    elif role == "DOCTOR":
        owner_sql = "doctor_id = :subject AND source = :source"
        binds = {"id": prescription_id, "subject": subject, "source": SOURCE_DOCTOR}
    else:
        raise HTTPException(status_code=403, detail="Role not permitted.")

    connection = _require_connection()
    cursor = connection.cursor()
    cursor.execute(
        f"SELECT {_COLS} FROM prescription "
        f"WHERE prescription_id = :id AND {owner_sql}",
        **binds,
    )
    row = cursor.fetchone()
    if row is None:
        cursor.close()
        connection.close()
        raise HTTPException(status_code=404, detail="Prescription not found.")
    return connection, cursor, row


@router.get("/{prescription_id}", response_model=PrescriptionResponse)
def read_prescription(prescription_id: int, principal: Principal = Depends(require_authenticated)):
    """Read a single prescription the caller owns (patient self or doctor issued)."""
    connection, cursor, row = _load_owned(principal, prescription_id)
    try:
        return _row_to_response(row)
    finally:
        cursor.close()
        connection.close()

@router.put("/{prescription_id}", response_model=PrescriptionResponse)
def update_prescription(
    prescription_id: int,
    payload: PrescriptionUpdate,
    principal: Principal = Depends(require_authenticated),
):
    """Update a prescription the caller owns.

    Only ``restrictions`` and ``status`` may change. ``validated`` is never
    written here, so a patient's self-submission remains unvalidated. A patient
    can never reach a doctor-issued row (owner + source filter -> 404).
    """
    connection, cursor, row = _load_owned(principal, prescription_id)
    try:
        new_status = payload.validated_status()
        new_restrictions = payload.normalised_restrictions()

        current_status = row[7] or STATUS_ACTIVE
        current_restrictions = _clob_to_str(row[4])
        status_to_set = new_status if new_status is not None else current_status
        restrictions_to_set = (
            new_restrictions if payload.restrictions is not None else current_restrictions
        )

        cursor.execute(
            "UPDATE prescription SET restrictions = :restrictions, "
            "status = :status, updated_at = :updated_at WHERE prescription_id = :id",
            restrictions=restrictions_to_set, status=status_to_set,
            updated_at=_utc_now_naive(), id=prescription_id,
        )
        connection.commit()

        cursor.execute(
            f"SELECT {_COLS} FROM prescription WHERE prescription_id = :id",
            id=prescription_id,
        )
        return _row_to_response(cursor.fetchone())
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        logger.error("SCRUM-46 update failed: %s", getattr(error.args[0] if error.args else None, "code", None))
        raise HTTPException(status_code=500, detail="Unable to update the prescription.")
    finally:
        cursor.close()
        connection.close()


@router.get("/{prescription_id}/file")
def download_prescription_file(prescription_id: int, principal: Principal = Depends(require_authenticated)):
    """Stream the stored original for a prescription the caller owns.

    Ownership is re-checked here exactly as for read/update, so a file can only
    be downloaded by its owner. Files live outside any served/static directory;
    this endpoint is the only way to reach them.
    """
    connection, cursor, row = _load_owned(principal, prescription_id)
    try:
        relative_path = row[9]
        file_name = row[10] or "prescription"
        mime_type = row[11] or "application/octet-stream"
        if not relative_path:
            raise HTTPException(status_code=404, detail="No file attached to this prescription.")
        try:
            absolute = resolve_path(relative_path)
        except UploadValidationError:
            raise HTTPException(status_code=404, detail="File is no longer available.")
        if not absolute.is_file():
            raise HTTPException(status_code=404, detail="File is no longer available.")
        return FileResponse(
            path=str(absolute),
            media_type=mime_type,
            filename=file_name,
            content_disposition_type="attachment",
        )
    finally:
        cursor.close()
        connection.close()



