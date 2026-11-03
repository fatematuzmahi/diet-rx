-- =====================================================================================
-- SCRUM-47: Dish DNA — Idempotent seed migration (ORACLE MERGE)
-- ----------------------------------------------------------------------------
-- This script inserts 5 development/demo dishes into the DISH_DNA table using
-- MERGE statements keyed on the table's name column.
--
-- WARNING: The name column is NOT NULL but is NOT UNIQUE. The MERGE ON d.name = s.name
-- is idempotent only when dish names are globally unique (which they are in this set).
-- A sequential rerun will skip existing names but will not raise an error if names
-- collide with pre-existing rows -- it simply will not re-insert.
--
-- Values are taken EXACTLY from backend/routers/dish_dna_router.py lines 276–318,
-- passed through DishDnaResponse.build(). No nutritional or allergen values are
-- invented or medically estimated; these are unvalidated development/demo data.
--
-- Preserved writable fields (26 total, matching _WRITABLE_COLUMNS):
--   name, cuisine, category, description, ingredients, serving_size_g, serving_description,
--   calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g, glycemic_index, sodium_mg,
--   saturated_fat_g, vitamin_a_mcg, vitamin_c_mg, vitamin_d_mcg, vitamin_k_mcg,
--   b_vitamin_mg, calcium_mg, iron_mg, potassium_mg, diet_tags, allergens
--
-- Omitted columns: dish_id (GENERATED ALWAYS AS IDENTITY, let Oracle generate),
--   created_at/updated_at (DEFAULT CURRENT_TIMESTAMP, let defaults apply).
--
-- Critical: empty Python lists for diet_tags/allergens (Steamed Rice) are stored as NULL,
-- NOT as the literal string "[]". All other diet_tags/allergens are stored as-is.
-- ----------------------------------------------------------------------------
-- Run once or repeatedly; duplicate executions are safe for the INSERT-NOT-MATCHED path.
-- ----------------------------------------------------------------------------
MERGE INTO dish_dna d
USING (SELECT 'Steamed Rice' AS name FROM dual) s
ON (d.name = s.name)
WHEN NOT MATCHED THEN
  INSERT (name, cuisine, category, description, ingredients,
          serving_size_g, serving_description, calories, protein_g, carbs_g, fat_g,
          fiber_g, sugar_g, glycemic_index, sodium_mg, saturated_fat_g,
          vitamin_a_mcg, vitamin_c_mg, vitamin_d_mcg, vitamin_k_mcg, b_vitamin_mg,
          calcium_mg, iron_mg, potassium_mg, diet_tags, allergens)
  VALUES ('Steamed Rice', 'Indian', 'Main', 'Plain steamed white rice', 'Rice, water',
          150, '1 serving', 150, 2.5, 33, 0.3, 0.5, 0.1, 73, 1.0, 0.1,
          0, 0, 0, 0, 0, 5, 0.3, 30, NULL, NULL);

MERGE INTO dish_dna d
USING (SELECT 'Rui Fish Curry' AS name FROM dual) s
ON (d.name = s.name)
WHEN NOT MATCHED THEN
  INSERT (name, cuisine, category, description, ingredients,
          serving_size_g, serving_description, calories, protein_g, carbs_g, fat_g,
          fiber_g, sugar_g, glycemic_index, sodium_mg, saturated_fat_g,
          vitamin_a_mcg, vitamin_c_mg, vitamin_d_mcg, vitamin_k_mcg, b_vitamin_mg,
          calcium_mg, iron_mg, potassium_mg, diet_tags, allergens)
  VALUES ('Rui Fish Curry', 'Bengali', 'Main', 'Rui fish in spicy curry sauce', 'Rui fish, spices, coconut milk',
          200, '1 serving', 350, 25, 15, 25, 2, 3, 55, 400, 5,
          200, 10, 2, 5, 3, 40, 3.5, 400, 'non-veg', 'fish');

MERGE INTO dish_dna d
USING (SELECT 'Chicken Curry' AS name FROM dual) s
ON (d.name = s.name)
WHEN NOT MATCHED THEN
  INSERT (name, cuisine, category, description, ingredients,
          serving_size_g, serving_description, calories, protein_g, carbs_g, fat_g,
          fiber_g, sugar_g, glycemic_index, sodium_mg, saturated_fat_g,
          vitamin_a_mcg, vitamin_c_mg, vitamin_d_mcg, vitamin_k_mcg, b_vitamin_mg,
          calcium_mg, iron_mg, potassium_mg, diet_tags, allergens)
  VALUES ('Chicken Curry', 'North Indian', 'Main', 'Tender chicken in aromatic curry', 'Chicken, spices, yogurt',
          200, '1 serving', 420, 30, 12, 28, 1, 3, 50, 380, 8,
          150, 8, 1, 4, 2, 30, 2.5, 350, 'non-veg', 'milk');

MERGE INTO dish_dna d
USING (SELECT 'Lentil Soup (Dal)' AS name FROM dual) s
ON (d.name = s.name)
WHEN NOT MATCHED THEN
  INSERT (name, cuisine, category, description, ingredients,
          serving_size_g, serving_description, calories, protein_g, carbs_g, fat_g,
          fiber_g, sugar_g, glycemic_index, sodium_mg, saturated_fat_g,
          vitamin_a_mcg, vitamin_c_mg, vitamin_d_mcg, vitamin_k_mcg, b_vitamin_mg,
          calcium_mg, iron_mg, potassium_mg, diet_tags, allergens)
  VALUES ('Lentil Soup (Dal)', 'Indian', 'Soup', 'Hearty red lentil soup with spices', 'Red lentils, tomatoes, spices',
          250, '1 bowl', 200, 12, 35, 3.5, 8, 3, 30, 350, 0.5,
          50, 15, 0, 10, 2, 40, 4, 250, 'vegetarian', 'none');

MERGE INTO dish_dna d
USING (SELECT 'Mixed Veg Fry' AS name FROM dual) s
ON (d.name = s.name)
WHEN NOT MATCHED THEN
  INSERT (name, cuisine, category, description, ingredients,
          serving_size_g, serving_description, calories, protein_g, carbs_g, fat_g,
          fiber_g, sugar_g, glycemic_index, sodium_mg, saturated_fat_g,
          vitamin_a_mcg, vitamin_c_mg, vitamin_d_mcg, vitamin_k_mcg, b_vitamin_mg,
          calcium_mg, iron_mg, potassium_mg, diet_tags, allergens)
  VALUES ('Mixed Veg Fry', 'Indian', 'Side', 'Assorted vegetables stir-fried with spices', 'Carrot, beans, peas, spices',
          180, '1 serving', 180, 4, 25, 8, 4, 3, 45, 250, 1,
          300, 40, 0, 5, 1, 30, 1.5, 200, 'vegetarian', 'none');