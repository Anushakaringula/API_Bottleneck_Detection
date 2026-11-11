"""Run exclusion rules of the experiment protocol (experiments/protocol.md §7).

A run is excluded if its telemetry is incomplete, too many requests failed, the client did
not keep the timetable, or the code/image differs from the campaign's. The rules are applied
the same way by the orchestrator (to decide re-runs) and by the summary (to report them).
"""
from __future__ import annotations

from typing import Any

DEFAULT_EXCLUSION = {
    "require_complete_correlation": True,
    "max_failed_fraction": 0.01,
    "max_send_lag_p99_ms": 10.0,
    "require_ac_power": False,
}


def exclusion_rules(config: dict[str, Any]) -> dict[str, Any]:
    return {**DEFAULT_EXCLUSION, **(config.get("exclusion") or {})}


def exclusion_reasons(record: dict[str, Any], environment: dict[str, Any], rules: dict[str, Any]) -> list[str]:
    """Reasons to exclude one completed run (empty list = the run is kept).

    `record` is the run's line in progress.jsonl; `environment` is the campaign manifest's.
    """
    reasons = []
    if rules["require_complete_correlation"] and record.get("correlation_status") != "complete":
        reasons.append("telemetry: request-to-span correlation incomplete")
    requests = record.get("measured_requests") or 0
    failed = record.get("failed_requests") or 0
    if requests == 0 or failed / requests > rules["max_failed_fraction"]:
        reasons.append(f"failed requests {failed}/{requests} > {rules['max_failed_fraction']:.0%}")
    lag = record.get("send_lag_p99_ms")
    if lag is None or lag > rules["max_send_lag_p99_ms"]:
        reasons.append(f"client send-lag p99 {lag if lag is None else round(lag, 1)} ms > {rules['max_send_lag_p99_ms']} ms")
    if rules.get("require_ac_power") and record.get("ac_power") is False:
        reasons.append("power: on battery during the run (CPU boost limited)")
    if environment.get("git_dirty") is True:
        reasons.append("code: uncommitted changes (git_dirty)")
    # Only content identities (lab.image_id) are comparable. Campaigns recorded before
    # protocol amendment 4 hold Docker image IDs, which change on every rebuild of identical
    # code; for those the rule cannot be applied from the IDs (see protocol.md, amendments).
    run_image, campaign_image = record.get("image_id"), environment.get("target_api_image_id")
    if all(isinstance(i, str) and i.startswith("content:") for i in (run_image, campaign_image)) and run_image != campaign_image:
        reasons.append("image: target-API image differs from the campaign's")
    return reasons
