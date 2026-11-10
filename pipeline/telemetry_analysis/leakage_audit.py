#!/usr/bin/env python3
"""Leakage checks for an analysis output directory.

Direct leakage (hard failure): label, scenario, endpoint or ID information reaching
run_features.jsonl. Proxy leakage (warning only): a single numeric predictor that on
its own perfectly separates the scenario groups, i.e. it may act as a stand-in for
the endpoint or scenario. With few runs, perfect separation can also occur by chance,
so the proxy result is a warning sign, not proof.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from .schema import JOIN_KEY_FIELDS, PREDICTOR_FIELDS

FORBIDDEN_NAME_PATTERN = re.compile(
    r"scenario|label|variant|fault|severity|endpoint|path|route|url|method|"
    r"run_id|experiment_id|repeat|condition|span_name|trace_id|timestamp|_at$",
    re.IGNORECASE,
)
# Metadata values that must never appear inside the predictor file.
METADATA_TEXT_FIELDS = ("scenario", "target_endpoint", "experiment_id", "run_id", "repeat_group_id")


def forbidden_predictor_names(names: set[str] | None = None) -> list[str]:
    return sorted(name for name in (names or PREDICTOR_FIELDS) if FORBIDDEN_NAME_PATTERN.search(name))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def direct_leakage_problems(analysis_dir: Path) -> list[str]:
    """Return a list of hard-failure findings (empty when clean)."""
    problems: list[str] = []
    features_path = analysis_dir / "run_features.jsonl"
    metadata_path = analysis_dir / "run_metadata.jsonl"
    features = _read_jsonl(features_path)
    metadata = _read_jsonl(metadata_path)
    allowed = PREDICTOR_FIELDS | JOIN_KEY_FIELDS
    for name in forbidden_predictor_names():
        problems.append(f"predictor allowlist contains forbidden name: {name}")
    for row in features:
        extra = set(row) - allowed
        if extra:
            problems.append(f"feature row has non-allowlisted keys: {sorted(extra)}")
        for key, value in row.items():
            if key in JOIN_KEY_FIELDS:
                continue
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                problems.append(f"predictor {key} has non-numeric value {value!r}")
    shared = set().union(*(set(r) for r in features)) & set().union(*(set(r) for r in metadata)) if features and metadata else set()
    if shared - JOIN_KEY_FIELDS:
        problems.append(f"features and metadata share keys other than the join key: {sorted(shared - JOIN_KEY_FIELDS)}")
    if metadata and not all("scenario" in row for row in metadata):
        problems.append("metadata rows are missing the scenario label (labels must be kept, separately)")
    feature_text = features_path.read_text(encoding="utf-8")
    tokens = {str(row[field]) for row in metadata for field in METADATA_TEXT_FIELDS if row.get(field)}
    for token in sorted(tokens):
        if token in feature_text:
            problems.append(f"metadata value appears in run_features.jsonl: {token!r}")
    return problems


def proxy_leakage_report(analysis_dir: Path, label_field: str = "scenario") -> list[dict[str, Any]]:
    """For each predictor, report whether it alone perfectly separates the label groups."""
    features = {row["analysis_record_id"]: row for row in _read_jsonl(analysis_dir / "run_features.jsonl")}
    labels = {row["analysis_record_id"]: row.get(label_field) for row in _read_jsonl(analysis_dir / "run_metadata.jsonl")}
    report = []
    for name in sorted(PREDICTOR_FIELDS):
        groups: dict[Any, list[float]] = defaultdict(list)
        complete = True
        for record_id, row in features.items():
            value = row.get(name)
            if value is None or record_id not in labels:
                complete = False
                break
            groups[labels[record_id]].append(float(value))
        if not complete or len(groups) < 2:
            continue
        intervals = sorted((min(values), max(values)) for values in groups.values())
        separates = all(intervals[i][1] < intervals[i + 1][0] for i in range(len(intervals) - 1))
        if separates:
            report.append({"predictor": name, "groups": len(groups), "runs": sum(len(v) for v in groups.values())})
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("analysis_dir", type=Path, help="Directory containing run_features.jsonl and run_metadata.jsonl")
    args = parser.parse_args(argv)
    problems = direct_leakage_problems(args.analysis_dir)
    for problem in problems:
        print(f"DIRECT LEAKAGE: {problem}")
    if not problems:
        print("direct leakage checks: PASS")
    flagged = proxy_leakage_report(args.analysis_dir)
    if flagged:
        for item in flagged:
            print(f"PROXY WARNING: {item['predictor']} alone separates all {item['groups']} scenario groups ({item['runs']} runs)")
    else:
        print("proxy audit: no single predictor separates all scenario groups")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
