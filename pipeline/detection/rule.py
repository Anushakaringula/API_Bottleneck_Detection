"""The detector of record (experiments/protocol.md v1, §9 decisions 1–2).

A run is ANOMALOUS when its server-side run p50 exceeds its operation's threshold:

    threshold = max(P99 of the healthy run p50s, (1 + MIN_SLOWDOWN) × healthy median)

The baseline of an operation (identified by its route) is fitted on designated healthy
reference runs. A run is INCONCLUSIVE when its evidence is unusable: it was excluded by the
protocol rules, its requests are not covered by spans, or its operation has no baseline.
This module never reads ground truth: the caller chooses the reference runs.
"""
from __future__ import annotations

import math
import statistics
from typing import Any

METRIC = "server_latency_p50_ms"
MIN_SLOWDOWN = 0.20
PERCENTILE = 0.99
MIN_SPAN_COVERAGE = 0.95
MIN_REFERENCE_RUNS = 2


def percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    rank = (len(s) - 1) * q
    lo, hi = math.floor(rank), math.ceil(rank)
    return s[lo] + (s[hi] - s[lo]) * (rank - lo)


def baseline(values: list[float]) -> dict[str, Any] | None:
    """Baseline of one operation from its healthy reference run values."""
    values = [v for v in values if v is not None]
    if len(values) < MIN_REFERENCE_RUNS:
        return None
    median = statistics.median(values)
    p99 = percentile(values, PERCENTILE)
    return {"n": len(values), "median": median, "p99": p99, "threshold": max(p99, (1 + MIN_SLOWDOWN) * median)}


def fit(reference: list[tuple[str, float]]) -> dict[str, dict[str, Any]]:
    """Baselines per route from (route, metric value) pairs of reference runs."""
    by_route: dict[str, list[float]] = {}
    for route, value in reference:
        by_route.setdefault(route, []).append(value)
    return {route: b for route, values in by_route.items() if (b := baseline(values)) is not None}


def verdict(run: dict[str, Any], features: dict[str, Any], base: dict[str, Any] | None) -> dict[str, Any]:
    """Verdict for one run: normal / anomalous / inconclusive, with its evidence."""
    value = features.get(METRIC)
    evidence = {"metric": METRIC, "value": value, "threshold": base and base["threshold"],
                "baseline_median": base and base["median"], "baseline_runs": base and base["n"],
                "ratio_to_baseline": (value / base["median"]) if base and value is not None and base["median"] else None}
    if run.get("excluded_reasons"):
        return {"verdict": "inconclusive", "reason": "excluded by the protocol: " + "; ".join(run["excluded_reasons"]), **evidence}
    if (run.get("span_coverage") or 0) < MIN_SPAN_COVERAGE:
        return {"verdict": "inconclusive", "reason": f"span coverage {run.get('span_coverage')} < {MIN_SPAN_COVERAGE}", **evidence}
    if base is None:
        return {"verdict": "inconclusive", "reason": "no baseline for this operation", **evidence}
    if value is None:
        return {"verdict": "inconclusive", "reason": f"{METRIC} missing", **evidence}
    if value > base["threshold"]:
        return {"verdict": "anomalous", "reason": f"{METRIC} {value:.2f} ms > threshold {base['threshold']:.2f} ms (×{value / base['median']:.2f} of the healthy median)", **evidence}
    return {"verdict": "normal", "reason": f"{METRIC} {value:.2f} ms ≤ threshold {base['threshold']:.2f} ms", **evidence}
