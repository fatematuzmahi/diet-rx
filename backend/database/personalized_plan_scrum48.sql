-- ============================================================================
-- SCRUM-48: Personalized Diet + Activity Plan — additive DDL
-- ----------------------------------------------------------------------------
-- SRS 3.3.6 "Personalized Diet + Activity Plan" requires that the system:
--   1. generates a personalized MEAL plan from the profile, prescription,
--      health conditions, preferences, goals and budget context;
--   2. respects known dietary restrictions and unresolved safety conflicts;
--   3. combines diet guidance with ACTIVITY guidance where appropriate;
--   4. allows the user to REGENERATE a plan after changing goals/preferences;
--   5. STORES the current accepted plan so Module 2 can monitor adherence.
--
-- The base DIET_PLAN and ACTIVITY_PLAN tables already exist (schema.sql) with
-- only a thin skeleton (calorie_target / meal_schedule / exercise_list /
-- duration / intensity). The generated plan needs more than that, so the two
-- tables are EXTENDED (never recreated) with:
--
--   DIET_PLAN
--     status              CURRENT | SUPERSEDED  -- only one CURRENT plan per
--                             patient at a time; regenerating supersedes the
--                             previous one (SRS 3.3.6.5).
--     meal_plan           CLOB  -- JSON array of generated meals.
--     activity_plan       CLOB  -- JSON array of generated activities.
--     profile_snapshot    CLOB  -- JSON of the inputs used (goals, preferences,
--                             conditions, allergies, limitations, budget) so a
--                             stored plan stays explainable.
--     restrictions_applied CLOB -- JSON array of the restrictions honoured.
--     conflict_notes      CLOB  -- JSON array of detected conflicts (SRS 3.3.4).
--     requires_review     NUMBER(1) -- 1 when the plan must be reviewed by a
--                             doctor/nutritionist before use (SRS 3.3.4.5).
--     updated_at          TIMESTAMP
--
--   ACTIVITY_PLAN
--     status              CURRENT | SUPERSEDED (mirrors the parent plan).
--     activity_plan       CLOB  -- JSON array of the condition-aware activities.
--     cautions            CLOB  -- JSON array of caution texts shown to the user.
--     updated_at          TIMESTAMP
--
-- Idempotency: apply once against a database that already has the base
-- DIET_PLAN / ACTIVITY_PLAN tables but not these columns. The rollback script
-- personalized_plan_scrum48_rollback.sql reverses it.
-- ============================================================================

ALTER TABLE diet_plan ADD (
    status               VARCHAR2(20) DEFAULT 'CURRENT',
    meal_plan            CLOB,
    activity_plan        CLOB,
    profile_snapshot     CLOB,
    restrictions_applied CLOB,
    conflict_notes       CLOB,
    requires_review      NUMBER(1) DEFAULT 0 CHECK (requires_review IN (0, 1)),
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE activity_plan ADD (
    status         VARCHAR2(20) DEFAULT 'CURRENT',
    activity_plan  CLOB,
    cautions       CLOB,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Lookup index for "the patient's current plan" (GET /api/plans/me).
CREATE INDEX idx_diet_plan_patient_status ON diet_plan(patient_id, status);

-- Lookup index for the plan history (GET /api/plans/me/history).
CREATE INDEX idx_diet_plan_patient_created ON diet_plan(patient_id, created_at);
