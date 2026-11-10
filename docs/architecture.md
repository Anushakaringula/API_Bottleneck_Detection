# Architecture

The repository is organised around the research workflow. It contains two
separate systems that never import each other's code:

- **`target-api/`**: the *system under test*. A Node.js/Express API with its
  OpenTelemetry instrumentation. It contains no detection logic.
- **`pipeline/`**: the *investigation system*. Offline Python stages that
  process telemetry and, in later phases, detect, diagnose, remediate and verify.

`experiments/` drives load into the target API and records what happened.
`data/` is how the stages hand work to each other: each stage reads the folder of
the stage before it and writes its own.

## Layout

```
target-api/                    system under test (workflow step 2: emits telemetry)
  src/server.js                Express app + experiment-span middleware
  instrumentation.mjs          OpenTelemetry SDK, Prometheus exporter, JSONL span exporter
  test/smoke.test.mjs          endpoints, spans, metrics, trace correlation; span-parenting TODO
  TELEMETRY.md                 what the API emits, correlation scope, known defect
experiments/                   workload generation + experiment execution (steps 1 and 3)
  experiment_runner/           Python package: runner.py, tests/
  configs/                     experiment definitions (example-config.json)
pipeline/                      investigation system (steps 4–7)
  telemetry_analysis/          step 4: processing, feature engineering, leakage checks
data/                          data handed between stages (see data/README.md)
scripts/                       env.sh, test-all.sh, verify-legacy.sh, compare_manifest.py
docs/                          documentation
```

**Planned, created only when their code is written:**

| Path | Phase | Purpose |
|---|---|---|
| `target-api/src/lab/` | 3.5C | Healthy, faulty and remediated variants |
| `target-api/db/` | 3.5C | PostgreSQL schema, seed data, index migrations |
| `infrastructure/` | 3.5C | User-run PostgreSQL and Redis setup |
| `experiments/configs/campaigns/` | 3.5D | Campaign definitions |
| `experiments/protocol.md` | 3.5D | Frozen experiment protocol |
| `pipeline/detection/` | 4 | Detection |
| `pipeline/diagnosis/` | 5 | Diagnosis, including `ml/` for Random Forest and XGBoost |
| `pipeline/remediation/` | 6 | Fix recommendations |
| `pipeline/verification/` | 7 | Before-and-after verification |
| `pipeline/evaluation/` | 9 | Evaluation; also owns the label-access module |
| `dashboard/` | 8 | Next.js dashboard |
| `observability/` | Optional | Only if Collector, Prometheus or Grafana configs are added |

## Detection and diagnosis

- **Detection** (`pipeline/detection/`, Phase 4) identifies **abnormal API
  behaviour**. It asks whether an operation, under a given workload, behaves
  differently from **its own healthy baseline**, beyond normal run-to-run
  variation.
  - Method: statistical, at run level. Thresholds come from repeated healthy runs
    and are agreed with the guide.
  - Output: `normal`, `anomalous` or `inconclusive`, with evidence.
  - It never names a cause and never reads labels.
- **Diagnosis** (`pipeline/diagnosis/`, Phase 5) runs only after a detection. It
  identifies the **likely bottleneck type** using **supporting evidence**.
  - Output: one or more types or `insufficient evidence`, with supporting,
    contradicting and missing evidence and a confidence level.
  - The primary method is a rule-based evidence engine.
- **Random Forest and XGBoost** are diagnosis classifiers (`pipeline/diagnosis/ml/`)
  compared against the rules.
  - They use the same baseline-relative features, the same runs and splits, and
    each telemetry level L1–L4.
  - Results use the same output schema as the rules.
  - They are evaluated with repeat groups kept together, on held-out operations
    and severities, and on a held-out session. They may abstain.
  - Optionally, they may also serve as a comparator for detection, but never as
    the detector of record.
  - They get training labels only through one label-access module, which returns
    labels only for training-fold runs. No label is read at prediction time.

## How a request flows through the system (today)

1. **Schedule.** `experiment_runner` reads a config from `experiments/configs/`,
   creates a run ID, and sends warm-up and then measured requests at the
   configured rate and concurrency.
2. **Send.** Each HTTP request carries `X-Experiment-Run-Id` and
   `X-Experiment-Scenario`. The scenario header is removed in Phase 3.5A, which
   also adds `traceparent`.
3. **Instrument.** In `target-api/`:
   - The OpenTelemetry HTTP instrumentation opens the server span, and Express
     instrumentation adds middleware and handler spans.
   - The experiment middleware opens `experiment.http.request` and records
     latency, status, response bytes, and process CPU and heap deltas.
   - It also updates the Prometheus metrics and appends a CSV row.
4. **Respond.** The handler returns JSON. The runner records client latency,
   status, bytes and outcome.
5. **Export.** The batch span processor writes the spans to
   `data/raw/spans/otel-spans.jsonl`.
6. **Record the run.** The runner writes `configuration.json`, `requests.jsonl`
   and `summary.json` to `data/raw/experiments/<run-id>/`. It then checks
   **run-level** correlation: that the number of spans tagged with the run ID
   equals the number of attempts.
7. **Process.** `telemetry_analysis` writes features, metadata (labels kept
   apart), observations, data quality and a manifest to
   `data/processed/<analysis-id>/`.
8. **Later phases.**
   - Detection compares the processed data with healthy baselines.
   - Diagnosis explains detected anomalies.
   - Remediation names a pre-written variant to try.
   - The runner re-runs the same workload with that variant.
   - Verification compares the before and after runs.
   - Evaluation joins the results with the labels.
   - The dashboard shows the results.

## Correlation and known defects

- **Correlation is per run today, not per request.** Exact per-request
  correlation (W3C `traceparent`) is introduced and tested in Phase 3.5A.
- **Span-parenting defect.** The experiment span is not the parent of the Express
  handler spans. It is documented in `target-api/TELEMETRY.md`, reported as TODO
  by `target-api/test/smoke.test.mjs` on every test run, and fixed first in
  Phase 3.5A.

## Rules enforced by tests

| Rule | Enforced by | When |
|---|---|---|
| Predictor allowlist has no label, scenario, endpoint or ID names | `pipeline/telemetry_analysis/tests/test_leakage.py` | Now |
| Feature output has no such text and only numeric values | `pipeline/telemetry_analysis/tests/test_leakage.py` | Now |
| Features and metadata are separated | `pipeline/telemetry_analysis/tests/test_leakage.py` | Now |
| Unapproved fields are rejected at serialization | `pipeline/telemetry_analysis/tests/test_leakage.py` | Now |
| Same checks on the real 9 Nov output; proxy-leakage audit (warning) | `scripts/verify-legacy.sh` | Now |
| Historical data unchanged; 9 Nov analysis reproducible byte-for-byte | `scripts/verify-legacy.sh` | Now |
| No variant or label in target-API telemetry | target-API tests | From 3.5C |
| `pipeline/` does not import `experiments/` or `target-api/`; only ML training imports the label loader | Import-boundary tests | From Phases 4–5 |

## Configuration

All settings come from environment variables (template: `.env.example`). Data
paths default to `<repo>/data`. They can be redirected with `ABR_DATA_DIR`, or
per file with `ABR_SPAN_FILE`, `ABR_TELEMETRY_CSV`, `ABR_EXPERIMENTS_DIR` and
`ABR_ANALYSIS_DIR`. The tests use these variables to keep their output in
temporary folders. `source scripts/env.sh` loads `.env` and sets `PYTHONPATH`.
