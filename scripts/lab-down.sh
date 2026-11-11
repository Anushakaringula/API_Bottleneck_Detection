#!/usr/bin/env bash
# Stops the local lab. The PostgreSQL data volume (including the seeded template) is kept.
# --purge also deletes the volume: the next lab-up starts from an empty cluster and the
# seed has to be rebuilt.
source "$(dirname "${BASH_SOURCE[0]}")/lab-common.sh"
if [ "${1:-}" = "--purge" ]; then
  compose --profile api down --volumes
  echo "lab stopped; data volume deleted"
else
  compose --profile api down
  echo "lab stopped; data volume kept"
fi
