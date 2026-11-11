"""Baseline-relative features: how a run differs from its operation's healthy baseline.

The baseline of an operation (route) is the median of each feature over its healthy
reference runs. A reference run is compared with a baseline built without it (leave-one-out),
as in detection. Magnitudes (ms, bytes, counts, rates) become log2 ratios to the baseline;
shares and ratios (0–1) become differences. Rules and ML models use the same transform.
"""
from __future__ import annotations

import math
import statistics
from typing import Any

from features.schema import FEATURES

EPS = 0.01
DIFFERENCE_FEATURES = {name for name in FEATURES if any(k in name for k in ("share", "ratio", "utilization", "error_rate"))}


def medians(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    out = {}
    for name in FEATURES:
        values = [r[name] for r in rows if r.get(name) is not None]
        out[name] = statistics.median(values) if values else None
    return out


def baselines(data: dict[str, dict[str, Any]], reference_ids: set[str]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Usable reference feature rows per route (excluded runs never define a baseline)."""
    by_route: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for eid, r in data.items():
        if eid in reference_ids and not r["run"].get("excluded_reasons"):
            by_route.setdefault(r["run"]["route"], []).append((eid, r["features"]))
    return by_route


def relative(features: dict[str, Any], base: dict[str, float | None]) -> dict[str, float]:
    out = {}
    for name in FEATURES:
        x, b = features.get(name), base.get(name)
        if name in DIFFERENCE_FEATURES:
            out[name] = (x - b) if x is not None and b is not None else 0.0
        elif x is None or b is None:
            out[name] = 0.0
        else:
            out[name] = math.log2((max(x, 0.0) + EPS) / (max(b, 0.0) + EPS))
    return out


def relative_table(data: dict[str, dict[str, Any]], reference: dict[str, dict[str, Any]], reference_ids: set[str],
                   same_campaign: bool) -> dict[str, dict[str, Any]]:
    """For every run: the baseline medians it is compared with, and its relative features."""
    pools = baselines(reference, reference_ids)
    full = {route: medians([f for _, f in rows]) for route, rows in pools.items()}
    out = {}
    for eid, r in data.items():
        route = r["run"]["route"]
        rows = pools.get(route, [])
        if same_campaign and any(oid == eid for oid, _ in rows):
            base = medians([f for oid, f in rows if oid != eid])
        else:
            base = full.get(route)
        if base is None:
            continue
        out[eid] = {"base": base, "relative": relative(r["features"], base)}
    return out
