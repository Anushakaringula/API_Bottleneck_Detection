// Integration test against the local lab (Phase 3.5C-2). Skipped unless ABR_LAB_TESTS=1.
//   scripts/lab-up.sh && scripts/lab-seed.sh small
//   cd target-api && npm run test:lab
// Places one order in the lab database; scripts/lab-reset.sh restores the seed.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes, randomUUID } from "node:crypto";

const enabled = process.env.ABR_LAB_TESTS === "1";
const skip = enabled ? false : "set ABR_LAB_TESTS=1 (and run against a seeded lab) to enable";
const appDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const runId = `labtest-${randomUUID()}`;
// Every operation explicitly set, so the test also proves the variant names never leak.
const VARIANTS = JSON.stringify({
  "users.list": "healthy", "users.get": "healthy", "products.list": "healthy", "products.get": "healthy",
  "orders.list": "healthy", "orders.create": "healthy", search: "healthy", reports: "healthy", dashboard: "healthy",
});
const calls = {};
let spans = [];
let csv = "";
let tempDir;

const freePort = () => new Promise((resolve) => {
  const server = net.createServer();
  server.listen(0, "127.0.0.1", () => { const { port } = server.address(); server.close(() => resolve(port)); });
});

before(async () => {
  if (!enabled) return;
  tempDir = await mkdtemp(path.join(os.tmpdir(), "abr-labtest-"));
  const port = await freePort();
  const env = {
    ...process.env, PORT: String(port), METRICS_PORT: String(await freePort()), ABR_DATA_DIR: tempDir,
    ABR_SPAN_FILE: "", ABR_SPAN_RUN_DIR: "", ABR_TELEMETRY_CSV: "", ABR_RUNTIME_FILE: "", ABR_RUNTIME_SAMPLE_MS: "0",
    ABR_ENV_PROFILE: "lab", ABR_VARIANTS: VARIANTS,
  };
  const child = spawn(process.execPath, ["--import", "./instrumentation.mjs", "./src/server.js"], { cwd: appDir, env, stdio: ["ignore", "pipe", "pipe", "ipc"] });
  let output = "";
  child.stdout.on("data", (c) => { output += c; });
  child.stderr.on("data", (c) => { output += c; });
  const base = `http://127.0.0.1:${port}`;
  try {
    for (let i = 0; i < 100; i++) {
      if (child.exitCode !== null) throw new Error(`server exited: ${output}`);
      try { if ((await fetch(`${base}/health`)).ok) break; } catch { /* starting */ }
      await new Promise((r) => setTimeout(r, 200));
    }
    const call = async (name, method, url, body) => {
      const traceId = randomBytes(16).toString("hex");
      const response = await fetch(base + url, {
        method,
        headers: { "X-Experiment-Run-Id": runId, traceparent: `00-${traceId}-${randomBytes(8).toString("hex")}-01`, ...(body ? { "Content-Type": "application/json" } : {}) },
        body: body ? JSON.stringify(body) : undefined,
      });
      calls[name] = { status: response.status, body: await response.json(), traceId };
    };
    await call("users.list", "GET", "/api/users?limit=5");
    await call("users.get", "GET", "/api/users/1");
    await call("users.get.missing", "GET", "/api/users/999999999");
    await call("users.list.bad", "GET", "/api/users?limit=abc");
    await call("products.list", "GET", "/api/products?limit=5&category=3");
    await call("products.get.miss", "GET", "/api/products/7");
    await call("products.get.hit", "GET", "/api/products/7");
    await call("orders.list", "GET", "/api/orders?user_id=1");
    await call("orders.create", "POST", "/api/orders", { user_id: 1, items: [{ product_id: 1, quantity: 2 }, { product_id: 2 }] });
    await call("search", "GET", "/api/search?q=smart");
    await call("reports", "GET", "/api/reports?days=30");
    await call("dashboard", "GET", "/api/dashboard");
  } finally {
    const exited = new Promise((r) => child.once("exit", r));
    child.send("shutdown");
    const timer = setTimeout(() => child.kill("SIGKILL"), 20000);
    await exited;
    clearTimeout(timer);
  }
  spans = (await readFile(path.join(tempDir, "raw/spans/otel-spans.jsonl"), "utf8")).split("\n").filter(Boolean).map((l) => JSON.parse(l));
  csv = await readFile(path.join(tempDir, "raw/spans/telemetry.csv"), "utf8");
});

after(async () => {
  if (tempDir) await rm(tempDir, { recursive: true, force: true });
});

const traceSpans = (name) => spans.filter((s) => s.trace_id === calls[name].traceId);
const dbSystem = (s) => s.attributes?.["db.system.name"] ?? s.attributes?.["db.system"];
const isPg = (s) => dbSystem(s) === "postgresql";
const isRedis = (s) => dbSystem(s) === "redis";

function underExperimentSpan(trace, span) {
  const byId = new Map(trace.map((s) => [s.span_id, s]));
  const experiment = trace.find((s) => s.span_name === "experiment.http.request");
  for (let current = span; current?.parent_span_id; current = byId.get(current.parent_span_id)) {
    if (current.parent_span_id === experiment?.span_id) return true;
  }
  return false;
}

test("every domain operation answers with data", { skip }, () => {
  assert.equal(calls["users.list"].status, 200);
  assert.equal(calls["users.list"].body.users.length, 5);
  assert.ok(calls["users.list"].body.users.every((u) => Number.isInteger(u.order_count)));
  assert.equal(calls["users.get"].status, 200);
  assert.equal(calls["users.get"].body.user.id, "1");
  assert.equal(calls["products.list"].status, 200);
  assert.ok(calls["products.list"].body.products.every((p) => p.category_id === 3));
  assert.equal(calls["products.get.miss"].status, 200);
  assert.deepEqual(calls["products.get.hit"].body, calls["products.get.miss"].body);
  assert.equal(calls["orders.list"].status, 200);
  assert.equal(calls["orders.create"].status, 201);
  assert.equal(calls["orders.create"].body.order.items, 2);
  assert.equal(calls.search.status, 200);
  assert.ok(calls.search.body.results.length > 0);
  assert.ok(calls.search.body.results.every((r) => r.name.toLowerCase().startsWith("smart")));
  assert.equal(calls.reports.status, 200);
  assert.ok(calls.reports.body.categories.length > 0);
  assert.equal(calls.dashboard.status, 200);
  assert.equal(calls.dashboard.body.latest_orders.length, 10);
});

test("client errors are reported with their reason", { skip }, () => {
  assert.equal(calls["users.get.missing"].status, 404);
  assert.deepEqual(calls["users.get.missing"].body, { error: "user not found" });
  assert.equal(calls["users.list.bad"].status, 400);
  assert.match(calls["users.list.bad"].body.error, /integer between 1 and 100/);
});

test("PostgreSQL spans are recorded under the experiment span of every database operation", { skip }, () => {
  for (const name of ["users.list", "users.get", "products.list", "products.get.miss", "orders.list", "orders.create", "search", "reports", "dashboard"]) {
    const trace = traceSpans(name);
    const pg = trace.filter(isPg);
    assert.ok(pg.length > 0, `${name}: PostgreSQL spans`);
    for (const span of pg) assert.ok(underExperimentSpan(trace, span), `${name}: ${span.span_name} under the experiment span`);
  }
  // dashboard runs four queries (one of them cached on first use)
  assert.ok(traceSpans("dashboard").filter(isPg).length >= 4);
});

test("Redis cache-aside: first product read misses (database), second hits (no database)", { skip }, () => {
  const miss = traceSpans("products.get.miss");
  const hit = traceSpans("products.get.hit");
  assert.ok(miss.some(isRedis), "miss: Redis spans");
  assert.ok(miss.some(isPg), "miss: database query");
  assert.ok(hit.some(isRedis), "hit: Redis span");
  assert.equal(hit.filter(isPg).length, 0, "hit: no database query");
  for (const span of [...miss, ...hit].filter(isRedis)) assert.ok(underExperimentSpan(span.trace_id === calls["products.get.miss"].traceId ? miss : hit, span));
});

test("active variants never reach telemetry", { skip }, () => {
  const text = JSON.stringify(spans) + csv;
  assert.doesNotMatch(text, /healthy/);
  assert.doesNotMatch(text, /ABR_VARIANTS|users\.list|products\.get/);
});
