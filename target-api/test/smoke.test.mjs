// Smoke test for the target API and its OpenTelemetry output.
// Starts the real server (with instrumentation) on free ports, writes all telemetry
// into a temporary directory, and never touches the project's data/ folder.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes, randomUUID } from "node:crypto";

const appDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const endpoints = ["/health", "/api/normal", "/api/cpu", "/api/payload", "/api/query-sim"];
const runId = `smoke-${randomUUID()}`;
// The client chooses each request's trace ID and sends it as a W3C traceparent.
const clientTraces = Object.fromEntries(endpoints.map((endpoint) => [endpoint, { traceId: randomBytes(16).toString("hex"), parentId: randomBytes(8).toString("hex") }]));
const calibRunId = `smoke-calib-${randomUUID()}`;
const state = { statuses: {}, metricsStatus: null, metricsBody: "", spans: [], runSpans: [], runtime: [], csv: "", calibration: {} };
let tempDir;

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

async function waitForHealth(url, child, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`server exited early with code ${child.exitCode}`);
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // not listening yet
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("server did not become healthy in time");
}

const readJsonl = async (file) => (await readFile(file, "utf8")).split("\n").filter(Boolean).map((line) => JSON.parse(line));

before(async () => {
  tempDir = await mkdtemp(path.join(os.tmpdir(), "abr-smoke-"));
  const port = await freePort();
  const metricsPort = await freePort();
  const env = {
    ...process.env,
    PORT: String(port),
    METRICS_PORT: String(metricsPort),
    ABR_DATA_DIR: tempDir,
    ABR_SPAN_FILE: "",
    ABR_SPAN_RUN_DIR: "",
    ABR_TELEMETRY_CSV: "",
    ABR_RUNTIME_FILE: "",
    ABR_RUNTIME_SAMPLE_MS: "200",
    ABR_CALIBRATION: "1",
  };
  // The IPC channel gives a graceful shutdown on every platform (no SIGTERM handler on Windows).
  const child = spawn(process.execPath, ["--import", "./instrumentation.mjs", "./src/server.js"], { cwd: appDir, env, stdio: ["ignore", "pipe", "pipe", "ipc"] });
  let output = "";
  child.stdout.on("data", (chunk) => { output += chunk; });
  child.stderr.on("data", (chunk) => { output += chunk; });
  try {
    const base = `http://127.0.0.1:${port}`;
    await waitForHealth(`${base}/health`, child);
    for (const endpoint of endpoints) {
      const { traceId, parentId } = clientTraces[endpoint];
      const response = await fetch(base + endpoint, {
        headers: {
          "X-Experiment-Run-Id": runId,
          // Sent deliberately: the server must not copy it into telemetry.
          "X-Experiment-Scenario": "label-must-not-leak",
          traceparent: `00-${traceId}-${parentId}-01`,
        },
      });
      await response.arrayBuffer();
      state.statuses[endpoint] = response.status;
    }
    // Calibration endpoints (separate run ID so the checks above stay per-endpoint).
    const calib = { headers: { "X-Experiment-Run-Id": calibRunId } };
    const sleepStart = performance.now();
    const sleeps = await Promise.all(Array.from({ length: 5 }, () => fetch(`${base}/__calib/sleep?ms=300`, calib).then(async (r) => { await r.arrayBuffer(); return r.status; })));
    state.calibration.sleepWallMs = performance.now() - sleepStart;
    state.calibration.sleepStatuses = sleeps;
    const noop = await fetch(`${base}/__calib/noop`, calib);
    await noop.arrayBuffer();
    const cpu = await fetch(`${base}/__calib/cpu?ms=120`, calib);
    await cpu.arrayBuffer();
    state.calibration.otherStatuses = [noop.status, cpu.status];
    await new Promise((resolve) => setTimeout(resolve, 450));
    const metrics = await fetch(`http://127.0.0.1:${metricsPort}/metrics`);
    state.metricsStatus = metrics.status;
    state.metricsBody = await metrics.text();
  } finally {
    const exited = new Promise((resolve) => child.once("exit", resolve));
    child.send("shutdown");
    const timer = setTimeout(() => child.kill("SIGKILL"), 20000);
    await exited;
    clearTimeout(timer);
  }
  state.output = output;
  state.spans = await readJsonl(path.join(tempDir, "raw/spans/otel-spans.jsonl"));
  state.runSpans = await readJsonl(path.join(tempDir, "raw/spans/runs", `${runId}.jsonl`));
  state.runtime = await readJsonl(path.join(tempDir, "raw/runtime/runtime-metrics.jsonl"));
  state.csv = await readFile(path.join(tempDir, "raw/spans/telemetry.csv"), "utf8");
});

after(async () => {
  if (tempDir) await rm(tempDir, { recursive: true, force: true });
});

const experimentSpans = () => state.spans.filter((span) => span.span_name === "experiment.http.request" && span.attributes?.["experiment.run_id"] === runId);
const spanFor = (endpoint) => experimentSpans().find((span) => span.attributes["url.path"] === endpoint);

test("every endpoint answers HTTP 200", () => {
  for (const endpoint of endpoints) assert.equal(state.statuses[endpoint], 200, endpoint);
});

test("Prometheus endpoint serves the experiment metrics", () => {
  assert.equal(state.metricsStatus, 200);
  assert.match(state.metricsBody, /experiment_http_request/);
});

test("one experiment span per request, carrying the run ID and telemetry schema", () => {
  const spans = experimentSpans();
  assert.equal(spans.length, endpoints.length);
  assert.deepEqual(spans.map((span) => span.attributes["url.path"]).sort(), [...endpoints].sort());
  for (const span of spans) {
    assert.match(span.trace_id, /^[0-9a-f]{32}$/);
    assert.match(span.span_id, /^[0-9a-f]{16}$/);
    assert.equal(span.attributes["experiment.telemetry_schema"], "2");
  }
});

test("experiment span is a child of the request's HTTP server span (trace correlation)", () => {
  for (const span of experimentSpans()) {
    const sameTrace = state.spans.filter((other) => other.trace_id === span.trace_id);
    const serverSpans = sameTrace.filter((other) => other.span_kind === 1);
    assert.equal(serverSpans.length, 1, `exactly one HTTP server span for ${span.attributes["url.path"]}`);
    assert.equal(span.parent_span_id, serverSpans[0].span_id, "experiment span's parent is the server span");
  }
});

test("W3C traceparent from the client sets the request's trace ID (per-request correlation)", () => {
  for (const endpoint of endpoints) {
    const span = spanFor(endpoint);
    const { traceId, parentId } = clientTraces[endpoint];
    assert.equal(span.trace_id, traceId, endpoint);
    const server = state.spans.find((other) => other.trace_id === traceId && other.span_kind === 1);
    assert.equal(server.parent_span_id, parentId, "server span's parent is the client's span ID");
  }
});

test("experiment span is an ancestor of the route-handler span", () => {
  for (const span of experimentSpans()) {
    const byId = new Map(state.spans.filter((other) => other.trace_id === span.trace_id).map((other) => [other.span_id, other]));
    const handlers = [...byId.values()].filter((other) => other.attributes?.["express.type"] === "request_handler");
    assert.ok(handlers.length > 0, "handler span exists");
    for (const handler of handlers) {
      let current = handler;
      let found = false;
      while (current?.parent_span_id) {
        if (current.parent_span_id === span.span_id) { found = true; break; }
        current = byId.get(current.parent_span_id);
      }
      assert.ok(found, `handler span ${handler.span_name} is not under the experiment span`);
    }
  }
});

test("no scenario label reaches telemetry", () => {
  const text = JSON.stringify(state.spans) + state.csv;
  assert.doesNotMatch(text, /label-must-not-leak/);
  assert.doesNotMatch(text, /experiment\.scenario/);
  assert.doesNotMatch(state.csv.split("\n")[0], /scenario/);
});

test("every span of the run's traces carries the run ID and is in the per-run span file", () => {
  const runTraces = new Set(experimentSpans().map((span) => span.trace_id));
  const traceSpans = state.spans.filter((span) => runTraces.has(span.trace_id));
  for (const span of traceSpans) assert.equal(span.attributes?.["experiment.run_id"], runId, span.span_name);
  assert.deepEqual(state.runSpans.map((span) => span.span_id).sort(), traceSpans.map((span) => span.span_id).sort());
});

test("runtime metrics are sampled and name the active run", () => {
  assert.ok(state.runtime.length >= 1, "at least one runtime sample");
  for (const sample of state.runtime) {
    for (const key of ["event_loop_delay_p99_ms", "event_loop_utilization", "gc_count", "process_cpu_pct", "heap_used_mb", "rss_mb", "requests_in_flight", "requests_in_flight_max"]) {
      assert.ok(key in sample, `${key} present`);
    }
  }
  assert.ok(state.runtime.some((sample) => sample.run_ids.includes(runId)), "a sample lists the run ID");
});

test("calibration endpoints: sleep is non-blocking, cpu holds the server for the requested time", () => {
  assert.deepEqual(state.calibration.sleepStatuses, [200, 200, 200, 200, 200]);
  assert.deepEqual(state.calibration.otherStatuses, [200, 200]);
  // Five concurrent 300 ms sleeps finish together, not one after another (1500 ms).
  assert.ok(state.calibration.sleepWallMs < 1000, `5 concurrent sleeps took ${state.calibration.sleepWallMs} ms`);
  assert.ok(state.runtime.some((s) => s.run_ids.includes(calibRunId) && s.requests_in_flight_max >= 5), "runtime sampler saw 5 requests in flight");
  const calibSpans = state.spans.filter((s) => s.span_name === "experiment.http.request" && s.attributes?.["experiment.run_id"] === calibRunId);
  const cpuSpan = calibSpans.find((s) => s.attributes["url.path"] === "/__calib/cpu");
  assert.ok(cpuSpan.attributes["experiment.latency_ms"] >= 120, "cpu span covers the busy loop");
  assert.ok(cpuSpan.attributes["experiment.cpu_user_ms"] + cpuSpan.attributes["experiment.cpu_system_ms"] >= 60, "busy loop shows as process CPU");
});

test("CSV log has one row per request for this run, with the request's trace ID", () => {
  assert.equal(state.csv.split("\n")[0], "timestamp,run_id,trace_id,method,path,latency_ms,cpu_user_ms,cpu_system_ms,heap_delta_mb,response_size_bytes,status_code,complete");
  const rows = state.csv.split("\n").filter((line) => line.includes(runId));
  assert.equal(rows.length, endpoints.length);
  for (const endpoint of endpoints) {
    assert.ok(rows.some((row) => row.includes(clientTraces[endpoint].traceId)), `CSV row for ${endpoint} has its trace ID`);
  }
});
