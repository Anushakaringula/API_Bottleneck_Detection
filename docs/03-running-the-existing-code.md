# 3. Running the existing code

Requirements: Node.js 20, Python 3.10, and the target API's npm packages (already
installed in `target-api/node_modules`). The Python code uses only the standard
library.

Load the environment once per terminal, from the repository root:

```sh
cd ~/api-bottleneck-research
source scripts/env.sh        # loads .env if present, puts the Python packages on PYTHONPATH
```

> **Note:** by default the API writes to `data/raw/spans/` and the runner to
> `data/raw/experiments/`. The frozen historical data in
> `data/raw/legacy-2026-11-09/` is never written to. To keep a trial run out of
> `data/`, point `ABR_DATA_DIR` at another folder first, for example
> `export ABR_DATA_DIR=/tmp/abr-trial`.

## 1. Start the instrumented API

```sh
cd target-api
npm start
```

- API: `http://localhost:3000` (endpoints `/api/normal`, `/api/cpu`,
  `/api/payload`, `/api/query-sim`, `/health`)
- Prometheus metrics: `http://localhost:9464/metrics`
- Environment variables: `PORT`, `METRICS_PORT`, `EXPERIMENT_RUN_ID`, and the
  `ABR_*` data paths (see `.env.example`).
- Stop it with Ctrl+C. The server flushes its spans on shutdown.

## 2. Run an experiment

In a second terminal (after `source scripts/env.sh`):

```sh
python3 -m experiment_runner.runner \
  --config experiments/configs/example-config.json \
  --scenario cpu \
  --request-count 100 \
  --warmup-requests 10 \
  --concurrency 5 \
  --request-rate-per-second 5
```

- Scenario names: `normal`, `cpu`, `payload`, `query-sim`, `custom`. Use
  `query-sim`, not `query`.
- Output goes to `data/raw/experiments/<experiment-id>/` and contains
  `configuration.json`, `requests.jsonl` and `summary.json`.
- Span correlation in `summary.json` is **per run** (by run ID), not per request.
- See `experiments/experiment_runner/README.md` for all options and safety limits.

## 3. Analyze the experiments

```sh
python3 -m telemetry_analysis.analyzer
python3 -m telemetry_analysis.leakage_audit data/processed/<analysis-id>
```

The analyzer writes a new folder `data/processed/<analysis-id>/` containing:

| File | Contents |
|---|---|
| `run_features.jsonl` | Predictor features only |
| `run_metadata.jsonl` | Labels, endpoints and IDs. These are never used as predictors. |
| `telemetry_observations.jsonl` | Span counts and process-level resource observations |
| `data_quality.jsonl` | Problems found in the data |
| `manifest.json` | Input file hashes and versions |

Raw files are only read, never changed. See
`pipeline/telemetry_analysis/README.md` for details.

## 4. Run the tests and checks

```sh
scripts/test-all.sh          # every test suite; nothing is written under data/
scripts/verify-legacy.sh     # historical checksums + byte-for-byte reproduction of the 9 Nov analysis
```

Expected results as of 10 Nov 2026:

| Suite | Result |
|---|---|
| Target API | 5 tests pass, plus 1 TODO: the open span-parenting defect, reported but not failing |
| Experiment runner | 12 tests pass |
| Telemetry analysis | 12 tests pass |
| `verify-legacy.sh` | Prints two proxy-leakage warnings (`p95_latency_ms`, `p99_latency_ms`), which are expected for the legacy data |

## Where things are

| Path | What it is |
|---|---|
| `target-api/` | System under test: Express API, OpenTelemetry instrumentation, `TELEMETRY.md` |
| `experiments/experiment_runner/` | Workload generation and experiment execution |
| `experiments/configs/` | Experiment definitions |
| `pipeline/telemetry_analysis/` | Telemetry processing, feature engineering, leakage checks |
| `data/` | All recorded data. See `data/README.md`. Never delete or edit `data/raw/`. |
| `scripts/` | Environment setup, test runner, legacy verification |
