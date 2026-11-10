#!/usr/bin/env bash
# Starts the local lab (PostgreSQL + Redis) and waits until both are healthy.
source "$(dirname "${BASH_SOURCE[0]}")/lab-common.sh"
compose up -d --wait
echo "lab is up: PostgreSQL 127.0.0.1:5433, Redis 127.0.0.1:6380"
"$ABR_ROOT/scripts/lab-status.sh"
