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
import { randomUUID } from "node:crypto";

const appDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const endpoints = ["/health", "/api/normal", "/api/cpu", "/api/payload", "/api/query-sim"];
const runId = `smoke-${randomUUID()}`;
const state = { statuses: {}, metricsStatus: null, metricsBody: "", spans: [], csv: "" };
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
    ABR_TELEMETRY_CSV: "",
  };
  const child = spawn(process.execPath, ["--import", "./instrumentation.mjs", "./src/server.js"], { cwd: appDir, env, stdio: ["ignore", "pipe", "pipe"] });
  let output = "";
  child.stdout.on("data", (chunk) => { output += chunk; });
  child.stderr.on("data", (chunk) => { output += chunk; });
  try {
    const base = `http://127.0.0.1:${port}`;
    await waitForHealth(`${base}/health`, child);
    for (const endpoint of endpoints) {
      const response = await fetch(base + endpoint, { headers: { "X-Experiment-Run-Id": runId } });
      await response.arrayBuffer();
      state.statuses[endpoint] = response.status;
    }
    const metrics = await fetch(`http://127.0.0.1:${metricsPort}/metrics`);
    state.metricsStatus = metrics.status;
    state.metricsBody = await metrics.text();
  } finally {
    const exited = new Promise((resolve) => child.once("exit", resolve));
    child.kill("SIGTERM");
    const timer = setTimeout(() => child.kill("SIGKILL"), 20000);
    await exited;
    clearTimeout(timer);
  }
  const spanFile = path.join(tempDir, "raw/spans/otel-spans.jsonl");
  state.spans = (await readFile(spanFile, "utf8")).split("\n").filter(Boolean).map((line) => JSON.parse(line));
  state.csv = await readFile(path.join(tempDir, "raw/spans/telemetry.csv"), "utf8");
  state.output = output;
});

after(async () => {
  if (tempDir) await rm(tempDir, { recursive: true, force: true });
});

const experimentSpans = () => state.spans.filter((span) => span.span_name === "experiment.http.request" && span.attributes?.["experiment.run_id"] === runId);

test("every endpoint answers HTTP 200", () => {
  for (const endpoint of endpoints) assert.equal(state.statuses[endpoint], 200, endpoint);
});

test("Prometheus endpoint serves the experiment metrics", () => {
  assert.equal(state.metricsStatus, 200);
  assert.match(state.metricsBody, /experiment_http_request/);
});

test("one experiment span per request, carrying the run ID", () => {
  const spans = experimentSpans();
  assert.equal(spans.length, endpoints.length);
  assert.deepEqual(spans.map((span) => span.attributes["url.path"]).sort(), [...endpoints].sort());
  for (const span of spans) {
    assert.match(span.trace_id, /^[0-9a-f]{32}$/);
    assert.match(span.span_id, /^[0-9a-f]{16}$/);
  }
});

test("experiment span shares its trace with the request's HTTP server span (trace correlation)", () => {
  for (const span of experimentSpans()) {
    const sameTrace = state.spans.filter((other) => other.trace_id === span.trace_id);
    const serverSpans = sameTrace.filter((other) => other.span_kind === 1 && !other.parent_span_id);
    assert.equal(serverSpans.length, 1, `exactly one HTTP server root span for ${span.attributes["url.path"]}`);
    assert.ok(span.parent_span_id, "experiment span has a parent");
    assert.ok(sameTrace.some((other) => other.span_id === span.parent_span_id), "parent is in the same trace");
  }
});

test("CSV log has one row per request for this run", () => {
  const rows = state.csv.split("\n").filter((line) => line.includes(runId));
  assert.equal(rows.length, endpoints.length);
});

// KNOWN DEFECT (open, fix planned as the first item of Phase 3.5A).
// The experiment span is meant to be the parent of the Express route-handler spans,
// but in recorded data it never has children. This test states the intended behaviour;
// it is marked todo so it is reported on every run without failing the suite.
test("experiment span is an ancestor of the route-handler span", { todo: "known defect: Express handler spans are not children of the experiment span (Phase 3.5A)" }, () => {
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
