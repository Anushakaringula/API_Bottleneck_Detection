#!/usr/bin/env python3
"""Run an experiment campaign against the local lab (Phase 3.5D).

  python -m campaign.run --config experiments/configs/campaigns/main.json --dry-run
  python -m campaign.run --config experiments/configs/campaigns/main.json
  python -m campaign.run --resume <campaign-id>          # continue after an interruption

Each run: reset the lab -> start the target API in the lab network with the run's variant
-> open-loop workload (warm-up + measured) -> database statistics -> stop the API
(telemetry flushed). Protocol rules (experiments/protocol.md), switched on per config:
  require_clean_git     refuse to start with uncommitted changes
  discard_warmup_runs   N unlabelled healthy runs at the start of every session
  rerun_excluded        re-run each excluded run once at the end (exclusion rules: quality.py)

Outputs:
  data/raw/campaigns/<campaign-id>/manifest.json         config, run plan, environment
  data/raw/campaigns/<campaign-id>/progress.jsonl         one line per finished run
  data/raw/campaigns/<campaign-id>/exclusions.json        excluded runs and their reasons
  data/raw/campaigns/<campaign-id>/experiments/<run>/     runner artifacts (no ground truth)
  data/raw/campaigns/<campaign-id>/db-stats/<run>.json    database-side statistics
  data/labels/<campaign-id>.jsonl                         ground truth, kept apart
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable

from experiment_runner.runner import DATA_DIR, environment_metadata, execute_experiment, resolve_configuration, utc_now

from . import __version__
from .plan import LABEL_SCHEMA_VERSION, estimate_seconds, expand, label_for, load_config, parse_variant, runner_config, stable_seed
from .quality import exclusion_reasons, exclusion_rules

RERUN_INDEX_BASE = 100000
WARMUP_INDEX_BASE = 900000


def _append(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def preflight(config: dict[str, Any], runs: list[dict[str, Any]], lab, git_dirty: bool | None = None) -> None:
    """Checks before the first run: clean code (if required), right seed scale, variants known to the API."""
    if config.get("require_clean_git") and git_dirty:
        raise SystemExit("protocol: this campaign requires committed code (git status must be clean); commit first")
    if exclusion_rules(config).get("require_ac_power") and hasattr(lab, "ac_online") and lab.ac_online() is False:
        raise SystemExit("protocol: the machine is on battery; plug in the charger (runs on battery are excluded, rule 5)")
    seed = lab.seed_info()
    if seed.get("seed_scale") != config["seed_scale"]:
        raise SystemExit(f"campaign needs seed scale '{config['seed_scale']}', lab has '{seed.get('seed_scale')}': run scripts/lab-seed.sh {config['seed_scale']}")
    registry = lab.registered_variants()
    for run in runs:
        name, _ = parse_variant(run["variant"])
        if name not in registry.get(run["operation"], []):
            raise SystemExit(f"target API has no variant '{name}' for {run['operation']} (available: {registry.get(run['operation'])})")


class Executor:
    """Executes single runs of one campaign and records them."""

    def __init__(self, config, campaign_dir, labels_file, lab, execute, log):
        self.config = config
        self.campaign_dir = campaign_dir
        self.labels_file = labels_file
        self.lab = lab
        self.execute = execute
        self.log = log
        self.manifest = json.loads((campaign_dir / "manifest.json").read_text(encoding="utf-8"))
        self.campaign_id = self.manifest["campaign_id"]
        self.progress_file = campaign_dir / "progress.jsonl"
        self.experiments_dir = campaign_dir / "experiments"
        self.stats_dir = campaign_dir / "db-stats"
        self.stats_dir.mkdir(parents=True, exist_ok=True)
        self.span_file = Path(os.environ.get("ABR_SPAN_FILE") or DATA_DIR / "raw" / "spans" / "otel-spans.jsonl")

    def run(self, run: dict[str, Any], status: str = "completed", label: bool = True, extra: dict[str, Any] | None = None) -> bool:
        """One run. Returns True if it completed (it may still be excluded later)."""
        started = time.monotonic()
        started_at = utc_now()
        experiment_id = None
        extra = extra or {}
        power = []
        try:
            power.append(self.lab.ac_online() if hasattr(self.lab, "ac_online") else None)
            self.lab.reset()
            self.lab.api_up({run["operation"]: run["variant"]}, f"{self.campaign_id}-r{run['run_index']:06d}")
            try:
                image_id = self.lab.image_id() if hasattr(self.lab, "image_id") else None
                raw = runner_config(self.config, run, self.campaign_id)
                summary = self.execute(resolve_configuration(raw), output_root=self.experiments_dir, span_file=self.span_file,
                                       repeat_group_id=f"{self.campaign_id}:{run['condition_id']}", repeat_index=run["repeat"])
                experiment_id = summary["experiment_id"]
                (self.stats_dir / f"{experiment_id}.json").write_text(json.dumps(self.lab.db_statistics(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
            finally:
                self.lab.api_down()
            power.append(self.lab.ac_online() if hasattr(self.lab, "ac_online") else None)
            measured = summary["measured"]
            correlation = summary["telemetry_correlation"]
            if label:
                _append(self.labels_file, {
                    "label_schema_version": LABEL_SCHEMA_VERSION, "campaign_id": self.campaign_id, "experiment_id": experiment_id,
                    "run_index": run["run_index"], "condition_id": run["condition_id"], "operation": run["operation"],
                    "repeat": run["repeat"], "workload_seed": run["workload_seed"], "seed_scale": self.config["seed_scale"],
                    **label_for(run["variant"]), **extra,
                })
            _append(self.progress_file, {
                "run_index": run["run_index"], "experiment_id": experiment_id, "status": status, "condition_id": run["condition_id"],
                "started_at": started_at, "ended_at": utc_now(), "wall_seconds": round(time.monotonic() - started, 2),
                "measured_requests": measured["total_requests"], "failed_requests": measured["failed_requests"],
                "send_lag_p99_ms": (measured.get("send_lag_ms") or {}).get("p99_ms"),
                "correlation_status": correlation["correlation_status"], "image_id": image_id,
                "ac_power": False if False in power else (True if all(power) else None), **extra,
            })
            p50 = measured["median_ms"]
            self.log(f"    done in {time.monotonic() - started:.0f} s: {measured['total_requests']} requests, "
                     f"p50 {'-' if p50 is None else f'{p50:.1f}'} ms, failed {measured['failed_requests']}, correlation {correlation['correlation_status']}")
            return True
        except KeyboardInterrupt:
            self.log("interrupted; stopping the target API")
            try:
                self.lab.api_down()
            finally:
                raise
        except Exception as error:  # a failed run is recorded, the campaign continues
            _append(self.progress_file, {"run_index": run["run_index"], "experiment_id": experiment_id, "status": "failed",
                                         "condition_id": run["condition_id"], "started_at": started_at, "ended_at": utc_now(),
                                         "error": f"{type(error).__name__}: {error}"[:2000], "api_log_tail": self.lab.api_logs_tail()[-2000:], **extra})
            self.log(f"    FAILED: {error}")
            self.log(traceback.format_exc().rstrip())
            try:
                self.lab.api_down()
            except Exception:
                pass
            return False


def evaluate_exclusions(campaign_dir: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Applies the exclusion rules to every completed, labelled run; writes exclusions.json."""
    manifest = json.loads((campaign_dir / "manifest.json").read_text(encoding="utf-8"))
    rules = exclusion_rules(config)
    progress = _read_jsonl(campaign_dir / "progress.jsonl")
    reruns = {p["rerun_of"]: p for p in progress if p.get("rerun_of") is not None and p["status"] == "completed"}
    excluded = []
    for record in progress:
        if record["status"] != "completed":
            continue
        reasons = exclusion_reasons(record, manifest.get("environment", {}), rules)
        if reasons:
            rerun = reruns.get(record["run_index"])
            excluded.append({"experiment_id": record["experiment_id"], "run_index": record["run_index"], "condition_id": record.get("condition_id"),
                             "reasons": reasons, "rerun_of": record.get("rerun_of"),
                             "rerun_experiment_id": rerun["experiment_id"] if rerun else None})
    (campaign_dir / "exclusions.json").write_text(json.dumps({"rules": rules, "excluded": excluded}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return excluded


def run_campaign(config: dict[str, Any], campaign_dir: Path, labels_file: Path, lab,
                 execute: Callable[..., dict[str, Any]] = execute_experiment, limit: int | None = None,
                 log: Callable[[str], None] = print) -> dict[str, int]:
    executor = Executor(config, campaign_dir, labels_file, lab, execute, log)
    runs = executor.manifest["runs"]
    progress = _read_jsonl(executor.progress_file)
    done = {r["run_index"] for r in progress if r["status"] == "completed"}
    counts = {"completed": 0, "failed": 0, "skipped": len([r for r in runs if r["run_index"] in done]), "warmup": 0, "rerun": 0}
    pending = [r for r in runs if r["run_index"] not in done]
    if limit is not None:
        pending = pending[:limit]

    # Discarded warm-up run(s) at the start of every session: the first run after a cold
    # start had the worst client send lag in the pilot. Not labelled, never analysed.
    session = len([p for p in progress if p.get("status") == "discarded_warmup"])
    if pending:
        for k in range(int(config.get("discard_warmup_runs", 0))):
            first = runs[0]
            warmup = {**first, "variant": "healthy", "condition_id": f"{first['operation']}/healthy", "repeat": 0,
                      "run_index": WARMUP_INDEX_BASE + session * 10 + k}
            log(f"[warm-up {k + 1}] discarded run: {warmup['operation']} healthy")
            counts["warmup"] += executor.run(warmup, status="discarded_warmup", label=False)

    for position, run in enumerate(pending, 1):
        log(f"[{position}/{len(pending)}] run {run['run_index']}/{len(runs)}: {run['operation']} (repeat {run['repeat']})")
        if executor.run(run):
            counts["completed"] += 1
        else:
            counts["failed"] += 1

    # Re-run each excluded planned run once, at the end, in a shuffled order (only once all
    # planned runs are done).
    all_done = {r["run_index"] for r in _read_jsonl(executor.progress_file) if r["status"] == "completed"}
    if config.get("rerun_excluded") and all(r["run_index"] in all_done for r in runs):
        by_index = {r["run_index"]: r for r in runs}
        to_rerun = [e for e in evaluate_exclusions(campaign_dir, config)
                    if e["run_index"] in by_index and e["rerun_experiment_id"] is None]
        import random
        random.Random(stable_seed(config["random_seed"], "reruns")).shuffle(to_rerun)
        for k, item in enumerate(to_rerun, 1):
            original = by_index[item["run_index"]]
            rerun = {**original, "run_index": RERUN_INDEX_BASE + original["run_index"]}
            log(f"[re-run {k}/{len(to_rerun)}] excluded run {original['run_index']} ({'; '.join(item['reasons'])}): {original['condition_id']}")
            counts["rerun"] += executor.run(rerun, extra={"rerun_of": original["run_index"]})
    evaluate_exclusions(campaign_dir, config)
    return counts


def git_dirty(root: Path) -> bool:
    import subprocess
    return bool(subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout.strip())


def create_campaign(config: dict[str, Any], config_path: Path, root: Path, lab) -> Path:
    campaign_id = f"{config['name']}-{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    campaign_dir = root / campaign_id
    campaign_dir.mkdir(parents=True, exist_ok=False)
    runs = expand(config)
    if hasattr(lab, "build_image"):
        lab.build_image()  # the recorded image ID must be the one every run uses
    manifest = {
        "campaign_id": campaign_id, "campaign_tool_version": __version__, "created_at": utc_now(),
        "config_file": str(config_path), "config": config, "runs": runs,
        "environment": {**environment_metadata(), **lab.environment(), "lab_seed": lab.seed_info()},
        "labels_file": f"data/labels/{campaign_id}.jsonl",
    }
    (campaign_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return campaign_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, help="campaign config (experiments/configs/campaigns/*.json)")
    parser.add_argument("--resume", help="campaign id to continue")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and estimated duration; run nothing")
    parser.add_argument("--limit", type=int, help="run at most this many pending runs now")
    parser.add_argument("--campaigns-dir", type=Path, default=DATA_DIR / "raw" / "campaigns")
    parser.add_argument("--labels-dir", type=Path, default=DATA_DIR / "labels")
    args = parser.parse_args(argv)
    if bool(args.config) == bool(args.resume):
        parser.error("use either --config or --resume")

    from .lab import ROOT, Lab
    if args.resume:
        campaign_dir = args.campaigns_dir / args.resume
        manifest = json.loads((campaign_dir / "manifest.json").read_text(encoding="utf-8"))
        config = manifest["config"]
        runs = manifest["runs"]
    else:
        config = load_config(args.config)
        runs = expand(config)
    print(f"campaign '{config['name']}': {len(runs)} runs, estimated {estimate_seconds(config, runs) / 60:.0f} min", flush=True)
    if args.dry_run:
        for run in runs:
            print(f"  {run['run_index']:4d}  {run['condition_id']:<40} repeat {run['repeat']}")
        return 0
    lab = Lab()
    preflight(config, runs, lab, git_dirty(ROOT))
    if not args.resume:
        campaign_dir = create_campaign(config, args.config, args.campaigns_dir, lab)
    args.labels_dir.mkdir(parents=True, exist_ok=True)
    campaign_id = campaign_dir.name
    print(f"campaign id: {campaign_id}", flush=True)
    try:
        counts = run_campaign(config, campaign_dir, args.labels_dir / f"{campaign_id}.jsonl", lab, limit=args.limit,
                              log=lambda message: print(message, flush=True))
    except KeyboardInterrupt:
        print(f"interrupted; continue with: python -m campaign.run --resume {campaign_id}")
        return 130
    finally:
        try:
            lab.reset()
        except Exception as error:
            print(f"warning: final lab reset failed: {error}")
    excluded = json.loads((campaign_dir / "exclusions.json").read_text(encoding="utf-8"))["excluded"] if (campaign_dir / "exclusions.json").exists() else []
    print(f"campaign {campaign_id}: {counts['completed']} completed, {counts['failed']} failed, {counts['skipped']} already done, "
          f"{counts['warmup']} warm-up (discarded), {counts['rerun']} re-runs; {len(excluded)} runs excluded")
    print(f"summarize with: python -m campaign.summarize {campaign_id}")
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
