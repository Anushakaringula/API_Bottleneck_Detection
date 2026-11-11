#!/usr/bin/env python3
"""Extract run-level features (L1–L4) for every run of a campaign. Reads no ground truth.

  python -m features.extract <campaign-id>

Inputs (per run): the runner's requests.jsonl (client), the run's span file (server, database,
cache) and the runtime samples naming the run. Requests are joined to their spans by trace ID.
Outputs, data/features/<campaign-id>/:
  features.jsonl   experiment_id + numeric features (allowlist: schema.FEATURES)
  runs.jsonl       metadata, never predictors: route (operation identity, for per-operation
                   baselines), request/span coverage, protocol exclusion reasons
  schema.json      feature -> level, meaning
  manifest.json    versions, inputs, counts
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from . import __version__
from .schema import FEATURES, SCHEMA_VERSION

DATA_DIR = Path(os.environ.get("ABR_DATA_DIR") or Path(__file__).resolve().parents[2] / "data").resolve()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    rank = (len(s) - 1) * q
    lo, hi = math.floor(rank), math.ceil(rank)
    return s[lo] + (s[hi] - s[lo]) * (rank - lo)


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _system(span: dict[str, Any]) -> str | None:
    attrs = span.get("attributes") or {}
    return attrs.get("db.system.name") or attrs.get("db.system")


def _is_query(span: dict[str, Any]) -> bool:
    return _system(span) == "postgresql" and span.get("span_name", "").startswith("pg.query")


def _is_cache_call(span: dict[str, Any]) -> bool:
    return _system(span) == "redis" and span.get("span_name") != "redis-connect"


def _interval(span: dict[str, Any]) -> tuple[float, float]:
    start = dt.datetime.fromisoformat(span["timestamp"].replace("Z", "+00:00")).timestamp() * 1000.0
    return start, start + span["duration_ms"]


def covered_ms(spans: list[dict[str, Any]]) -> float:
    """Wall-clock time covered by the spans (union of intervals): parallel calls are counted once."""
    total, end = 0.0, None
    for start, stop in sorted(_interval(s) for s in spans):
        if end is None or start > end:
            total += stop - start
            end = stop
        elif stop > end:
            total += stop - end
            end = stop
    return total


def request_metrics(trace: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Server-side metrics of one request from its spans (None if no experiment span)."""
    experiment = next((s for s in trace if s.get("span_name") == "experiment.http.request"), None)
    if experiment is None:
        return None
    attrs = experiment["attributes"]
    queries = [s for s in trace if _is_query(s)]
    cache = [s for s in trace if _is_cache_call(s)]
    texts = Counter((s.get("attributes") or {}).get("db.query.text", s.get("span_name")) for s in queries)
    server_ms = attrs.get("experiment.latency_ms")
    # Wall-clock time waiting on the database / on database or cache (dashboard runs queries in parallel).
    db_ms = covered_ms(queries)
    external_ms = covered_ms(queries + cache)
    reads = [s for s in cache if (s.get("attributes") or {}).get("db.operation.name") == "GET"]
    return {
        "route": attrs.get("http.route") or attrs.get("url.path"),
        "server_ms": server_ms,
        "cpu_ms": (attrs.get("experiment.cpu_user_ms") or 0) + (attrs.get("experiment.cpu_system_ms") or 0),
        "heap_delta_mb": attrs.get("experiment.heap_delta_mb"),
        "db_queries": len(queries),
        "db_ms": db_ms,
        "db_share": db_ms / server_ms if server_ms else None,
        "repeated_query_max": max(texts.values()) if texts else 0,
        "distinct_queries": len(texts),
        "cache_calls": len(cache),
        "cache_writes": sum(1 for s in cache if (s.get("attributes") or {}).get("db.operation.name") == "SET"),
        "cache_read": bool(reads),
        "app_ms": max(server_ms - external_ms, 0.0) if server_ms is not None else None,
        "app_share": max(server_ms - external_ms, 0.0) / server_ms if server_ms else None,
    }


def run_features(requests: list[dict[str, Any]], spans: list[dict[str, Any]], runtime: list[dict[str, Any]],
                 measured_seconds: float | None) -> tuple[dict[str, Any], dict[str, Any]]:
    measured = [r for r in requests if r.get("phase") == "measured"]
    by_trace: dict[str, list[dict[str, Any]]] = {}
    for span in spans:
        by_trace.setdefault(span.get("trace_id"), []).append(span)
    joined = []
    for request in measured:
        metrics = request_metrics(by_trace.get(request.get("trace_id"), []))
        if metrics is not None:
            joined.append((request, metrics))
    server = [m for _, m in joined]
    ok = [r for r in measured if r.get("outcome") == "success"]
    client_ms = [r["latency_ms"] for r in measured if r.get("latency_ms") is not None]
    reads = [m for m in server if m["cache_read"]]
    f: dict[str, Any] = {
        "client_latency_p50_ms": percentile(client_ms, 0.5),
        "client_latency_p95_ms": percentile(client_ms, 0.95),
        "client_latency_p99_ms": percentile(client_ms, 0.99),
        "client_latency_mean_ms": _mean(client_ms),
        "client_latency_from_intended_p50_ms": percentile([r["latency_from_intended_ms"] for r in measured if r.get("latency_from_intended_ms") is not None], 0.5),
        "response_bytes_median": percentile([r["response_bytes"] for r in ok if r.get("response_bytes") is not None], 0.5),
        "error_rate": (len(measured) - len(ok)) / len(measured) if measured else None,
        "achieved_rate_rps": len(measured) / measured_seconds if measured and measured_seconds else None,
        "server_latency_p50_ms": percentile([m["server_ms"] for m in server if m["server_ms"] is not None], 0.5),
        "server_latency_p95_ms": percentile([m["server_ms"] for m in server if m["server_ms"] is not None], 0.95),
        "server_latency_p99_ms": percentile([m["server_ms"] for m in server if m["server_ms"] is not None], 0.99),
        "client_server_gap_p50_ms": percentile([r["latency_ms"] - m["server_ms"] for r, m in joined if r.get("latency_ms") is not None and m["server_ms"] is not None], 0.5),
        "cpu_ms_per_request_mean": _mean([m["cpu_ms"] for m in server]),
        "heap_delta_mb_median": percentile([m["heap_delta_mb"] for m in server if m["heap_delta_mb"] is not None], 0.5),
        "event_loop_delay_p99_ms": percentile([w["event_loop_delay_p99_ms"] for w in runtime if w.get("event_loop_delay_p99_ms") is not None], 0.5),
        "event_loop_utilization_mean": _mean([w["event_loop_utilization"] for w in runtime if w.get("event_loop_utilization") is not None]),
        "gc_ms_per_second": (sum(w.get("gc_total_ms", 0) for w in runtime) / (sum(w.get("window_ms", 0) for w in runtime) / 1000.0)) if runtime and sum(w.get("window_ms", 0) for w in runtime) else None,
        "rss_mb_mean": _mean([w["rss_mb"] for w in runtime if w.get("rss_mb") is not None]),
        "in_flight_max": max((w.get("requests_in_flight_max", w.get("requests_in_flight", 0)) for w in runtime), default=None),
        "db_queries_per_request_mean": _mean([m["db_queries"] for m in server]),
        "db_queries_per_request_max": max((m["db_queries"] for m in server), default=None),
        "db_time_p50_ms": percentile([m["db_ms"] for m in server], 0.5),
        "db_time_share_p50": percentile([m["db_share"] for m in server if m["db_share"] is not None], 0.5),
        "repeated_query_max_median": percentile([m["repeated_query_max"] for m in server], 0.5),
        "distinct_queries_per_request_mean": _mean([m["distinct_queries"] for m in server]),
        "cache_calls_per_request_mean": _mean([m["cache_calls"] for m in server]),
        "cache_writes_per_request_mean": _mean([m["cache_writes"] for m in server]),
        "cache_hit_ratio": (sum(1 for m in reads if m["db_queries"] == 0) / len(reads)) if reads else None,
        "app_time_p50_ms": percentile([m["app_ms"] for m in server if m["app_ms"] is not None], 0.5),
        "app_time_share_p50": percentile([m["app_share"] for m in server if m["app_share"] is not None], 0.5),
    }
    routes = Counter(m["route"] for m in server if m["route"])
    meta = {
        "route": routes.most_common(1)[0][0] if routes else None,
        "measured_requests": len(measured),
        "requests_with_spans": len(joined),
        "span_coverage": len(joined) / len(measured) if measured else None,
        "runtime_windows": len(runtime),
    }
    return f, meta


def extract(campaign_dir: Path, spans_dir: Path, runtime_file: Path, output_dir: Path) -> Path:
    experiments = sorted(p for p in (campaign_dir / "experiments").iterdir() if p.is_dir())
    runtime_all = _jsonl(runtime_file)
    exclusions = {}
    if (campaign_dir / "exclusions.json").exists():
        exclusions = {e["experiment_id"]: e["reasons"] for e in json.loads((campaign_dir / "exclusions.json").read_text(encoding="utf-8"))["excluded"]}
    completed = {p["experiment_id"] for p in _jsonl(campaign_dir / "progress.jsonl") if p.get("status") in ("completed",)}
    features_out, runs_out = [], []
    for run_dir in experiments:
        experiment_id = run_dir.name
        if completed and experiment_id not in completed:
            continue  # failed runs and discarded warm-up runs are not features
        config = json.loads((run_dir / "configuration.json").read_text(encoding="utf-8"))
        seconds = (config.get("execution") or {}).get("measured_phase_elapsed_seconds")
        runtime = [w for w in runtime_all if experiment_id in (w.get("run_ids") or [])]
        f, meta = run_features(_jsonl(run_dir / "requests.jsonl"), _jsonl(spans_dir / f"{experiment_id}.jsonl"), runtime, seconds)
        unexpected = set(f) - set(FEATURES)
        if unexpected:
            raise ValueError(f"unapproved features: {sorted(unexpected)}")
        features_out.append({"experiment_id": experiment_id, **f})
        runs_out.append({"experiment_id": experiment_id, "campaign_id": campaign_dir.name,
                         "run_index": (config.get("workload_configuration") or {}).get("user_metadata", {}).get("run_index"),
                         **meta, "excluded_reasons": exclusions.get(experiment_id, [])})
    out = output_dir / campaign_dir.name
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("features.jsonl", features_out), ("runs.jsonl", runs_out)):
        with (out / name).open("w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    (out / "schema.json").write_text(json.dumps({"schema_version": SCHEMA_VERSION, "features": {k: {"level": v[0], "meaning": v[1]} for k, v in FEATURES.items()}}, indent=2) + "\n", encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps({
        "extractor_version": __version__, "schema_version": SCHEMA_VERSION, "campaign_id": campaign_dir.name,
        "runs": len(features_out), "runtime_file": str(runtime_file), "spans_dir": str(spans_dir),
        "excluded_runs": sum(1 for r in runs_out if r["excluded_reasons"]),
        "note": "features.jsonl holds predictors only; runs.jsonl is metadata (route = operation identity for baselines). No ground truth is read.",
    }, indent=2) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("campaign_id")
    parser.add_argument("--campaigns-dir", type=Path, default=DATA_DIR / "raw" / "campaigns")
    parser.add_argument("--spans-dir", type=Path, default=DATA_DIR / "raw" / "spans" / "runs")
    parser.add_argument("--runtime-file", type=Path, default=DATA_DIR / "raw" / "runtime" / "runtime-metrics.jsonl")
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR / "features")
    args = parser.parse_args(argv)
    out = extract(args.campaigns_dir / args.campaign_id, args.spans_dir, args.runtime_file, args.output_dir)
    print(f"features: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
