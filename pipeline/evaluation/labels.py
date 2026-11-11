"""Label access: the single module that reads ground truth (data/labels/<campaign>.jsonl).

Detection and diagnosis code never import this module. Evaluation uses it to choose
reference runs (healthy runs that define baselines) and to score verdicts. ML training (Phase 5)
uses it for training-fold runs only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.environ.get("ABR_DATA_DIR") or Path(__file__).resolve().parents[2] / "data").resolve()


def load(campaign_id: str, labels_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    path = (labels_dir or DATA_DIR / "labels") / f"{campaign_id}.jsonl"
    with path.open(encoding="utf-8") as stream:
        return {row["experiment_id"]: row for row in map(json.loads, filter(str.strip, stream))}


def reference_runs(labels: dict[str, dict[str, Any]]) -> set[str]:
    """Healthy runs (variant 'healthy'): they define each operation's baseline."""
    return {eid for eid, label in labels.items() if label["variant"] == "healthy"}
