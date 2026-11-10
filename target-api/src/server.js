const express = require("express");
const http = require("node:http");
const fsSync = require("node:fs");
const fs = require("node:fs/promises");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const { context, SpanKind, SpanStatusCode, trace, metrics } = require("@opentelemetry/api");
const { startRuntimeMetrics } = require("./runtime-metrics");
const { checkLabProfile, closeAll, database } = require("./db");
const variants = require("./lab/variants");
const domainRoutes = require("./routes/domain");

const app = express();
const PORT = Number(process.env.PORT || 3000);
// Version of the experiment-span attribute set. "2": scenario removed, trace-level
// correlation, span is the parent of all Express spans (Phase 3.5A).
const TELEMETRY_SCHEMA = "2";
// Shared with instrumentation.mjs (RunIdSpanProcessor).
const RUN_ID_CONTEXT_KEY = Symbol.for("abr.experiment.run_id");
const tracer = trace.getTracer("api-bottleneck-research", "1.0.0");
const meter = metrics.getMeter("api-bottleneck-research", "1.0.0");
const requestCount = meter.createCounter("experiment.http.request.count", { description: "Completed HTTP requests", unit: "{request}" });
const errorCount = meter.createCounter("experiment.http.error.count", { description: "HTTP responses with status 4xx or 5xx", unit: "{request}" });
const requestDuration = meter.createHistogram("experiment.http.request.duration", { description: "HTTP request duration", unit: "ms" });
const responseSize = meter.createHistogram("experiment.http.response.size", { description: "HTTP response body size", unit: "By" });
const requestsInFlight = meter.createUpDownCounter("experiment.http.requests.in_flight", { description: "HTTP requests currently being served", unit: "{request}" });

const dataDirectory = path.resolve(process.env.ABR_DATA_DIR || path.resolve(__dirname, "../../data"));
const metricsFile = path.resolve(process.env.ABR_TELEMETRY_CSV || path.join(dataDirectory, "raw/spans/telemetry.csv"));
const runtimeFile = path.resolve(process.env.ABR_RUNTIME_FILE || path.join(dataDirectory, "raw/runtime/runtime-metrics.jsonl"));
const CSV_HEADER = "timestamp,run_id,trace_id,method,path,latency_ms,cpu_user_ms,cpu_system_ms,heap_delta_mb,response_size_bytes,status_code,complete\n";
const processRunId = process.env.EXPERIMENT_RUN_ID || randomUUID();

// Startup refusals are written synchronously: console.error on a pipe (e.g. in Docker) is
// asynchronous, and the message would be lost when the process exits right after it.
function refuseToStart(message) {
  fsSync.writeSync(2, `Refusing to start: ${message}
`);
}
let inFlight = 0;

// Raw data is append-only: never append rows of this format to a CSV written with a
// different header (for example one from before Phase 3.5A).
try {
  const existingHeader = fsSync.readFileSync(metricsFile, "utf8").split("\n", 1)[0] + "\n";
  if (existingHeader !== CSV_HEADER) {
    refuseToStart(`${metricsFile} has a different column header. Set ABR_TELEMETRY_CSV to a new file.`);
    process.exit(1);
  }
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}

// Empty means default (.env.example leaves values empty); "0" disables sampling.
const sampleMs = process.env.ABR_RUNTIME_SAMPLE_MS;
const runtime = startRuntimeMetrics({
  file: runtimeFile,
  intervalMs: sampleMs === undefined || sampleMs === "" ? 1000 : Number(sampleMs),
  inFlight: () => inFlight,
});

let telemetryWriteQueue = Promise.resolve();
let csvInitialized = false;
function queueTelemetryWrite(row) {
  telemetryWriteQueue = telemetryWriteQueue.then(async () => {
    if (!csvInitialized) {
      await fs.mkdir(path.dirname(metricsFile), { recursive: true });
      try {
        await fs.access(metricsFile);
      } catch {
        await fs.writeFile(metricsFile, CSV_HEADER);
      }
      csvInitialized = true;
    }
    await fs.appendFile(metricsFile, row);
  }).catch((error) => {
    console.error("Telemetry CSV write failed:", error);
  });
}

function csv(value) {
  const text = String(value ?? "");
  return `"${text.replaceAll('"', '""')}"`;
}

function pathOf(url) {
  try {
    return new URL(url || "/", "http://localhost").pathname;
  } catch {
    return "/";
  }
}

// Runs for every request before Express sees it. Express instrumentation resets the
// active span to each layer's *parent* when a middleware calls next(), so a span
// started inside a middleware can never parent the route-handler spans. Starting
// the experiment span here makes it the parent context of the whole Express stack.
function experimentEntry(req, res) {
  const header = req.headers["x-experiment-run-id"];
  const runId = String((Array.isArray(header) ? header[0] : header) || processRunId).slice(0, 128);
  const requestPath = pathOf(req.url);
  const cpuStart = process.cpuUsage();
  const heapStart = process.memoryUsage().heapUsed;
  const start = process.hrtime.bigint();
  let bytes = 0;
  let recorded = false;
  inFlight += 1;
  runtime.noteRequest(runId);

  trace.getActiveSpan()?.setAttribute("experiment.run_id", runId);
  const span = tracer.startSpan("experiment.http.request", { kind: SpanKind.INTERNAL });
  span.setAttributes({
    "experiment.telemetry_schema": TELEMETRY_SCHEMA,
    "experiment.run_id": runId,
    "http.request.method": req.method,
    "url.path": requestPath,
  });
  requestsInFlight.add(1, { "http.request.method": req.method });

  const countChunk = (chunk, encoding) => {
    if (chunk === undefined || chunk === null || typeof chunk === "function") return;
    bytes += Buffer.isBuffer(chunk) || chunk instanceof Uint8Array ? chunk.length : Buffer.byteLength(String(chunk), encoding || "utf8");
  };
  const originalWrite = res.write;
  const originalEnd = res.end;
  res.write = function (chunk, encoding, callback) {
    countChunk(chunk, typeof encoding === "string" ? encoding : "utf8");
    return originalWrite.call(this, chunk, encoding, callback);
  };
  res.end = function (chunk, encoding, callback) {
    countChunk(chunk, typeof encoding === "string" ? encoding : "utf8");
    return originalEnd.call(this, chunk, encoding, callback);
  };

  const finish = (complete) => {
    if (recorded) return;
    recorded = true;
    inFlight -= 1;
    const latencyMs = Number(process.hrtime.bigint() - start) / 1e6;
    const cpu = process.cpuUsage(cpuStart);
    const cpuUserMs = cpu.user / 1000;
    const cpuSystemMs = cpu.system / 1000;
    const heapDeltaMb = (process.memoryUsage().heapUsed - heapStart) / (1024 * 1024);
    const statusCode = res.statusCode;
    // req.route is set by Express once a route matched (parameterized, low cardinality).
    const route = req.route?.path;
    const labels = { "http.request.method": req.method, "http.route": route || requestPath, "http.response.status_code": statusCode };

    span.setAttributes({
      "experiment.latency_ms": latencyMs,
      "experiment.cpu_user_ms": cpuUserMs,
      "experiment.cpu_system_ms": cpuSystemMs,
      "experiment.heap_delta_mb": heapDeltaMb,
      "experiment.response_size_bytes": bytes,
      "experiment.http_status_code": statusCode,
      "experiment.response_complete": complete,
      "http.response.status_code": statusCode,
    });
    if (route) span.setAttribute("http.route", route);
    if (!complete) {
      span.setAttribute("experiment.incomplete_reason", "connection_closed_before_response_finish");
      span.setStatus({ code: SpanStatusCode.ERROR, message: "Response did not finish" });
    } else if (statusCode >= 500) {
      span.setStatus({ code: SpanStatusCode.ERROR, message: `HTTP ${statusCode}` });
    }
    requestCount.add(1, labels);
    requestDuration.record(latencyMs, labels);
    responseSize.record(bytes, labels);
    if (statusCode >= 400) errorCount.add(1, labels);
    requestsInFlight.add(-1, { "http.request.method": req.method });

    const traceId = span.spanContext().traceId;
    const row = [new Date().toISOString(), runId, traceId, req.method, requestPath, latencyMs.toFixed(3), cpuUserMs.toFixed(3), cpuSystemMs.toFixed(3), heapDeltaMb.toFixed(4), bytes, statusCode, complete].map(csv).join(",") + "\n";
    queueTelemetryWrite(row);
    span.end();
  };

  res.once("finish", () => finish(true));
  res.once("close", () => finish(res.writableFinished));

  const requestContext = trace.setSpan(context.active(), span).setValue(RUN_ID_CONTEXT_KEY, runId);
  context.with(requestContext, () => app(req, res));
}

// Existing experimental endpoints are preserved. The query route is an in-memory
// filter simulation, not a database benchmark.
app.use(express.json());

app.get("/api/normal", (req, res) => {
  res.json({ message: "Normal API response", users: [1, 2, 3, 4, 5] });
});

app.get("/api/cpu", (req, res) => {
  let result = 0;
  for (let i = 0; i < 5000000; i++) result += Math.sqrt(i);
  res.json({ message: "CPU-intensive operation completed", result });
});

app.get("/api/payload", (req, res) => {
  const data = [];
  for (let i = 0; i < 3000; i++) data.push({ id: i, name: `User ${i}`, description: "Sample experimental response data ".repeat(10) });
  res.json({ message: "Large payload generated", data });
});

const records = Array.from({ length: 100000 }, (_, i) => ({ id: i, value: i % 100 }));
app.get("/api/query-sim", (req, res) => {
  const result = records.filter((item) => item.value === 50);
  res.json({ message: "Query simulation completed", count: result.length });
});

// Calibration operations with known cost, for the workload client (Phase 3.5B) and
// for checking telemetry against known values. Off unless ABR_CALIBRATION=1.
if (process.env.ABR_CALIBRATION === "1") {
  const boundedMs = (value) => Math.min(Math.max(Number(value) || 0, 0), 10000);
  app.get("/__calib/noop", (req, res) => res.json({ ok: true }));
  // Non-blocking wait: many of these can be in flight at once.
  app.get("/__calib/sleep", (req, res) => {
    setTimeout(() => res.json({ ok: true }), boundedMs(req.query.ms));
  });
  // Blocking busy loop: holds the event loop for the given time.
  app.get("/__calib/cpu", (req, res) => {
    const until = performance.now() + boundedMs(req.query.ms);
    while (performance.now() < until);
    res.json({ ok: true });
  });
}

// Domain API on PostgreSQL + Redis (Phase 3.5C-2); 503 when no database is configured.
app.use(domainRoutes);

app.get("/health", (req, res) => res.json({ status: "running" }));

app.use((error, req, res, next) => {
  console.error("Request failed:", error.message);
  if (res.headersSent) return next(error);
  const status = error.status || 500;
  // Messages of deliberate client errors (HttpError) are safe to show; others are not.
  res.status(status).json({ error: status < 500 ? (error.expose ? error.message : "Invalid request") : "Internal server error" });
});

// Fail fast: a lab run must use the local lab, and a mistyped variant must not silently run
// the healthy implementation. Startup steps of the active variants (e.g. a database fault
// dropping an index) finish before the first request is accepted.
const server = http.createServer(experimentEntry);
(async () => {
  try {
    checkLabProfile();
    variants.configure(process.env.ABR_VARIANTS);
    await variants.prepareActive(database());
  } catch (error) {
    refuseToStart(error.message);
    await closeAll();
    process.exit(1);
  }
  server.listen(PORT, () => console.log(`Experimental API running at http://localhost:${PORT}; process run ID ${processRunId}`));
})();

let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`${signal} received; shutting down`);
  server.close(async () => {
    await telemetryWriteQueue;
    await runtime.stop();
    await closeAll();
    await globalThis.__otelShutdown?.();
    process.exit(0);
  });
}
process.once("SIGINT", () => shutdown("SIGINT"));
process.once("SIGTERM", () => shutdown("SIGTERM"));
// Windows has no catchable SIGTERM for child processes; a parent that spawned this
// server with an IPC channel (tests, orchestrators) sends "shutdown" instead.
process.on("message", (message) => {
  if (message === "shutdown") shutdown("IPC shutdown");
});
