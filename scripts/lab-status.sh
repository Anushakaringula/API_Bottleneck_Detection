#!/usr/bin/env bash
# Prints lab versions, health, seed description, and per-table row counts and checksums
# of database `lab`. Two resets of the same seed must print identical checksums.
source "$(dirname "${BASH_SOURCE[0]}")/lab-common.sh"
echo "== containers =="
compose ps --format "table {{.Service}}\t{{.Image}}\t{{.Status}}"
echo "== versions =="
lab_psql -d lab -t -A -c "SHOW server_version;" | sed 's/^/postgres /'
lab_redis INFO server | tr -d '\r' | grep '^redis_version:' | sed 's/:/ /'
echo "== seed (lab_meta) =="
lab_psql -d lab -t -A -F ' = ' -c "SELECT key, value FROM lab_meta ORDER BY key;" 2>/dev/null || echo "(database 'lab' has no lab_meta; run scripts/lab-reset.sh)"
if [ "${1:-}" = "--checksums" ]; then
  # Content checksum per table (order-independent). Slow on the large seed (~3M rows).
  echo "== tables in 'lab': rows and content checksum =="
  lab_psql -d lab -t -A <<'SQL' 2>&1 | sed 's/^NOTICE:  //'
DO $$
DECLARE t record; n bigint; h text;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename LOOP
    EXECUTE format('SELECT count(*), coalesce(md5(string_agg(md5(x::text), '''' ORDER BY md5(x::text))), ''empty'') FROM %I x', t.tablename)
      INTO n, h;
    RAISE NOTICE '% rows=% md5=%', t.tablename, n, h;
  END LOOP;
END $$;
SQL
else
  echo "== tables in 'lab': rows (add --checksums for content checksums) =="
  lab_psql -d lab -t -A <<'SQL' 2>&1 | sed 's/^NOTICE:  //'
DO $$
DECLARE t record; n bigint;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename LOOP
    EXECUTE format('SELECT count(*) FROM %I', t.tablename) INTO n;
    RAISE NOTICE '% rows=%', t.tablename, n;
  END LOOP;
END $$;
SQL
fi
echo "== redis =="
echo "keys: $(lab_redis DBSIZE | tr -d '\r')"
