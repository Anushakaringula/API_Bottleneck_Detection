"""Campaign configuration: validation, expansion into runs, reproducible run order.

A campaign config (JSON) names conditions, operation × variant (× severity), with a number
of repeats each. Every condition-repeat is one run. The run order is shuffled with the
campaign seed, so slow drifts (machine temperature, background load) do not line up with
conditions. All runs of one operation use the same workload seed: same timetable, same
request mix. Only the variant differs, which makes runs of one operation directly
comparable.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any

LABEL_SCHEMA_VERSION = "1.0.0"

# Ground truth: diagnosis class of each fault (target-api/src/routes/domain.js).
FAULT_CLASSES = {
    "healthy": "NONE",
    "noindex": "DB_BOTTLENECK",
    "nplus1": "N_PLUS_ONE",
    "payload": "LARGE_PAYLOAD",
    "cache": "CACHE_OPPORTUNITY",
    "cpu": "CPU_BOTTLENECK",
}

# What the workload sends to each operation. Paths and parameters are fixed lists, so the
# request mix is identical in every run of an operation (only the drawing order depends
# on the workload seed).
OPERATION_REQUESTS: dict[str, dict[str, Any]] = {
    "users.list": {"path": "/api/users", "variants": [{"query": {"offset": 20 * i}} for i in range(50)]},
    "users.get": {"path": "/api/users/1", "variants": [{"path": f"/api/users/{1 + 1999 * i}"} for i in range(50)]},
    "products.list": {"path": "/api/products", "variants": [{"query": {"offset": 20 * i}} for i in range(50)]},
    "products.get": {"path": "/api/products/1", "variants": [{"path": f"/api/products/{1 + 2477 * i}"} for i in range(20)]},
    "orders.list": {"path": "/api/orders", "variants": [{"query": {"user_id": 1 + 1999 * i}} for i in range(50)]},
    "search": {"path": "/api/search", "variants": [{"query": {"q": q}} for q in ("smart", "pro", "eco", "ultra", "mini", "max", "prime", "classic")]},
    # days=90 is ~3x heavier than 30 and made the cache fault collapse instead of slow down (smoke campaign, 11 Nov).
    "reports": {"path": "/api/reports", "variants": [{"query": {"days": d}} for d in (7, 14, 30)]},
    "dashboard": {"path": "/api/dashboard", "variants": None},
}

VARIANT_PATTERN = re.compile(r"^([a-z][a-z0-9_-]*)(?::([0-9]+(?:\.[0-9]+)?))?$")
WORKLOAD_KEYS = {"load_model", "arrival_process", "request_rate_per_second", "duration_seconds",
                 "warmup_requests", "concurrency", "timeout_seconds", "span_wait_seconds"}


def parse_variant(text: str) -> tuple[str, float | None]:
    match = VARIANT_PATTERN.match(str(text))
    if not match:
        raise ValueError(f"invalid variant {text!r}")
    return match.group(1), (float(match.group(2)) if match.group(2) is not None else None)


def label_for(variant: str) -> dict[str, Any]:
    """Ground-truth fields of a variant: fault class, severity, whether it is a fix."""
    name, severity = parse_variant(variant)
    fixed = name.endswith("-fixed")
    fault = name[: -len("-fixed")] if fixed else name
    if fault not in FAULT_CLASSES:
        raise ValueError(f"unknown fault {fault!r} in variant {variant!r}")
    return {
        "variant": variant,
        "variant_name": name,
        "severity": severity,
        "fault": None if fault == "healthy" else fault,
        # A fixed variant should behave like a healthy one: its class is NONE.
        "fault_class": "NONE" if fixed else FAULT_CLASSES[fault],
        "is_fixed": fixed,
        "remediation_of": fault if fixed else None,
    }


def stable_seed(*parts: Any) -> int:
    digest = hashlib.sha256(":".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def load_config(path: Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_config(raw)


def validate_config(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("campaign config must be a JSON object")
    config = dict(raw)
    if not isinstance(config.get("name"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", config["name"]):
        raise ValueError("name must be 1-41 lowercase letters, digits or '-'")
    if not isinstance(config.get("random_seed"), int):
        raise ValueError("random_seed must be an integer")
    if config.get("seed_scale") not in {"small", "medium", "large"}:
        raise ValueError("seed_scale must be small, medium or large")
    config.setdefault("target_url", "http://127.0.0.1:3000")
    workload = config.get("workload")
    if not isinstance(workload, dict) or set(workload) - WORKLOAD_KEYS:
        raise ValueError(f"workload must be an object with keys from {sorted(WORKLOAD_KEYS)}")
    overrides = config.setdefault("operation_workload", {})
    for operation, override in overrides.items():
        if operation not in OPERATION_REQUESTS or not isinstance(override, dict) or set(override) - WORKLOAD_KEYS:
            raise ValueError(f"operation_workload[{operation!r}] is invalid")
    conditions = config.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise ValueError("conditions must be a non-empty list")
    for index, condition in enumerate(conditions):
        if not isinstance(condition, dict) or condition.get("operation") not in OPERATION_REQUESTS:
            raise ValueError(f"conditions[{index}].operation must be one of {sorted(OPERATION_REQUESTS)}")
        variants = condition.get("variants")
        if not isinstance(variants, list) or not variants:
            raise ValueError(f"conditions[{index}].variants must be a non-empty list")
        for variant in variants:
            label_for(variant)
        repeats = condition.get("repeats", 1)
        if not isinstance(repeats, int) or not 1 <= repeats <= 50:
            raise ValueError(f"conditions[{index}].repeats must be 1-50")
    return config


def workload_for(config: dict[str, Any], operation: str) -> dict[str, Any]:
    return {**config["workload"], **config["operation_workload"].get(operation, {})}


def expand(config: dict[str, Any]) -> list[dict[str, Any]]:
    """All runs of the campaign, in their (shuffled, reproducible) execution order."""
    runs = []
    for condition in config["conditions"]:
        operation = condition["operation"]
        for variant in condition["variants"]:
            for repeat in range(1, condition.get("repeats", 1) + 1):
                runs.append({
                    "operation": operation,
                    "variant": variant,
                    "condition_id": f"{operation}/{variant}",
                    "repeat": repeat,
                    "workload_seed": stable_seed(config["random_seed"], "workload", operation),
                })
    seen = set()
    for run in runs:
        key = (run["condition_id"], run["repeat"])
        if key in seen:
            raise ValueError(f"condition {run['condition_id']} is listed more than once")
        seen.add(key)
    random.Random(stable_seed(config["random_seed"], "order")).shuffle(runs)
    for index, run in enumerate(runs, 1):
        run["run_index"] = index
    return runs


def runner_config(config: dict[str, Any], run: dict[str, Any], campaign_id: str) -> dict[str, Any]:
    """Configuration for experiment_runner. Carries no ground truth: the variant is only in the labels."""
    plan = OPERATION_REQUESTS[run["operation"]]
    workload = workload_for(config, run["operation"])
    return {
        "target_url": config["target_url"].rstrip("/") + plan["path"],
        "scenario": "custom",
        "scenario_label": "campaign-run",
        "method": "GET",
        "load_model": workload.get("load_model", "open"),
        "arrival_process": workload.get("arrival_process", "poisson"),
        "request_rate_per_second": workload["request_rate_per_second"],
        "request_count": None,
        "duration_seconds": workload["duration_seconds"],
        "warmup_requests": workload.get("warmup_requests", 20),
        "concurrency": workload.get("concurrency", 50),
        "timeout_seconds": workload.get("timeout_seconds", 30),
        "span_wait_seconds": workload.get("span_wait_seconds", 15),
        "random_seed": run["workload_seed"],
        "request_variants": plan["variants"],
        "metadata": {"campaign_id": campaign_id, "run_index": run["run_index"]},
    }


def estimate_seconds(config: dict[str, Any], runs: list[dict[str, Any]], overhead_seconds: float = 25.0) -> float:
    total = 0.0
    for run in runs:
        workload = workload_for(config, run["operation"])
        rate = workload["request_rate_per_second"]
        total += overhead_seconds + workload["duration_seconds"] + workload.get("warmup_requests", 20) / rate + workload.get("span_wait_seconds", 15) / 3
    return total
