const express = require("express");
const fs = require("node:fs/promises");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const { context, SpanKind, SpanStatusCode, trace } = require("@opentelemetry/api");

const app = express();
const PORT = Number(process.env.PORT || 3000);
const tracer = trace.getTracer("api-bottleneck-research", "1.0.0");
const meter = require("@opentelemetry/api").metrics.getMeter("api-bottleneck-research", "1.0.0");
const requestCount = meter.createCounter("experiment.http.request.count", { description: "Completed HTTP requests", unit: "{request}" });
const errorCount = meter.createCounter("experiment.http.error.count", { description: "HTTP responses with status 4xx or 5xx", unit: "{request}" });
const requestDuration = meter.createHistogram("experiment.http.request.duration", { description: "HTTP request duration", unit: "ms" });
const responseSize = meter.createHistogram("experiment.http.response.size", { description: "HTTP response body size", unit: "By" });
const requestsInFlight = meter.createUpDownCounter("experiment.http.requests.in_flight", { description: "HTTP requests currently being served", unit: "{request}" });

const dataDirectory = path.resolve(process.env.ABR_DATA_DIR || path.resolve(__dirname, "../../data"));
const metricsFile = path.resolve(process.env.ABR_TELEMETRY_CSV || path.join(dataDirectory, "raw/spans/telemetry.csv"));
let telemetryWriteQueue = Promise.resolve();
let csvInitialized = false;
const processRunId = process.env.EXPERIMENT_RUN_ID || randomUUID();

function queueTelemetryWrite(row) {
  telemetryWriteQueue = telemetryWriteQueue.then(async () => {
    if (!csvInitialized) {
      await fs.mkdir(path.dirname(metricsFile), { recursive: true });
      try {
        await fs.access(metricsFile);
      } catch {
        await fs.writeFile(metricsFile, "timestamp,run_id,scenario,method,path,latency_ms,cpu_user_ms,cpu_system_ms,heap_delta_mb,response_size_bytes,status_code,complete\n");
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

// Start this before body parsing so malformed requests also receive an experiment span.
app.use((req, res, next) => {
  const runId = String(req.get("x-experiment-run-id") || processRunId).slice(0, 128);
  const scenario = String(req.get("x-experiment-scenario") || "unspecified").slice(0, 128);
  const requestPath = req.path;
  const cpuStart = process.cpuUsage();
  const heapStart = process.memoryUsage().heapUsed;
  const start = process.hrtime.bigint();
  let bytes = 0;
  let recorded = false;

  const incomingSpan = trace.getActiveSpan();
  incomingSpan?.setAttributes({
    "experiment.run_id": runId,
    "experiment.scenario": scenario,
  });
  const span = tracer.startSpan("experiment.http.request", { kind: SpanKind.INTERNAL });
  span.setAttributes({
    "experiment.run_id": runId,
    "experiment.scenario": scenario,
    "http.request.method": req.method,
    "url.path": requestPath,
    "http.route": requestPath,
  });
  requestsInFlight.add(1, { "http.request.method": req.method });

  const countChunk = (chunk, encoding) => {
    if (chunk === undefined || chunk === null) return;
    bytes += Buffer.isBuffer(chunk) ? chunk.length : Buffer.byteLength(String(chunk), encoding || "utf8");
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
    const latencyMs = Number(process.hrtime.bigint() - start) / 1e6;
    const cpu = process.cpuUsage(cpuStart);
    const cpuUserMs = cpu.user / 1000;
    const cpuSystemMs = cpu.system / 1000;
    const heapDeltaMb = (process.memoryUsage().heapUsed - heapStart) / (1024 * 1024);
    const statusCode = res.statusCode;
    const labels = { "http.request.method": req.method, "http.route": req.route?.path || requestPath, "http.response.status_code": statusCode };

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

    const row = [new Date().toISOString(), runId, scenario, req.method, requestPath, latencyMs.toFixed(3), cpuUserMs.toFixed(3), cpuSystemMs.toFixed(3), heapDeltaMb.toFixed(4), bytes, statusCode, complete].map(csv).join(",") + "\n";
    queueTelemetryWrite(row);
    span.end();
  };

  res.once("finish", () => finish(true));
  res.once("close", () => finish(res.writableFinished));

  // Intended to make the experiment span the parent of downstream spans; it currently is not (known defect, see TELEMETRY.md and test/smoke.test.mjs).
  context.with(trace.setSpan(context.active(), span), next);
});

app.use(express.json());

// Existing experimental endpoints are preserved. The query route is an in-memory
// filter simulation, not a database benchmark.
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

app.get("/health", (req, res) => res.json({ status: "running" }));

app.use((error, req, res, next) => {
  console.error("Request failed:", error.message);
  if (res.headersSent) return next(error);
  res.status(error.status || 500).json({ error: error.status && error.status < 500 ? "Invalid request" : "Internal server error" });
});

const server = app.listen(PORT, () => console.log(`Experimental API running at http://localhost:${PORT}; process run ID ${processRunId}`));

async function shutdown(signal) {
  console.log(`${signal} received; shutting down`);
  server.close(async () => {
    await telemetryWriteQueue;
    await globalThis.__otelShutdown?.();
    process.exit(0);
  });
}
process.once("SIGINT", () => shutdown("SIGINT"));
process.once("SIGTERM", () => shutdown("SIGTERM"));
