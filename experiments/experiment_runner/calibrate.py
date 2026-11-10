#!/usr/bin/env python3
"""Client calibration (Phase 3.5B): can the runner keep to an open-loop timetable?

For each calibration operation and target rate, one open-loop run is sent to the
target API's calibration endpoints (start it with ABR_CALIBRATION=1):

- noop:  /__calib/noop       near-zero server work; measures client overhead
- sleep: /__calib/sleep?ms=N  non-blocking server wait; needs about rate x N/1000
                              requests in flight, so it tests real concurrency

A run passes when the send lag p99 is within the limit, no request failed, and
the achieved send rate is within the tolerance of the target. The report gives
the highest rate at which every operation passed (the gate for Phase 3.5B).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from .runner import DATA_DIR, MAX_CONCURRENCY, environment_metadata, execute_experiment, resolve_configuration, utc_now

OPERATIONS = {
    "noop": "/__calib/noop",
    "sleep": "/__calib/sleep?ms={sleep_ms}",
}


def evaluate(summary: dict[str, Any], target_rate: float, max_lag_p99_ms: float, rate_tolerance: float) -> dict[str, Any]:
    measured = summary["measured"]
    lag = measured["send_lag_ms"]
    achieved = measured["achieved_send_rate_per_second"]
    checks = {
        "send_lag_p99_within_limit": lag["p99_ms"] is not None and lag["p99_ms"] <= max_lag_p99_ms,
        "no_failed_requests": measured["failed_requests"] == 0 and measured["total_requests"] > 0,
        "achieved_rate_within_tolerance": achieved is not None and abs(achieved / target_rate - 1.0) <= rate_tolerance,
    }
    return {
        "target_rate_per_second": target_rate,
        "achieved_send_rate_per_second": achieved,
        "measured_requests": measured["total_requests"],
        "failed_requests": measured["failed_requests"],
        "send_lag_p50_ms": lag["median_ms"],
        "send_lag_p95_ms": lag["p95_ms"],
        "send_lag_p99_ms": lag["p99_ms"],
        "send_lag_max_ms": lag["maximum_ms"],
        "latency_p50_ms": measured["median_ms"],
        "latency_p99_ms": measured["p99_ms"],
        "maximum_in_flight": measured["maximum_observed_in_flight"],
        "checks": checks,
        "pass": all(checks.values()),
    }


def max_sustainable_rate(results: list[dict[str, Any]]) -> float | None:
    """Highest rate at which every operation, and every lower rate, passed."""
    rates = sorted({r["target_rate_per_second"] for r in results})
    best = None
    for rate in rates:
        if all(r["pass"] for r in results if r["target_rate_per_second"] == rate):
            best = rate
        else:
            break
    return best


def _markdown(report: dict[str, Any]) -> str:
    p = report["parameters"]
    lines = [
        f"# Client calibration {report['calibration_id']}",
        "",
        f"Target: `{p['target_url']}`. Open loop, {p['arrival_process']} arrivals, at least {p['duration_seconds']} s and {p['min_requests']} requests per run, "
        f"sleep operation {p['sleep_ms']} ms. Pass: send lag p99 <= {p['max_lag_p99_ms']} ms, no failures, "
        f"achieved rate within {p['rate_tolerance'] * 100:.1f} % of target.",
        "",
        "| Operation | Target rps | Achieved rps | Seconds | Requests | Failed | Lag p50 ms | Lag p99 ms | Lag max ms | Latency p50 ms | Latency p99 ms | Max in flight | Pass |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    fmt = lambda v: "-" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))
    for r in report["results"]:
        lines.append(f"| {r['operation']} | {fmt(r['target_rate_per_second'])} | {fmt(r['achieved_send_rate_per_second'])} | {fmt(r['duration_seconds'])} | "
                     f"{r['measured_requests']} | {r['failed_requests']} | {fmt(r['send_lag_p50_ms'])} | {fmt(r['send_lag_p99_ms'])} | "
                     f"{fmt(r['send_lag_max_ms'])} | {fmt(r['latency_p50_ms'])} | {fmt(r['latency_p99_ms'])} | {r['maximum_in_flight']} | "
                     f"{'PASS' if r['pass'] else 'FAIL'} |")
    lines += ["", f"**Highest rate passing for all operations: {fmt(report['max_sustainable_rate_per_second'])} rps.**",
              f"Verdict: {report['verdict']}", ""]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target-url", default="http://127.0.0.1:3000", help="Base URL of the target API (started with ABR_CALIBRATION=1)")
    parser.add_argument("--rates", default="5,10,20,50,100", help="Comma-separated target rates (requests/second)")
    parser.add_argument("--operations", default="noop,sleep", help=f"Comma-separated: {', '.join(OPERATIONS)}")
    parser.add_argument("--duration-seconds", type=float, default=15.0, help="Minimum measured duration per run")
    parser.add_argument("--min-requests", type=int, default=500,
                        help="Runs are lengthened to at least this many measured requests, so the p99 is not decided by one or two samples")
    parser.add_argument("--warmup-seconds", type=float, default=2.0)
    parser.add_argument("--sleep-ms", type=int, default=50)
    parser.add_argument("--max-in-flight", type=int, default=MAX_CONCURRENCY)
    parser.add_argument("--arrival-process", choices=("constant", "poisson"), default="constant")
    parser.add_argument("--max-lag-p99-ms", type=float, default=5.0)
    parser.add_argument("--rate-tolerance", type=float, default=0.02, help="Allowed relative difference between achieved and target rate")
    parser.add_argument("--random-seed", type=int, default=None)
    parser.add_argument("--output-dir", default=str(DATA_DIR / "raw" / "calibration"), help="Raw run artifacts")
    parser.add_argument("--report-dir", default=str(DATA_DIR / "results" / "calibration"), help="Calibration reports")
    parser.add_argument("--span-file", default=os.environ.get("ABR_SPAN_FILE") or str(DATA_DIR / "raw" / "spans" / "otel-spans.jsonl"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        rates = [float(x) for x in args.rates.split(",") if x.strip()]
        operations = [x.strip() for x in args.operations.split(",") if x.strip()]
        if not rates or any(not math.isfinite(r) or r <= 0 for r in rates):
            raise ValueError("--rates must be positive numbers")
        unknown = set(operations) - set(OPERATIONS)
        if unknown or not operations:
            raise ValueError(f"unknown operations: {sorted(unknown)}")
    except ValueError as error:
        parser.error(str(error))
    calibration_id = dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    runs_dir = Path(args.output_dir).resolve() / calibration_id
    report_dir = Path(args.report_dir).resolve() / calibration_id
    results = []
    for operation in operations:
        path = OPERATIONS[operation].format(sleep_ms=args.sleep_ms)
        for index, rate in enumerate(rates, 1):
            # p99 of n samples sits between the two largest values when n < 100; enough
            # samples make one OS scheduling hiccup count as 1 in n, not as "the p99".
            duration = max(args.duration_seconds, args.min_requests / rate)
            raw = {
                "target_url": args.target_url.rstrip("/") + path,
                "scenario": "custom",
                "scenario_label": f"calibration-{operation}",
                "load_model": "open",
                "arrival_process": args.arrival_process,
                "request_count": None,
                "duration_seconds": duration,
                "warmup_requests": max(1, round(rate * args.warmup_seconds)),
                "request_rate_per_second": rate,
                "concurrency": args.max_in_flight,
                "timeout_seconds": 10,
                "span_wait_seconds": 0,
                "random_seed": args.random_seed,
                "metadata": {"purpose": "client calibration", "calibration_id": calibration_id, "operation": operation, "target_rate_per_second": rate},
            }
            summary = execute_experiment(resolve_configuration(raw), output_root=runs_dir, span_file=Path(args.span_file),
                                         repeat_group_id=calibration_id, repeat_index=index)
            result = evaluate(summary, rate, args.max_lag_p99_ms, args.rate_tolerance)
            result.update({"operation": operation, "experiment_id": summary["experiment_id"], "duration_seconds": duration})
            results.append(result)
    best = max_sustainable_rate(results)
    all_pass = all(r["pass"] for r in results)
    report = {
        "calibration_id": calibration_id,
        "created_at": utc_now(),
        "environment": environment_metadata(),
        "parameters": {
            "target_url": args.target_url, "rates": rates, "operations": operations, "duration_seconds": args.duration_seconds, "min_requests": args.min_requests,
            "warmup_seconds": args.warmup_seconds, "sleep_ms": args.sleep_ms, "max_in_flight": args.max_in_flight,
            "arrival_process": args.arrival_process, "max_lag_p99_ms": args.max_lag_p99_ms, "rate_tolerance": args.rate_tolerance,
        },
        "runs_directory": str(runs_dir),
        "results": results,
        "max_sustainable_rate_per_second": best,
        "verdict": "all rates passed" if all_pass else (f"timetable kept up to {best} rps" if best else "timetable not kept at the lowest rate"),
    }
    report_dir.mkdir(parents=True, exist_ok=False)
    (report_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (report_dir / "report.md").write_text(_markdown(report), encoding="utf-8")
    print(f"calibration report: {report_dir / 'report.md'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
