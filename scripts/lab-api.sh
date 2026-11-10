#!/usr/bin/env bash
# Runs the target API inside the lab network (Phase 3.5C-2).
#   scripts/lab-api.sh up        build the image if needed, start, wait until healthy
#   scripts/lab-api.sh down      stop and remove the container (SIGTERM: telemetry is flushed)
#   scripts/lab-api.sh restart   down + up (e.g. with a different ABR_VARIANTS)
#   scripts/lab-api.sh logs      follow the container log
# Variables passed through from this shell: ABR_VARIANTS, ABR_CALIBRATION, PG_POOL_MAX,
# EXPERIMENT_RUN_ID, ABR_RUNTIME_SAMPLE_MS. Telemetry is written to data/raw/ as usual.
#   ABR_VARIANTS='{"users.list":"healthy"}' scripts/lab-api.sh restart
source "$(dirname "${BASH_SOURCE[0]}")/lab-common.sh"
mkdir -p "$ABR_ROOT/data"
api() { compose --profile api "$@"; }
case "${1:-}" in
  up)
    api up -d --build --wait api
    echo "target API is up in the lab network: http://127.0.0.1:3000 (metrics :9464)"
    ;;
  down)
    api stop --timeout 30 api >/dev/null
    api rm -f api >/dev/null
    echo "target API stopped"
    ;;
  restart)
    "$0" down
    "$0" up
    ;;
  logs)
    api logs -f api
    ;;
  *)
    echo "usage: scripts/lab-api.sh up|down|restart|logs" >&2
    exit 2
    ;;
esac
