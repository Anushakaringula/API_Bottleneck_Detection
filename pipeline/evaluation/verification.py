#!/usr/bin/env python3
"""Remediation and verification evaluation (Phases 6–7; RQ6).

  python -m evaluation.verification --campaign <main-id>

1. Remediation mapping: for every fault run, the rules' diagnosis is turned into a fix
   recommendation. Does the recommended fix match the injected fault?
2. Verification: for every fixed variant, compare its runs with the runs of the same fault
   (same operation, fault, severity) and with the operation's healthy runs, on the server-side
   run p50 (protocol v1):
   - improved: one-sided Welch on log latency, Holm-corrected across pairs, alpha 0.05;
   - back to healthy: TOST equivalence within ±10 %;
   - mechanism: the metric the fix targets moved the expected way.
Runs excluded by the protocol are left out. Output: data/results/verification/<campaign-id>/.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from diagnosis.rules import diagnose
from remediation.catalogue import CATALOGUE, recommend
from verification.stats import equivalent, holm, mann_whitney_less, welch_log

from . import labels as label_access
from .detection import load_features
from .diagnosis import fault_rows
from .labels import DATA_DIR

METRIC = "server_latency_p50_ms"
MARGIN = 0.10
ALPHA = 0.05
FAULT_OF_CLASS = {cls: entry["variant"].removesuffix("-fixed") for cls, entry in CATALOGUE.items()}


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def remediation_mapping(campaign_id: str, features_dir: Path, labels_dir: Path) -> dict[str, Any]:
    rows = fault_rows(campaign_id, campaign_id, features_dir, labels_dir)
    matched, per_class = 0, {}
    for r in rows:
        rec = recommend(diagnose(r["features"], r["base"]))
        ok = rec["variant"] == f"{FAULT_OF_CLASS[r['truth']]}-fixed"
        matched += ok
        c = per_class.setdefault(r["truth"], {"runs": 0, "matched": 0})
        c["runs"] += 1
        c["matched"] += ok
    return {"fault_runs": len(rows), "recommendation_matches_fault": matched, "rate": matched / len(rows) if rows else None, "per_class": per_class}


def verify(campaign_id: str, features_dir: Path, labels_dir: Path) -> dict[str, Any]:
    data = load_features(campaign_id, features_dir)
    labels = label_access.load(campaign_id, labels_dir)
    usable = {eid: r for eid, r in data.items() if eid in labels and not r["run"].get("excluded_reasons")}

    def group(operation_route: str, variant: str) -> list[dict[str, Any]]:
        return [r["features"] for eid, r in usable.items() if labels[eid]["variant"] == variant and r["run"]["route"] == operation_route]

    pairs = []
    for condition in sorted({l["condition_id"] for l in labels.values() if l["is_fixed"]}):
        sample = next(eid for eid, l in labels.items() if l["condition_id"] == condition)
        route = data[sample]["run"]["route"] if sample in data else None
        fixed_variant = labels[sample]["variant"]
        faulty_variant = fixed_variant.replace("-fixed", "", 1)
        fixed, faulty, healthy = group(route, fixed_variant), group(route, faulty_variant), group(route, "healthy")
        cls = next((c for c, f in FAULT_OF_CLASS.items() if f == labels[sample]["remediation_of"]), None)
        improve = welch_log([f[METRIC] for f in faulty], [f[METRIC] for f in fixed], confidence=0.95)
        back = equivalent([f[METRIC] for f in healthy], [f[METRIC] for f in fixed], MARGIN)
        mechanism = {}
        for metric, direction in CATALOGUE[cls]["expected_change"].items():
            before, after = _median([f.get(metric) for f in faulty]), _median([f.get(metric) for f in fixed])
            moved = None if before is None or after is None else (after < before if direction == "down" else after > before)
            mechanism[metric] = {"faulty": before, "fixed": after, "expected": direction, "moved_as_expected": moved}
        pairs.append({"pair": f"{labels[sample]['operation']}: {faulty_variant} -> {fixed_variant}", "class": cls,
                      "runs": {"faulty": len(faulty), "fixed": len(fixed), "healthy": len(healthy)},
                      "median_ms": {"faulty": _median([f[METRIC] for f in faulty]), "fixed": _median([f[METRIC] for f in fixed]), "healthy": _median([f[METRIC] for f in healthy])},
                      "improvement": {**improve, "mann_whitney_p": mann_whitney_less([f[METRIC] for f in faulty], [f[METRIC] for f in fixed])},
                      "back_to_healthy": back, "mechanism": mechanism})
    adjusted = holm({p["pair"]: p["improvement"]["p_one_sided"] for p in pairs})
    for p in pairs:
        p["improvement"]["p_holm"] = adjusted[p["pair"]]
        p["improved"] = adjusted[p["pair"]] is not None and adjusted[p["pair"]] < ALPHA
        p["verdict"] = ("improved, back to healthy" if p["improved"] and p["back_to_healthy"]["equivalent"] else
                        "improved, not shown equivalent to healthy" if p["improved"] else "improvement not shown")
    return {"campaign_id": campaign_id, "metric": METRIC, "margin": MARGIN, "alpha": ALPHA,
            "remediation_mapping": remediation_mapping(campaign_id, features_dir, labels_dir), "pairs": pairs}


def markdown(r: dict[str, Any]) -> str:
    f = lambda v, d=2: "-" if v is None else f"{v:.{d}f}"
    m = r["remediation_mapping"]
    lines = [f"# Remediation and verification: {r['campaign_id']}", "",
             "## Remediation mapping (Phase 6)", "",
             f"The rules' diagnosis of each fault run, turned into a fix recommendation, names the right fix for "
             f"**{m['recommendation_matches_fault']} / {m['fault_runs']}** runs ({f(100 * m['rate'] if m['rate'] is not None else None, 1)} %).", "",
             "| Class | Fault runs | Right fix recommended |", "|---|---|---|",
             *[f"| {c} | {v['runs']} | {v['matched']} |" for c, v in sorted(m["per_class"].items())], "",
             "## Verification (Phase 7)", "",
             f"Server-side run p50. Improved = one-sided Welch on log latency, Holm-corrected, alpha {r['alpha']}. "
             f"Back to healthy = TOST, 90 % CI of fixed/healthy within ±{int(100 * r['margin'])} %.", "",
             "| Fix | Runs (faulty / fixed / healthy) | Median ms (faulty → fixed; healthy) | Fixed/faulty ratio [95 % CI] | p (Holm) | Fixed/healthy ratio [90 % CI] | Mechanism metric moved | Verdict |",
             "|---|---|---|---|---|---|---|---|"]
    for p in r["pairs"]:
        i, b = p["improvement"], p["back_to_healthy"]
        mech = ", ".join(f"{k} {'yes' if v['moved_as_expected'] else 'no'}" for k, v in p["mechanism"].items())
        lines.append(f"| {p['pair']} | {p['runs']['faulty']} / {p['runs']['fixed']} / {p['runs']['healthy']} | "
                     f"{f(p['median_ms']['faulty'])} → {f(p['median_ms']['fixed'])}; {f(p['median_ms']['healthy'])} | "
                     f"{f(i['ratio'], 3)} [{f(i['ci'][0], 3) if i['ci'] else '-'}, {f(i['ci'][1], 3) if i['ci'] else '-'}] | {f(i['p_holm'], 4)} | "
                     f"{f(b['ratio'], 3)} [{f(b['ci90'][0], 3) if b['ci90'] else '-'}, {f(b['ci90'][1], 3) if b['ci90'] else '-'}] | {mech} | **{p['verdict']}** |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--features-dir", type=Path, default=DATA_DIR / "features")
    parser.add_argument("--labels-dir", type=Path, default=DATA_DIR / "labels")
    parser.add_argument("--results-dir", type=Path, default=DATA_DIR / "results" / "verification")
    args = parser.parse_args(argv)
    report = verify(args.campaign, args.features_dir, args.labels_dir)
    out = args.results_dir / args.campaign
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "report.md").write_text(markdown(report), encoding="utf-8")
    verdicts = [p["verdict"] for p in report["pairs"]]
    print(f"{sum(v.startswith('improved') for v in verdicts)}/{len(verdicts)} fixes improved, "
          f"{sum(v == 'improved, back to healthy' for v in verdicts)} back to healthy; report: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
