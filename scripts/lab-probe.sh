#!/usr/bin/env bash
# Lab gate (Phase 3.5C-1): times SELECT 1 and PING from a Node 20 container on the lab's
# Docker network. That is where the target API runs during experiments (Phase 3.5C-2), so
# this is the path its database and cache calls take. Gate: local hosts, p99 <= 2 ms.
# Report: data/results/lab-probe/<timestamp>.json (network_path "lab-network").
#
# For comparison, `cd target-api && npm run lab:probe` measures the path from the host
# shell, which on Windows/macOS also crosses Docker Desktop's port forwarding.
# Extra arguments are passed to the probe, e.g. --samples 2000.
source "$(dirname "${BASH_SOURCE[0]}")/lab-common.sh"
NODE_IMAGE="node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0"
app_dir="$ABR_ROOT/target-api"
data_dir="$ABR_ROOT/data"
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) app_dir="$(cygpath -w "$app_dir")"; data_dir="$(cygpath -w "$data_dir")" ;; esac
mkdir -p "$ABR_ROOT/data/results/lab-probe"
# target-api's dependencies are pure JavaScript, so the host's node_modules run unchanged
# in the Linux container (Phase 3.5C-2 replaces this with a built target-API image).
docker run --rm --network abr-lab_default \
  -v "$app_dir:/app:ro" -v "$data_dir:/data" -w /app \
  -e DATABASE_URL=postgresql://lab:lab@postgres:5432/lab \
  -e REDIS_URL=redis://default:labpass@redis:6379 \
  -e ABR_DATA_DIR=/data -e ABR_ENV_PROFILE=lab -e ABR_PROBE_PATH=lab-network \
  "$NODE_IMAGE" node scripts/lab-probe.mjs "$@"
