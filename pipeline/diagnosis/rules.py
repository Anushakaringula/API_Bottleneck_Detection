"""Interpretable diagnosis rules (Phase 5 baseline method).

Each bottleneck class has one rule built from its mechanism. It compares a run's features
with its operation's healthy baseline medians (`base`). A rule fires with a strength (how far
past its threshold the main evidence is) and a list of evidence. Exclusions keep the classes
apart:
- a large payload also costs CPU, so the CPU rule needs an unchanged response size;
- a cache fault adds queries, so the DB rule needs an unchanged query count.

No rule firing gives INSUFFICIENT_EVIDENCE. Several rules firing: the strongest wins.
The thresholds were set from the fault mechanisms and checked on the pilot campaign only
(development data). The main and held-out campaigns are used for evaluation.
"""
from __future__ import annotations

from typing import Any

from . import INSUFFICIENT

# Thresholds (relative to the operation's healthy baseline).
EXTRA_QUERIES = 2.0         # N+1: at least this many more queries per request ...
EXTRA_REPEATS = 2.0         # ... and the same query repeated at least this many more times
DB_TIME_FACTOR = 2.0        # DB: database time at least doubled ...
DB_TIME_MIN_MS = 2.0        # ... by at least 2 ms ...
DB_SHARE_INCREASE = 0.15    # ... and a larger share of the request spent in the database
SAME_QUERIES = 0.5          # "unchanged" query count: at most this many more queries per request
PAYLOAD_FACTOR = 4.0        # PAYLOAD: response at least 4x larger
CACHE_HIT_DROP = 0.5        # CACHE: hit ratio at least 0.5 lower than a cache-using baseline
CPU_APP_FACTOR = 2.0        # CPU: time in application code at least doubled ...
CPU_APP_MIN_MS = 3.0        # ... by at least 3 ms ...
SAME_BYTES_FACTOR = 2.0     # ... with the response less than 2x larger


def _v(d: dict[str, Any], name: str, default: float = 0.0) -> float:
    value = d.get(name)
    return default if value is None else value


def evaluate(features: dict[str, Any], base: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every rule that fires: class -> {strength, evidence}."""
    fired = {}
    q, bq = _v(features, "db_queries_per_request_mean"), _v(base, "db_queries_per_request_mean")
    rep, brep = _v(features, "repeated_query_max_median"), _v(base, "repeated_query_max_median")
    dbt, bdbt = _v(features, "db_time_p50_ms"), _v(base, "db_time_p50_ms")
    share, bshare = _v(features, "db_time_share_p50"), _v(base, "db_time_share_p50")
    size, bsize = _v(features, "response_bytes_median"), _v(base, "response_bytes_median")
    app, bapp = _v(features, "app_time_p50_ms"), _v(base, "app_time_p50_ms")
    hit, bhit = features.get("cache_hit_ratio"), base.get("cache_hit_ratio")

    if q - bq >= EXTRA_QUERIES and rep - brep >= EXTRA_REPEATS:
        fired["N_PLUS_ONE"] = {"strength": (q + 1) / (bq + 1),
                               "evidence": [f"{q:.1f} queries per request vs {bq:.1f} healthy",
                                            f"one query repeated {rep:.0f}x per request vs {brep:.0f}x"]}
    if dbt >= DB_TIME_FACTOR * bdbt and dbt - bdbt >= DB_TIME_MIN_MS and q - bq <= SAME_QUERIES and share - bshare >= DB_SHARE_INCREASE:
        fired["DB_BOTTLENECK"] = {"strength": (dbt + 0.1) / (bdbt + 0.1),
                                  "evidence": [f"database time {dbt:.1f} ms vs {bdbt:.1f} ms healthy with the same number of queries ({q:.1f} vs {bq:.1f})",
                                               f"database share of the request {share:.0%} vs {bshare:.0%}"]}
    if bsize > 0 and size >= PAYLOAD_FACTOR * bsize:
        fired["LARGE_PAYLOAD"] = {"strength": size / bsize,
                                  "evidence": [f"response {size / 1024:.0f} kB vs {bsize / 1024:.1f} kB healthy ({size / bsize:.0f}x)"]}
    if bhit is not None and bhit >= CACHE_HIT_DROP and (hit if hit is not None else 0.0) <= bhit - CACHE_HIT_DROP:
        current = hit if hit is not None else 0.0
        fired["CACHE_OPPORTUNITY"] = {"strength": 1 + (bhit - current) * 10,
                                      "evidence": [f"cache hit ratio {current:.0%} vs {bhit:.0%} healthy",
                                                   f"{q:.2f} queries per request vs {bq:.2f} (repeated work reaches the database)"]}
    if app >= CPU_APP_FACTOR * bapp and app - bapp >= CPU_APP_MIN_MS and (bsize == 0 or size < SAME_BYTES_FACTOR * bsize) and q - bq <= SAME_QUERIES:
        fired["CPU_BOTTLENECK"] = {"strength": (app + 0.1) / (bapp + 0.1),
                                   "evidence": [f"time in application code {app:.1f} ms vs {bapp:.1f} ms healthy",
                                                f"same database work ({q:.1f} queries) and response size ({size / 1024:.1f} kB)"]
                                               + ([f"event-loop delay p99 {features['event_loop_delay_p99_ms']:.0f} ms vs {base['event_loop_delay_p99_ms']:.0f} ms"]
                                                  if features.get("event_loop_delay_p99_ms") is not None and base.get("event_loop_delay_p99_ms") is not None else [])}
    return fired


def diagnose(features: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    fired = evaluate(features, base)
    if not fired:
        return {"diagnosis": INSUFFICIENT, "confidence": "none", "fired": [], "evidence": ["no bottleneck rule matched the evidence"]}
    best = max(fired, key=lambda name: fired[name]["strength"])
    return {"diagnosis": best, "confidence": "high" if len(fired) == 1 else "medium",
            "fired": sorted(fired), "evidence": fired[best]["evidence"]}
