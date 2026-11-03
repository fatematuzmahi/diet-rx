-- SCRUM-47 rollback: reverse of dish_dna_scrum47.sql.
--
-- The DISH_DNA table and its 3 indexes were ADDED by dish_dna_scrum47.sql, so
-- rolling back simply drops them. Order matters: drop the indexes first (they
-- depend on the table columns), then the table.
--
-- WARNING: this destroys every Dish DNA row. Export the data first if it is
-- still needed.
DROP INDEX idx_dish_dna_protein;
DROP INDEX idx_dish_dna_glycemic_index;
DROP INDEX idx_dish_dna_name;

DROP TABLE dish_dna PURGE;
