# Experiment runner

This Python standard-library runner sends bounded workloads to the Phase 1 API
and writes one reproducible artifact directory per run under
`data/raw/experiments/<experiment-id>/` by default (override with `--output-dir`,
`ABR_EXPERIMENTS_DIR` or `ABR_DATA_DIR`):

- `configuration.json`: full workload, timestamps, repeat metadata, and runtime
  environment details.
- `requests.jsonl`: one result per warm-up or measured request, tagged with its
  phase. It stores status, request latency, completion timestamps, errors, and
  response byte count; it does not store response bodies.
- `summary.json`: measured-only latency and throughput statistics, warm-up
  counts, status totals, and OpenTelemetry span correlation.

## Run an experiment

Start the instrumented API in another terminal:

```sh
cd target-api
npm start
```

From the repository root, load the environment (sets `PYTHONPATH`) and run the
sample configuration:

```sh
source scripts/env.sh
python3 -m experiment_runner.runner --config experiments/configs/example-config.json
```

Choose a built-in workload with `scenario`: `normal`, `cpu`, `payload`, or
`query-sim`. Set `target_url` to the API base URL. The runner resolves those
scenarios to `/api/normal`, `/api/cpu`, `/api/payload`, and
`/api/query-sim`. The last route is an in-memory filter simulation, not a real
database benchmark. For a custom endpoint, use `scenario: "custom"`, set
`target_url` to its full URL, and select `method` (and optional `headers` or
`request_body`). A custom scenario label can be provided as `scenario_label`.

Configuration can be overridden on the command line, for example:

```sh
python3 -m experiment_runner.runner \
  --config experiments/configs/example-config.json \
  --scenario cpu --request-count 40 --warmup-requests 5 \
  --concurrency 4 --request-rate-per-second 4 --timeout-seconds 15
```

Use either `request_count` or `duration_seconds`. Duration mode launches at
least one request immediately, then schedules additional requests until the
duration expires; already launched requests are allowed to finish. Its
throughput denominator is the actual measured phase time through completion of
the final in-flight request. `repeats` creates separate experiment IDs sharing
a repeat-group ID and identical workload settings.

## Rate, concurrency, and safety

Defaults are 20 measured requests, two warm-up requests, concurrency 2, target
rate 2 requests/second, and a 10 second per-request timeout. Hard limits are
20 concurrent workers, 5,000 total requests per experiment including warm-up,
50 request launches per second, 300 seconds per duration run, and 120 seconds
per request timeout. The dispatcher waits for a worker slot when the selected
rate exceeds available capacity and never issues catch-up bursts after a slow
request. Set `request_rate_per_second` to `null` to pace only by the concurrency
limit, or set `request_interval_seconds` instead of a target rate.

The runner uses `X-Experiment-Run-Id` and `X-Experiment-Scenario` for every
warm-up and measured request in that run. The scenario label is experiment
ground truth only. It appears in configuration and summary metadata and is
sent to Phase 1 telemetry; it is deliberately omitted from per-request
measurement records and must be excluded from future diagnosis model features.

Warm-up request results remain in `requests.jsonl` and in the warm-up count
section, but they are excluded from measured success/error totals, latency
percentiles, and throughput. OpenTelemetry expected-span counts include both
phases. A connection failure is still an attempted request and is included in
the expected count; as it may never reach the API, it can cause correlation to
be reported as partial. Span matching is by run ID and dedicated span name.
Measured latency and throughput include failed attempts because they are part
of the observed workload; a connection failure has no HTTP status code and is
reported separately in the status-count map.
Phase 1 does not return a trace ID to the client, so request-to-span matching is
not one-to-one; the summary reports experiment-level expected, observed,
missing, and duplicate counts instead.

The experiment ID directory must not already exist. `configuration.json` is
written at start and finalized with the end time and actual execution details.
Authorization, cookie, and proxy-authorization header values are redacted in
saved configuration; supply those credentials again through a protected local
configuration when reproducing a secured workload.
The runner reads the configured span JSONL file after each run and waits up to
`span_wait_seconds` (default eight seconds) for the batch exporter. The default
span file is `data/raw/spans/otel-spans.jsonl` (override with `--span-file`,
`ABR_SPAN_FILE` or `ABR_DATA_DIR`). It must be the same file the target API
writes to.

## Tests

```sh
source scripts/env.sh
python3 -m unittest discover -s experiments/experiment_runner/tests -v
```
