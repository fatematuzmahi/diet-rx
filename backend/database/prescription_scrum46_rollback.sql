-- SCRUM-46 rollback: reverse of prescription_scrum46.sql (additive changes only).
-- Drops the 7 added columns, the 2 new CHECK constraints and the 2 new indexes,
-- and restores DOCTOR_ID to NOT NULL. Order matters: drop indexes, then
-- constraints, then columns, then restore nullability last.
DROP INDEX idx_prescription_patient;
DROP INDEX idx_prescription_doctor;

ALTER TABLE prescription DROP CONSTRAINT ck_prescription_status;
ALTER TABLE prescription DROP CONSTRAINT ck_prescription_source;

ALTER TABLE prescription DROP (file_path, file_name, file_mime_type, file_size_bytes);
ALTER TABLE prescription DROP (updated_at);
ALTER TABLE prescription DROP (status);
ALTER TABLE prescription DROP (source);

ALTER TABLE prescription MODIFY (doctor_id NUMBER NOT NULL);
