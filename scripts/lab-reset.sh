#!/usr/bin/env bash
# Puts the lab back to the exact seeded state, for the start of every experiment run:
#  - database `lab` is dropped and recreated as a copy of the template `lab_seed`
#    (byte-identical data, fresh table layout, same planner statistics);
#  - pg_stat_statements is cleared, so database-side counts cover one run only;
#  - Redis is emptied.
# Stop the target API first: its open connections to `lab` are terminated.
source "$(dirname "${BASH_SOURCE[0]}")/lab-common.sh"
terminated="$(lab_psql -d postgres -t -A <<'SQL'
SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname = 'lab' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS lab;
CREATE DATABASE lab TEMPLATE lab_seed;
SQL
)"
lab_psql -d lab -c "SELECT pg_stat_statements_reset();" >/dev/null
lab_redis FLUSHALL >/dev/null
echo "lab reset: database 'lab' recreated from 'lab_seed' (${terminated:-0} open connections closed), query statistics cleared, Redis emptied"
