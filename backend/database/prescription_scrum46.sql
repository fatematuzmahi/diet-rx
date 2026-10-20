-- ============================================================================
-- SCRUM-46: Prescription Management — additive DDL for the PRESCRIPTION table
-- ----------------------------------------------------------------------------
-- Approved design:
--   * DOCTOR_ID made NULLABLE so a patient can self-submit (no doctor).
--   * SOURCE distinguishes DOCTOR vs PATIENT submissions (drives RBAC).
--   * Uploaded originals are stored in secure on-disk storage; this table only
--     keeps a reference (FILE_*) plus the structured RESTRICTIONS column.
--   * No new tables (no PRESCRIPTION_FILE, no DOCTOR_PATIENT).
--
-- PRESCRIPTION_ID is GENERATED ALWAYS AS IDENTITY (see schema.sql). It is NOT
-- backed by a named sequence, so application code must never insert into it or
-- reference a sequence such as PRESCRIPTION_SEQ:
--     INSERT ... (prescription_id, ...) VALUES (prescription_seq.NEXTVAL, ...)
-- fails with ORA-02289 (sequence does not exist) and ORA-32795 (cannot insert
-- into a generated always identity column). Leave the column out of the INSERT
-- and read the generated key back with RETURNING prescription_id INTO :id.
--
-- Idempotency: preflight verified none of these objects existed and 0 rows in
-- PRESCRIPTION (so the SOURCE CHECK is vacuously satisfied). Re-running is NOT
-- guarded — apply once against a clean PRESCRIPTION.
-- ============================================================================

-- 1) Relax NOT NULL only (no data loss; existing FK stays, NULL passes it).
ALTER TABLE prescription MODIFY (doctor_id NUMBER NULL);

-- 2) Submitter role. Existing rows backfill to 'DOCTOR'.
ALTER TABLE prescription ADD (source VARCHAR2(10) DEFAULT 'DOCTOR' NOT NULL);

-- 3) Invariant: DOCTOR rows carry doctor_id; PATIENT rows have doctor_id NULL.
ALTER TABLE prescription ADD (
  CONSTRAINT ck_prescription_source CHECK (
    (source = 'DOCTOR'  AND doctor_id IS NOT NULL) OR
    (source = 'PATIENT' AND doctor_id IS NULL)
  )
);

-- 4) Lifecycle status (the existing VALIDATED flag covers review state).
ALTER TABLE prescription ADD (status VARCHAR2(20) DEFAULT 'ACTIVE' NOT NULL);
ALTER TABLE prescription ADD (
  CONSTRAINT ck_prescription_status
  CHECK (status IN ('ACTIVE', 'COMPLETED', 'CANCELLED'))
);

-- 5) Edit tracking.
ALTER TABLE prescription ADD (updated_at TIMESTAMP);

-- 6) Reference to the securely-stored uploaded original (PDF/image).
--    The file bytes live in secure storage, never in the database or web root.
ALTER TABLE prescription ADD (file_path       VARCHAR2(500));
ALTER TABLE prescription ADD (file_name       VARCHAR2(255));
ALTER TABLE prescription ADD (file_mime_type  VARCHAR2(100));
ALTER TABLE prescription ADD (file_size_bytes NUMBER);

-- 7) Per-owner listing indexes (additive, safe).
CREATE INDEX idx_prescription_patient ON prescription(patient_id);
CREATE INDEX idx_prescription_doctor  ON prescription(doctor_id);
