// PostgreSQL pool and Redis client for the domain API (Phase 3.5C-2).
// Both are optional: without DATABASE_URL / REDIS_URL the domain endpoints answer 503,
// and the rest of the service (experiment and calibration endpoints) works as before.
const pg = require("pg");
const { createClient } = require("redis");

const LOOPBACK = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

// Lab profile: the database and cache must be the local lab (loopback, or a single-label
// Docker service name such as "postgres"). Refuse to start otherwise, so a lab run can
// never silently measure the cloud.
function checkLabProfile(env = process.env) {
  if (env.ABR_ENV_PROFILE !== "lab") return;
  for (const name of ["DATABASE_URL", "REDIS_URL"]) {
    if (!env[name]) continue;
    const host = new URL(env[name]).hostname;
    if (!LOOPBACK.has(host) && !/^[a-z0-9-]+$/i.test(host)) {
      throw new Error(`ABR_ENV_PROFILE=lab but ${name} points to a non-local host`);
    }
  }
}

let pool = null;
function database() {
  if (!process.env.DATABASE_URL) return null;
  if (!pool) {
    pool = new pg.Pool({
      connectionString: process.env.DATABASE_URL,
      // Pool size is a controlled experiment variable (and the knob for the queueing fault).
      max: Number(process.env.PG_POOL_MAX || 10),
      idleTimeoutMillis: 30000,
    });
    pool.on("error", (error) => console.error("PostgreSQL pool error:", error.message));
  }
  return pool;
}

let redisClient = null;
let redisReady = null;
async function cache() {
  if (!process.env.REDIS_URL) return null;
  if (!redisClient) {
    redisClient = createClient({ url: process.env.REDIS_URL });
    let reported = false;
    redisClient.on("error", (error) => {
      if (!reported) console.error("Redis error:", error.message);
      reported = true;
    });
    redisReady = redisClient.connect();
  }
  await redisReady;
  return redisClient;
}

async function closeAll() {
  const closing = [];
  if (pool) closing.push(pool.end().catch(() => {}));
  if (redisClient) closing.push(redisClient.quit().catch(() => {}));
  await Promise.all(closing);
}

module.exports = { checkLabProfile, database, cache, closeAll };
