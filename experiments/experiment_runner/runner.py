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
import random
import secrets
import socket
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
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
# Safety caps (raised in Phase 3.5B for open-loop load; one laptop runs client and server).
MAX_CONCURRENCY = 200
MAX_REQUEST_COUNT = 50000
MAX_REQUEST_RATE = 200.0
MAX_DURATION_SECONDS = 900.0
MAX_REPEATS = 20
MAX_TIMEOUT_SECONDS = 120.0
MAX_WARMUP_REQUESTS = 50000
MAX_STAGES = 20
MAX_REQUEST_VARIANTS = 100
LOAD_MODELS = ("closed", "open")
ARRIVAL_PROCESSES = ("constant", "poisson")
# Below this remaining time the open-loop dispatcher yields instead of sleeping, because
# sleep() can overshoot by about a millisecond (much more on Windows before Python 3.11).
SPIN_THRESHOLD_SECONDS = 0.002


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
        "load_model": "closed",
        "arrival_process": "constant",
        "random_seed": None,
        "stages": None,
        "request_variants": None,
    }
    config.update(raw)
    if config["load_model"] not in LOAD_MODELS:
        raise ValueError(f"load_model must be one of {', '.join(LOAD_MODELS)}")
    if config["arrival_process"] not in ARRIVAL_PROCESSES:
        raise ValueError(f"arrival_process must be one of {', '.join(ARRIVAL_PROCESSES)}")
    if config["load_model"] == "closed" and (config["arrival_process"] != "constant" or config["stages"] is not None):
        raise ValueError("arrival_process 'poisson' and stages require load_model 'open'")
    if config["stages"] is not None:
        config["stages"] = _stages(config["stages"])
        stage_total = sum(stage["duration_seconds"] for stage in config["stages"])
        if "request_count" not in raw:
            config["request_count"] = None
        if config["request_count"] is not None:
            raise ValueError("stages define the measured duration; set request_count to null")
        if config["duration_seconds"] is not None and not math.isclose(float(config["duration_seconds"]), stage_total):
            raise ValueError("stages define the measured duration; set duration_seconds to null")
        config["duration_seconds"] = stage_total
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
    if config["load_model"] == "open" and config["stages"] is None and config["request_rate_per_second"] is None:
        raise ValueError("load_model 'open' needs request_rate_per_second (or request_interval_seconds) or stages")
    if config["load_model"] == "open" and config["warmup_requests"] and warmup_rate(config) <= 0:
        raise ValueError("open-loop warm-up needs a positive request rate")
    if config["load_model"] == "open":
        planned = config["request_count"] if config["request_count"] is not None else sum(_stage_arrivals(stage) for stage in stage_list(config))
        if planned + config["warmup_requests"] > MAX_REQUEST_COUNT:
            raise ValueError(f"planned measured plus warm-up requests ({planned + config['warmup_requests']:.0f}) cannot exceed {MAX_REQUEST_COUNT}")
    config["request_variants"] = _request_variants(config["request_variants"])
    if config["random_seed"] is None:
        config["random_seed"] = secrets.randbelow(2**32)
    else:
        config["random_seed"] = _integer(config["random_seed"], "random_seed", 0, 2**63 - 1)
    return config


def _stages(raw: Any) -> list[dict[str, float]]:
    """Normalize stages to {duration_seconds, rate_per_second_start, rate_per_second_end}."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_STAGES:
        raise ValueError(f"stages must be a list of 1-{MAX_STAGES} objects")
    stages = []
    for index, stage in enumerate(raw):
        if not isinstance(stage, dict):
            raise ValueError(f"stages[{index}] must be an object")
        name = f"stages[{index}]"
        duration = _number(stage.get("duration_seconds"), f"{name}.duration_seconds", 0.1, MAX_DURATION_SECONDS)
        if "rate_per_second" in stage:
            if "rate_per_second_start" in stage or "rate_per_second_end" in stage:
                raise ValueError(f"{name}: use rate_per_second or rate_per_second_start/end, not both")
            start = end = _number(stage["rate_per_second"], f"{name}.rate_per_second", 0.0, MAX_REQUEST_RATE)
        else:
            start = _number(stage.get("rate_per_second_start"), f"{name}.rate_per_second_start", 0.0, MAX_REQUEST_RATE)
            end = _number(stage.get("rate_per_second_end"), f"{name}.rate_per_second_end", 0.0, MAX_REQUEST_RATE)
        stages.append({"duration_seconds": duration, "rate_per_second_start": start, "rate_per_second_end": end})
    total = sum(stage["duration_seconds"] for stage in stages)
    if total > MAX_DURATION_SECONDS:
        raise ValueError(f"total stage duration cannot exceed {MAX_DURATION_SECONDS} seconds")
    if not any(stage["rate_per_second_start"] > 0 or stage["rate_per_second_end"] > 0 for stage in stages):
        raise ValueError("at least one stage needs a positive rate")
    return stages


def _request_variants(raw: Any) -> list[dict[str, Any]] | None:
    """Normalize request variants to {query, weight}; one is drawn per request."""
    if raw is None:
        return None
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_REQUEST_VARIANTS:
        raise ValueError(f"request_variants must be a list of 1-{MAX_REQUEST_VARIANTS} objects")
    variants = []
    for index, variant in enumerate(raw):
        if not isinstance(variant, dict):
            raise ValueError(f"request_variants[{index}] must be an object")
        query = variant.get("query", {})
        if not isinstance(query, dict) or not all(isinstance(k, str) and isinstance(v, (str, int, float)) and not isinstance(v, bool) for k, v in query.items()):
            raise ValueError(f"request_variants[{index}].query must map names to strings or numbers")
        weight = _number(variant.get("weight", 1), f"request_variants[{index}].weight", 1e-9, 1e9)
        variants.append({"query": dict(query), "weight": weight})
    return variants


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


def stage_list(config: dict[str, Any]) -> list[dict[str, float]]:
    """Measured-phase rate stages; a plain rate is one stage (unbounded in request-count mode)."""
    if config["stages"] is not None:
        return config["stages"]
    rate = config["request_rate_per_second"]
    duration = config["duration_seconds"] if config["duration_seconds"] is not None else math.inf
    return [{"duration_seconds": duration, "rate_per_second_start": rate, "rate_per_second_end": rate}]


def warmup_rate(config: dict[str, Any]) -> float:
    """Open-loop warm-up runs at the configured rate, or at the highest stage rate."""
    if config["request_rate_per_second"] is not None and config["stages"] is None:
        return config["request_rate_per_second"]
    return max(max(stage["rate_per_second_start"], stage["rate_per_second_end"]) for stage in config["stages"] or [{"rate_per_second_start": 0.0, "rate_per_second_end": 0.0}])


def _stage_arrivals(stage: dict[str, float]) -> float:
    """Expected arrivals in a stage: the integral of its (linear) rate."""
    return (stage["rate_per_second_start"] + stage["rate_per_second_end"]) / 2 * stage["duration_seconds"]


def _time_for_cumulative(target: float, stages: list[dict[str, float]]) -> float | None:
    """Invert the cumulative arrival function: the time at which `target` arrivals are expected."""
    base_time = 0.0
    base_count = 0.0
    for stage in stages:
        duration = stage["duration_seconds"]
        start, end = stage["rate_per_second_start"], stage["rate_per_second_end"]
        stage_count = _stage_arrivals(stage) if math.isfinite(duration) else math.inf
        if target < base_count + stage_count:
            remaining = target - base_count
            if start == end:
                offset = remaining / start
            else:
                slope = (end - start) / duration
                offset = (-start + math.sqrt(max(start * start + 2 * slope * remaining, 0.0))) / slope
            return base_time + min(max(offset, 0.0), duration)
        base_time += duration
        base_count += stage_count
    return None


def arrival_offsets(stages: list[dict[str, float]], process: str, rng: random.Random,
                    count: int | None = None, cap: int = MAX_REQUEST_COUNT) -> list[float]:
    """Send offsets in seconds from phase start.

    `constant`: evenly spaced in expected-arrival units (the first request at 0).
    `poisson`: unit-rate exponential gaps mapped through the rate profile (time
    rescaling), which gives a Poisson process with the configured, possibly varying, rate.
    """
    offsets: list[float] = []
    target = rng.expovariate(1.0) if process == "poisson" else 0.0
    while (count is None or len(offsets) < count) and len(offsets) < cap:
        offset = _time_for_cumulative(target, stages)
        if offset is None:
            break
        offsets.append(offset)
        target += rng.expovariate(1.0) if process == "poisson" else 1.0
    return offsets


def _seeded(config: dict[str, Any], purpose: str) -> random.Random:
    """Independent reproducible streams, so e.g. the warm-up count cannot shift the measured timetable."""
    return random.Random(f"{config['random_seed']}:{purpose}")


def _choose_variant(config: dict[str, Any], rng: random.Random) -> int | None:
    variants = config["request_variants"]
    if not variants:
        return None
    return rng.choices(range(len(variants)), weights=[variant["weight"] for variant in variants])[0]


def _variant_url(url: str, config: dict[str, Any], variant_index: int | None) -> str:
    if variant_index is None:
        return url
    query = config["request_variants"][variant_index]["query"]
    if not query:
        return url
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)


def _sleep_until(deadline: float) -> None:
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        if remaining > SPIN_THRESHOLD_SECONDS:
            time.sleep(remaining - SPIN_THRESHOLD_SECONDS / 2)
        else:
            time.sleep(0)


class InFlightTracker:
    """Counts requests that are actually being sent or awaited by a worker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current = 0
        self.maximum = 0

    def enter(self) -> None:
        with self._lock:
            self.current += 1
            self.maximum = max(self.maximum, self.current)

    def exit(self) -> None:
        with self._lock:
            self.current -= 1


def new_trace_context() -> tuple[str, str]:
    """Random W3C trace ID (32 hex) and parent span ID (16 hex); all-zero IDs are invalid."""
    while True:
        trace_id, span_id = os.urandom(16).hex(), os.urandom(8).hex()
        if trace_id != "0" * 32 and span_id != "0" * 16:
            return trace_id, span_id


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
    phase: str,
    request_index: int,
    scheduled_at: str,
    intended_perf: float | None = None,
    phase_started_perf: float | None = None,
    tracker: InFlightTracker | None = None,
    variant_index: int | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    if tracker is not None:
        tracker.enter()
    try:
        record = _send(url, method, headers, body, timeout, run_id, started)
    finally:
        if tracker is not None:
            tracker.exit()
    completed = record.pop("_completed_perf")
    record.update({
        "request_index": request_index,
        "phase": phase,
        "scheduled_at": scheduled_at,
        "variant_index": variant_index,
        # Open loop: intended = timetable slot; closed loop: the moment the dispatcher released it.
        "intended_send_offset_ms": (intended_perf - phase_started_perf) * 1000.0 if intended_perf is not None and phase_started_perf is not None else None,
        "sent_offset_ms": (started - phase_started_perf) * 1000.0 if phase_started_perf is not None else None,
        "send_lag_ms": (started - intended_perf) * 1000.0 if intended_perf is not None else None,
        # Response time as a user arriving at the intended time sees it (includes client-side delay).
        "latency_from_intended_ms": (completed - intended_perf) * 1000.0 if intended_perf is not None else None,
    })
    return record


def _send(url: str, method: str, headers: dict[str, str], body: bytes | None, timeout: float,
          run_id: str, started: float) -> dict[str, Any]:
    start_timestamp = utc_now()
    trace_id, client_span_id = new_trace_context()
    # The scenario label is ground truth and is never sent to the system under test.
    request_headers = {key: value for key, value in headers.items() if key.lower() != "traceparent"}
    request_headers["X-Experiment-Run-Id"] = run_id
    request_headers["traceparent"] = f"00-{trace_id}-{client_span_id}-01"
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
        "_completed_perf": completed,
        "trace_id": trace_id,
        "client_span_id": client_span_id,
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
    phase: str,
    start_index: int,
    request_count: int | None,
    duration_seconds: float | None,
    variant_rng: random.Random | None = None,
) -> PhaseResult:
    """Closed loop: a request is released only when a worker slot is free and the pacing interval has passed."""
    variant_rng = variant_rng or random.Random(0)
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
            variant_index = _choose_variant(config, variant_rng)
            future = executor.submit(
                _perform_request,
                _variant_url(url, config, variant_index),
                config["method"],
                headers,
                request_body,
                config["timeout_seconds"],
                run_id,
                phase,
                index,
                scheduled_at,
                intended_perf=time.perf_counter(),
                phase_started_perf=phase_started,
                variant_index=variant_index,
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


def run_phase_open(
    executor: concurrent.futures.ThreadPoolExecutor,
    *,
    url: str,
    config: dict[str, Any],
    run_id: str,
    phase: str,
    start_index: int,
    offsets: list[float],
    variant_rng: random.Random,
) -> PhaseResult:
    """Open loop: each request is released at its timetable slot, whether or not earlier
    requests have finished. If all workers are busy, the request waits in the executor
    queue and that delay shows up as send lag instead of being hidden."""
    request_body = _request_body(config)
    headers = dict(config["headers"])
    if isinstance(config["request_body"], (dict, list)) and not any(key.lower() == "content-type" for key in headers):
        headers["Content-Type"] = "application/json"
    tracker = InFlightTracker()
    futures: list[concurrent.futures.Future[dict[str, Any]]] = []
    stop_reason = "timetable_completed"
    phase_started = time.perf_counter()
    started_at = utc_now()
    for number, offset in enumerate(offsets):
        index = start_index + number
        if index >= MAX_REQUEST_COUNT:
            stop_reason = "safety_request_cap_reached"
            break
        intended = phase_started + offset
        _sleep_until(intended)
        variant_index = _choose_variant(config, variant_rng)
        futures.append(executor.submit(
            _perform_request,
            _variant_url(url, config, variant_index),
            config["method"],
            headers,
            request_body,
            config["timeout_seconds"],
            run_id,
            phase,
            index,
            utc_now(),
            intended_perf=intended,
            phase_started_perf=phase_started,
            tracker=tracker,
            variant_index=variant_index,
        ))
    records = [future.result() for future in futures]
    elapsed = max(time.perf_counter() - phase_started, 0.0)
    records.sort(key=lambda item: item["request_index"])
    return PhaseResult(records, started_at, utc_now(), elapsed, tracker.maximum, stop_reason)


def achieved_send_rate(records: list[dict[str, Any]]) -> float | None:
    """Requests per second between the first and last actual send."""
    sent = sorted(record["sent_offset_ms"] for record in records if record.get("sent_offset_ms") is not None)
    if len(sent) < 2 or sent[-1] <= sent[0]:
        return None
    return (len(sent) - 1) / ((sent[-1] - sent[0]) / 1000.0)


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def span_correlation(span_file: Path, run_id: str, expected_spans: int, wait_seconds: float,
                     trace_ids: list[str] | None = None) -> dict[str, Any]:
    """Run-level span counts, plus exact per-request matching when trace IDs are given.

    Each request's trace ID was sent as a W3C traceparent, so its experiment span is
    the one with that trace ID and this run ID.
    """
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
    result = {
        "span_file": str(span_file),
        "expected_spans": expected_spans,
        "observed_spans": len(matches),
        "unique_observed_spans": unique_observed,
        "missing_spans": missing,
        "duplicate_spans": duplicates,
        "malformed_lines_ignored": malformed_lines,
        "correlation_status": "complete" if missing == 0 and duplicates == 0 else "partial",
        "correlation_note": "Counts are joined by experiment run ID.",
    }
    if trace_ids is None:
        return result
    spans_per_trace = Counter()
    for span in {span.get("span_id"): span for span in matches if span.get("span_id")}.values():
        spans_per_trace[span.get("trace_id")] += 1
    requested = Counter(trace_ids)
    matched = sum(1 for trace_id in requested if spans_per_trace.get(trace_id) == 1)
    result["request_level"] = {
        "client_requests": len(trace_ids),
        "duplicate_client_trace_ids": sum(count - 1 for count in requested.values() if count > 1),
        "requests_matched_to_one_span": matched,
        "requests_without_span": sum(1 for trace_id in requested if trace_id not in spans_per_trace),
        "requests_with_multiple_spans": sum(1 for trace_id in requested if spans_per_trace.get(trace_id, 0) > 1),
        "spans_without_client_request": sum(count for trace_id, count in spans_per_trace.items() if trace_id not in requested),
    }
    exact = matched == len(trace_ids) and not result["request_level"]["spans_without_client_request"] and not result["request_level"]["duplicate_client_trace_ids"]
    result["correlation_status"] = "complete" if exact and duplicate_records == 0 else "partial"
    result["correlation_note"] = "Each request is joined to its experiment span by W3C trace ID (traceparent sent by the runner) and run ID."
    return result


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
            "load_model": config["load_model"],
            "arrival_process": config["arrival_process"],
            "random_seed": config["random_seed"],
            "stages": config["stages"],
            "request_variants": config["request_variants"],
        },
        "environment": environment_metadata(),
        "artifacts": {"configuration": "configuration.json", "request_results": "requests.jsonl", "summary": "summary.json"},
    }
    _write_json(configuration_file, configuration)

    all_records: list[dict[str, Any]] = []
    warmup_result: PhaseResult | None = None
    measured_result: PhaseResult | None = None
    open_loop = config["load_model"] == "open"
    planned_measured: int | None = None
    # In the open loop, `concurrency` is the number of workers: the most requests that can be in flight.
    with concurrent.futures.ThreadPoolExecutor(max_workers=config["concurrency"], thread_name_prefix="experiment-request") as executor:
        if config["warmup_requests"]:
            if open_loop:
                rate = warmup_rate(config)
                warmup_offsets = arrival_offsets(
                    [{"duration_seconds": math.inf, "rate_per_second_start": rate, "rate_per_second_end": rate}],
                    config["arrival_process"], _seeded(config, "warmup:arrivals"), count=config["warmup_requests"])
                warmup_result = run_phase_open(executor, url=endpoint, config=config, run_id=experiment_id, phase="warmup",
                                               start_index=0, offsets=warmup_offsets, variant_rng=_seeded(config, "warmup:variants"))
            else:
                warmup_result = run_phase(
                    executor,
                    url=endpoint,
                    config=config,
                    run_id=experiment_id,
                    phase="warmup",
                    start_index=0,
                    request_count=config["warmup_requests"],
                    duration_seconds=None,
                    variant_rng=_seeded(config, "warmup:variants"),
                )
            all_records.extend(warmup_result.records)
            _append_jsonl(requests_file, warmup_result.records)
        if open_loop:
            measured_offsets = arrival_offsets(stage_list(config), config["arrival_process"], _seeded(config, "measured:arrivals"),
                                               count=request_count, cap=MAX_REQUEST_COUNT - len(all_records))
            planned_measured = len(measured_offsets)
            measured_result = run_phase_open(executor, url=endpoint, config=config, run_id=experiment_id, phase="measured",
                                             start_index=len(all_records), offsets=measured_offsets,
                                             variant_rng=_seeded(config, "measured:variants"))
        else:
            measured_result = run_phase(
                executor,
                url=endpoint,
                config=config,
                run_id=experiment_id,
                phase="measured",
                start_index=len(all_records),
                request_count=request_count,
                duration_seconds=duration_seconds,
                variant_rng=_seeded(config, "measured:variants"),
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
    correlation = span_correlation(span_file, experiment_id, expected_spans, config["span_wait_seconds"],
                                   trace_ids=[record["trace_id"] for record in all_records])
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
        "planned_measured_requests": planned_measured,
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
            "load_model": config["load_model"],
            "arrival_process": config["arrival_process"],
            "random_seed": config["random_seed"],
            "planned_measured_requests": planned_measured,
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
            # Client-side timing (Phase 3.5B). send_lag = actual send - intended send.
            "send_lag_ms": latency_summary([r["send_lag_ms"] for r in measured_records if r.get("send_lag_ms") is not None]),
            "latency_from_intended_ms": latency_summary([r["latency_from_intended_ms"] for r in measured_records if r.get("latency_from_intended_ms") is not None]),
            "achieved_send_rate_per_second": achieved_send_rate(measured_records),
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
    for name in ("target-url", "scenario", "method", "request-count", "duration-seconds", "concurrency", "warmup-requests", "request-rate-per-second", "request-interval-seconds", "timeout-seconds", "repeats", "random-seed"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), default=None)
    parser.add_argument("--load-model", dest="load_model", choices=LOAD_MODELS, default=None)
    parser.add_argument("--arrival-process", dest="arrival_process", choices=ARRIVAL_PROCESSES, default=None)
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
            "random_seed": args.random_seed,
            "load_model": args.load_model,
            "arrival_process": args.arrival_process,
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
            if key in {"request_count", "warmup_requests", "concurrency", "repeats", "random_seed"}:
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
