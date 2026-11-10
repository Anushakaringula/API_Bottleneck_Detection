// Lab probe (Phase 3.5C-1): proves the experiment database and cache are local and quiet.
// Times SELECT 1 (PostgreSQL) and PING (Redis) over the same drivers the API uses, and
// writes a report to data/results/lab-probe/<timestamp>.json.
//
//   npm run lab:probe                      (reads ../.env.lab)
//   node --env-file=<file> scripts/lab-probe.mjs [--samples 500] [--max-p99-ms 2] [--allow-remote]
//
// Exit status: 0 = gate passed, 1 = gate failed, 2 = could not run.
// Connection strings are never printed.
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import pg from "pg";
import { createClient } from "redis";

const args = process.argv.slice(2);
const option = (name, fallback) => {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : fallback;
};
const samples = Number(option("--samples", 500));
const maxP99 = Number(option("--max-p99-ms", 2));
const allowRemote = args.includes("--allow-remote");
const LOOPBACK = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

const directory = path.dirname(fileURLToPath(import.meta.url));
const dataDirectory = path.resolve(process.env.ABR_DATA_DIR || path.resolve(directory, "../../data"));

function percentile(values, q) {
  const sorted = [...values].sort((a, b) => a - b);
  const rank = (sorted.length - 1) * q;
  const lo = Math.floor(rank);
  const hi = Math.ceil(rank);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (rank - lo);
}
const summary = (values) => ({
  samples: values.length,
  p50_ms: percentile(values, 0.5),
  p95_ms: percentile(values, 0.95),
  p99_ms: percentile(values, 0.99),
  max_ms: Math.max(...values),
});

async function time(operation, n) {
  for (let i = 0; i < Math.min(50, n); i++) await operation(); // warm-up, not recorded
  const values = [];
  for (let i = 0; i < n; i++) {
    const start = performance.now();
    await operation();
    values.push(performance.now() - start);
  }
  return values;
}

// Local = loopback (probe on the host) or a single-label name such as "postgres", which
// only resolves on the lab's private Docker network. Cloud hosts always contain dots.
function target(name, url) {
  if (!url) throw new Error(`${name} is not set (use npm run lab:probe, which loads .env.lab)`);
  const host = new URL(url).hostname;
  const local = LOOPBACK.has(host) || /^[a-z0-9-]+$/i.test(host);
  if (!local && !allowRemote) throw new Error(`${name} points to a non-local host; the lab profile must use the local lab (or pass --allow-remote)`);
  return { local, host: local ? host : "<remote>" };
}

async function main() {
  const report = {
    created_at: new Date().toISOString(),
    profile: process.env.ABR_ENV_PROFILE || "unspecified",
    // "host": from the machine's own shell (through Docker's port forwarding on Windows/macOS);
    // "lab-network": from a container on the lab network, where the target API runs in experiments.
    network_path: process.env.ABR_PROBE_PATH || "host",
    samples_per_operation: samples,
    gate: { max_p99_ms: maxP99 },
    node_version: process.version,
  };
  let postgresTarget;
  let redisTarget;
  try {
    postgresTarget = target("DATABASE_URL", process.env.DATABASE_URL);
    redisTarget = target("REDIS_URL", process.env.REDIS_URL);
  } catch (error) {
    console.error(`lab-probe: ${error.message}`);
    return 2;
  }

  const client = new pg.Client({ connectionString: process.env.DATABASE_URL });
  const redis = createClient({ url: process.env.REDIS_URL });
  redis.on("error", () => {});
  try {
    await client.connect();
    await redis.connect();
  } catch (error) {
    console.error(`lab-probe: cannot connect (${error.code || error.message}); is the lab running? scripts/lab-up.sh`);
    await client.end().catch(() => {});
    await redis.disconnect().catch(() => {});
    return 2;
  }
  try {
    const version = (await client.query("SHOW server_version")).rows[0].server_version;
    const redisVersion = /redis_version:(\S+)/.exec(await redis.info("server"))?.[1];
    report.postgres = { ...postgresTarget, server_version: version, select_1: summary(await time(() => client.query("SELECT 1"), samples)) };
    report.redis = { ...redisTarget, server_version: redisVersion, ping: summary(await time(() => redis.ping(), samples)) };
  } finally {
    await client.end().catch(() => {});
    await redis.quit().catch(() => {});
  }
  report.checks = {
    postgres_is_local: report.postgres.local,
    redis_is_local: report.redis.local,
    postgres_p99_within_limit: report.postgres.select_1.p99_ms <= maxP99,
    redis_p99_within_limit: report.redis.ping.p99_ms <= maxP99,
  };
  report.pass = Object.values(report.checks).every(Boolean);

  const outputDirectory = path.join(dataDirectory, "results", "lab-probe");
  await fs.mkdir(outputDirectory, { recursive: true });
  const file = path.join(outputDirectory, `${report.created_at.replace(/[:.]/g, "-")}.json`);
  await fs.writeFile(file, JSON.stringify(report, null, 2) + "\n");

  const row = (name, s) => `${name.padEnd(18)} p50 ${s.p50_ms.toFixed(3)}  p95 ${s.p95_ms.toFixed(3)}  p99 ${s.p99_ms.toFixed(3)}  max ${s.max_ms.toFixed(3)} ms`;
  console.log(`PostgreSQL ${report.postgres.server_version} (${report.postgres.host}), Redis ${report.redis.server_version} (${report.redis.host}), path: ${report.network_path}, ${samples} samples each`);
  console.log(row("postgres SELECT 1", report.postgres.select_1));
  console.log(row("redis PING", report.redis.ping));
  console.log(`gate (local, p99 <= ${maxP99} ms): ${report.pass ? "PASS" : "FAIL"}  ${JSON.stringify(report.checks)}`);
  console.log(`report: ${file}`);
  return report.pass ? 0 : 1;
}

process.exitCode = await main();
