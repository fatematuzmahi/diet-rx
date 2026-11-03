-- SCRUM-48 rollback: reverse of personalized_plan_scrum48.sql.
--
-- The SCRUM-48 migration only ADDED columns to the pre-existing DIET_PLAN and
-- ACTIVITY_PLAN tables (plus two indexes on DIET_PLAN). Rolling back therefore
-- drops those indexes and columns again. The base tables themselves are kept,
-- because they pre-date SCRUM-48.
--
-- WARNING: this destroys every generated meal plan, activity plan, snapshot and
-- conflict note. Export the data first if it is still needed.
--
-- Order matters: drop the indexes first (they depend on the table columns),
-- then the columns.
DROP INDEX idx_diet_plan_patient_created;
DROP INDEX idx_diet_plan_patient_status;

ALTER TABLE activity_plan DROP (
    status,
    activity_plan,
    cautions,
    updated_at
);

ALTER TABLE diet_plan DROP (
    status,
    meal_plan,
    activity_plan,
    profile_snapshot,
    restrictions_applied,
    conflict_notes,
    requires_review,
    updated_at
);
