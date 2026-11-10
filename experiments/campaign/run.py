#!/usr/bin/env python3
"""Run an experiment campaign against the local lab (Phase 3.5D).

  python -m campaign.run --config experiments/configs/campaigns/pilot.json --dry-run
  python -m campaign.run --config experiments/configs/campaigns/pilot.json
  python -m campaign.run --resume <campaign-id>          # continue after an interruption

Each run: reset the lab -> start the target API in the lab network with the run's variant
-> open-loop workload (warm-up + measured) -> database statistics -> stop the API
(telemetry flushed). Outputs:

  data/raw/campaigns/<campaign-id>/manifest.json         config, run plan, environment
  data/raw/campaigns/<campaign-id>/progress.jsonl         one line per finished run
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
from .plan import LABEL_SCHEMA_VERSION, estimate_seconds, expand, label_for, load_config, parse_variant, runner_config


def _append(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def preflight(config: dict[str, Any], runs: list[dict[str, Any]], lab) -> None:
    """Checks before the first run: lab seeded at the right scale, every variant known to the API."""
    seed = lab.seed_info()
    if seed.get("seed_scale") != config["seed_scale"]:
        raise SystemExit(f"campaign needs seed scale '{config['seed_scale']}', lab has '{seed.get('seed_scale')}': run scripts/lab-seed.sh {config['seed_scale']}")
    registry = lab.registered_variants()
    for run in runs:
        name, _ = parse_variant(run["variant"])
        if name not in registry.get(run["operation"], []):
            raise SystemExit(f"target API has no variant '{name}' for {run['operation']} (available: {registry.get(run['operation'])})")


def run_campaign(config: dict[str, Any], campaign_dir: Path, labels_file: Path, lab,
                 execute: Callable[..., dict[str, Any]] = execute_experiment, limit: int | None = None,
                 log: Callable[[str], None] = print) -> dict[str, int]:
    manifest = json.loads((campaign_dir / "manifest.json").read_text(encoding="utf-8"))
    campaign_id = manifest["campaign_id"]
    runs = manifest["runs"]
    progress_file = campaign_dir / "progress.jsonl"
    done = {r["run_index"] for r in _read_jsonl(progress_file) if r["status"] == "completed"}
    experiments_dir = campaign_dir / "experiments"
    stats_dir = campaign_dir / "db-stats"
    stats_dir.mkdir(parents=True, exist_ok=True)
    span_file = Path(os.environ.get("ABR_SPAN_FILE") or DATA_DIR / "raw" / "spans" / "otel-spans.jsonl")
    counts = {"completed": 0, "failed": 0, "skipped": len(done)}
    pending = [r for r in runs if r["run_index"] not in done]
    if limit is not None:
        pending = pending[:limit]
    for position, run in enumerate(pending, 1):
        started = time.monotonic()
        started_at = utc_now()
        experiment_id = None
        log(f"[{position}/{len(pending)}] run {run['run_index']}/{len(runs)}: {run['operation']} (repeat {run['repeat']})")
        try:
            lab.reset()
            run_token = f"{campaign_id}-r{run['run_index']:04d}"
            lab.api_up({run["operation"]: run["variant"]}, run_token)
            try:
                raw = runner_config(config, run, campaign_id)
                summary = execute(resolve_configuration(raw), output_root=experiments_dir, span_file=span_file,
                                  repeat_group_id=f"{campaign_id}:{run['condition_id']}", repeat_index=run["repeat"])
                experiment_id = summary["experiment_id"]
                (stats_dir / f"{experiment_id}.json").write_text(json.dumps(lab.db_statistics(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
            finally:
                lab.api_down()
            correlation = summary["telemetry_correlation"]
            _append(labels_file, {
                "label_schema_version": LABEL_SCHEMA_VERSION, "campaign_id": campaign_id, "experiment_id": experiment_id,
                "run_index": run["run_index"], "condition_id": run["condition_id"], "operation": run["operation"],
                "repeat": run["repeat"], "workload_seed": run["workload_seed"], "seed_scale": config["seed_scale"],
                **label_for(run["variant"]),
            })
            _append(progress_file, {
                "run_index": run["run_index"], "experiment_id": experiment_id, "status": "completed",
                "started_at": started_at, "ended_at": utc_now(), "wall_seconds": round(time.monotonic() - started, 2),
                "measured_requests": summary["measured"]["total_requests"], "failed_requests": summary["measured"]["failed_requests"],
                "correlation_status": correlation["correlation_status"],
            })
            counts["completed"] += 1
            p50 = summary["measured"]["median_ms"]
            log(f"    done in {time.monotonic() - started:.0f} s: {summary['measured']['total_requests']} requests, "
                f"p50 {'-' if p50 is None else f'{p50:.1f}'} ms, failed {summary['measured']['failed_requests']}, correlation {correlation['correlation_status']}")
        except KeyboardInterrupt:
            log("interrupted; stopping the target API")
            try:
                lab.api_down()
            finally:
                raise
        except Exception as error:  # a failed run is recorded, the campaign continues
            counts["failed"] += 1
            _append(progress_file, {"run_index": run["run_index"], "experiment_id": experiment_id, "status": "failed",
                                    "started_at": started_at, "ended_at": utc_now(), "error": f"{type(error).__name__}: {error}"[:2000],
                                    "api_log_tail": lab.api_logs_tail()[-2000:]})
            log(f"    FAILED: {error}")
            log(traceback.format_exc().rstrip())
            try:
                lab.api_down()
            except Exception:
                pass
    return counts


def create_campaign(config: dict[str, Any], config_path: Path, root: Path, lab) -> Path:
    campaign_id = f"{config['name']}-{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    campaign_dir = root / campaign_id
    campaign_dir.mkdir(parents=True, exist_ok=False)
    runs = expand(config)
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

    from .lab import Lab
    if args.resume:
        campaign_dir = args.campaigns_dir / args.resume
        manifest = json.loads((campaign_dir / "manifest.json").read_text(encoding="utf-8"))
        config = manifest["config"]
        runs = manifest["runs"]
    else:
        config = load_config(args.config)
        runs = expand(config)
    print(f"campaign '{config['name']}': {len(runs)} runs, estimated {estimate_seconds(config, runs) / 60:.0f} min")
    if args.dry_run:
        for run in runs:
            print(f"  {run['run_index']:4d}  {run['condition_id']:<40} repeat {run['repeat']}")
        return 0
    lab = Lab()
    preflight(config, runs, lab)
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
    print(f"campaign {campaign_id}: {counts['completed']} completed, {counts['failed']} failed, {counts['skipped']} already done")
    print(f"summarize with: python -m campaign.summarize {campaign_id}")
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
