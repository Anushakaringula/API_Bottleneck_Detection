#!/usr/bin/env python3
"""Diagnosis evaluation (Phase 5; RQ2–RQ5): rules vs Random Forest vs XGBoost, per telemetry level.

  python -m evaluation.diagnosis --campaign <main-id>                                # development
  python -m evaluation.diagnosis --campaign <heldout-id> --train-campaign <main-id>  # final

Diagnosis is evaluated on fault runs (the question "which bottleneck?" after a slowdown).
Runs excluded by the protocol are left out. Features are made relative to each operation's
healthy baseline, from the training campaign's healthy runs.

Single campaign, two cross-validation schemes (all repeats of a condition stay together):
  grouped     GroupKFold by condition (operation × fault × severity): the condition is unseen
  operation   leave-one-operation-out: the operation is unseen
Final: models trained on all fault runs of --train-campaign, tested once on --campaign.
The rules have no training; they are evaluated on the same runs.

Outputs, data/results/diagnosis/<campaign-id>/: summary.json, report.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from diagnosis import CLASSES, INSUFFICIENT, ml
from diagnosis.relative import relative_table
from diagnosis.rules import diagnose
from remediation.catalogue import recommend
from features.schema import LEVELS, features_at_level
from sklearn.metrics import confusion_matrix, f1_score
from sklearn.model_selection import GroupKFold

from . import labels as label_access
from .detection import load_features
from .labels import DATA_DIR

ABSTAIN_THRESHOLDS = (0.0, 0.5, 0.7)


def metrics(truth: list[str], predicted: list[str]) -> dict[str, Any]:
    """Accuracy and macro-F1 over the bottleneck classes; abstentions count as wrong."""
    if not truth:
        return {"runs": 0}
    covered = [(t, p) for t, p in zip(truth, predicted) if p != INSUFFICIENT]
    per_class = f1_score(truth, predicted, labels=list(CLASSES), average=None, zero_division=0)
    present = [c for c in CLASSES if c in truth]
    return {
        "runs": len(truth),
        "accuracy": sum(t == p for t, p in zip(truth, predicted)) / len(truth),
        "macro_f1": float(f1_score(truth, predicted, labels=present, average="macro", zero_division=0)),
        "coverage": len(covered) / len(truth),
        "selective_accuracy": (sum(t == p for t, p in covered) / len(covered)) if covered else None,
        "f1_per_class": {c: float(v) for c, v in zip(CLASSES, per_class) if c in present},
        "confusion": {"labels": list(CLASSES) + [INSUFFICIENT],
                      "matrix": confusion_matrix(truth, predicted, labels=list(CLASSES) + [INSUFFICIENT]).tolist()},
    }


def cross_validated(rows: list[dict[str, Any]], names: list[str], kind: str, groups_key: str) -> tuple[list[str], Any]:
    """Out-of-fold predictions (and probabilities) with all runs of a group in the same fold."""
    groups = [r[groups_key] for r in rows]
    n_splits = min(5, len(set(groups)))
    x = ml.matrix([r["relative"] for r in rows], names)
    y = [r["truth"] for r in rows]
    predicted = [None] * len(rows)
    confidence = [0.0] * len(rows)
    for train, test in GroupKFold(n_splits=n_splits).split(x, y, groups):
        pred, proba, _ = ml.fit_predict(kind, x[train], [y[i] for i in train], x[test])
        for k, i in enumerate(test):
            predicted[i] = pred[k]
            confidence[i] = float(proba[k].max())
    return predicted, confidence


def abstain(predicted: list[str], confidence: list[float], threshold: float) -> list[str]:
    return [p if c >= threshold else INSUFFICIENT for p, c in zip(predicted, confidence)]


def fault_rows(campaign_id: str, train_id: str, features_dir: Path, labels_dir: Path) -> list[dict[str, Any]]:
    data = load_features(campaign_id, features_dir)
    reference = data if train_id == campaign_id else load_features(train_id, features_dir)
    reference_ids = label_access.reference_runs(label_access.load(train_id, labels_dir))
    table = relative_table(data, reference, reference_ids, same_campaign=train_id == campaign_id)
    labels = label_access.load(campaign_id, labels_dir)
    rows = []
    for eid, r in data.items():
        label = labels.get(eid)
        if not label or label["fault_class"] == "NONE" or r["run"].get("excluded_reasons") or eid not in table:
            continue
        rows.append({"experiment_id": eid, "truth": label["fault_class"], "condition": label["condition_id"],
                     "operation": r["run"]["route"], "features": r["features"], **table[eid]})
    return sorted(rows, key=lambda r: r["experiment_id"])


def evaluate(campaign_id: str, train_id: str, features_dir: Path, labels_dir: Path) -> dict[str, Any]:
    test = fault_rows(campaign_id, train_id, features_dir, labels_dir)
    truth = [r["truth"] for r in test]
    rule_results = [diagnose(r["features"], r["base"]) for r in test]
    report: dict[str, Any] = {
        "campaign_id": campaign_id, "train_campaign_id": train_id, "fault_runs": len(test),
        "class_counts": {c: truth.count(c) for c in CLASSES},
        "rules": {"metrics": metrics(truth, [d["diagnosis"] for d in rule_results]),
                  "misdiagnosed": [{"experiment_id": r["experiment_id"], "condition": r["condition"], "diagnosis": d["diagnosis"], "fired": d["fired"]}
                                   for r, d in zip(test, rule_results) if d["diagnosis"] != r["truth"]],
                  # Per run, for the dashboard: what the rules concluded, why, and which fix follows.
                  "per_run": [{"experiment_id": r["experiment_id"], "condition": r["condition"], "operation": r["operation"], "truth": r["truth"],
                               "diagnosis": d["diagnosis"], "confidence": d["confidence"], "evidence": d["evidence"],
                               "recommendation": recommend(d)} for r, d in zip(test, rule_results)]},
        "ml": {},
    }
    final = train_id != campaign_id
    train = fault_rows(train_id, train_id, features_dir, labels_dir) if final else test
    for level in LEVELS:
        names = features_at_level(level)
        for kind in ml.MODELS:
            entry = {}
            if final:
                pred, proba, _ = ml.fit_predict(kind, ml.matrix([r["relative"] for r in train], names), [r["truth"] for r in train],
                                                 ml.matrix([r["relative"] for r in test], names))
                schemes = {"final": (pred, [float(p.max()) for p in proba])}
            else:
                schemes = {"grouped": cross_validated(test, names, kind, "condition"),
                           "operation": cross_validated(test, names, kind, "operation")}
            for scheme, (pred, conf) in schemes.items():
                entry[scheme] = {f"abstain_{t}": metrics(truth, abstain(pred, conf, t)) for t in ABSTAIN_THRESHOLDS}
            if level == 4:
                entry["top_features"] = ml.importances(kind, ml.matrix([r["relative"] for r in train], names), [r["truth"] for r in train], names)
            report["ml"][f"L{level}/{kind}"] = entry
    return report


def markdown(r: dict[str, Any]) -> str:
    f = lambda v: "-" if v is None else f"{v:.3f}"
    rules = r["rules"]["metrics"]
    final = r["train_campaign_id"] != r["campaign_id"]
    scheme_names = ["final"] if final else ["grouped", "operation"]
    lines = [
        f"# Diagnosis: {r['campaign_id']}", "",
        (f"Final evaluation: models trained on `{r['train_campaign_id']}`, tested once on this campaign." if final else
         "Development evaluation on one campaign: cross-validation with all repeats of a condition kept together "
         "(`grouped` = condition unseen; `operation` = whole operation unseen)."),
        f"Fault runs: {r['fault_runs']} ({', '.join(f'{c} {n}' for c, n in r['class_counts'].items())}). "
        "Macro-F1 over the bottleneck classes; INSUFFICIENT_EVIDENCE (abstention) counts as wrong in accuracy and F1.", "",
        "## Rules vs Random Forest vs XGBoost (macro-F1 / accuracy)", "",
        "| Method | Telemetry | " + " | ".join(f"{s}: macro-F1 | {s}: accuracy" for s in scheme_names) + " |",
        "|---|---|" + "---|---|" * len(scheme_names),
        f"| Rules | L4 | " + " | ".join(f"{f(rules['macro_f1'])} | {f(rules['accuracy'])}" for _ in scheme_names) + " |",
    ]
    for key, entry in r["ml"].items():
        level, kind = key.split("/")
        lines.append(f"| {kind.replace('_', ' ').title()} | {level} | " + " | ".join(
            f"{f(entry[s]['abstain_0.0']['macro_f1'])} | {f(entry[s]['abstain_0.0']['accuracy'])}" for s in scheme_names) + " |")
    s0 = scheme_names[-1]
    lines += ["", f"## Abstention (L4, scheme `{s0}`): coverage and accuracy when the model answers", "",
              "| Method | Threshold | Coverage | Selective accuracy | Macro-F1 |", "|---|---|---|---|---|",
              f"| Rules | – | {f(rules['coverage'])} | {f(rules['selective_accuracy'])} | {f(rules['macro_f1'])} |"]
    for kind in ml.MODELS:
        for t in ABSTAIN_THRESHOLDS:
            m = r["ml"][f"L4/{kind}"][s0][f"abstain_{t}"]
            lines.append(f"| {kind.replace('_', ' ').title()} | {t} | {f(m['coverage'])} | {f(m['selective_accuracy'])} | {f(m['macro_f1'])} |")
    lines += ["", "## Per-class F1 (L4, no abstention)", "", "| Class | Rules | " + " | ".join(k.replace('_', ' ').title() for k in ml.MODELS) + " |", "|---|---|" + "---|" * len(ml.MODELS)]
    for c in CLASSES:
        if c not in rules["f1_per_class"]:
            continue
        lines.append(f"| {c} | {f(rules['f1_per_class'].get(c))} | " + " | ".join(f(r['ml'][f'L4/{k}'][s0]['abstain_0.0']['f1_per_class'].get(c)) for k in ml.MODELS) + " |")
    conf = rules["confusion"]
    lines += ["", "## Rules: confusion matrix (rows = true class)", "", "| | " + " | ".join(l.replace("_", " ") for l in conf["labels"]) + " |", "|---|" + "---|" * len(conf["labels"])]
    for label, row in zip(conf["labels"], conf["matrix"]):
        if label in r["class_counts"] and r["class_counts"][label]:
            lines.append(f"| {label.replace('_', ' ')} | " + " | ".join(str(v) for v in row) + " |")
    lines += ["", "## Evidence the models rely on (L4, trained on all runs)", ""]
    for kind in ml.MODELS:
        top = r["ml"][f"L4/{kind}"].get("top_features", [])
        lines.append(f"- {kind.replace('_', ' ').title()}: " + ", ".join(f"`{n}` {v:.2f}" for n, v in top[:6]))
    if r["rules"]["misdiagnosed"]:
        lines += ["", "## Rules: misdiagnosed runs", ""] + [f"- {m['condition']}: {m['diagnosis']} (fired: {', '.join(m['fired']) or 'none'})" for m in r["rules"]["misdiagnosed"][:30]]
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--campaign", required=True, help="campaign to evaluate (test campaign in the final evaluation)")
    parser.add_argument("--train-campaign", help="campaign to train models and fit baselines on (final evaluation)")
    parser.add_argument("--features-dir", type=Path, default=DATA_DIR / "features")
    parser.add_argument("--labels-dir", type=Path, default=DATA_DIR / "labels")
    parser.add_argument("--results-dir", type=Path, default=DATA_DIR / "results" / "diagnosis")
    args = parser.parse_args(argv)
    report = evaluate(args.campaign, args.train_campaign or args.campaign, args.features_dir, args.labels_dir)
    out = args.results_dir / args.campaign
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=ml.to_python) + "\n", encoding="utf-8")
    (out / "report.md").write_text(markdown(report), encoding="utf-8")
    print(f"rules macro-F1 {report['rules']['metrics']['macro_f1']:.3f}; report: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
