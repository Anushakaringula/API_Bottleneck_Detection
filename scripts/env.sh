# Source this file from the repository root or anywhere else:
#   source scripts/env.sh
# It loads .env (if present), puts the Python packages on PYTHONPATH and picks a
# Python 3.10+ interpreter ($ABR_PYTHON), so that
#   $ABR_PYTHON -m experiment_runner.runner ...
#   $ABR_PYTHON -m telemetry_analysis.analyzer ...
# work from any directory. On Ubuntu $ABR_PYTHON is python3.
ABR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ABR_ROOT
# Environment profile: .env (cloud, default) or, for experiments, .env.lab:
#   ABR_ENV_FILE=.env.lab source scripts/env.sh
ABR_ENV_FILE="${ABR_ENV_FILE:-.env}"
case "$ABR_ENV_FILE" in /*|[A-Za-z]:*) ;; *) ABR_ENV_FILE="$ABR_ROOT/$ABR_ENV_FILE" ;; esac
# The file is parsed, not executed: plain KEY=value lines, optional surrounding quotes.
# A variable already set (non-empty) in the environment wins over the file, as with
# Node's --env-file, so e.g. `ABR_PYTHON=... source scripts/env.sh` is respected.
if [ -f "$ABR_ENV_FILE" ]; then
  while IFS= read -r abr_line || [ -n "$abr_line" ]; do
    abr_line="${abr_line%$'\r'}"
    case "$abr_line" in ''|'#'*) continue ;; esac
    abr_key="${abr_line%%=*}"
    [[ "$abr_key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] && [ "$abr_key" != "$abr_line" ] || continue
    [ -n "${!abr_key:-}" ] && continue
    abr_value="${abr_line#*=}"
    if [[ "$abr_value" =~ ^\"(.*)\"$ ]] || [[ "$abr_value" =~ ^\'(.*)\'$ ]]; then abr_value="${BASH_REMATCH[1]}"; fi
    export "$abr_key=$abr_value"
  done < "$ABR_ENV_FILE"
  unset abr_line abr_key abr_value
elif [ "$ABR_ENV_FILE" != "$ABR_ROOT/.env" ]; then
  echo "env.sh: $ABR_ENV_FILE not found" >&2
fi
export ABR_ENV_FILE

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
