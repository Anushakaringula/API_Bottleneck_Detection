// Startup safety checks and the variant registry (Phase 3.5C-2). No database needed.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import os from "node:os";

const require = createRequire(import.meta.url);
const appDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const variants = require("../src/lab/variants.js");

// Starts the real server with the given environment and expects it to refuse to start.
function startupFailure(extraEnv) {
  return new Promise((resolve) => {
    const env = {
      ...process.env, PORT: "0", METRICS_PORT: "0", ABR_DATA_DIR: os.tmpdir(), ABR_RUNTIME_SAMPLE_MS: "0",
      DATABASE_URL: "", REDIS_URL: "", ABR_ENV_PROFILE: "", ABR_VARIANTS: "", ...extraEnv,
    };
    const child = spawn(process.execPath, ["./src/server.js"], { cwd: appDir, env, stdio: ["ignore", "pipe", "pipe"] });
    let output = "";
    child.stdout.on("data", (c) => { output += c; });
    child.stderr.on("data", (c) => { output += c; });
    const timer = setTimeout(() => child.kill("SIGKILL"), 15000);
    child.on("exit", (code) => { clearTimeout(timer); resolve({ code, output }); });
  });
}

test("variant strings parse into name and severity", () => {
  assert.deepEqual(variants.parseVariant("healthy"), { name: "healthy", severity: null });
  assert.deepEqual(variants.parseVariant("nplus1:100"), { name: "nplus1", severity: 100 });
  assert.deepEqual(variants.parseVariant("payload:2.5"), { name: "payload", severity: 2.5 });
  assert.throws(() => variants.parseVariant("Bad Variant"));
  assert.throws(() => variants.parseVariant("x:"));
});

test("variant configuration is validated against the registered operations", () => {
  variants.resetForTests();
  variants.register("users.list", ["healthy", "nplus1"]);
  variants.configure('{"users.list": "nplus1:50"}');
  assert.deepEqual(variants.variantFor("users.list"), { name: "nplus1", severity: 50 });
  variants.configure("");
  assert.deepEqual(variants.variantFor("users.list"), { name: "healthy", severity: null });
  assert.throws(() => variants.configure('{"orders.list": "healthy"}'), /unknown operation/);
  assert.throws(() => variants.configure('{"users.list": "cache"}'), /no variant "cache"/);
  assert.throws(() => variants.configure("[1]"), /JSON object/);
  assert.throws(() => variants.register("users.list", ["healthy"]), /registered twice/);
  assert.throws(() => variants.register("search", ["nplus1"]), /healthy/);
  variants.resetForTests();
});

test("server refuses to start with an unknown operation or variant", async () => {
  const unknownOperation = await startupFailure({ ABR_VARIANTS: '{"no.such.operation": "healthy"}' });
  assert.equal(unknownOperation.code, 1);
  assert.match(unknownOperation.output, /Refusing to start: ABR_VARIANTS: unknown operation/);
  const unknownVariant = await startupFailure({ ABR_VARIANTS: '{"users.list": "no-such-variant"}' });
  assert.equal(unknownVariant.code, 1);
  assert.match(unknownVariant.output, /has no variant "no-such-variant"/);
});

test("lab profile refuses a non-local database or cache", async () => {
  const cloudDb = await startupFailure({ ABR_ENV_PROFILE: "lab", DATABASE_URL: "postgresql://u:p@ep-example.aws.neon.tech/db" });
  assert.equal(cloudDb.code, 1);
  assert.match(cloudDb.output, /ABR_ENV_PROFILE=lab but DATABASE_URL points to a non-local host/);
  const cloudRedis = await startupFailure({ ABR_ENV_PROFILE: "lab", REDIS_URL: "redis://default:p@example.db.redis.io:15291" });
  assert.equal(cloudRedis.code, 1);
  assert.match(cloudRedis.output, /REDIS_URL points to a non-local host/);
});
