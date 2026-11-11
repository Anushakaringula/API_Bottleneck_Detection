#!/usr/bin/env python3
"""Detection evaluation (Phase 4, RQ1): run the detector of record and score it.

  python -m evaluation.detection --campaign <main-id>                         # in-campaign, leave-one-out
  python -m evaluation.detection --campaign <heldout-id> --baseline-campaign <main-id>   # final

Baselines come from the healthy runs of the baseline campaign. When a healthy run is scored
against a baseline from its own campaign, it is left out of that baseline (leave-one-out), so
no run is judged by a baseline it helped to build. Excluded runs are inconclusive by the
protocol and are not counted as detections or false alarms.

Outputs, data/results/detection/<campaign-id>/:
  verdicts.jsonl   per run: verdict, evidence (no ground truth)
  summary.json, report.md   false-alarm rate, detection rate per fault and severity, fixed variants
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from detection.rule import METRIC, baseline, fit, verdict

from . import labels as label_access
from .labels import DATA_DIR


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95 % Wilson score interval of a proportion."""
    if n == 0:
        return None
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_features(campaign_id: str, features_dir: Path) -> dict[str, dict[str, Any]]:
    features = {r["experiment_id"]: r for r in _jsonl(features_dir / campaign_id / "features.jsonl")}
    runs = {r["experiment_id"]: r for r in _jsonl(features_dir / campaign_id / "runs.jsonl")}
    return {eid: {"features": features[eid], "run": runs[eid]} for eid in features}


def detect(evaluated: dict[str, dict[str, Any]], reference_pool: dict[str, dict[str, Any]], reference_ids: set[str],
           same_campaign: bool) -> dict[str, dict[str, Any]]:
    """Verdicts for every evaluated run. reference_pool/ids: runs that define baselines."""
    usable = {eid: r for eid, r in reference_pool.items()
              if eid in reference_ids and not r["run"].get("excluded_reasons") and r["features"].get(METRIC) is not None}
    baselines = fit([(r["run"]["route"], r["features"][METRIC]) for r in usable.values()])
    verdicts = {}
    for eid, r in evaluated.items():
        route = r["run"]["route"]
        base = baselines.get(route)
        loo = same_campaign and eid in usable
        if loo:  # leave this run out of its own baseline
            base = baseline([o["features"][METRIC] for oid, o in usable.items() if oid != eid and o["run"]["route"] == route])
        verdicts[eid] = {"experiment_id": eid, "route": route, "leave_one_out": loo, **verdict(r["run"], r["features"], base)}
    return verdicts


def score(verdicts: dict[str, dict[str, Any]], labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    def rate(items: list[dict[str, Any]]) -> dict[str, Any]:
        decided = [v for v in items if v["verdict"] != "inconclusive"]
        flagged = sum(1 for v in decided if v["verdict"] == "anomalous")
        ci = wilson(flagged, len(decided))
        return {"runs": len(items), "decided": len(decided), "inconclusive": len(items) - len(decided),
                "anomalous": flagged, "rate": flagged / len(decided) if decided else None,
                "ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None}
    groups: dict[str, list] = {"healthy": [], "fault": [], "fixed": []}
    by_class: dict[str, list] = {}
    by_condition: dict[str, list] = {}
    for eid, v in verdicts.items():
        label = labels.get(eid)
        if label is None:
            continue
        kind = "healthy" if label["variant"] == "healthy" else ("fixed" if label["is_fixed"] else "fault")
        groups[kind].append(v)
        if kind == "fault":
            by_class.setdefault(label["fault_class"], []).append(v)
            by_condition.setdefault(label["condition_id"], []).append(v)
    healthy = rate(groups["healthy"])
    fault = rate(groups["fault"])
    tp, fp = fault["anomalous"], healthy["anomalous"]
    return {
        "false_alarms_on_healthy": healthy,
        "detection_on_faults": fault,
        "flagged_fixed_variants": rate(groups["fixed"]),
        "precision": tp / (tp + fp) if tp + fp else None,
        "by_fault_class": {k: rate(v) for k, v in sorted(by_class.items())},
        "by_condition": {k: {**rate(v), "median_ratio_to_baseline": _median([x["ratio_to_baseline"] for x in v])} for k, v in sorted(by_condition.items())},
        "protocol_false_alarm_limit": 0.05,
        "false_alarm_rate_within_limit": healthy["rate"] is not None and healthy["rate"] <= 0.05,
    }


def _median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    m = len(values) // 2
    return values[m] if len(values) % 2 else (values[m - 1] + values[m]) / 2


def markdown(meta: dict[str, Any], s: dict[str, Any]) -> str:
    pct = lambda r: "-" if r is None else f"{100 * r:.1f} %"
    ci = lambda c: "-" if not c else f"{100 * c[0]:.1f}–{100 * c[1]:.1f} %"
    h, f, x = s["false_alarms_on_healthy"], s["detection_on_faults"], s["flagged_fixed_variants"]
    lines = [
        f"# Detection: {meta['campaign_id']}", "",
        f"Detector of record (protocol v1): `{METRIC}` > max(P99 of healthy run p50s, 1.2 × healthy median), per operation.",
        f"Baselines from: `{meta['baseline_campaign_id']}` ({'same campaign, healthy runs scored leave-one-out' if meta['same_campaign'] else 'separate campaign'}).", "",
        "| Group | Runs | Decided | Inconclusive | Flagged anomalous | Rate | 95 % CI |", "|---|---|---|---|---|---|---|",
        f"| Healthy (false alarms) | {h['runs']} | {h['decided']} | {h['inconclusive']} | {h['anomalous']} | {pct(h['rate'])} | {ci(h['ci95'])} |",
        f"| Faults (detection) | {f['runs']} | {f['decided']} | {f['inconclusive']} | {f['anomalous']} | {pct(f['rate'])} | {ci(f['ci95'])} |",
        f"| Fixed variants (should be normal) | {x['runs']} | {x['decided']} | {x['inconclusive']} | {x['anomalous']} | {pct(x['rate'])} | {ci(x['ci95'])} |", "",
        f"Precision (flagged runs that are faults): {pct(s['precision'])}. False-alarm rate within the protocol limit (≤ 5 %): **{'yes' if s['false_alarm_rate_within_limit'] else 'no'}**.", "",
        "## Detection rate by fault class", "", "| Class | Runs | Detected | Rate | 95 % CI |", "|---|---|---|---|---|",
        *[f"| {k} | {v['decided']} | {v['anomalous']} | {pct(v['rate'])} | {ci(v['ci95'])} |" for k, v in s["by_fault_class"].items()], "",
        "## Detection rate by condition (fault, severity)", "", "| Condition | Runs | Detected | Rate | Median ratio to baseline |", "|---|---|---|---|---|",
        *[f"| {k} | {v['decided']} | {v['anomalous']} | {pct(v['rate'])} | {'-' if v['median_ratio_to_baseline'] is None else f'{v['median_ratio_to_baseline']:.2f}'} |" for k, v in s["by_condition"].items()], "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--campaign", required=True, help="campaign to evaluate")
    parser.add_argument("--baseline-campaign", help="campaign whose healthy runs define the baselines (default: the evaluated one)")
    parser.add_argument("--features-dir", type=Path, default=DATA_DIR / "features")
    parser.add_argument("--labels-dir", type=Path, default=DATA_DIR / "labels")
    parser.add_argument("--results-dir", type=Path, default=DATA_DIR / "results" / "detection")
    args = parser.parse_args(argv)
    base_id = args.baseline_campaign or args.campaign
    same = base_id == args.campaign
    evaluated = load_features(args.campaign, args.features_dir)
    pool = evaluated if same else load_features(base_id, args.features_dir)
    reference_ids = label_access.reference_runs(label_access.load(base_id, args.labels_dir))
    verdicts = detect(evaluated, pool, reference_ids, same)
    summary = score(verdicts, label_access.load(args.campaign, args.labels_dir))
    meta = {"campaign_id": args.campaign, "baseline_campaign_id": base_id, "same_campaign": same, "metric": METRIC}
    out = args.results_dir / args.campaign
    out.mkdir(parents=True, exist_ok=True)
    with (out / "verdicts.jsonl").open("w", encoding="utf-8") as stream:
        for v in verdicts.values():
            stream.write(json.dumps(v, sort_keys=True) + "\n")
    (out / "summary.json").write_text(json.dumps({**meta, **summary}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "report.md").write_text(markdown(meta, summary), encoding="utf-8")
    h, f = summary["false_alarms_on_healthy"], summary["detection_on_faults"]
    print(f"false alarms {h['anomalous']}/{h['decided']}, detected {f['anomalous']}/{f['decided']} faults; report: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
