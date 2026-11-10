# Source this file from the repository root or anywhere else:
#   source scripts/env.sh
# It loads .env (if present) and puts the Python packages on PYTHONPATH, so that
#   python3 -m experiment_runner.runner ...
#   python3 -m telemetry_analysis.analyzer ...
# work from any directory.
ABR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ABR_ROOT
if [ -f "$ABR_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ABR_ROOT/.env"
  set +a
fi
export PYTHONPATH="$ABR_ROOT/experiments:$ABR_ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}"
