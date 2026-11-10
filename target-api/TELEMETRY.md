# Phase 1 telemetry

Start the service from `target-api/` with `npm start` (or `npm run start:env` to
load the repository's `.env`). OpenTelemetry is loaded before
the Express application so automatic HTTP/Express instrumentation can establish
the incoming server span before request middleware runs.

Each request gets one `experiment.http.request` span. It is a child of the
active Express middleware span, within the incoming HTTP trace, and remains
open until the response emits `finish`; a premature `close` ends it with
`experiment.response_complete=false` and an error status. The experiment span
includes method, path, scenario, run ID, duration, process CPU deltas, heap
delta, response body bytes, status code, and completion state. The run ID and
scenario are also added to the active Express middleware span; trace and parent
IDs connect it to the incoming HTTP span. IDs and parent IDs are written to
`data/raw/spans/otel-spans.jsonl` by default (`ABR_SPAN_FILE` or `ABR_DATA_DIR`
override the location).

Send `X-Experiment-Run-Id` on every request in one experiment to correlate its
telemetry. If omitted, `EXPERIMENT_RUN_ID` is used; if that is also unset, one
ID is generated for the server process. Send `X-Experiment-Scenario` to label
the experimental workload. The scenario defaults to `unspecified`; it is never
inferred from the endpoint name. Keep run IDs and scenarios low-cardinality in
the telemetry corpus. Neither is used as a Prometheus metric label.

Prometheus metrics are served at `http://localhost:9464/metrics` by default.
The service records `experiment.http.request.count`,
`experiment.http.request.duration`, `experiment.http.response.size`,
`experiment.http.error.count`, and `experiment.http.requests.in_flight`.
Labels use method,
matched route (falling back to path), and status code. For production systems,
avoid unbounded route values by ensuring routes are parameterized.

The existing CSV collector remains enabled at `data/raw/spans/telemetry.csv`
(override with `ABR_TELEMETRY_CSV` or `ABR_DATA_DIR`) and
queues asynchronous append operations. JSONL span writes are also asynchronous
and serialized. A write failure is logged; this lightweight file-based
collector does not provide durable buffering, rotation, multi-process locking,
or backpressure. Do not run multiple server processes writing the same files.

## Correlation scope

All spans of one request share a `trace_id`, and the experiment span carries
`experiment.run_id`. Client request records can currently be matched to spans
**per run** (by run ID and span count), **not per request**: the client does not
send a W3C `traceparent` header, so it never learns a request's trace ID.
Exact per-request correlation is planned as part of Phase 3.5A.

## Known defect (open): span parenting

- **Intended:** the middleware runs `next()` inside a context whose active span
  is the experiment span, so Express route-handler spans (and anything below
  them) would be children of `experiment.http.request`.
- **Observed:** the experiment span never has children. In the Phase 1–3 corpus,
  0 of 3,988 span records have an experiment span as parent. The experiment span
  is a leaf under `middleware - <anonymous>`, and the handler spans are its
  siblings under the HTTP server span. The cause has not been established yet.
- **Impact:** handler time cannot be found by walking down from the experiment
  span. Spans of one request must be joined by `trace_id`. Trace correlation
  itself is unaffected.
- **Tracking:** `test/smoke.test.mjs` contains "experiment span is an ancestor of
  the route-handler span", marked `todo`. It runs and reports the failure on
  every `npm test` without failing the suite. The fix is the first item of
  Phase 3.5A: reproduce with an in-memory exporter, find the cause in
  `@opentelemetry/instrumentation-express` with Express 5, then either fix the
  parenting or move the measurements onto the HTTP server span. After the fix,
  the `todo` mark is removed.

## Measurement limitations

- `process.cpuUsage()` reports CPU consumed by the entire Node.js process
  between two observations. Concurrent requests overlap, so the delta cannot
  be attributed to one request. Use it as a coarse process signal in
  low-concurrency controlled experiments, not as per-request CPU truth.
- The heap delta is the process heap-used difference at two instants. Garbage
  collection, unrelated requests, and runtime activity can dominate it; a
  negative delta is possible. It is not allocation volume or retained memory.
- Response bytes count body chunks passed through the Node response methods.
  They do not include headers and may differ from wire bytes after compression
  or transport framing.
- A response closed before `finish` has partial measurements and is explicitly
  marked incomplete. Status may still be the default status if the handler did
  not complete.
- This phase only collects telemetry. A route or scenario name is not a
  bottleneck diagnosis, and `/api/query-sim` remains an in-memory filter
  simulation rather than a database benchmark.
