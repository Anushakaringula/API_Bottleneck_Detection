# Source this file from the repository root or anywhere else:
#   source scripts/env.sh
# It loads .env (if present), puts the Python packages on PYTHONPATH and picks a
# Python 3.10+ interpreter ($ABR_PYTHON), so that
#   $ABR_PYTHON -m experiment_runner.runner ...
#   $ABR_PYTHON -m telemetry_analysis.analyzer ...
# work from any directory. On Ubuntu $ABR_PYTHON is python3.
ABR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ABR_ROOT
if [ -f "$ABR_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ABR_ROOT/.env"
  set +a
fi

# Native Windows Python (Git Bash) separates PYTHONPATH entries with ';' and needs
# Windows paths; Linux, macOS and WSL use ':'.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    ABR_PYTHONPATH="$(cygpath -w "$ABR_ROOT/experiments");$(cygpath -w "$ABR_ROOT/pipeline")"
    export PYTHONPATH="$ABR_PYTHONPATH${PYTHONPATH:+;$PYTHONPATH}"
    ;;
  *)
    export PYTHONPATH="$ABR_ROOT/experiments:$ABR_ROOT/pipeline${PYTHONPATH:+:$PYTHONPATH}"
    ;;
esac

# First interpreter that really runs Python 3.10+ (the Windows Store "python3" alias
# exists but does not run).
if [ -z "${ABR_PYTHON:-}" ]; then
  for candidate in python3 python; do
    if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
      ABR_PYTHON="$candidate"
      break
    fi
  done
fi
if [ -z "${ABR_PYTHON:-}" ]; then
  echo "env.sh: no Python 3.10+ interpreter found (tried python3, python); set ABR_PYTHON" >&2
  ABR_PYTHON=python3
fi
export ABR_PYTHON
