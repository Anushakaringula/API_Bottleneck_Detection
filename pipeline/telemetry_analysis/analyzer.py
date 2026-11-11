#!/usr/bin/env python3
"""Analyze existing experiment artifacts without changing their contents."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import statistics
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from . import __version__
from .schema import JOIN_KEY_FIELDS, PREDICTOR_FIELDS, REQUEST_JOIN_SCHEMA_VERSION, SCHEMA_VERSION
DATA_DIR = Path(os.environ.get("ABR_DATA_DIR") or Path(__file__).resolve().parents[2] / "data").resolve()
SPAN_KIND_SERVER = 1
SPAN_REQUIRED_ATTRIBUTES_V1 = ("experiment.run_id", "experiment.scenario", "http.request.method", "url.path", "experiment.latency_ms", "experiment.response_size_bytes", "http.response.status_code")
SPAN_REQUIRED_ATTRIBUTES_V2 = ("experiment.run_id", "http.request.method", "url.path", "experiment.latency_ms", "experiment.response_size_bytes", "http.response.status_code")


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q
    lo, hi = math.floor(rank), math.ceil(rank)
    return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _read_json(path: Path, issues: list[dict[str, Any]], run_key: str | None = None) -> dict[str, Any] | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("expected JSON object")
        return obj
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues.append({"severity": "error", "code": "invalid_json_artifact", "run_id": run_key, "source": str(path), "detail": str(exc)})
        return None


def _read_jsonl(path: Path, issues: list[dict[str, Any]], run_key: str | None = None) -> list[dict[str, Any]]:
    rows = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_no, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("expected JSON object")
                    rows.append(row)
                except (ValueError, json.JSONDecodeError) as exc:
                    issues.append({"severity": "warning", "code": "malformed_jsonl_record", "run_id": run_key, "source": str(path), "line": line_no, "detail": str(exc)})
    except OSError as exc:
        issues.append({"severity": "error", "code": "unreadable_jsonl", "run_id": run_key, "source": str(path), "detail": str(exc)})
    return rows


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_spans(rows: list[dict[str, Any]], issues: list[dict[str, Any]]) -> None:
    available = {(r.get("trace_id"), r.get("span_id")) for r in rows}
    for index, row in enumerate(rows, 1):
        for field, length in (("trace_id", 32), ("span_id", 16)):
            value = row.get(field)
            if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", value):
                issues.append({"severity": "warning", "code": "invalid_span_identifier", "record": index, "field": field})
        parent = row.get("parent_span_id")
        if parent is not None and (not isinstance(parent, str) or not re.fullmatch(r"[0-9a-fA-F]{16}", parent)):
            issues.append({"severity": "warning", "code": "invalid_parent_span_id", "record": index})
        elif parent is not None and (row.get("trace_id"), parent) not in available and row.get("span_kind") != SPAN_KIND_SERVER:
            # A server span's parent is the client's span (W3C traceparent); it is never exported.
            issues.append({"severity": "warning", "code": "parent_span_not_in_input", "record": index, "parent_span_id": parent})
        duration = _number(row.get("duration_ms"))
        if duration is None or duration < 0:
            issues.append({"severity": "warning", "code": "invalid_span_duration", "record": index})
        if row.get("span_name") == "experiment.http.request":
            attrs = row.get("attributes") or {}
            # Telemetry schema 2 (Phase 3.5A) no longer records the scenario label.
            required = SPAN_REQUIRED_ATTRIBUTES_V2 if attrs.get("experiment.telemetry_schema") == "2" else SPAN_REQUIRED_ATTRIBUTES_V1
            for key in required:
                if key not in attrs:
                    issues.append({"severity": "warning", "code": "dedicated_span_attribute_missing", "record": index, "attribute": key})
            for key in ("experiment.latency_ms", "experiment.cpu_user_ms", "experiment.cpu_system_ms", "experiment.heap_delta_mb", "experiment.response_size_bytes"):
                if key in attrs and _number(attrs[key]) is None:
                    issues.append({"severity": "warning", "code": "invalid_span_measurement", "record": index, "attribute": key})


def _run_features(requests: list[dict[str, Any]], config: dict[str, Any], summary: dict[str, Any],
                  record_id: str, issues: list[dict[str, Any]], run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    measured = [r for r in requests if r.get("phase") == "measured"]
    warmup = [r for r in requests if r.get("phase") == "warmup"]
    unknown = [r for r in requests if r.get("phase") not in {"measured", "warmup"}]
    if unknown:
        issues.append({"severity": "warning", "code": "unknown_request_phase", "run_id": run_id, "count": len(unknown)})
    latencies = []
    for row in measured:
        n = _number(row.get("latency_ms"))
        if n is None or n < 0:
            issues.append({"severity": "warning", "code": "invalid_measured_latency", "run_id": run_id, "request_index": row.get("request_index")})
        else:
            latencies.append(n)
    status_counts = Counter(str(r["status_code"]) for r in measured if r.get("status_code") is not None)
    no_status = sum(r.get("status_code") is None for r in measured)
    successes = sum(r.get("outcome") == "success" and r.get("status_code") is not None and 200 <= int(r["status_code"]) < 300 for r in measured)
    failures = sum(r.get("outcome") != "success" for r in measured)
    sizes = [_number(r.get("response_bytes")) for r in measured if r.get("status_code") is not None]
    sizes = [x for x in sizes if x is not None and x >= 0]

    execution = config.get("execution") or {}
    measured_summary = summary.get("measured") or {}
    duration = _number(execution.get("measured_phase_elapsed_seconds"))
    duration_source = "configuration.execution.measured_phase_elapsed_seconds"
    if duration is None:
        duration = _number(measured_summary.get("phase_elapsed_seconds"))
        duration_source = "summary.measured.phase_elapsed_seconds"
    if duration is None or duration <= 0:
        duration = None
        issues.append({"severity": "warning", "code": "throughput_duration_unavailable", "run_id": run_id})
    elif execution.get("measured_attempted_requests") is None and measured_summary.get("phase_elapsed_seconds") is None:
        duration = None
        issues.append({"severity": "warning", "code": "throughput_duration_unvalidated", "run_id": run_id})

    feature = {
        "analysis_record_id": record_id,
        "measured_attempts": len(measured),
        "successful_responses": successes,
        "failed_requests": failures,
        "error_rate": failures / len(measured) if measured else None,
        "average_latency_ms": statistics.fmean(latencies) if latencies else None,
        "median_latency_ms": percentile(latencies, .5),
        "p95_latency_ms": percentile(latencies, .95),
        "p99_latency_ms": percentile(latencies, .99),
        "minimum_latency_ms": min(latencies) if latencies else None,
        "maximum_latency_ms": max(latencies) if latencies else None,
        "attempt_throughput_rps": len(measured) / duration if duration else None,
        "successful_throughput_rps": successes / duration if duration else None,
        "measured_phase_duration_seconds": duration,
        "responses_with_size": len(sizes),
        "average_response_bytes": statistics.fmean(sizes) if sizes else None,
        "median_response_bytes": percentile(sizes, .5),
        "p95_response_bytes": percentile(sizes, .95),
        "maximum_response_bytes": max(sizes) if sizes else None,
        "runner_max_in_flight": _number(execution.get("maximum_observed_in_flight", measured_summary.get("maximum_observed_in_flight"))),
        "status_2xx_count": sum(v for k, v in status_counts.items() if k.isdigit() and int(k) // 100 == 2),
        "status_3xx_count": sum(v for k, v in status_counts.items() if k.isdigit() and int(k) // 100 == 3),
        "status_4xx_count": sum(v for k, v in status_counts.items() if k.isdigit() and int(k) // 100 == 4),
        "status_5xx_count": sum(v for k, v in status_counts.items() if k.isdigit() and int(k) // 100 == 5),
        "status_other_count": sum(v for k, v in status_counts.items() if not k.isdigit() or int(k) // 100 not in {2, 3, 4, 5}),
        "requests_without_status": no_status,
    }
    if len(measured) != measured_summary.get("total_requests"):
        issues.append({"severity": "warning", "code": "measured_count_summary_mismatch", "run_id": run_id, "request_records": len(measured), "summary": measured_summary.get("total_requests")})
    if len(warmup) != (summary.get("warmup") or {}).get("total_requests"):
        issues.append({"severity": "warning", "code": "warmup_count_summary_mismatch", "run_id": run_id, "request_records": len(warmup), "summary": (summary.get("warmup") or {}).get("total_requests")})
    if duration and measured_summary.get("phase_elapsed_seconds") is not None and not math.isclose(duration, float(measured_summary["phase_elapsed_seconds"]), rel_tol=1e-6, abs_tol=1e-6):
        issues.append({"severity": "warning", "code": "duration_source_mismatch", "run_id": run_id, "configuration_seconds": duration, "summary_seconds": measured_summary.get("phase_elapsed_seconds")})
    calculated = {
        "average_ms": statistics.fmean(latencies) if latencies else None,
        "median_ms": percentile(latencies, .5), "p95_ms": percentile(latencies, .95),
        "p99_ms": percentile(latencies, .99), "minimum_ms": min(latencies) if latencies else None,
        "maximum_ms": max(latencies) if latencies else None,
    }
    for field, actual in calculated.items():
        expected = _number(measured_summary.get(field))
        if expected is not None and (actual is None or not math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-6)):
            issues.append({"severity": "warning", "code": "latency_summary_mismatch", "run_id": run_id, "field": field, "recomputed": actual, "summary": expected})
    for field, actual in (("successful_requests", successes), ("failed_requests", failures)):
        expected = measured_summary.get(field)
        if expected is not None and actual != expected:
            issues.append({"severity": "warning", "code": "outcome_summary_mismatch", "run_id": run_id, "field": field, "recomputed": actual, "summary": expected})
    if measured_summary.get("http_status_codes") is not None:
        summary_status = {str(k): int(v) for k, v in measured_summary["http_status_codes"].items()}
        if dict(status_counts) != summary_status:
            issues.append({"severity": "warning", "code": "status_distribution_mismatch", "run_id": run_id, "recomputed": dict(status_counts), "summary": summary_status})
    meta = {
        "analysis_record_id": record_id,
        "experiment_id": config.get("experiment_id"),
        "run_id": config.get("experiment_id"),
        "repeat_group_id": config.get("repeat_group_id"),
        "repeat_index": config.get("repeat_index"),
        "scenario": config.get("scenario"),
        "scenario_role": config.get("scenario_role", "experimental_ground_truth_only"),
        "target_endpoint": config.get("target_endpoint"),
        "http_method": config.get("http_method"),
        "schema_version": config.get("schema_version"),
        "started_at": config.get("started_at"),
        "ended_at": config.get("ended_at"),
        "warmup_requests": len(warmup),
        "measured_requests": len(measured),
        "workload_configuration": config.get("workload_configuration", {}),
        "environment": config.get("environment", {}),
        "throughput_duration_source": duration_source if duration is not None else None,
        "throughput_duration_valid": duration is not None,
    }
    return feature, meta


def _status(value: Any) -> int | None:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _request_records(requests: list[dict[str, Any]], run_spans: list[dict[str, Any]],
                     spans_by_trace: dict[str, list[dict[str, Any]]], record_id: str,
                     run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Join each client request to its experiment span by W3C trace ID (Phase 3.5A).

    Requests recorded before the runner sent traceparent have no trace ID and are
    reported as not joinable; nothing is inferred for them.
    """
    rows = []
    client_traces = set()
    for request in requests:
        trace_id = request.get("trace_id") if isinstance(request.get("trace_id"), str) and request.get("trace_id") else None
        row = {
            "analysis_record_id": record_id,
            "request_index": request.get("request_index"),
            "phase": request.get("phase"),
            "trace_id": trace_id,
            "client_latency_ms": _number(request.get("latency_ms")),
            "client_status_code": _status(request.get("status_code")),
            "client_response_bytes": _number(request.get("response_bytes")),
            "server_latency_ms": None,
            "server_status_code": None,
            "server_response_bytes": None,
            "client_minus_server_ms": None,
            "status_agrees": None,
        }
        if trace_id is None:
            row["match_status"] = "no_client_trace_id"
            rows.append(row)
            continue
        client_traces.add(trace_id)
        unique = list({s.get("span_id"): s for s in spans_by_trace.get(trace_id, []) if s.get("span_id")}.values())
        if not unique:
            row["match_status"] = "no_server_span"
        elif len(unique) > 1:
            row["match_status"] = "multiple_server_spans"
        elif str((unique[0].get("attributes") or {}).get("experiment.run_id")) != run_id:
            row["match_status"] = "run_id_mismatch"
        else:
            row["match_status"] = "matched"
            attrs = unique[0].get("attributes") or {}
            row["server_latency_ms"] = _number(attrs.get("experiment.latency_ms"))
            row["server_status_code"] = _status(attrs.get("http.response.status_code"))
            row["server_response_bytes"] = _number(attrs.get("experiment.response_size_bytes"))
            if row["server_latency_ms"] is not None and row["client_latency_ms"] is not None:
                row["client_minus_server_ms"] = row["client_latency_ms"] - row["server_latency_ms"]
            if row["client_status_code"] is not None and row["server_status_code"] is not None:
                row["status_agrees"] = row["client_status_code"] == row["server_status_code"]
        rows.append(row)
    counts = Counter(row["match_status"] for row in rows)
    joinable = len(rows) - counts.get("no_client_trace_id", 0)
    unique_run_spans = {s.get("span_id"): s for s in run_spans if s.get("span_id")}.values()
    summary = {
        "analysis_record_id": record_id,
        "run_id": run_id,
        "client_requests": len(rows),
        "client_requests_with_trace_id": joinable,
        "matched_requests": counts.get("matched", 0),
        "match_status_counts": dict(sorted(counts.items())),
        "exact_match_rate": counts.get("matched", 0) / joinable if joinable else None,
        # Only meaningful when the client recorded trace IDs.
        "server_spans_without_client_request": sum(s.get("trace_id") not in client_traces for s in unique_run_spans) if joinable else None,
    }
    return rows, summary


def analyze(experiments_dir: Path, span_file: Path, output_root: Path) -> Path:
    issues: list[dict[str, Any]] = []
    span_rows = _read_jsonl(span_file, issues)
    _validate_spans(span_rows, issues)
    span_ids = Counter(r.get("span_id") for r in span_rows if r.get("span_id"))
    for span_id, count in span_ids.items():
        if count > 1:
            issues.append({"severity": "error", "code": "duplicate_span_id", "span_id": span_id, "count": count})
    dedicated = [r for r in span_rows if r.get("span_name") == "experiment.http.request"]
    spans_by_run: dict[str, list[dict[str, Any]]] = {}
    for span in dedicated:
        attrs = span.get("attributes") or {}
        rid = attrs.get("experiment.run_id")
        if not rid:
            issues.append({"severity": "warning", "code": "dedicated_span_missing_run_id", "span_id": span.get("span_id")})
        else:
            spans_by_run.setdefault(str(rid), []).append(span)
    spans_by_trace: dict[str, list[dict[str, Any]]] = {}
    for span in dedicated:
        if isinstance(span.get("trace_id"), str):
            spans_by_trace.setdefault(span["trace_id"], []).append(span)

    out_dir = output_root / (dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8])
    out_dir.mkdir(parents=True, exist_ok=False)
    features, metadata, observations = [], [], []
    request_rows, join_summaries = [], []
    seen_runs = set()
    for folder in sorted(p for p in experiments_dir.iterdir() if p.is_dir()):
        run_id = folder.name
        config = _read_json(folder / "configuration.json", issues, run_id)
        summary = _read_json(folder / "summary.json", issues, run_id)
        if config is None or summary is None:
            continue
        canonical_id = str(config.get("experiment_id") or run_id)
        if summary.get("experiment_id") and str(summary["experiment_id"]) != canonical_id:
            issues.append({"severity": "error", "code": "summary_experiment_id_mismatch", "run_id": canonical_id, "summary_experiment_id": summary["experiment_id"]})
        seen_runs.add(canonical_id)
        if canonical_id != run_id:
            issues.append({"severity": "error", "code": "directory_experiment_id_mismatch", "run_id": run_id, "configuration_experiment_id": canonical_id})
        requests = _read_jsonl(folder / "requests.jsonl", issues, canonical_id)
        record_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "telemetry-analysis:" + canonical_id))
        feature, meta = _run_features(requests, config, summary, record_id, issues, canonical_id)
        features.append(feature); metadata.append(meta)

        run_spans = spans_by_run.get(canonical_id, [])
        unique_spans = {s.get("span_id"): s for s in run_spans if s.get("span_id")}
        attempt_count = len(requests)
        valid_statuses = Counter(str(r.get("status_code")) for r in requests if r.get("status_code") is not None)
        span_statuses = Counter(str((s.get("attributes") or {}).get("http.response.status_code")) for s in run_spans if (s.get("attributes") or {}).get("http.response.status_code") is not None)
        failed_rows = [r for r in requests if r.get("outcome") != "success"]
        failed_with_status = Counter(str(r.get("status_code")) for r in failed_rows if r.get("status_code") is not None)
        # Status-code overlap is count-level evidence only; no request-to-span IDs exist.
        failed_status_matches = sum(min(count, span_statuses[code]) for code, count in failed_with_status.items())
        failed_without_status = sum(r.get("status_code") is None for r in failed_rows)
        known_http_failures = sum(r.get("status_code") is not None for r in failed_rows)
        total_missing = max(attempt_count - len(unique_spans), 0)
        statuses_for_http_failures = sum(failed_with_status.values())
        status_based_unmatched = max(statuses_for_http_failures - failed_status_matches, 0)
        response_attempts = sum(valid_statuses.values())
        transport_possible_matches = min(failed_without_status, max(len(unique_spans) - response_attempts, 0))
        observations.append({
            "analysis_record_id": record_id,
            "observed_dedicated_spans": len(run_spans),
            "unique_dedicated_spans": len(unique_spans),
            "duplicate_dedicated_spans": len(run_spans) - len(unique_spans),
            "expected_attempts_including_warmup": attempt_count,
            "missing_span_count_upper_bound": total_missing,
            "unexplained_correlation_discrepancy": len(unique_spans) != attempt_count,
            "failed_requests_with_status_correspondence_count_level": failed_status_matches,
            "failed_http_requests_without_status_correspondence_count_level": status_based_unmatched,
            "failed_connection_requests_without_span_count": failed_without_status,
            "failed_connection_requests_with_possible_span_count": transport_possible_matches,
            "failed_requests_with_span_count_lower_bound": failed_status_matches,
            "failed_requests_with_span_count_upper_bound": min(len(failed_rows), failed_status_matches + transport_possible_matches),
            "failed_request_span_matching_limitation": "No client-visible request ID is shared with spans; status matching is aggregate evidence, not one-to-one proof.",
            "span_duration_ms_mean": statistics.fmean([v for s in run_spans if (v := _number(s.get("duration_ms"))) is not None]) if run_spans else None,
            "span_phase_coverage": "warmup_and_measured_unseparated" if meta["warmup_requests"] else "measured_only_by_run_configuration",
            "span_duration_scope": "server span duration; distinct from client latency",
            "resource_observations": {
                "scope": "process-level deltas sampled around request span; not per-request consumption",
                "phase_coverage": "warmup_and_measured_unseparated" if meta["warmup_requests"] else "measured_only_by_run_configuration",
                "reliability": "low; concurrent requests, garbage collection, and unrelated process work can affect values",
                "cpu_user_ms_mean": _mean_attr(run_spans, "experiment.cpu_user_ms"),
                "cpu_system_ms_mean": _mean_attr(run_spans, "experiment.cpu_system_ms"),
                "heap_delta_mb_mean": _mean_attr(run_spans, "experiment.heap_delta_mb"),
            },
        })
        if len(run_spans) != attempt_count:
            issues.append({"severity": "warning", "code": "span_count_discrepancy", "run_id": canonical_id, "request_attempts": attempt_count, "observed_dedicated_spans": len(run_spans), "interpretation": "Unexplained by this analyzer; transport failures may not reach the server, and run-level counts cannot establish request-level matching."})
        if len(run_spans) > attempt_count:
            issues.append({"severity": "warning", "code": "excess_dedicated_spans", "run_id": canonical_id, "request_attempts": attempt_count, "observed_dedicated_spans": len(run_spans)})
        rows, join_summary = _request_records(requests, run_spans, spans_by_trace, record_id, canonical_id)
        request_rows.extend(rows)
        join_summaries.append(join_summary)
        # Raised only for requests that carry a trace ID, so pre-3.5A data is unaffected.
        if join_summary["client_requests_with_trace_id"] and (join_summary["matched_requests"] != join_summary["client_requests_with_trace_id"] or join_summary["server_spans_without_client_request"]):
            issues.append({"severity": "warning", "code": "request_level_correlation_incomplete", "run_id": canonical_id, "match_status_counts": join_summary["match_status_counts"], "server_spans_without_client_request": join_summary["server_spans_without_client_request"]})

    for run_id in sorted(set(spans_by_run) - seen_runs):
        issues.append({"severity": "warning", "code": "orphan_span_run", "run_id": run_id, "span_count": len(spans_by_run[run_id])})
    # Enforce schema at serialization time: no unapproved predictor key can leak in.
    for row in features:
        unexpected = set(row) - JOIN_KEY_FIELDS - PREDICTOR_FIELDS
        if unexpected:
            raise ValueError(f"unapproved feature fields: {sorted(unexpected)}")
    outputs = {
        "run_features.jsonl": features,
        "run_metadata.jsonl": metadata,
        "telemetry_observations.jsonl": observations,
        "data_quality.jsonl": issues,
    }
    for name, rows in outputs.items():
        with (out_dir / name).open("w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    # Request-level join (Phase 3.5A). Separate files, not predictors: they hold trace
    # IDs and are kept out of manifest.json so earlier analyses reproduce exactly.
    with (out_dir / "request_records.jsonl").open("w", encoding="utf-8") as stream:
        for row in request_rows:
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    joinable = sum(s["client_requests_with_trace_id"] for s in join_summaries)
    matched = sum(s["matched_requests"] for s in join_summaries)
    join_report = {
        "request_join_schema_version": REQUEST_JOIN_SCHEMA_VERSION,
        "join_key": "trace_id (W3C traceparent sent by the runner) plus experiment.run_id",
        "role": "correlation and data quality only; not model predictors",
        "totals": {
            "client_requests": len(request_rows),
            "client_requests_with_trace_id": joinable,
            "matched_requests": matched,
            "exact_match_rate": matched / joinable if joinable else None,
        },
        "runs": join_summaries,
    }
    (out_dir / "request_join_summary.json").write_text(json.dumps(join_report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    source_paths = [span_file] + sorted(experiments_dir.glob("*/configuration.json")) + sorted(experiments_dir.glob("*/requests.jsonl")) + sorted(experiments_dir.glob("*/summary.json"))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "analyzer_version": __version__,
        "analysis_created_at": _utc(),
        "input_sources": [{"path": str(p), "sha256": _hash(p)} for p in source_paths if p.is_file()],
        "counts": {"experiment_runs": len(features), "raw_span_records": len(span_rows), "dedicated_experiment_spans": len(dedicated), "quality_findings": len(issues)},
        "predictor_fields": sorted(PREDICTOR_FIELDS),
        "join_key_fields": sorted(JOIN_KEY_FIELDS),
        "non_predictor_metadata_fields": ["scenario", "target_endpoint", "experiment_id", "run_id", "repeat_group_id", "analysis_record_id"],
        "repeat_group_partition_rule": "All runs sharing repeat_group_id must remain in the same future train/test partition.",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_dir


def _mean_attr(spans: list[dict[str, Any]], key: str) -> float | None:
    vals = [_number((s.get("attributes") or {}).get(key)) for s in spans]
    vals = [v for v in vals if v is not None]
    return statistics.fmean(vals) if vals else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiments-dir", type=Path, default=Path(os.environ.get("ABR_EXPERIMENTS_DIR") or DATA_DIR / "raw" / "experiments"))
    parser.add_argument("--span-file", type=Path, default=Path(os.environ.get("ABR_SPAN_FILE") or DATA_DIR / "raw" / "spans" / "otel-spans.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=Path(os.environ.get("ABR_ANALYSIS_DIR") or DATA_DIR / "processed"))
    args = parser.parse_args()
    result = analyze(args.experiments_dir, args.span_file, args.output_dir)
    print(result)


if __name__ == "__main__":
    main()
