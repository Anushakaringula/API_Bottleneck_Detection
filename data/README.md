# data/

All recorded and derived research data. Pipeline stages exchange work through files
in this folder; they do not import each other's code. Git ignores everything here
except this README and the integrity files (`SHA256SUMS`, `PROVENANCE.md`,
`MIGRATION_INVENTORY.tsv`).

Folders are created by the code that first writes to them.

| Folder | Written by | Read by | Rules |
|---|---|---|---|
| `raw/legacy-2026-11-09/` | Nobody (frozen) | Analyzer, `scripts/verify-legacy.sh` | Phase 1–3 data, moved on 10 Nov 2026. Never edit. Verify with `sha256sum -c SHA256SUMS`. |
| `processed/legacy/` | Nobody (frozen) | `scripts/verify-legacy.sh` | The 9 Nov 2026 analysis. Never edit. |
| `raw/spans/` | Target API | Runner (span correlation), analyzer | Raw telemetry. Append-only; never edit. |
| `raw/experiments/<run-id>/` | Experiment runner | Analyzer | One folder per run. Never edit. |
| `processed/<analysis-id>/` | `pipeline/telemetry_analysis` | Later pipeline stages, dashboard | Each output has a manifest of input hashes. Features (`run_features.jsonl`) are kept apart from metadata and labels (`run_metadata.jsonl`). |
| `labels/` *(from 3.5D)* | Campaign tool | Evaluation, and ML training for training-fold runs only, through one label-access module | Ground truth only. Never read at prediction time. |
| `features/` *(from analyzer v2)* | `pipeline/telemetry_analysis` | Detection, diagnosis | Allowlisted predictor fields only |
| `results/{detection,diagnosis,remediation,verification,evaluation}/` *(from Phase 4)* | The matching pipeline stage | Later stages, dashboard | Each result names the feature set it came from |

## Leakage rules

- Scenario names, endpoint paths, run, experiment and repeat-group IDs, and
  implementation variants never appear in predictor features. This is checked by
  `pipeline/telemetry_analysis/tests/test_leakage.py` and
  `python3 -m telemetry_analysis.leakage_audit <analysis-dir>`.
- Legacy exception: the raw `configuration.json` and `summary.json` files in
  `raw/legacy-2026-11-09/` contain the scenario label. They are historical and are
  not edited. The analyzer moves the label into `run_metadata.jsonl` only.
