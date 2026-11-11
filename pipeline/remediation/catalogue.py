"""Remediation catalogue and recommender (Phase 6).

Each bottleneck class maps to one fix: what a developer changes, the target-API variant that
implements it (applied by switching the operation to that reviewed variant; the system never
edits code), and the metric that must improve if the fix works (checked in verification).
"""
from __future__ import annotations

from typing import Any

CATALOGUE: dict[str, dict[str, Any]] = {
    "DB_BOTTLENECK": {
        "fix": "add the missing index",
        "action": "recreate the index the query filters or sorts on, then refresh planner statistics (ANALYZE)",
        "variant": "noindex-fixed",
        "expected_change": {"db_time_p50_ms": "down", "db_time_share_p50": "down"},
    },
    "N_PLUS_ONE": {
        "fix": "replace the per-row queries with one JOIN / batched query",
        "action": "load the related rows in a single query (JOIN or WHERE id = ANY($1)) instead of one query per row",
        "variant": "nplus1-fixed",
        "expected_change": {"db_queries_per_request_mean": "down", "repeated_query_max_median": "down"},
    },
    "LARGE_PAYLOAD": {
        "fix": "paginate and select only the needed fields",
        "action": "return one page (limit/offset) with summary columns instead of every row with all columns",
        "variant": "payload-fixed",
        "expected_change": {"response_bytes_median": "down"},
    },
    "CACHE_OPPORTUNITY": {
        "fix": "use a stable cache key with a TTL",
        "action": "cache the result under a key that repeats for identical requests (TTL 60 s), so repeated work is served from Redis",
        "variant": "cache-fixed",
        "expected_change": {"cache_hit_ratio": "up", "db_queries_per_request_mean": "down"},
    },
    "CPU_BOTTLENECK": {
        "fix": "remove the expensive computation from the request path",
        "action": "replace the per-request key derivation with an ordinary hash (or move heavy work off the event loop)",
        "variant": "cpu-fixed",
        "expected_change": {"app_time_p50_ms": "down", "event_loop_delay_p99_ms": "down"},
    },
}


def recommend(diagnosis: dict[str, Any]) -> dict[str, Any]:
    """Recommendation for one diagnosis (output of diagnosis.rules.diagnose or an ML model)."""
    entry = CATALOGUE.get(diagnosis["diagnosis"])
    if entry is None:
        return {"recommendation": "collect more evidence", "variant": None,
                "action": "no fix is recommended: the evidence does not identify a bottleneck (re-run with full tracing, L4)",
                "based_on": diagnosis.get("evidence", [])}
    return {"recommendation": entry["fix"], "variant": entry["variant"], "action": entry["action"],
            "expected_change": entry["expected_change"], "confidence": diagnosis.get("confidence"),
            "based_on": diagnosis.get("evidence", [])}
