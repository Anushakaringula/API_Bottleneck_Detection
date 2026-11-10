// Fault-activation checks (Phase 3.5C-3 gate). For every injected fault in every operation:
// reset the lab, start the target API with the faulty (then the fixed) variant, send a few
// requests, read back the telemetry, and check that the fault's mechanism is present when
// switched on and gone when fixed. Also a severity ladder for the faults that have one.
//
//   scripts/lab-up.sh && scripts/lab-seed.sh large
//   cd target-api && npm run lab:fault-checks
//
// Report: data/results/fault-checks/<timestamp>/report.{json,md}. Exit 0 = all checks pass.
// This checks mechanisms, not detection: it reads ground truth and is never part of a model.
// The API runs on the host here (lab profile); the mechanisms do not depend on that.
import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes } from "node:crypto";
import pg from "pg";

const appDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoDir = path.resolve(appDir, "..");
const dataDir = path.resolve(process.env.ABR_DATA_DIR || path.join(repoDir, "data"));
const MEASURED = 8;
const WARMUP = 2;

const median = (xs) => { const s = [...xs].sort((a, b) => a - b); const m = Math.floor(s.length / 2); return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const freePort = () => new Promise((resolve) => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => resolve(port)); }); });

function labReset() {
  const result = spawnSync("bash", [path.join(repoDir, "scripts", "lab-reset.sh")], { encoding: "utf8" });
  if (result.status !== 0) throw new Error(`lab-reset failed: ${result.stderr || result.stdout}`);
}

const REQUESTS = {
  "users.list": () => ({ path: "/api/users?offset=0" }),
  "users.get": (i) => ({ path: `/api/users/${1 + i}` }),
  "products.list": () => ({ path: "/api/products?limit=20" }),
  "products.get": () => ({ path: "/api/products/7" }),
  "orders.list": (i) => ({ path: `/api/orders?user_id=${1 + i * 7}` }),
  search: (i) => ({ path: `/api/search?q=${["smart", "pro", "eco", "mini"][i % 4]}` }),
  reports: () => ({ path: "/api/reports?days=30" }),
  dashboard: () => ({ path: "/api/dashboard" }),
};

// Runs one variant of one operation and returns per-request telemetry (measured requests only).
async function measure(operation, variant) {
  labReset();
  const tempDir = await mkdtemp(path.join(os.tmpdir(), "abr-faultcheck-"));
  const port = await freePort();
  const env = {
    ...process.env, PORT: String(port), METRICS_PORT: String(await freePort()), ABR_DATA_DIR: tempDir,
    ABR_SPAN_FILE: "", ABR_SPAN_RUN_DIR: "", ABR_TELEMETRY_CSV: "", ABR_RUNTIME_FILE: "", ABR_RUNTIME_SAMPLE_MS: "0",
    ABR_ENV_PROFILE: "lab", ABR_VARIANTS: JSON.stringify({ [operation]: variant }),
  };
  const child = spawn(process.execPath, ["--import", "./instrumentation.mjs", "./src/server.js"], { cwd: appDir, env, stdio: ["ignore", "pipe", "pipe", "ipc"] });
  let output = "";
  child.stdout.on("data", (c) => { output += c; });
  child.stderr.on("data", (c) => { output += c; });
  const base = `http://127.0.0.1:${port}`;
  const sent = [];
  try {
    for (let i = 0; i < 150; i++) {
      if (child.exitCode !== null) throw new Error(`server exited (${operation}=${variant}): ${output.trim()}`);
      try { if ((await fetch(`${base}/health`)).ok) break; } catch { /* starting */ }
      await new Promise((r) => setTimeout(r, 200));
    }
    for (let i = 0; i < WARMUP + MEASURED; i++) {
      const traceId = randomBytes(16).toString("hex");
      const { path: url } = REQUESTS[operation](i);
      const response = await fetch(base + url, { headers: { "X-Experiment-Run-Id": "fault-check", traceparent: `00-${traceId}-${randomBytes(8).toString("hex")}-01` } });
      const body = await response.text();
      if (response.status !== 200) throw new Error(`${operation}=${variant} ${url}: HTTP ${response.status} ${body.slice(0, 200)}`);
      if (i >= WARMUP) sent.push({ traceId, body });
    }
  } finally {
    const exited = new Promise((r) => child.once("exit", r));
    if (child.exitCode === null) child.send("shutdown");
    const timer = setTimeout(() => child.kill("SIGKILL"), 20000);
    await exited;
    clearTimeout(timer);
  }
  // Index state after the run (before the next reset): does the database fault really apply?
  const client = new pg.Client({ connectionString: process.env.DATABASE_URL });
  await client.connect();
  const indexes = (await client.query("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")).rows.map((r) => r.indexname);
  await client.end();
  const spans = (await readFile(path.join(tempDir, "raw/spans/otel-spans.jsonl"), "utf8")).split("\n").filter(Boolean).map((l) => JSON.parse(l));
  await rm(tempDir, { recursive: true, force: true });
  const system = (s) => s.attributes?.["db.system.name"] ?? s.attributes?.["db.system"];
  const requests = sent.map(({ traceId, body }) => {
    const trace = spans.filter((s) => s.trace_id === traceId);
    const experiment = trace.find((s) => s.span_name === "experiment.http.request");
    const queries = trace.filter((s) => system(s) === "postgresql" && s.span_kind === 2 && !/connect/i.test(s.span_name));
    return {
      server_ms: experiment.attributes["experiment.latency_ms"],
      cpu_ms: experiment.attributes["experiment.cpu_user_ms"] + experiment.attributes["experiment.cpu_system_ms"],
      bytes: experiment.attributes["experiment.response_size_bytes"],
      db_queries: queries.length,
      db_ms: queries.reduce((sum, s) => sum + s.duration_ms, 0),
      redis_calls: trace.filter((s) => system(s) === "redis").length,
      body,
    };
  });
  const summary = {};
  for (const key of ["server_ms", "cpu_ms", "bytes", "db_queries", "db_ms", "redis_calls"]) summary[key] = median(requests.map((r) => r[key]));
  summary.min_db_queries = Math.min(...requests.map((r) => r.db_queries));
  summary.max_db_queries = Math.max(...requests.map((r) => r.db_queries));
  return { operation, variant, summary, indexes, bodies: requests.map((r) => r.body) };
}

// Each check: run faulty and fixed, then judge the mechanism. `same` = the fixed variant must
// return byte-identical responses to the faulty one (fair before/after comparison).
const CHECKS = [
  { fault: "nplus1", operation: "users.list", severity: 20, same: true,
    judge: (f, x) => ({ "faulty: 1 + N queries per request": f.summary.min_db_queries >= 21, "fixed: one query per request": x.summary.max_db_queries === 1 }) },
  { fault: "nplus1", operation: "users.get", severity: 10, same: true,
    judge: (f, x) => ({ "faulty: >= 2 + N queries per request": f.summary.min_db_queries >= 12, "fixed: two queries per request": x.summary.max_db_queries === 2 }) },
  { fault: "noindex", operation: "orders.list", same: true,
    judge: (f, x) => ({ "faulty: index orders_user_id_idx absent": !f.indexes.includes("orders_user_id_idx"), "fixed: index present": x.indexes.includes("orders_user_id_idx"), "faulty: database time >= 5x fixed": f.summary.db_ms >= 5 * x.summary.db_ms }) },
  { fault: "noindex", operation: "search", same: true,
    judge: (f, x) => ({ "faulty: index products_name_lower_idx absent": !f.indexes.includes("products_name_lower_idx"), "fixed: index present": x.indexes.includes("products_name_lower_idx"), "faulty: database time >= 5x fixed": f.summary.db_ms >= 5 * x.summary.db_ms }) },
  { fault: "payload", operation: "products.list", severity: 2000,
    judge: (f, x) => ({ "faulty: response >= 20x fixed": f.summary.bytes >= 20 * x.summary.bytes, "same number of queries": f.summary.db_queries === x.summary.db_queries }) },
  { fault: "payload", operation: "search", severity: 1000,
    judge: (f, x) => ({ "faulty: response >= 20x fixed": f.summary.bytes >= 20 * x.summary.bytes, "same number of queries": f.summary.db_queries === x.summary.db_queries }) },
  { fault: "cache", operation: "products.get", same: true,
    judge: (f, x) => ({ "faulty: every repeat request queries the database": f.summary.min_db_queries >= 1, "fixed: repeat requests served from Redis": x.summary.max_db_queries === 0, "both use Redis": f.summary.redis_calls >= 1 && x.summary.redis_calls >= 1 }) },
  { fault: "cache", operation: "reports", same: true,
    judge: (f, x) => ({ "faulty: every repeat request queries the database": f.summary.min_db_queries >= 1, "fixed: repeat requests served from Redis": x.summary.max_db_queries === 0, "faulty: server time >= 5x fixed": f.summary.server_ms >= 5 * x.summary.server_ms }) },
  { fault: "cpu", operation: "products.get", severity: 100,
    judge: (f, x) => ({ "faulty: >= 10 ms more server time": f.summary.server_ms - x.summary.server_ms >= 10, "faulty: CPU time >= 5x fixed": f.summary.cpu_ms >= 5 * Math.max(x.summary.cpu_ms, 0.5), "same database queries": f.summary.db_queries === x.summary.db_queries }) },
  { fault: "cpu", operation: "dashboard", severity: 100,
    judge: (f, x) => ({ "faulty: >= 10 ms more server time": f.summary.server_ms - x.summary.server_ms >= 10, "faulty: CPU time >= 5x fixed": f.summary.cpu_ms >= 5 * Math.max(x.summary.cpu_ms, 0.5), "same database queries": f.summary.db_queries === x.summary.db_queries }) },
];

// Severity ladders: the effect must grow with severity.
const LADDERS = [
  { fault: "nplus1", operation: "users.list", levels: [10, 50, 100], metric: "db_queries" },
  { fault: "payload", operation: "products.list", levels: [500, 2000, 5000], metric: "bytes" },
  { fault: "cpu", operation: "products.get", levels: [20, 100, 300], metric: "server_ms" },
];

function fmt(n) { return typeof n === "number" ? (Number.isInteger(n) ? String(n) : n.toFixed(2)) : String(n); }

async function main() {
  if (!process.env.DATABASE_URL) {
    console.error("fault-checks: DATABASE_URL is not set (use npm run lab:fault-checks, which loads .env.lab)");
    return 2;
  }
  // --only <fault>: re-check one fault (no report-wide verdict is implied for the others).
  const onlyIndex = process.argv.indexOf("--only");
  const only = onlyIndex >= 0 ? process.argv[onlyIndex + 1] : null;
  const started = new Date();
  const results = [];
  for (const check of CHECKS.filter((c) => !only || c.fault === only)) {
    const suffix = check.severity ? `:${check.severity}` : "";
    const faulty = await measure(check.operation, `${check.fault}${suffix}`);
    const fixed = await measure(check.operation, `${check.fault}-fixed${suffix}`);
    const healthy = await measure(check.operation, "healthy");
    const criteria = check.judge(faulty, fixed);
    if (check.same) criteria["fixed returns the same responses as faulty"] = JSON.stringify(faulty.bodies) === JSON.stringify(fixed.bodies);
    const pass = Object.values(criteria).every(Boolean);
    results.push({ fault: check.fault, operation: check.operation, severity: check.severity ?? null, criteria, pass, faulty: faulty.summary, fixed: fixed.summary, healthy: healthy.summary });
    console.log(`${pass ? "PASS" : "FAIL"}  ${check.fault.padEnd(8)} ${check.operation.padEnd(14)} server ms faulty/fixed/healthy ${fmt(faulty.summary.server_ms)} / ${fmt(fixed.summary.server_ms)} / ${fmt(healthy.summary.server_ms)}${pass ? "" : "  " + JSON.stringify(criteria)}`);
  }
  const ladders = [];
  for (const ladder of LADDERS.filter((l) => !only || l.fault === only)) {
    const values = [];
    for (const level of ladder.levels) values.push((await measure(ladder.operation, `${ladder.fault}:${level}`)).summary[ladder.metric]);
    const pass = values.every((v, i) => i === 0 || v > values[i - 1]);
    ladders.push({ ...ladder, values, pass });
    console.log(`${pass ? "PASS" : "FAIL"}  ladder ${ladder.fault}/${ladder.operation} ${ladder.metric}: ${ladder.levels.map((l, i) => `${l} -> ${fmt(values[i])}`).join(", ")}`);
  }
  labReset();
  const allPass = results.every((r) => r.pass) && ladders.every((l) => l.pass);
  const report = { created_at: started.toISOString(), only_fault: only, measured_requests_per_variant: MEASURED, warmup_requests: WARMUP, api_location: "host (lab profile)", checks: results, ladders, pass: allPass };
  const outDir = path.join(dataDir, "results", "fault-checks", started.toISOString().replace(/[:.]/g, "-"));
  await mkdir(outDir, { recursive: true });
  await writeFile(path.join(outDir, "report.json"), JSON.stringify(report, null, 2) + "\n");
  const lines = [
    `# Fault-activation checks ${report.created_at}`, "",
    `Medians over ${MEASURED} measured requests per variant (after ${WARMUP} warm-up), lab reset before every variant.`, "",
    "| Fault | Operation | Severity | Server ms faulty / fixed / healthy | DB queries faulty / fixed | DB ms faulty / fixed | Bytes faulty / fixed | CPU ms faulty / fixed | Result |",
    "|---|---|---|---|---|---|---|---|---|",
    ...results.map((r) => `| ${r.fault} | ${r.operation} | ${r.severity ?? "-"} | ${fmt(r.faulty.server_ms)} / ${fmt(r.fixed.server_ms)} / ${fmt(r.healthy.server_ms)} | ${fmt(r.faulty.db_queries)} / ${fmt(r.fixed.db_queries)} | ${fmt(r.faulty.db_ms)} / ${fmt(r.fixed.db_ms)} | ${fmt(r.faulty.bytes)} / ${fmt(r.fixed.bytes)} | ${fmt(r.faulty.cpu_ms)} / ${fmt(r.fixed.cpu_ms)} | ${r.pass ? "PASS" : "FAIL"} |`),
    "", "| Severity ladder | Metric | Levels -> values | Result |", "|---|---|---|---|",
    ...ladders.map((l) => `| ${l.fault} / ${l.operation} | ${l.metric} | ${l.levels.map((lv, i) => `${lv} -> ${fmt(l.values[i])}`).join(", ")} | ${l.pass ? "PASS" : "FAIL"} |`),
    "", `**Overall: ${allPass ? "PASS" : "FAIL"}**`, "",
  ];
  await writeFile(path.join(outDir, "report.md"), lines.join("\n"));
  console.log(`overall: ${allPass ? "PASS" : "FAIL"}\nreport: ${path.join(outDir, "report.md")}`);
  return allPass ? 0 : 1;
}

process.exitCode = await main();
