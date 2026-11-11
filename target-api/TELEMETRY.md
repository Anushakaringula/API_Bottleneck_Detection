# Target API telemetry

Start the service from `target-api/` with `npm start` (or `npm run start:env` to
load the repository's `.env`). OpenTelemetry is loaded before
the Express application so automatic HTTP/Express instrumentation can establish
the incoming server span before any request code runs.

Telemetry schema: **2** (Phase 3.5A). Every experiment span carries
`experiment.telemetry_schema = "2"`. Spans without it are schema 1 (Phases 1–3,
for example the legacy data); see [Schema 1 differences](#schema-1-differences).

## Span tree of one request

```
GET (HTTP server span, kind SERVER)          parent = client span from traceparent
└── experiment.http.request (INTERNAL)       measurements below
    ├── middleware - jsonParser ...          Express middleware spans
    └── request handler - /api/...           Express route-handler span
        └── (database / cache spans, from Phase 3.5C)
```

The experiment span is started in the `http.createServer` callback
(`experimentEntry` in `src/server.js`), **before** Express sees the request, and
Express runs inside its context. It stays open until the response emits
`finish`; a premature `close` ends it with `experiment.response_complete=false`
and an error status.

It records method, path, matched route, run ID, duration, process CPU deltas,
heap delta, response body bytes, status code and completion state.

## Correlation

- **Per request (exact).** The client sends a W3C `traceparent` header. The HTTP
  instrumentation adopts its trace ID, so the client's recorded `trace_id` equals
  the trace ID of every server span of that request. The server span's
  `parent_span_id` is the client's span ID, which is never exported.
- **Per run.** Send `X-Experiment-Run-Id` on every request in one experiment. If
  omitted, `EXPERIMENT_RUN_ID` is used; if that is also unset, one ID is generated
  for the server process. The run ID is put into the request context, and
  `RunIdSpanProcessor` (`instrumentation.mjs`) copies it as `experiment.run_id`
  onto **every** span of the request, including the server span. Keep run IDs
  low-cardinality; they are never Prometheus labels.
- **No labels.** The scenario label is ground truth and is not sent by the
  runner. The server ignores any `X-Experiment-Scenario` header; a smoke test
  checks that it never reaches spans or the CSV.

## Outputs

| Output | Default location | Override | Contents |
|---|---|---|---|
| All spans | `data/raw/spans/otel-spans.jsonl` | `ABR_SPAN_FILE` | One JSON record per span (trace/span/parent IDs, name, kind, duration, attributes, status) |
| Per-run spans | `data/raw/spans/runs/<run-id>.jsonl` | `ABR_SPAN_RUN_DIR` | The same records, only for spans carrying that run ID. Written when the run ID matches `[A-Za-z0-9._-]{1,128}`. |
| Request CSV | `data/raw/spans/telemetry.csv` | `ABR_TELEMETRY_CSV` | One row per request: `timestamp,run_id,trace_id,method,path,latency_ms,cpu_user_ms,cpu_system_ms,heap_delta_mb,response_size_bytes,status_code,complete` |
| Runtime samples | `data/raw/runtime/runtime-metrics.jsonl` | `ABR_RUNTIME_FILE`, `ABR_RUNTIME_SAMPLE_MS` (default 1000, `0` disables) | One record per window: event-loop delay mean/p50/p99/max, event-loop utilization, GC count/total/max pause, process CPU ms and %, heap used/total, RSS, requests in flight (at sample time and window maximum), and the run IDs active in the window |
| Prometheus | `http://localhost:9464/metrics` | `METRICS_PORT` | `experiment.http.request.count`, `.request.duration`, `.response.size`, `.error.count`, `.requests.in_flight`, plus the runtime-node instrumentation metrics |

`ABR_DATA_DIR` moves all file outputs at once.

The server **refuses to start** if the request CSV already exists with a
different header, for example a schema-1 file. Raw data is never rewritten, so
point `ABR_TELEMETRY_CSV` at a new file instead.

All file writes are asynchronous and serialized per file. A write failure is
logged. This lightweight file-based collector does not provide durable
buffering, rotation, multi-process locking, or backpressure. Do not run multiple
server processes writing the same files.

Prometheus labels use method, matched route (falling back to path) and status
code. For production systems, avoid unbounded route values by ensuring routes are
parameterized.

## Calibration endpoints

With `ABR_CALIBRATION=1` the server also serves operations of known cost. They are
off by default.

| Endpoint | Cost | Used for |
|---|---|---|
| `GET /__calib/noop` | none | client overhead (`experiment_runner.calibrate`) |
| `GET /__calib/sleep?ms=N` | non-blocking wait of N ms (max 10,000) | real concurrency: many can be in flight at once |
| `GET /__calib/cpu?ms=N` | busy loop holding the event loop for N ms | checking CPU and latency telemetry against a known value |

They are instrumented like every other route.

## Shutdown

`SIGINT`/`SIGTERM` (Ctrl+C) close the server, then flush the CSV queue, the
runtime sampler and the OpenTelemetry SDK. On Windows a child process cannot
catch `SIGTERM`, so a parent that spawns the server with an IPC channel sends the
message `"shutdown"` instead (the smoke test does this).

## Measurement limitations

- `process.cpuUsage()` reports CPU consumed by the entire Node.js process
  between two observations. Concurrent requests overlap, so the delta cannot
  be attributed to one request. Use it as a coarse process signal in
  low-concurrency controlled experiments, not as per-request CPU truth.
- The heap delta is the process heap-used difference at two instants. Garbage
  collection, unrelated requests, and runtime activity can dominate it; a
  negative delta is possible. It is not allocation volume or retained memory.
- Runtime samples are process-level over a time window. A run is joined to the
  windows that list its run ID; a window can overlap several runs.
- Response bytes count body chunks passed through the Node response methods.
  They do not include headers and may differ from wire bytes after compression
  or transport framing.
- A response closed before `finish` has partial measurements and is explicitly
  marked incomplete. Status may still be the default status if the handler did
  not complete.
- This service only collects telemetry. A route name is not a bottleneck
  diagnosis, and `/api/query-sim` remains an in-memory filter simulation rather
  than a database benchmark.

## Schema 1 differences

Schema 1 is the Phase 1–3 data, including `data/raw/legacy-2026-11-09/`:

- The experiment span was started inside an Express middleware. Express
  instrumentation restores each layer's *parent* span when a middleware calls
  `next()`, so the experiment span was a leaf under `middleware - <anonymous>` and
  the handler spans were its siblings. In the Phase 1–3 corpus, 0 of 3,988 span
  records have an experiment span as parent.
- It carried `experiment.scenario` (from the `X-Experiment-Scenario` header).
- There was no `traceparent`, so client records matched spans only per run, by
  count.
- The CSV had a `scenario` column and no `trace_id`.
- There were no per-run span files and no runtime samples.

The analyzer checks schema-1 spans against the schema-1 attribute list, so
historical analyses still reproduce byte-for-byte.
