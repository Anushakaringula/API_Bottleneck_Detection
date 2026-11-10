"""Control of the local lab for campaigns: reset, start/stop the target API, snapshots.

Wraps scripts/lab-*.sh and docker. Kept behind a small class so the orchestrator can be
tested with a fake lab.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "infrastructure" / "docker-compose.yml"


def find_bash() -> str:
    """Git Bash on Windows (the System32 bash.exe is WSL, which may have no distribution)."""
    if os.environ.get("ABR_BASH"):
        return os.environ["ABR_BASH"]
    if platform.system() == "Windows":
        for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe",
                          r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if Path(candidate).is_file():
                return candidate
    found = shutil.which("bash")
    if not found:
        raise RuntimeError("bash not found; set ABR_BASH")
    return found


class LabError(RuntimeError):
    pass


class Lab:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.bash = find_bash()

    def _script(self, name: str, *args: str, env: dict[str, str] | None = None, timeout: float = 600) -> str:
        result = subprocess.run([self.bash, str(self.root / "scripts" / name), *args], cwd=self.root,
                                env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise LabError(f"{name} {' '.join(args)} failed ({result.returncode}): {(result.stderr or result.stdout).strip()[-800:]}")
        return result.stdout

    def _docker(self, *args: str, timeout: float = 120) -> str:
        result = subprocess.run(["docker", *args], cwd=self.root, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise LabError(f"docker {' '.join(args[:4])} failed: {(result.stderr or result.stdout).strip()[-800:]}")
        return result.stdout

    def psql(self, sql: str) -> str:
        return self._docker("compose", "-f", str(COMPOSE_FILE), "exec", "-T", "postgres",
                            "psql", "-U", "lab", "-d", "lab", "-X", "-q", "-t", "-A", "-c", sql)

    def reset(self) -> None:
        self._script("lab-reset.sh")

    def api_up(self, variants: dict[str, str], run_id: str) -> None:
        env = {"ABR_VARIANTS": json.dumps(variants), "EXPERIMENT_RUN_ID": run_id, "ABR_CALIBRATION": "", "ABR_RUNTIME_SAMPLE_MS": "1000"}
        self._script("lab-api.sh", "up", env=env, timeout=900)

    def api_down(self) -> None:
        self._script("lab-api.sh", "down", timeout=180)

    def api_logs_tail(self, lines: int = 20) -> str:
        try:
            return self._docker("compose", "-f", str(COMPOSE_FILE), "--profile", "api", "logs", "--no-color", "--tail", str(lines), "api")
        except LabError as error:
            return str(error)

    def seed_info(self) -> dict[str, str]:
        rows = self.psql("SELECT key || '=' || value FROM lab_meta ORDER BY key;").splitlines()
        return dict(row.split("=", 1) for row in rows if "=" in row)

    def db_statistics(self) -> dict[str, Any]:
        """Database-side ground truth for one run (pg_stat_statements is reset by lab-reset)."""
        sql = """SELECT json_build_object(
          'statements', (SELECT coalesce(json_agg(s ORDER BY s.total_exec_ms DESC), '[]') FROM (
              SELECT left(query, 300) AS query, calls, round(total_exec_time::numeric, 3) AS total_exec_ms,
                     round(mean_exec_time::numeric, 4) AS mean_exec_ms, rows, shared_blks_hit, shared_blks_read
                FROM pg_stat_statements WHERE dbid = (SELECT oid FROM pg_database WHERE datname = 'lab')
               ORDER BY total_exec_time DESC LIMIT 25) s),
          'database', (SELECT row_to_json(d) FROM (SELECT xact_commit, xact_rollback, blks_read, blks_hit, tup_returned, tup_fetched
                         FROM pg_stat_database WHERE datname = 'lab') d),
          'indexes', (SELECT json_agg(indexname ORDER BY indexname) FROM pg_indexes WHERE schemaname = 'public'))"""
        return json.loads(self.psql(" ".join(sql.split())))

    def environment(self) -> dict[str, Any]:
        def safe(fn):
            try:
                return fn()
            except Exception as error:  # recorded, not fatal
                return f"unavailable: {error}"
        return {
            "docker_server_version": safe(lambda: self._docker("version", "--format", "{{.Server.Version}}").strip()),
            "target_api_image_id": safe(lambda: self._docker("image", "inspect", "abr-target-api:lab", "--format", "{{.Id}}").strip()),
            "postgres_version": safe(lambda: self.psql("SHOW server_version;").strip()),
            "git_commit": safe(lambda: subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True).stdout.strip()),
            "git_dirty": safe(lambda: bool(subprocess.run(["git", "status", "--porcelain"], cwd=self.root, capture_output=True, text=True).stdout.strip())),
        }

    def registered_variants(self) -> dict[str, list[str]]:
        """Operations and variants the target API knows, read from its own registry."""
        script = "require('./src/routes/domain.js'); console.log(JSON.stringify(require('./src/lab/variants').operations()))"
        result = subprocess.run(["node", "-e", script], cwd=self.root / "target-api", capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise LabError(f"cannot read the variant registry: {result.stderr.strip()}")
        return json.loads(result.stdout)
