#!/usr/bin/env python3
"""Summarize a campaign (Phase 3.5D): noise, effect sizes, telemetry completeness, run time.

  python -m campaign.summarize <campaign-id>

Writes data/results/campaigns/<campaign-id>/summary.json and report.md. This is evaluation
tooling: it reads the ground-truth labels, so it is never part of detection or diagnosis.
Run-level latencies are client-side (from the runner); a run's p50 is the median latency of
its measured requests, and conditions are compared on the distribution of run p50s.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

from experiment_runner.runner import DATA_DIR, utc_now

from .quality import exclusion_reasons, exclusion_rules


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def server_p50(run_dir: Path, spans_dir: Path) -> float | None:
    """Median server-side latency (experiment span) of the run's measured requests, matched
    by trace ID; None if the run's span file is not available."""
    span_file = spans_dir / f"{run_dir.name}.jsonl"
    requests_file = run_dir / "requests.jsonl"
    if not span_file.exists() or not requests_file.exists():
        return None
    measured = {r["trace_id"] for r in _jsonl(requests_file) if r.get("phase") == "measured" and r.get("trace_id")}
    values = [s["attributes"]["experiment.latency_ms"] for s in _jsonl(span_file)
              if s.get("span_name") == "experiment.http.request" and s.get("trace_id") in measured]
    return statistics.median(values) if values else None


def run_rows(campaign_dir: Path, labels: list[dict[str, Any]], progress: list[dict[str, Any]],
             spans_dir: Path | None = None) -> list[dict[str, Any]]:
    spans_dir = spans_dir or DATA_DIR / "raw" / "spans" / "runs"
    wall = {p["experiment_id"]: p.get("wall_seconds") for p in progress if p.get("status") == "completed"}
    image = {p["experiment_id"]: p.get("image_id") for p in progress if p.get("status") == "completed"}
    rows = []
    for label in labels:
        summary_file = campaign_dir / "experiments" / label["experiment_id"] / "summary.json"
        if not summary_file.exists():
            continue
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        measured = summary["measured"]
        request_level = summary["telemetry_correlation"].get("request_level", {})
        rows.append({
            **{k: label[k] for k in ("experiment_id", "run_index", "condition_id", "operation", "variant", "fault", "fault_class", "severity", "is_fixed", "repeat")},
            "requests": measured["total_requests"],
            "failed": measured["failed_requests"],
            "p50_ms": measured["median_ms"],
            "server_p50_ms": server_p50(summary_file.parent, spans_dir),
            "p95_ms": measured["p95_ms"],
            "p99_ms": measured["p99_ms"],
            "p50_from_intended_ms": (measured.get("latency_from_intended_ms") or {}).get("median_ms"),
            "send_lag_p99_ms": (measured.get("send_lag_ms") or {}).get("p99_ms"),
            "achieved_rate": measured.get("achieved_send_rate_per_second"),
            "max_in_flight": measured.get("maximum_observed_in_flight"),
            "correlation_complete": summary["telemetry_correlation"]["correlation_status"] == "complete",
            "requests_matched": request_level.get("requests_matched_to_one_span"),
            "requests_attempted": request_level.get("client_requests"),
            "wall_seconds": wall.get(label["experiment_id"]),
            "image_id": image.get(label["experiment_id"]),
            "rerun_of": label.get("rerun_of"),
        })
    return rows


def describe(values: list[float]) -> dict[str, Any]:
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0}
    mean = statistics.fmean(values)
    return {
        "n": len(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "cv": statistics.stdev(values) / mean if len(values) > 1 and mean > 0 else None,
    }


def repeats_for(cv: float | None, relative_effect: float = 0.2) -> int | None:
    """Runs per condition to detect a relative slowdown with a two-sample comparison
    (alpha 0.05 two-sided, power 0.8), from the healthy run-to-run CV: n = 2 (z_a + z_b)^2 CV^2 / d^2."""
    if cv is None:
        return None
    return max(2, math.ceil(2 * (1.96 + 0.84) ** 2 * cv ** 2 / relative_effect ** 2))


def summarize(campaign_dir: Path, labels_file: Path, spans_dir: Path | None = None) -> dict[str, Any]:
    manifest = json.loads((campaign_dir / "manifest.json").read_text(encoding="utf-8"))
    labels = _jsonl(labels_file)
    progress = _jsonl(campaign_dir / "progress.jsonl")
    all_rows = run_rows(campaign_dir, labels, progress, spans_dir)
    # Protocol exclusion rules (quality.py), applied to every run; with apply_exclusions the
    # excluded runs are left out of all statistics below (main and held-out campaigns).
    config = manifest["config"]
    rules = exclusion_rules(config)
    for row in all_rows:
        row["excluded_reasons"] = exclusion_reasons({
            "correlation_status": "complete" if row["correlation_complete"] else "partial",
            "measured_requests": row["requests"], "failed_requests": row["failed"],
            "send_lag_p99_ms": row["send_lag_p99_ms"], "image_id": row["image_id"],
        }, manifest.get("environment", {}), rules)
    apply = bool(config.get("apply_exclusions"))
    rows = [r for r in all_rows if not (apply and r["excluded_reasons"])]
    conditions: dict[str, dict[str, Any]] = {}
    for row in rows:
        conditions.setdefault(row["condition_id"], {"operation": row["operation"], "variant": row["variant"], "fault_class": row["fault_class"], "is_fixed": row["is_fixed"], "runs": []})["runs"].append(row)
    healthy_p50 = {c["operation"]: [r["p50_ms"] for r in c["runs"]] for c in conditions.values() if c["variant"] == "healthy"}
    healthy_server = {c["operation"]: [r["server_p50_ms"] for r in c["runs"] if r["server_p50_ms"] is not None]
                      for c in conditions.values() if c["variant"] == "healthy"}
    out_conditions = []
    for condition_id in sorted(conditions):
        c = conditions[condition_id]
        p50 = describe([r["p50_ms"] for r in c["runs"]])
        entry = {
            "condition_id": condition_id, "operation": c["operation"], "variant": c["variant"], "fault_class": c["fault_class"],
            "runs": len(c["runs"]), "run_p50_ms": p50, "run_p95_ms": describe([r["p95_ms"] for r in c["runs"]]),
            "server_run_p50_ms": describe([r["server_p50_ms"] for r in c["runs"]]),
            "failed_requests": sum(r["failed"] for r in c["runs"]),
            "requests": sum(r["requests"] for r in c["runs"]),
        }
        base = healthy_p50.get(c["operation"])
        if c["variant"] != "healthy" and base and p50["n"]:
            entry["ratio_to_healthy"] = p50["median"] / statistics.median(base)
            # Fully separated: every run of the condition is slower than every healthy run.
            entry["separated_from_healthy"] = p50["min"] > max(base)
        server = entry["server_run_p50_ms"]
        server_base = healthy_server.get(c["operation"])
        if c["variant"] != "healthy" and server_base and server["n"]:
            entry["server_ratio_to_healthy"] = server["median"] / statistics.median(server_base)
            entry["server_separated_from_healthy"] = server["min"] > max(server_base)
        out_conditions.append(entry)
    healthy_noise = []
    for operation in sorted(healthy_p50):
        d = describe(healthy_p50[operation])
        ds = describe(healthy_server.get(operation, []))
        healthy_noise.append({"operation": operation, "runs": d["n"], "median_p50_ms": d.get("median"), "min_p50_ms": d.get("min"),
                              "max_p50_ms": d.get("max"), "cv": d.get("cv"), "repeats_for_20pct_slowdown": repeats_for(d.get("cv")),
                              "server_median_p50_ms": ds.get("median"), "server_cv": ds.get("cv"),
                              "server_repeats_for_20pct_slowdown": repeats_for(ds.get("cv"))})
    attempted = sum(r["requests_attempted"] or 0 for r in rows)
    matched = sum(r["requests_matched"] or 0 for r in rows)
    wall = [r["wall_seconds"] for r in rows if r["wall_seconds"] is not None]
    report = {
        "campaign_id": manifest["campaign_id"], "created_at": utc_now(), "planned_runs": len(manifest["runs"]),
        "completed_runs": len(all_rows), "analysed_runs": len(rows), "failed_runs": sum(1 for p in progress if p["status"] == "failed"),
        "exclusions": {
            "applied": apply, "rules": rules,
            "excluded_runs": [{"experiment_id": r["experiment_id"], "run_index": r["run_index"], "condition_id": r["condition_id"],
                               "reasons": r["excluded_reasons"], "rerun_of": r["rerun_of"]} for r in all_rows if r["excluded_reasons"]],
        },
        "telemetry": {
            "runs_with_complete_correlation": sum(1 for r in rows if r["correlation_complete"]),
            "requests_attempted_incl_warmup": attempted, "requests_matched_to_one_span": matched,
            "request_match_rate": matched / attempted if attempted else None,
            "failed_requests": sum(r["failed"] for r in rows),
            "max_send_lag_p99_ms": max((r["send_lag_p99_ms"] or 0) for r in rows) if rows else None,
        },
        "run_time": {"mean_wall_seconds": statistics.fmean(wall) if wall else None, "total_wall_minutes": sum(wall) / 60 if wall else None},
        "healthy_noise": healthy_noise, "conditions": out_conditions, "runs": all_rows,
    }
    return report


def markdown(report: dict[str, Any]) -> str:
    f = lambda v, d=2: "-" if v is None else (f"{v:.{d}f}" if isinstance(v, float) else str(v))
    t = report["telemetry"]
    lines = [
        f"# Campaign summary: {report['campaign_id']}", "",
        f"Runs: {report['completed_runs']} completed of {report['planned_runs']} planned, {report['failed_runs']} failed. "
        f"Total run time {f(report['run_time']['total_wall_minutes'], 1)} min (mean {f(report['run_time']['mean_wall_seconds'], 0)} s per run).", "",
        "## Exclusions (protocol §7)", "",
        (f"Applied: {len(report['exclusions']['excluded_runs'])} runs excluded; statistics below use the {report['analysed_runs']} remaining runs."
         if report["exclusions"]["applied"] else
         f"Not applied to this campaign (informational): {len(report['exclusions']['excluded_runs'])} runs would be excluded under the protocol rules."), "",
        *([f"- run {e['run_index']} ({e['condition_id']}{', re-run of ' + str(e['rerun_of']) if e['rerun_of'] else ''}): {'; '.join(e['reasons'])}"
           for e in report["exclusions"]["excluded_runs"][:40]] or ["- none"]),
        "",
        "## Telemetry completeness", "",
        f"- Runs with complete request-to-span correlation: {t['runs_with_complete_correlation']} / {report['completed_runs']}",
        f"- Requests matched to exactly one span: {t['requests_matched_to_one_span']} / {t['requests_attempted_incl_warmup']} ({f(100 * t['request_match_rate'] if t['request_match_rate'] is not None else None, 2)} %)",
        f"- Failed requests: {t['failed_requests']}; worst client send-lag p99: {f(t['max_send_lag_p99_ms'])} ms", "",
        "## Healthy run-to-run noise (per operation)", "",
        "Run p50 = median latency of one run's measured requests. Client = seen by the runner (includes the client and network floor);",
        "server = the API's experiment span. CV = coefficient of variation of run p50s across healthy repeats.",
        "Repeats = runs per condition to detect a 20 % slowdown (alpha 0.05, power 0.8) from that CV; a first estimate for the protocol.", "",
        "| Operation | Healthy runs | Client: median run p50 ms | Client range ms | Client CV | Client repeats | Server: median run p50 ms | Server CV | Server repeats |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for n in report["healthy_noise"]:
        lines.append(f"| {n['operation']} | {n['runs']} | {f(n['median_p50_ms'])} | {f(n['min_p50_ms'])}–{f(n['max_p50_ms'])} | {f(n['cv'], 3)} | "
                     f"{f(n['repeats_for_20pct_slowdown'])} | {f(n['server_median_p50_ms'])} | {f(n['server_cv'], 3)} | {f(n['server_repeats_for_20pct_slowdown'])} |")
    lines += ["", "## Conditions", "",
              "Ratio = median run p50 of the condition / median healthy run p50 of the same operation. Separated = every run slower than every healthy run.", "",
              "| Condition | Class | Runs | Client: median run p50 ms | Client range ms | Client ratio | Client separated | Server: median run p50 ms | Server ratio | Server separated | Failed requests |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in report["conditions"]:
        p = c["run_p50_ms"]
        def sep(key):
            return "yes" if c.get(key) else ("no" if key in c else "-")
        lines.append(f"| {c['condition_id']} | {c['fault_class']} | {c['runs']} | {f(p.get('median'))} | {f(p.get('min'))}–{f(p.get('max'))} | "
                     f"{f(c.get('ratio_to_healthy'))} | {sep('separated_from_healthy')} | {f(c['server_run_p50_ms'].get('median'))} | "
                     f"{f(c.get('server_ratio_to_healthy'))} | {sep('server_separated_from_healthy')} | {c['failed_requests']} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("campaign_id")
    parser.add_argument("--campaigns-dir", type=Path, default=DATA_DIR / "raw" / "campaigns")
    parser.add_argument("--labels-dir", type=Path, default=DATA_DIR / "labels")
    parser.add_argument("--results-dir", type=Path, default=DATA_DIR / "results" / "campaigns")
    args = parser.parse_args(argv)
    report = summarize(args.campaigns_dir / args.campaign_id, args.labels_dir / f"{args.campaign_id}.jsonl")
    out = args.results_dir / args.campaign_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "report.md").write_text(markdown(report), encoding="utf-8")
    print(f"campaign report: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
