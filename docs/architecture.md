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
  src/server.js                Express app + experiment-span entry point (before Express)
  src/routes/domain.js         domain API: users, products, orders, search, reports, dashboard (3.5C-2)
  src/lab/variants.js          healthy / faulty / fixed implementation per operation (ABR_VARIANTS)
  src/lab/faults.js            fault building blocks: index drop/recreate, CPU work, broken cache key (3.5C-3)
  scripts/fault-checks.mjs     gate: every fault active when on, gone when fixed (3.5C-3)
  src/db.js                    PostgreSQL pool + Redis client; lab-profile guard
  db/                          schema.sql, seed.sql (deterministic), constraints.sql (named indexes)
  Dockerfile                   target-API image for the lab network (Node 20)
  src/runtime-metrics.js       event-loop delay, GC, CPU, heap samples per time window
  instrumentation.mjs          OpenTelemetry SDK, Prometheus exporter, JSONL span exporter (+ per-run files)
  test/smoke.test.mjs          endpoints, spans, metrics, traceparent, parenting, no-label, runtime samples
  TELEMETRY.md                 what the API emits, correlation, schema 1 vs 2
  scripts/lab-probe.mjs        lab gate: database and cache are local and quiet (3.5C-1)
infrastructure/                local experiment lab: PostgreSQL 16 + Redis 7 (Docker Compose), see its README
experiments/                   workload generation + experiment execution (steps 1 and 3)
  experiment_runner/           Python package: runner.py, tests/
  campaign/                    campaigns (3.5D): plan.py, run.py (orchestrator), lab.py, summarize.py, tests/
  configs/                     experiment definitions; configs/campaigns/ (smoke.json, pilot.json)
pipeline/                      investigation system (steps 4–7)
  telemetry_analysis/          step 4: processing, feature engineering, leakage checks
  features/                    step 4 (Analyzer v2): run features L1–L4 per campaign, label-free
  detection/                   Phase 4: detector of record (protocol rule), verdicts with evidence
  evaluation/                  scoring; labels.py is the only reader of ground truth
data/                          data handed between stages (see data/README.md)
scripts/                       env.sh, test-all.sh, verify-legacy.sh, compare_manifest.py, lab-up/down/reset/status.sh
docs/                          documentation
```

**Planned, created only when their code is written:**

| Path | Phase | Purpose |
|---|---|---|
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

1. **Schedule.** `experiment_runner` reads a config from `experiments/configs/`
   and creates a run ID. It then sends warm-up and measured requests:
   - closed loop: at the configured rate, limited by concurrency;
   - open loop (3.5B): on a seeded timetable (constant or Poisson, optionally
     with rate stages), whether or not earlier requests have finished.

   Each request records its intended and actual send time.
   `experiment_runner.calibrate` checks beforehand that the client keeps to the
   timetable.
2. **Send.** Each HTTP request carries `X-Experiment-Run-Id` and a W3C
   `traceparent` with a fresh trace ID, which the runner records. The scenario
   label is never sent.
3. **Instrument.** In `target-api/`:
   - The OpenTelemetry HTTP instrumentation opens the server span, adopting the
     client's trace ID.
   - The entry point (`experimentEntry`, before Express) opens
     `experiment.http.request` and puts it, and the run ID, into the context in
     which Express runs. Express middleware and handler spans are therefore its
     children. Every span of the request gets `experiment.run_id`.
   - At the end of the response it records latency, status, response bytes, and
     process CPU and heap deltas, updates the Prometheus metrics and appends a CSV
     row.
   - A runtime sampler writes event-loop delay, GC, CPU, heap and RSS per time
     window.
4. **Respond.** The handler returns JSON. The runner records client latency,
   status, bytes and outcome.
5. **Export.** The batch span processor writes the spans to
   `data/raw/spans/otel-spans.jsonl` and to the per-run file
   `data/raw/spans/runs/<run-id>.jsonl`.
6. **Record the run.** The runner writes `configuration.json`, `requests.jsonl`
   and `summary.json` to `data/raw/experiments/<run-id>/`. It then checks
   correlation at run level (span count per run ID) and at **request level**
   (each request's trace ID has exactly one experiment span).
7. **Process.** `telemetry_analysis` writes features, metadata (labels kept
   apart), observations, data quality and a manifest to
   `data/processed/<analysis-id>/`, plus the request-to-span join
   (`request_records.jsonl`, `request_join_summary.json`).
8. **Later phases.**
   - Detection compares the processed data with healthy baselines.
   - Diagnosis explains detected anomalies.
   - Remediation names a pre-written variant to try.
   - The runner re-runs the same workload with that variant.
   - Verification compares the before and after runs.
   - Evaluation joins the results with the labels.
   - The dashboard shows the results.

## Correlation

- **Per request (since Phase 3.5A).** Requests are matched to spans by W3C trace
  ID, tested in `target-api/test/smoke.test.mjs`, the runner tests and the
  analyzer tests. Data from before Phase 3.5A (including the legacy data) can
  only be matched per run.
- **Span parenting (fixed in Phase 3.5A).** Express instrumentation resets the
  active span to each layer's parent when a middleware calls `next()`, so a span
  started in a middleware could never parent the handler spans. The experiment
  span is now started before Express. The schema-1 behaviour is described in
  `target-api/TELEMETRY.md`.

## Rules enforced by tests

| Rule | Enforced by | When |
|---|---|---|
| Predictor allowlist has no label, scenario, endpoint or ID names | `pipeline/telemetry_analysis/tests/test_leakage.py` | Now |
| Feature output has no such text and only numeric values | `pipeline/telemetry_analysis/tests/test_leakage.py` | Now |
| Features and metadata are separated | `pipeline/telemetry_analysis/tests/test_leakage.py` | Now |
| Unapproved fields are rejected at serialization | `pipeline/telemetry_analysis/tests/test_leakage.py` | Now |
| Same checks on the real 9 Nov output; proxy-leakage audit (warning) | `scripts/verify-legacy.sh` | Now |
| Historical data unchanged; 9 Nov analysis reproducible byte-for-byte | `scripts/verify-legacy.sh` | Now |
| No scenario label in target-API telemetry | `target-api/test/smoke.test.mjs`, runner tests | Now (3.5A) |
| No variant label in target-API telemetry | target-API tests | From 3.5C |
| `pipeline/` does not import `experiments/` or `target-api/`; only ML training imports the label loader | Import-boundary tests | From Phases 4–5 |

## Configuration

All settings come from environment variables (template: `.env.example`). Data
paths default to `<repo>/data`. They can be redirected with `ABR_DATA_DIR`, or
per file with `ABR_SPAN_FILE`, `ABR_TELEMETRY_CSV`, `ABR_EXPERIMENTS_DIR` and
`ABR_ANALYSIS_DIR`. The tests use these variables to keep their output in
temporary folders. `source scripts/env.sh` loads `.env` and sets `PYTHONPATH`.
