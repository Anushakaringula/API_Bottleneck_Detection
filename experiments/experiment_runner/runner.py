#!/usr/bin/env python3
"""Controlled API workload runner with reproducible experiment artifacts."""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
import http.client
import json
import math
import os
import platform
import socket
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("ABR_DATA_DIR") or ROOT / "data").resolve()
SCENARIOS = {
    "normal": ("/api/normal", "normal"),
    "cpu": ("/api/cpu", "cpu-intensive"),
    "payload": ("/api/payload", "large-payload"),
    "query-sim": ("/api/query-sim", "query-simulation"),
}
MAX_CONCURRENCY = 20
MAX_REQUEST_COUNT = 5000
MAX_REQUEST_RATE = 50.0
MAX_DURATION_SECONDS = 300.0
MAX_REPEATS = 20
MAX_TIMEOUT_SECONDS = 120.0
MAX_WARMUP_REQUESTS = 5000


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def percentile(values: list[float], quantile: float) -> float | None:
    """Linearly interpolated percentile using the (n - 1) rank convention."""
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * quantile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def latency_summary(latencies_ms: list[float]) -> dict[str, float | None]:
    """Summarize measured request latencies only."""
    if not latencies_ms:
        return {"average_ms": None, "median_ms": None, "p95_ms": None, "p99_ms": None, "minimum_ms": None, "maximum_ms": None}
    return {
        "average_ms": statistics.fmean(latencies_ms),
        "median_ms": percentile(latencies_ms, 0.50),
        "p95_ms": percentile(latencies_ms, 0.95),
        "p99_ms": percentile(latencies_ms, 0.99),
        "minimum_ms": min(latencies_ms),
        "maximum_ms": max(latencies_ms),
    }


def environment_metadata() -> dict[str, Any]:
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "system": platform.system(),
        "machine_architecture": platform.machine(),
        "logical_cpu_count": os.cpu_count(),
        "hostname": socket.gethostname(),
    }


def resolve_configuration(raw: dict[str, Any]) -> dict[str, Any]:
    config = {
        "target_url": None,
        "scenario": "normal",
        "method": "GET",
        "request_count": 20,
        "duration_seconds": None,
        "concurrency": 2,
        "warmup_requests": 2,
        "request_rate_per_second": 2.0,
        "request_interval_seconds": None,
        "timeout_seconds": 10.0,
        "repeats": 1,
        "repeat_interval_seconds": 0.0,
        "span_wait_seconds": 8.0,
        "headers": {},
        "request_body": None,
        "metadata": {},
    }
    config.update(raw)
    if not isinstance(config["target_url"], str) or not config["target_url"].strip():
        raise ValueError("target_url is required")
    scenario = config["scenario"]
    if scenario not in {*SCENARIOS, "custom"}:
        raise ValueError(f"scenario must be one of {', '.join([*SCENARIOS, 'custom'])}")
    if scenario == "custom" and not config["target_url"].startswith(("http://", "https://")):
        raise ValueError("custom scenario target_url must be an absolute HTTP(S) URL")
    if scenario != "custom" and not config["target_url"].startswith(("http://", "https://")):
        raise ValueError("target_url must be an absolute HTTP(S) base URL")
    if config["request_count"] is not None and config["duration_seconds"] is not None:
        raise ValueError("set request_count or duration_seconds, not both")
    if config["request_count"] is None and config["duration_seconds"] is None:
        raise ValueError("one of request_count or duration_seconds is required")
    if config["request_count"] is not None:
        config["request_count"] = _integer(config["request_count"], "request_count", 1, MAX_REQUEST_COUNT)
    if config["duration_seconds"] is not None:
        config["duration_seconds"] = _number(config["duration_seconds"], "duration_seconds", 0.1, MAX_DURATION_SECONDS)
    config["concurrency"] = _integer(config["concurrency"], "concurrency", 1, MAX_CONCURRENCY)
    config["warmup_requests"] = _integer(config["warmup_requests"], "warmup_requests", 0, MAX_WARMUP_REQUESTS - 1)
    if config["request_count"] is not None and config["request_count"] + config["warmup_requests"] > MAX_REQUEST_COUNT:
        raise ValueError(f"measured plus warm-up request count cannot exceed {MAX_REQUEST_COUNT}")
    if config["request_rate_per_second"] is not None and config["request_interval_seconds"] is not None:
        raise ValueError("set request_rate_per_second or request_interval_seconds, not both")
    if config["request_rate_per_second"] is not None:
        config["request_rate_per_second"] = _number(config["request_rate_per_second"], "request_rate_per_second", 0.01, MAX_REQUEST_RATE)
        config["request_interval_seconds"] = 1.0 / config["request_rate_per_second"]
    elif config["request_interval_seconds"] is not None:
        if float(config["request_interval_seconds"]) == 0.0:
            config["request_interval_seconds"] = 0.0
            config["request_rate_per_second"] = None
        else:
            interval = _number(config["request_interval_seconds"], "request_interval_seconds", 1.0 / MAX_REQUEST_RATE, 100.0)
            config["request_interval_seconds"] = interval
            config["request_rate_per_second"] = 1.0 / interval
    else:
        config["request_interval_seconds"] = 0.0
        config["request_rate_per_second"] = None
    config["timeout_seconds"] = _number(config["timeout_seconds"], "timeout_seconds", 0.1, MAX_TIMEOUT_SECONDS)
    config["repeats"] = _integer(config["repeats"], "repeats", 1, MAX_REPEATS)
    config["repeat_interval_seconds"] = _number(config["repeat_interval_seconds"], "repeat_interval_seconds", 0.0, MAX_DURATION_SECONDS)
    config["span_wait_seconds"] = _number(config["span_wait_seconds"], "span_wait_seconds", 0.0, 60.0)
    config["method"] = str(config["method"]).upper()
    if not config["method"].isalpha() or len(config["method"]) > 16:
        raise ValueError("method must contain 1-16 alphabetic characters")
    if not isinstance(config["headers"], dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in config["headers"].items()):
        raise ValueError("headers must be an object with string names and values")
    if not isinstance(config["metadata"], dict):
        raise ValueError("metadata must be an object")
    if scenario == "custom":
        label = config.get("scenario_label", "custom")
        if not isinstance(label, str) or not label.strip() or len(label) > 128 or "\n" in label or "\r" in label:
            raise ValueError("scenario_label must be a non-empty string of at most 128 characters")
    content_type_header = any(key.lower() == "content-type" for key in config["headers"])
    if isinstance(config["request_body"], (dict, list)) and not content_type_header:
        config["headers"] = {**config["headers"], "Content-Type": "application/json"}
    return config


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        integer = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if str(value).strip() not in {str(integer), f"{integer}.0"} or not minimum <= integer <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return integer


def endpoint_for(config: dict[str, Any]) -> tuple[str, str]:
    if config["scenario"] == "custom":
        return config["target_url"], str(config.get("scenario_label") or "custom")
    route, label = SCENARIOS[config["scenario"]]
    return config["target_url"].rstrip("/") + route, label


def _request_body(config: dict[str, Any]) -> bytes | None:
    body = config["request_body"]
    if body is None:
        return None
    if isinstance(body, str):
        return body.encode("utf-8")
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def _perform_request(
    url: str,
    method: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
    run_id: str,
    scenario_label: str,
    phase: str,
    request_index: int,
    scheduled_at: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    start_timestamp = utc_now()
    request_headers = dict(headers)
    request_headers["X-Experiment-Run-Id"] = run_id
    request_headers["X-Experiment-Scenario"] = scenario_label
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    status_code: int | None = None
    response_bytes = 0
    outcome = "success"
    error_type: str | None = None
    error_message: str | None = None
    try:
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error_response:
            response = error_response
        with response:
            status_code = response.getcode()
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                response_bytes += len(chunk)
        if not 200 <= int(status_code) < 300:
            outcome = "http_error"
            error_type = "HTTPError"
            error_message = f"HTTP status {status_code}"
    except (urllib.error.URLError, TimeoutError, socket.timeout, http.client.HTTPException, OSError) as error:
        outcome = "connection_error"
        error_type = type(error).__name__
        reason = getattr(error, "reason", error)
        error_message = str(reason)[:500]
    completed = time.perf_counter()
    return {
        "request_index": request_index,
        "phase": phase,
        "scheduled_at": scheduled_at,
        "started_at": start_timestamp,
        "completed_at": utc_now(),
        "latency_ms": (completed - started) * 1000.0,
        "status_code": status_code,
        "outcome": outcome,
        "error_type": error_type,
        "error_message": error_message,
        "response_bytes": response_bytes,
    }


@dataclasses.dataclass
class PhaseResult:
    records: list[dict[str, Any]]
    started_at: str
    ended_at: str
    elapsed_seconds: float
    max_in_flight: int
    stop_reason: str


def run_phase(
    executor: concurrent.futures.ThreadPoolExecutor,
    *,
    url: str,
    config: dict[str, Any],
    run_id: str,
    scenario_label: str,
    phase: str,
    start_index: int,
    request_count: int | None,
    duration_seconds: float | None,
) -> PhaseResult:
    phase_started = time.perf_counter()
    started_at = utc_now()
    deadline = phase_started + duration_seconds if duration_seconds is not None else None
    next_launch = phase_started
    launched = 0
    completed: list[dict[str, Any]] = []
    pending: set[concurrent.futures.Future[dict[str, Any]]] = set()
    max_in_flight = 0
    request_body = _request_body(config)
    headers = dict(config["headers"])
    if isinstance(config["request_body"], (dict, list)) and not any(key.lower() == "content-type" for key in headers):
        headers["Content-Type"] = "application/json"
    interval = config["request_interval_seconds"]
    stop_reason = "request_count_reached" if request_count is not None else "duration_elapsed"

    while True:
        now = time.perf_counter()
        under_limit = launched < request_count if request_count is not None else now < deadline
        if start_index + launched >= MAX_REQUEST_COUNT:
            under_limit = False
            stop_reason = "safety_request_cap_reached"
        can_launch = under_limit and len(pending) < config["concurrency"] and now >= next_launch
        if can_launch:
            index = start_index + launched
            scheduled_at = utc_now()
            future = executor.submit(
                _perform_request,
                url,
                config["method"],
                headers,
                request_body,
                config["timeout_seconds"],
                run_id,
                scenario_label,
                phase,
                index,
                scheduled_at,
            )
            pending.add(future)
            launched += 1
            max_in_flight = max(max_in_flight, len(pending))
            # Schedule from the actual dispatch time, avoiding catch-up bursts.
            next_launch = time.perf_counter() + interval if interval else time.perf_counter()
            continue

        may_launch_more = request_count is not None and launched < request_count or (deadline is not None and time.perf_counter() < deadline)
        if pending:
            timeout = None
            if may_launch_more and len(pending) < config["concurrency"] and next_launch > time.perf_counter():
                timeout = next_launch - time.perf_counter()
            elif not may_launch_more or len(pending) >= config["concurrency"]:
                timeout = None
            done, pending = concurrent.futures.wait(pending, timeout=timeout, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                completed.append(future.result())
            continue
        if not may_launch_more:
            break
        if interval and next_launch > time.perf_counter():
            time.sleep(min(next_launch - time.perf_counter(), 0.1))

    phase_ended = time.perf_counter()
    elapsed = max(phase_ended - phase_started, 0.0)
    completed.sort(key=lambda item: item["request_index"])
    if request_count is not None and launched < request_count and stop_reason != "safety_request_cap_reached":
        stop_reason = "request_count_not_reached"
    return PhaseResult(completed, started_at, utc_now(), elapsed, max_in_flight, stop_reason)


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def span_correlation(span_file: Path, run_id: str, expected_spans: int, wait_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + wait_seconds
    matches: list[dict[str, Any]] = []
    malformed_lines = 0
    while True:
        matches = []
        malformed_lines = 0
        try:
            with span_file.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        span = json.loads(line)
                    except json.JSONDecodeError:
                        malformed_lines += 1
                        continue
                    attributes = span.get("attributes") or {}
                    if span.get("span_name") == "experiment.http.request" and attributes.get("experiment.run_id") == run_id:
                        matches.append(span)
        except FileNotFoundError:
            pass
        unique_ids = {span.get("span_id") for span in matches if span.get("span_id")}
        if len(unique_ids) >= expected_spans or time.monotonic() >= deadline:
            break
        time.sleep(min(0.2, max(deadline - time.monotonic(), 0.0)))

    counts = Counter(span.get("span_id") for span in matches if span.get("span_id"))
    duplicate_records = sum(count - 1 for count in counts.values() if count > 1)
    unique_observed = len(counts)
    extra_unique = max(unique_observed - expected_spans, 0)
    duplicates = duplicate_records + extra_unique
    missing = max(expected_spans - unique_observed, 0)
    return {
        "span_file": str(span_file),
        "expected_spans": expected_spans,
        "observed_spans": len(matches),
        "unique_observed_spans": unique_observed,
        "missing_spans": missing,
        "duplicate_spans": duplicates,
        "malformed_lines_ignored": malformed_lines,
        "correlation_status": "complete" if missing == 0 and duplicates == 0 else "partial",
        "correlation_note": "Counts are joined by experiment run ID; Phase 1 does not expose a per-request ID to the client.",
    }


def _counts(records: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(record["status_code"]) if record["status_code"] is not None else "connection_error" for record in records)
    successful = sum(record["outcome"] == "success" for record in records)
    return {
        "total_requests": len(records),
        "successful_requests": successful,
        "failed_requests": len(records) - successful,
        "http_status_codes": dict(sorted(statuses.items())),
    }


def execute_experiment(
    config: dict[str, Any],
    *,
    output_root: Path,
    span_file: Path,
    repeat_group_id: str,
    repeat_index: int,
) -> dict[str, Any]:
    experiment_id = str(uuid.uuid4())
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / experiment_id
    run_dir.mkdir(parents=True, exist_ok=False)
    requests_file = run_dir / "requests.jsonl"
    configuration_file = run_dir / "configuration.json"
    summary_file = run_dir / "summary.json"
    endpoint, scenario_label = endpoint_for(config)
    started_at = utc_now()
    method = config["method"]
    request_count = config["request_count"]
    duration_seconds = config["duration_seconds"]
    configuration = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "repeat_group_id": repeat_group_id,
        "repeat_index": repeat_index,
        "scenario": scenario_label,
        "scenario_role": "experimental_ground_truth_only",
        "scenario_is_model_feature": False,
        "target_url": config["target_url"],
        "target_endpoint": endpoint,
        "http_method": method,
        "started_at": started_at,
        "ended_at": None,
        "workload_configuration": {
            "measured_request_count": request_count,
            "duration_seconds": duration_seconds,
            "warmup_requests": config["warmup_requests"],
            "concurrency": config["concurrency"],
            "request_rate_per_second": config["request_rate_per_second"],
            "request_interval_seconds": config["request_interval_seconds"],
            "timeout_seconds": config["timeout_seconds"],
            "repeats": config["repeats"],
            "repeat_interval_seconds": config["repeat_interval_seconds"],
            "headers": {
                key: "[REDACTED]" if key.lower() in {"authorization", "cookie", "proxy-authorization"} else value
                for key, value in config["headers"].items()
            },
            "redacted_header_names": [key for key in config["headers"] if key.lower() in {"authorization", "cookie", "proxy-authorization"}],
            "request_body": config["request_body"],
            "user_metadata": config["metadata"],
        },
        "environment": environment_metadata(),
        "artifacts": {"configuration": "configuration.json", "request_results": "requests.jsonl", "summary": "summary.json"},
    }
    _write_json(configuration_file, configuration)

    all_records: list[dict[str, Any]] = []
    warmup_result: PhaseResult | None = None
    measured_result: PhaseResult | None = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=config["concurrency"], thread_name_prefix="experiment-request") as executor:
        if config["warmup_requests"]:
            warmup_result = run_phase(
                executor,
                url=endpoint,
                config=config,
                run_id=experiment_id,
                scenario_label=scenario_label,
                phase="warmup",
                start_index=0,
                request_count=config["warmup_requests"],
                duration_seconds=None,
            )
            all_records.extend(warmup_result.records)
            _append_jsonl(requests_file, warmup_result.records)
        measured_result = run_phase(
            executor,
            url=endpoint,
            config=config,
            run_id=experiment_id,
            scenario_label=scenario_label,
            phase="measured",
            start_index=len(all_records),
            request_count=request_count,
            duration_seconds=duration_seconds,
        )
        all_records.extend(measured_result.records)
        _append_jsonl(requests_file, measured_result.records)

    measured_records = measured_result.records if measured_result else []
    measured_counts = _counts(measured_records)
    warmup_records = warmup_result.records if warmup_result else []
    measured_latencies = [record["latency_ms"] for record in measured_records]
    measured_elapsed = measured_result.elapsed_seconds if measured_result else 0.0
    expected_spans = len(all_records)
    workload_ended_at = utc_now()
    correlation = span_correlation(span_file, experiment_id, expected_spans, config["span_wait_seconds"])
    artifacts_finalized_at = utc_now()
    configuration["ended_at"] = workload_ended_at
    configuration["artifacts_finalized_at"] = artifacts_finalized_at
    configuration["execution"] = {
        "warmup_attempted_requests": len(warmup_records),
        "measured_attempted_requests": len(measured_records),
        "measured_phase_started_at": measured_result.started_at if measured_result else None,
        "measured_phase_ended_at": measured_result.ended_at if measured_result else None,
        "measured_phase_elapsed_seconds": measured_elapsed,
        "maximum_observed_in_flight": max(
            warmup_result.max_in_flight if warmup_result else 0,
            measured_result.max_in_flight if measured_result else 0,
        ),
        "measured_stop_reason": measured_result.stop_reason if measured_result else "not_started",
    }
    _write_json(configuration_file, configuration)
    summary = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "repeat_group_id": repeat_group_id,
        "repeat_index": repeat_index,
        "scenario": scenario_label,
        "scenario_role": "experimental_ground_truth_only",
        "scenario_is_model_feature": False,
        "started_at": started_at,
        "ended_at": workload_ended_at,
        "artifacts_finalized_at": artifacts_finalized_at,
        "target_endpoint": endpoint,
        "http_method": method,
        "workload": {
            "configured_measured_request_count": request_count,
            "configured_duration_seconds": duration_seconds,
            "warmup_requests_configured": config["warmup_requests"],
            "concurrency_limit": config["concurrency"],
            "request_rate_per_second": config["request_rate_per_second"],
            "timeout_seconds": config["timeout_seconds"],
        },
        "warmup": {
            **_counts(warmup_records),
            "latency_and_throughput_included": False,
        },
        "measured": {
            **measured_counts,
            **latency_summary(measured_latencies),
            "phase_elapsed_seconds": measured_elapsed,
            "throughput_requests_per_second": len(measured_records) / measured_elapsed if measured_elapsed > 0 else 0.0,
            "maximum_observed_in_flight": measured_result.max_in_flight if measured_result else 0,
            "stop_reason": measured_result.stop_reason if measured_result else "not_started",
        },
        "total_attempted_requests_including_warmup": expected_spans,
        "telemetry_correlation": correlation,
        "artifact_paths": {"configuration": "configuration.json", "request_results": "requests.jsonl", "summary": "summary.json"},
    }
    _write_json(summary_file, summary)
    print(json.dumps({"experiment_id": experiment_id, "scenario": scenario_label, "summary": str(summary_file), "measured_requests": len(measured_records), "correlation_status": correlation["correlation_status"]}))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to JSON workload configuration")
    parser.add_argument("--output-dir", default=os.environ.get("ABR_EXPERIMENTS_DIR") or str(DATA_DIR / "raw" / "experiments"), help="Experiment artifact directory")
    parser.add_argument("--span-file", default=os.environ.get("ABR_SPAN_FILE") or str(DATA_DIR / "raw" / "spans" / "otel-spans.jsonl"), help="Phase 1 JSONL span file")
    for name in ("target-url", "scenario", "method", "request-count", "duration-seconds", "concurrency", "warmup-requests", "request-rate-per-second", "request-interval-seconds", "timeout-seconds", "repeats"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        raw = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("configuration JSON must be an object")
        overrides = {
            "target_url": args.target_url,
            "scenario": args.scenario,
            "method": args.method,
            "request_count": args.request_count,
            "duration_seconds": args.duration_seconds,
            "concurrency": args.concurrency,
            "warmup_requests": args.warmup_requests,
            "request_rate_per_second": args.request_rate_per_second,
            "request_interval_seconds": args.request_interval_seconds,
            "timeout_seconds": args.timeout_seconds,
            "repeats": args.repeats,
        }
        if args.duration_seconds is not None:
            raw["request_count"] = None
        if args.request_count is not None:
            raw["duration_seconds"] = None
        if args.request_rate_per_second is not None:
            raw["request_interval_seconds"] = None
        if args.request_interval_seconds is not None:
            raw["request_rate_per_second"] = None
        for key, value in overrides.items():
            if value is None:
                continue
            if key in {"request_count", "warmup_requests", "concurrency", "repeats"}:
                value = int(value)
            elif key in {"duration_seconds", "request_rate_per_second", "request_interval_seconds", "timeout_seconds"}:
                value = None if value.lower() == "none" else float(value)
            raw[key] = value
        config = resolve_configuration(raw)
        output_root = Path(args.output_dir).resolve()
        span_file = Path(args.span_file).resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        repeat_group_id = str(uuid.uuid4())
        summaries = []
        for index in range(1, config["repeats"] + 1):
            summaries.append(execute_experiment(config, output_root=output_root, span_file=span_file, repeat_group_id=repeat_group_id, repeat_index=index))
            if index < config["repeats"] and config["repeat_interval_seconds"]:
                time.sleep(config["repeat_interval_seconds"])
        return 0 if all(item["measured"]["failed_requests"] == 0 for item in summaries) else 2
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
