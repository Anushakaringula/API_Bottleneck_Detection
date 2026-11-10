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

## Load models: closed and open loop (Phase 3.5B)

`load_model` selects how requests are released:

| | `closed` (default; Phases 1–3 behaviour) | `open` |
|---|---|---|
| Next request is sent | when a worker is free **and** the pacing interval has passed | at its **timetable slot**, whether or not earlier requests finished |
| `concurrency` means | most requests in flight | number of workers (most requests in flight) |
| A slow server | slows the client down, so queueing is hidden | does not slow the timetable; client-side delay is recorded as send lag |
| Use for | smoke tests, legacy-compatible runs | experiments (planned campaigns use open loop) |

Open-loop options:

- `arrival_process`: `constant` (evenly spaced) or `poisson` (random exponential
  gaps). Both are reproducible from `random_seed`; if no seed is given one is
  drawn and saved in `configuration.json`, so any run can be repeated exactly.
- Rate: `request_rate_per_second` with `request_count` or `duration_seconds`, or
  `stages` for a rate profile. Each stage is
  `{"duration_seconds": d, "rate_per_second": r}` (steady) or
  `{"duration_seconds": d, "rate_per_second_start": a, "rate_per_second_end": b}`
  (linear ramp). A rate of 0 is a pause. With `stages`, leave `request_count`
  and `duration_seconds` unset; the duration is the sum of the stages.
  Poisson arrivals follow ramps too.
- Warm-up requests are sent open-loop at `request_rate_per_second`, or at the
  highest stage rate.

Examples: `experiments/configs/open-loop-steady.json`, `open-loop-ramp.json`
and `open-loop-burst.json`.

```sh
python3 -m experiment_runner.runner --config experiments/configs/open-loop-steady.json
python3 -m experiment_runner.runner --config experiments/configs/example-config.json \
  --load-model open --arrival-process poisson --request-rate-per-second 20 \
  --duration-seconds 30 --concurrency 50 --random-seed 1
```

`request_variants` (both load models) draws one query-string variant per request,
so that, for example, page sizes vary within one operation:

```json
"request_variants": [
  {"query": {"limit": 10}, "weight": 3},
  {"query": {"limit": 100, "fields": "id,name"}, "weight": 1}
]
```

The drawn `variant_index` is recorded per request. It describes the workload and
is not a label.

### Send-time fields

Each request record has:

| Field | Meaning |
|---|---|
| `intended_send_offset_ms` | Timetable slot, from phase start. In closed loop: when the dispatcher released it. |
| `sent_offset_ms` | When a worker actually started sending it. |
| `send_lag_ms` | Actual minus intended. It grows when the client cannot keep up (all workers busy, or an overloaded machine). |
| `latency_ms` | Send to response end: what the server and network took. |
| `latency_from_intended_ms` | Intended send to response end: what a user arriving on time experiences, including client-side delay. |

`summary.json` → `measured` adds the `send_lag_ms` and `latency_from_intended_ms`
distributions and `achieved_send_rate_per_second`.

## Client calibration

Calibration checks that the client keeps to the timetable before experiment data
is trusted. Start the API with its calibration endpoints enabled:

```sh
cd target-api && ABR_CALIBRATION=1 npm start
```

Then, in a second terminal:

```sh
python3 -m experiment_runner.calibrate --rates 5,10,20,50,100 --duration-seconds 15
```

It runs one open-loop experiment per operation and rate:

- `noop`: no server work; this measures client overhead.
- `sleep`: a non-blocking 50 ms wait; this needs real concurrency.

A run passes when send lag p99 is at most 5 ms, no request failed, and the
achieved rate is within 2 % of the target. All three limits can be changed with
options.

Each run lasts at least `--duration-seconds` (default 15) **and** at least
`--min-requests` (default 500) measured requests. With fewer than 100 samples the
p99 is just one of the two largest values, so a single operating-system
scheduling hiccup would decide the result. In the first calibration on Windows
(10 Nov 2026), a 75-request run at 5 rps failed this way while every rate from 10
to 100 rps passed.

Output:

- raw runs: `data/raw/calibration/<id>/`
- report: `data/results/calibration/<id>/report.md` and `report.json`

The report gives the **highest rate passing for every operation**, which is the
3.5B gate. The exit status is 0 when all rates pass and 1 otherwise. If the
planned rates fail, that is the point to consider k6.

## Rate, concurrency, and safety

Defaults are 20 measured requests, two warm-up requests, concurrency 2, target
rate 2 requests/second, a 10 second per-request timeout, and the closed loop.
Hard limits (raised in Phase 3.5B) are:

- 200 concurrent workers,
- 50,000 total requests per experiment including warm-up (checked against the
  planned timetable in the open loop),
- 200 requests per second,
- 900 seconds per duration run or stage profile,
- 20 stages,
- 120 seconds per request timeout.

In the closed loop the dispatcher waits for a worker slot when the selected rate
exceeds available capacity, and never issues catch-up bursts after a slow
request. Set `request_rate_per_second` to `null` to pace only by the concurrency
limit, or set `request_interval_seconds` instead of a target rate.

The runner sends `X-Experiment-Run-Id` and a W3C `traceparent` header on every
warm-up and measured request in that run. Each request gets a fresh random trace
ID and client span ID, recorded as `trace_id` and `client_span_id` in
`requests.jsonl`; a `traceparent` given in the configured `headers` is replaced.
The scenario label is experiment ground truth only. It appears in configuration
and summary metadata, is **not sent** to the system under test (since Phase
3.5A), is omitted from per-request measurement records, and must be excluded from
diagnosis model features.

Warm-up request results remain in `requests.jsonl` and in the warm-up count
section, but they are excluded from measured success/error totals, latency
percentiles, and throughput. OpenTelemetry expected-span counts include both
phases. A connection failure is still an attempted request and is included in
the expected count; as it may never reach the API, it can cause correlation to
be reported as partial. Measured latency and throughput include failed attempts
because they are part of the observed workload; a connection failure has no HTTP
status code and is reported separately in the status-count map.

`summary.json` → `telemetry_correlation` reports two levels:

- run level: expected, observed, missing and duplicate `experiment.http.request`
  spans carrying the run ID;
- `request_level`: each request matched to exactly one experiment span by its
  trace ID (`requests_matched_to_one_span`, `requests_without_span`,
  `requests_with_multiple_spans`, `spans_without_client_request`).

`correlation_status` is `complete` only when every request matched exactly one
span and no unrequested or duplicate spans were found.

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
