-- Runs once, when the data volume is first created (docker-entrypoint-initdb.d).
--   lab       the database the target API uses; recreated from lab_seed by scripts/lab-reset.sh
--   lab_seed  the seeded template: filled once by the seed step (Phase 3.5C-2), never used directly
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

CREATE DATABASE lab_seed;
\connect lab_seed
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
-- Records what the seed contains; scripts/lab-status.sh prints it.
CREATE TABLE lab_meta (key text PRIMARY KEY, value text NOT NULL);
INSERT INTO lab_meta (key, value) VALUES ('seed_version', 'empty'), ('seed_scale', 'none');
\connect postgres
-- Only templates may be cloned while nobody is connected to them.
ALTER DATABASE lab_seed WITH IS_TEMPLATE true ALLOW_CONNECTIONS false;
