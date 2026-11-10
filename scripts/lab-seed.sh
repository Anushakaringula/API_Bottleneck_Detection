#!/usr/bin/env bash
# Rebuilds the template database lab_seed at a given scale, then resets `lab` from it.
#   scripts/lab-seed.sh small|medium|large
#
#   scale    users    products   orders      (order items: about 3 per order)
#   small    2,000    2,000      10,000
#   medium   20,000   10,000     100,000
#   large    100,000  50,000     1,000,000
#
# The data is deterministic: the same scale always produces byte-identical tables
# (target-api/db/seed.sql). Stop the target API first; lab-reset.sh closes its connections.
source "$(dirname "${BASH_SOURCE[0]}")/lab-common.sh"
scale="${1:-}"
case "$scale" in
  small)  users=2000;   products=2000;  orders=10000 ;;
  medium) users=20000;  products=10000; orders=100000 ;;
  large)  users=100000; products=50000; orders=1000000 ;;
  *) echo "usage: scripts/lab-seed.sh small|medium|large" >&2; exit 2 ;;
esac
seed_value=0.42
db_dir="$ABR_ROOT/target-api/db"
started=$(date +%s)

echo "lab-seed: rebuilding lab_seed at scale '$scale' ($users users, $products products, $orders orders)"
lab_psql -d postgres >/dev/null <<'SQL'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('lab', 'lab_seed') AND pid <> pg_backend_pid();
ALTER DATABASE lab_seed WITH IS_TEMPLATE false ALLOW_CONNECTIONS true;
DROP DATABASE lab_seed;
CREATE DATABASE lab_seed;
SQL
lab_psql -d lab_seed -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
lab_psql -d lab_seed < "$db_dir/schema.sql"
echo "lab-seed: loading data..."
lab_psql -d lab_seed -v users="$users" -v products="$products" -v orders="$orders" -v seed="$seed_value" < "$db_dir/seed.sql" >/dev/null
echo "lab-seed: constraints and indexes..."
lab_psql -d lab_seed < "$db_dir/constraints.sql"
echo "lab-seed: VACUUM ANALYZE..."
lab_psql -d lab_seed -c "VACUUM (ANALYZE, FREEZE);"
lab_psql -d lab_seed <<SQL
CREATE TABLE lab_meta (key text PRIMARY KEY, value text NOT NULL);
INSERT INTO lab_meta (key, value) VALUES
  ('seed_version', '1'),
  ('seed_scale', '$scale'),
  ('seed_value', '$seed_value'),
  ('rows', '$users users, $products products, $orders orders'),
  ('seeded_at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
SQL
lab_psql -d postgres -c "ALTER DATABASE lab_seed WITH IS_TEMPLATE true ALLOW_CONNECTIONS false;"
"$ABR_ROOT/scripts/lab-reset.sh"
size=$(lab_psql -d lab -t -A -c "SELECT pg_size_pretty(pg_database_size('lab'));")
echo "lab-seed: done in $(( $(date +%s) - started )) s; database size $size"
