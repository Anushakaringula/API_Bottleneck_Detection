#!/usr/bin/env python3
"""Closed loop on one operation (demo; Phases 4–7 end to end):
inject → detect → diagnose → recommend → apply fix → re-run → verify.

  python -m campaign.loop --operation users.list --variant nplus1:20 --baseline-campaign <main-id>

The injected variant is known only to this tool (it starts the API with it). Detection,
diagnosis and the recommendation see telemetry only. The baseline is the healthy runs of the
baseline campaign. The fix is applied by restarting the operation with the recommended
reviewed variant, keeping the same parameters (severity). Report: data/results/loops/<loop-id>/.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from detection.rule import METRIC, fit, verdict
from diagnosis.relative import baselines, medians
from diagnosis.rules import diagnose
from evaluation import labels as label_access
from evaluation.detection import load_features
from experiment_runner.runner import DATA_DIR, execute_experiment, resolve_configuration, utc_now
from features.extract import extract
from remediation.catalogue import recommend
from verification.stats import equivalent, welch_log

from .plan import parse_variant, runner_config, stable_seed


def run_phase(name: str, operation: str, variant: str, repeats: int, config: dict[str, Any], loop_dir: Path, lab,
              execute: Callable[..., dict[str, Any]], spans_file: Path, log) -> list[str]:
    ids = []
    for k in range(1, repeats + 1):
        run = {"operation": operation, "variant": variant, "condition_id": f"{operation}/{name}", "repeat": k,
               "workload_seed": stable_seed(config["random_seed"], "workload", operation), "run_index": len(ids) + 1}
        lab.reset()
        lab.api_up({operation: variant}, f"{loop_dir.name}-{name}-{k}")
        try:
            summary = execute(resolve_configuration(runner_config(config, run, loop_dir.name)), output_root=loop_dir / "experiments",
                              span_file=spans_file, repeat_group_id=f"{loop_dir.name}:{name}", repeat_index=k)
        finally:
            lab.api_down()
        ids.append(summary["experiment_id"])
        with (loop_dir / "progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"experiment_id": summary["experiment_id"], "status": "completed", "phase": name}) + "\n")
        log(f"  {name} run {k}/{repeats}: client p50 {summary['measured']['median_ms']:.1f} ms")
    return ids


def closed_loop(operation: str, variant: str, baseline_campaign: str, repeats: int, lab, *, data_dir: Path = DATA_DIR,
                execute: Callable[..., dict[str, Any]] = execute_experiment, log: Callable[[str], None] = print) -> dict[str, Any]:
    manifest = json.loads((data_dir / "raw" / "campaigns" / baseline_campaign / "manifest.json").read_text(encoding="utf-8"))
    config = manifest["config"]
    loop_id = f"loop-{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    loop_dir = data_dir / "raw" / "loops" / loop_id
    loop_dir.mkdir(parents=True)
    spans_dir = data_dir / "raw" / "spans" / "runs"
    runtime_file = data_dir / "raw" / "runtime" / "runtime-metrics.jsonl"
    features_dir = data_dir / "features"

    reference = load_features(baseline_campaign, features_dir)
    reference_ids = label_access.reference_runs(label_access.load(baseline_campaign, data_dir / "labels"))
    pools = baselines(reference, reference_ids)
    detection_baselines = fit([(r["run"]["route"], r["features"][METRIC]) for eid, r in reference.items()
                               if eid in reference_ids and not r["run"].get("excluded_reasons") and r["features"].get(METRIC) is not None])

    def measure(name: str, v: str) -> dict[str, dict[str, Any]]:
        ids = run_phase(name, operation, v, repeats, config, loop_dir, lab, execute, data_dir / "raw" / "spans" / "otel-spans.jsonl", log)
        extract(loop_dir, spans_dir, runtime_file, features_dir / "loops")
        data = load_features(loop_id, features_dir / "loops")
        return {eid: data[eid] for eid in ids}

    log(f"[1/5] inject: {operation} runs with a variant the system is not told about ({repeats} runs)")
    before = measure("before", variant)
    route = next(iter(before.values()))["run"]["route"]
    base_rows = [f for _, f in pools.get(route, [])]
    base = medians(base_rows)
    verdicts = [verdict(r["run"], r["features"], detection_baselines.get(route)) for r in before.values()]
    anomalous = sum(v["verdict"] == "anomalous" for v in verdicts) > len(verdicts) / 2
    log(f"[2/5] detect: {'ANOMALOUS' if anomalous else 'normal'} ({sum(v['verdict'] == 'anomalous' for v in verdicts)}/{len(verdicts)} runs); {verdicts[0]['reason']}")
    report: dict[str, Any] = {"loop_id": loop_id, "created_at": utc_now(), "operation": operation, "route": route,
                              "baseline_campaign": baseline_campaign, "repeats": repeats, "injected_variant": variant,
                              "detection": {"anomalous": anomalous, "verdicts": verdicts}}
    if not anomalous:
        report["outcome"] = "no slowdown detected: nothing to diagnose"
        return _finish(report, loop_dir, data_dir, log)

    diagnoses = [diagnose(r["features"], base) for r in before.values()]
    top = Counter(d["diagnosis"] for d in diagnoses).most_common(1)[0][0]
    chosen = next(d for d in diagnoses if d["diagnosis"] == top)
    rec = recommend(chosen)
    report["diagnosis"] = {"class": top, "confidence": chosen["confidence"], "evidence": chosen["evidence"], "per_run": [d["diagnosis"] for d in diagnoses]}
    report["recommendation"] = rec
    log(f"[3/5] diagnose: {top} ({chosen['confidence']}); " + "; ".join(chosen["evidence"]))
    if rec["variant"] is None:
        report["outcome"] = "insufficient evidence: no fix recommended"
        return _finish(report, loop_dir, data_dir, log)
    _, severity = parse_variant(variant)
    fix_variant = rec["variant"] + (f":{severity:g}" if severity is not None else "")
    available = lab.registered_variants().get(operation, [])
    if rec["variant"] not in available:
        report["outcome"] = f"recommended fix '{rec['variant']}' is not available for {operation}"
        return _finish(report, loop_dir, data_dir, log)
    log(f"[4/5] recommend + apply: {rec['recommendation']} -> restart {operation} with variant {fix_variant}")
    after = measure("after", fix_variant)
    b_values = [r["features"][METRIC] for r in before.values()]
    a_values = [r["features"][METRIC] for r in after.values()]
    improvement = welch_log(b_values, a_values, confidence=0.95)
    back = equivalent([f[METRIC] for f in base_rows], a_values)
    improved = improvement["p_one_sided"] is not None and improvement["p_one_sided"] < 0.05
    report["verification"] = {"fix_variant": fix_variant, "before_ms": b_values, "after_ms": a_values, "healthy_median_ms": base.get(METRIC),
                              "improvement": improvement, "back_to_healthy": back, "improved": improved,
                              "after_verdicts": [verdict(r["run"], r["features"], detection_baselines.get(route))["verdict"] for r in after.values()]}
    report["outcome"] = ("improvement verified, back to healthy" if improved and back["equivalent"] else
                         "improvement verified" if improved else "improvement not shown")
    log(f"[5/5] verify: server p50 {sorted(b_values)[len(b_values) // 2]:.2f} -> {sorted(a_values)[len(a_values) // 2]:.2f} ms "
        f"(×{improvement['ratio']:.3f}, p {improvement['p_one_sided']:.4f}); healthy {base.get(METRIC):.2f} ms; {report['outcome'].upper()}")
    return _finish(report, loop_dir, data_dir, log)


def _finish(report: dict[str, Any], loop_dir: Path, data_dir: Path, log) -> dict[str, Any]:
    out = data_dir / "results" / "loops" / report["loop_id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    log(f"outcome: {report['outcome']}; report: {out / 'report.json'}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operation", required=True)
    parser.add_argument("--variant", required=True, help="variant to inject, e.g. nplus1:20 (the loop must find it)")
    parser.add_argument("--baseline-campaign", required=True, help="campaign whose healthy runs define the baselines (features extracted)")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args(argv)
    from .lab import Lab
    lab = Lab()
    try:
        closed_loop(args.operation, args.variant, args.baseline_campaign, args.repeats, lab, log=lambda m: print(m, flush=True))
    finally:
        lab.reset()
    return 0


if __name__ == "__main__":
    sys.exit(main())
