#!/usr/bin/env python3
"""Compare a reproduced analysis manifest with the historical one.

Rules:
- analysis_created_at is ignored (it is a generation timestamp);
- input_sources: same number of entries, and each historical path, mapped to its new
  location through MIGRATION_INVENTORY.tsv, must have the same sha256 in the same order;
- every other key must be identical.
Exit status 0 when the manifests match under these rules, 1 otherwise.
"""
import csv
import json
import sys
from pathlib import Path


def main(reference: str, reproduced: str, inventory: str) -> int:
    ref = json.loads(Path(reference).read_text(encoding="utf-8"))
    new = json.loads(Path(reproduced).read_text(encoding="utf-8"))
    with open(inventory, encoding="utf-8") as handle:
        mapping = {row["old_path"]: row["new_path"] for row in csv.DictReader(handle, delimiter="\t")}
    problems = []
    ignored = {"analysis_created_at", "input_sources"}
    for key in sorted((set(ref) | set(new)) - ignored):
        if ref.get(key) != new.get(key):
            problems.append(f"key differs: {key}")
    ref_sources, new_sources = ref.get("input_sources", []), new.get("input_sources", [])
    if len(ref_sources) != len(new_sources):
        problems.append(f"input_sources count {len(ref_sources)} != {len(new_sources)}")
    for old, cur in zip(ref_sources, new_sources):
        expected_path = mapping.get(old["path"])
        if expected_path != cur["path"]:
            problems.append(f"path mapping: {old['path']} -> expected {expected_path}, got {cur['path']}")
        if old["sha256"] != cur["sha256"]:
            problems.append(f"sha256 differs for {old['path']}")
    for problem in problems:
        print(f"MANIFEST MISMATCH: {problem}")
    if problems:
        return 1
    print(f"manifest matches: {len(ref_sources)}/{len(new_sources)} input hashes equal; only analysis_created_at and input paths differ")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit("usage: compare_manifest.py REFERENCE_MANIFEST REPRODUCED_MANIFEST MIGRATION_INVENTORY.tsv")
    sys.exit(main(*sys.argv[1:]))
