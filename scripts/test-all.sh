#!/usr/bin/env bash
# Runs every test suite. Exits non-zero if any suite fails.
# Tests use temporary folders only; nothing is written under data/.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
cd "$ABR_ROOT"
export PYTHONDONTWRITEBYTECODE=1

echo "== target-api (node --test) =="
(cd target-api && npm test --silent)

echo "== experiments/experiment_runner =="
"$ABR_PYTHON" -m unittest discover -s experiments/experiment_runner/tests

echo "== experiments/campaign =="
"$ABR_PYTHON" -m unittest discover -s experiments/campaign/tests

echo "== pipeline/telemetry_analysis =="
"$ABR_PYTHON" -m unittest discover -s pipeline/telemetry_analysis/tests

echo "ALL TEST SUITES PASSED"
