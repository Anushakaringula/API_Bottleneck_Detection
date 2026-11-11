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
| `raw/spans/` | Target API | Runner (span correlation), analyzer | Raw telemetry: `otel-spans.jsonl` (all spans), `telemetry.csv` (one row per request). Append-only; never edit. |
| `raw/spans/runs/<run-id>.jsonl` *(from 3.5A)* | Target API | Later stages | Per-run copy of the spans carrying that run ID. Append-only. |
| `raw/runtime/runtime-metrics.jsonl` *(from 3.5A)* | Target API | Later stages (telemetry level L3) | Process/runtime samples per time window (event-loop delay, GC, CPU, heap, RSS), each listing the active run IDs. Append-only. |
| `raw/experiments/<run-id>/` | Experiment runner | Analyzer | One folder per run. Never edit. |
| `raw/calibration/<calibration-id>/` *(from 3.5B)* | `experiment_runner.calibrate` | Calibration report | Raw runs of one client calibration. Never edit. |
| `results/lab-probe/<timestamp>.json` *(from 3.5C-1)* | `scripts/lab-probe.sh` (gate, lab network) and `npm run lab:probe` (host path) | Team, protocol | Lab gate: database and cache hosts are local; SELECT 1 / PING latency distributions and versions |
| `results/fault-checks/<timestamp>/` *(from 3.5C-3)* | `target-api` `npm run lab:fault-checks` | Team, protocol | `report.md` / `report.json`: per fault and operation, faulty / fixed / healthy medians and mechanism checks; severity ladders. Uses ground truth; never a model input |
| `results/calibration/<calibration-id>/` *(from 3.5B)* | `experiment_runner.calibrate` | Team, protocol | `report.md` / `report.json`: send lag and achieved rate per operation and rate; the highest rate the client sustains |
| `processed/<analysis-id>/` | `pipeline/telemetry_analysis` | Later pipeline stages, dashboard | Each output has a manifest of input hashes. Features (`run_features.jsonl`) are kept apart from metadata and labels (`run_metadata.jsonl`). From 3.5A also `request_records.jsonl` and `request_join_summary.json` (request-to-span join; not predictors). |
| `raw/campaigns/<campaign-id>/` *(from 3.5D)* | `campaign.run` | Analyzer, `campaign.summarize` | `manifest.json` (plan, environment), `progress.jsonl`, `experiments/<run>/` (runner artifacts, no ground truth), `db-stats/<run>.json`. Never edit. |
| `labels/<campaign-id>.jsonl` *(from 3.5D)* | `campaign.run` | Evaluation and `campaign.summarize`; ML training for training-fold runs only, through one label-access module | Ground truth per run (operation, variant, fault, class, severity, fix). Never read at prediction time. |
| `results/campaigns/<campaign-id>/` *(from 3.5D)* | `campaign.summarize` | Team, protocol | `report.md` / `summary.json`: healthy noise, effect per condition, telemetry completeness, run time |
| `features/<campaign-id>/` *(Analyzer v2)* | `pipeline/features` | Detection, diagnosis | `features.jsonl` (allowlisted numeric predictors, L1–L4), `runs.jsonl` (route, coverage, exclusions; metadata), `schema.json`. No ground truth |
| `results/{detection,diagnosis,remediation,verification,evaluation}/` *(from Phase 4)* | The matching pipeline stage | Later stages, dashboard | Each result names the feature set it came from |

## Leakage rules

- Scenario names, endpoint paths, run, experiment and repeat-group IDs, and
  implementation variants never appear in predictor features. This is checked by
  `pipeline/telemetry_analysis/tests/test_leakage.py` and
  `python3 -m telemetry_analysis.leakage_audit <analysis-dir>`.
- Legacy exception: the raw `configuration.json` and `summary.json` files in
  `raw/legacy-2026-11-09/` contain the scenario label. They are historical and are
  not edited. The analyzer moves the label into `run_metadata.jsonl` only.
