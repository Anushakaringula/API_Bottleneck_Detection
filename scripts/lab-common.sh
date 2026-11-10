# Shared by scripts/lab-*.sh. Not meant to be run directly.
# Defines: compose (docker compose for the lab), lab_psql (psql inside the postgres
# container, as the lab superuser), lab_redis (redis-cli inside the redis container).
set -euo pipefail
ABR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAB_COMPOSE_FILE="$ABR_ROOT/infrastructure/docker-compose.yml"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    # Git Bash rewrites arguments that look like Unix paths before they reach docker.exe
    # (it would mangle SQL and container paths), so turn that off and pass a Windows path.
    export MSYS_NO_PATHCONV=1
    LAB_COMPOSE_FILE="$(cygpath -w "$LAB_COMPOSE_FILE")"
    ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  echo "lab: docker is not installed or not on PATH (see docs/02-development-setup.md)" >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "lab: the Docker engine is not running; start Docker Desktop and try again" >&2
  exit 1
fi

compose() { docker compose -f "$LAB_COMPOSE_FILE" "$@"; }
lab_psql() { compose exec -T postgres psql -U lab -v ON_ERROR_STOP=1 -X -q "$@"; }
lab_redis() { compose exec -T redis redis-cli -a labpass --no-auth-warning "$@"; }
