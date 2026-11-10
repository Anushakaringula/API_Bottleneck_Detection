# Telemetry analysis

This standard-library Python package turns existing Phase 1 spans and Phase 2
experiment artifacts into versioned, read-only analysis outputs. It does not
modify source artifacts and does not perform diagnosis, classification,
remediation, or ML.

## Run

From the repository root (or anywhere, after loading the environment):

```sh
source scripts/env.sh
python3 -m telemetry_analysis.analyzer
```

By default it reads `data/raw/experiments/` and `data/raw/spans/otel-spans.jsonl`
and writes to `data/processed/`. Locations can be set explicitly, or through
`ABR_EXPERIMENTS_DIR`, `ABR_SPAN_FILE`, `ABR_ANALYSIS_DIR` or `ABR_DATA_DIR`:

```sh
python3 -m telemetry_analysis.analyzer \
  --experiments-dir data/raw/experiments \
  --span-file data/raw/spans/otel-spans.jsonl \
  --output-dir data/processed
```

To reproduce the 9 Nov 2026 analysis from the frozen legacy data (output to a
temporary folder, compared byte-for-byte), run `scripts/verify-legacy.sh`.

The command prints the newly created analysis directory. It contains
`run_features.jsonl`, `run_metadata.jsonl`, `telemetry_observations.jsonl`,
`data_quality.jsonl`, and `manifest.json`. The source files are only read. Each
manifest records input SHA-256 hashes and schema/analyzer versions.

To run tests and inspect generated outputs:

```sh
python3 -m unittest discover -s pipeline/telemetry_analysis/tests -v
latest=$(ls -1dt data/processed/2* | head -n 1)
cat "$latest/manifest.json"
cat "$latest/data_quality.jsonl"
```

## Leakage checks

`tests/test_leakage.py` checks four things:

- the predictor allowlist contains no label, scenario, endpoint or ID names;
- feature output contains no such text and only numeric values;
- features and metadata share only the join key;
- the serialization guard rejects unapproved fields.

For any analysis output, run:

```sh
python3 -m telemetry_analysis.leakage_audit <analysis-dir>
```

It fails on direct leakage. It also prints a **proxy warning** for any predictor
that on its own perfectly separates the scenario groups, which means the predictor
may stand in for the endpoint. On the 9 Nov data, `p95_latency_ms` and
`p99_latency_ms` are flagged, because every scenario there is a different endpoint.
This is a known issue of that dataset.

## Features and interpretation

The feature record contains measured request count, successful responses,
failed requests, error rate, client latency mean/median/P95/P99/min/max,
response-size summaries, status class counts, and runner-observed maximum
in-flight count. Warm-up rows are excluded. Percentiles use linear interpolation
with the `(n - 1)` rank convention used by the runner.

`attempt_throughput_rps` is measured attempts divided by the positive measured
phase duration. `successful_throughput_rps` is successful measured responses
divided by that same duration. The analyzer uses the recorded actual measured
phase duration from configuration, falling back to the summary duration when
needed. If no valid positive duration exists, both values are null and a data
quality finding is written; no duration is guessed from wall-clock run times.

HTTP status class counts count measured responses, while requests without a
status (for example, connection failures) are counted separately. Response
size statistics include measured requests that received an HTTP response.
`analysis_record_id` joins feature records to metadata and is a key, not a
predictor.

## Leakage controls

The predictor allowlist is in `schema.py` and checked before writing. Scenario,
endpoint/path, experiment ID, run ID, repeat-group ID, and `analysis_record_id`
are kept outside the predictor values in `run_metadata.jsonl` (the analysis
record ID is only the join key). Scenario is ground truth for evaluation only.
When constructing future train/test splits, every run with the same
`repeat_group_id` must stay in the same partition. Do not treat repeat runs or
requests from one run as independent experimental units.

## Correlation and quality

Only `experiment.http.request` spans are counted as experiment request spans;
other auto-instrumented spans are not request counts. The Phase 1/2 data has no
client-visible request ID shared by request records and spans, so exact
request-to-span matching is impossible. For failed HTTP requests, status-code
overlap is reported as aggregate count-level correspondence, not proof of an
individual match. Connection failures without status are reported separately;
extra spans can indicate that some such requests reached the service, but this
cannot be assigned to specific requests. Count discrepancies are explicitly
reported as unexplained/ambiguous, not automatically called instrumentation
failure.

Span duration is server-side and distinct from client latency. Spans do not
identify warm-up phase, so span and resource observations are marked as
warm-up/measured mixed when warm-ups exist. CPU and heap deltas are process-level
observations, not exact request resource consumption. Concurrency, garbage
collection, and unrelated runtime activity reduce their attribution
reliability. In-memory query filtering does not establish a database
bottleneck, and large response size alone does not establish a payload
bottleneck.

Malformed lines, invalid measurements, artifact inconsistencies, duplicate
span IDs, orphan run spans, and span-count discrepancies are written to
`data_quality.jsonl`. Raw telemetry and original experiment outputs are
preserved.
