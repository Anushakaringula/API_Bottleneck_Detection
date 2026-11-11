# 3. Running the existing code

Requirements: Node.js 20, Python 3.10, and the target API's npm packages
(`cd target-api && npm ci`). The Python code uses only the standard library.
Setup on Ubuntu or Windows: [02-development-setup.md](02-development-setup.md).

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
- Environment variables: `PORT`, `METRICS_PORT`, `EXPERIMENT_RUN_ID`, the
  `ABR_*` data paths and `ABR_RUNTIME_SAMPLE_MS` (see `.env.example`).
- Writes spans (`data/raw/spans/otel-spans.jsonl` and per-run copies in
  `data/raw/spans/runs/`), a request CSV, and runtime samples
  (`data/raw/runtime/runtime-metrics.jsonl`). See `target-api/TELEMETRY.md`.
- Stop it with Ctrl+C. The server flushes its spans on shutdown.

### Against the local lab (Phase 3.5C)

Experiments run the target API inside the lab's Docker network, next to PostgreSQL and
Redis (see `infrastructure/README.md` and `target-api/README.md`):

```sh
scripts/lab-up.sh                # PostgreSQL 16 + Redis 7
scripts/lab-seed.sh large        # once: build the seeded template (~35 s)
scripts/lab-reset.sh             # before every run
scripts/lab-api.sh up            # target API on http://127.0.0.1:3000 (domain API + experiment endpoints)
scripts/lab-api.sh down          # after the run (flushes telemetry)
```

Inject a fault by starting the API with a variant (see `target-api/README.md`), for
example `ABR_VARIANTS='{"orders.list":"noindex"}' scripts/lab-api.sh restart`. Check that
every fault works with `cd target-api && npm run lab:fault-checks` (about 4 min).

### Campaigns: many labelled runs (Phase 3.5D)

A campaign runs every planned condition (operation × variant × repeat) in a shuffled,
reproducible order. Each run resets the lab, starts the API with the variant, measures and
stops it. Ground truth goes only to `data/labels/` (see `experiments/campaign/README.md`):

```sh
python -m campaign.run --config experiments/configs/campaigns/pilot.json --dry-run
python -m campaign.run --config experiments/configs/campaigns/pilot.json    # ~55 min
python -m campaign.summarize <campaign-id>
```

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
- Each request carries a W3C `traceparent`; `requests.jsonl` records its
  `trace_id`. Span correlation in `summary.json` is reported per run and **per
  request** (`telemetry_correlation.request_level`).
- See `experiments/experiment_runner/README.md` for all options and safety limits.

### Open-loop load and calibration (Phase 3.5B)

Experiments should use the open loop: requests are sent on a timetable, so load
that the server cannot keep up with shows as latency instead of being hidden.

```sh
python3 -m experiment_runner.runner --config experiments/configs/open-loop-steady.json
```

Ramp and burst profiles are in `open-loop-ramp.json` and `open-loop-burst.json`.

Before collecting data on a machine, calibrate the client. Restart the API with
`ABR_CALIBRATION=1 npm start`, then run:

```sh
python3 -m experiment_runner.calibrate --rates 5,10,20,50,100
cat data/results/calibration/*/report.md
```

The report states the highest rate at which the client keeps to its timetable
(send lag p99 ≤ 5 ms, no failures, rate within 2 %).

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
| `request_records.jsonl` | Each client request joined to its server span by trace ID (not predictors) |
| `request_join_summary.json` | Per-run and total request-to-span match rates |

Raw files are only read, never changed. See
`pipeline/telemetry_analysis/README.md` for details.

## 4. Run the tests and checks

```sh
scripts/test-all.sh          # every test suite; nothing is written under data/
scripts/verify-legacy.sh     # historical checksums + byte-for-byte reproduction of the 9 Nov analysis
```

Expected results after Phase 3.5D-1:

| Suite | Result |
|---|---|
| Target API | 16 tests pass, 5 skipped (lab integration tests: `ABR_LAB_TESTS=1 npm run test:lab` against a seeded lab, 5 pass) |
| Experiment runner | 23 tests pass |
| Campaign | 14 tests pass (fake lab, no Docker) |
| Features | 5 tests pass |
| Detection | 7 tests pass (incl. import-boundary rules) |
| Diagnosis | 7 tests pass (needs `pip install -r requirements.txt`) |
| Telemetry analysis | 14 tests pass |
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
