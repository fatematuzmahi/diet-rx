-- ============================================================================
-- SCRUM-47: Dish DNA — additive DDL for the DISH_DNA table
-- ----------------------------------------------------------------------------
-- The "Dish DNA" is the nutritional fingerprint of a dish: the macro
-- breakdown (protein / carbs / fats / calories), the micronutrient profile
-- (vitamins + minerals) and the metabolic markers (glycemic index, sodium,
-- sugar, fibre) that drive the dynamic health-status badges in the UI.
--
-- Design:
--   * One row per dish. DISH_ID is GENERATED ALWAYS AS IDENTITY, so Oracle
--     allocates the value itself. There is NO named sequence behind it --
--     application code must NEVER insert into this column or reference a
--     sequence such as DISH_DNA_SEQ:
--         INSERT ... (dish_id, ...) VALUES (dish_dna_seq.NEXTVAL, ...)
--     fails with ORA-02289 (sequence does not exist) and ORA-32795 (cannot
--     insert into a generated always identity column). Leave the column out of
--     the INSERT and read the generated key back with
--     RETURNING dish_id INTO :new_id.
--
--   * Units are stored in the column name (grams, milligrams, kcal) so the
--     API never has to guess a scale.
--
--   * VARCHAR2 text fields are CAPPED at their declared length so a bad
--     insert can never raise ORA-12899 (value too large) at request time.
--
-- Idempotency: apply once against a database without the DISH_DNA table.
-- The rollback script dish_dna_scrum47_rollback.sql reverses it.
-- ============================================================================

CREATE TABLE dish_dna (
    -- Identity key. GENERATED ALWAYS AS IDENTITY -> never insert, never
    -- reference a sequence. Read it back with RETURNING dish_id INTO :new_id.
    dish_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- Identity of the dish
    name            VARCHAR2(150) NOT NULL,
    cuisine         VARCHAR2(100),
    category        VARCHAR2(80),
    description     VARCHAR2(500),
    ingredients     CLOB,

    -- Serving basis (all nutrition values below are PER SERVING)
    serving_size_g      NUMBER(8, 2),
    serving_description VARCHAR2(120),

    -- Macro breakdown
    calories      NUMBER(8, 2),
    protein_g     NUMBER(8, 2),
    carbs_g       NUMBER(8, 2),
    fat_g         NUMBER(8, 2),
    fiber_g       NUMBER(8, 2),
    sugar_g       NUMBER(8, 2),

    -- Metabolic / dietary markers
    glycemic_index NUMBER(4, 1),
    sodium_mg      NUMBER(9, 2),
    saturated_fat_g NUMBER(8, 2),

    -- Vitamins
    vitamin_a_mcg NUMBER(9, 2),
    vitamin_c_mg  NUMBER(9, 2),
    vitamin_d_mcg NUMBER(9, 2),
    vitamin_k_mcg NUMBER(9, 2),
    b_vitamin_mg  NUMBER(9, 2),

    -- Minerals
    calcium_mg NUMBER(9, 2),
    iron_mg    NUMBER(9, 2),
    potassium_mg NUMBER(9, 2),

    -- Diet metadata
    diet_tags   VARCHAR2(300),
    allergens   VARCHAR2(300),

    -- Audit
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 1) Lookup index for the search/filter used by GET /api/dish-dna.
CREATE INDEX idx_dish_dna_name ON dish_dna(name);

-- 2) Filter index for the "diabetic friendly" / low-glycemic listing.
CREATE INDEX idx_dish_dna_glycemic_index ON dish_dna(glycemic_index);

-- 3) Filter index for the "high protein" listing.
CREATE INDEX idx_dish_dna_protein ON dish_dna(protein_g);
