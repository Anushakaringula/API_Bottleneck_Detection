// Building blocks for the injected faults (Phase 3.5C-3). Each fault is a realistic
// implementation mistake; its "-fixed" variant is the change a developer would make.
// Nothing here writes the variant name anywhere: the evidence must come from behaviour.
const crypto = require("node:crypto");

// Healthy index definitions, identical to db/constraints.sql.
const INDEXES = {
  orders_user_id_idx: { table: "orders", definition: "CREATE INDEX IF NOT EXISTS orders_user_id_idx ON orders (user_id)" },
  products_name_lower_idx: { table: "products", definition: 'CREATE INDEX IF NOT EXISTS products_name_lower_idx ON products ((lower(name) COLLATE "C"))' },
};

// DB_BOTTLENECK: the index an operation relies on is missing (e.g. lost in a migration).
function dropIndex(name) {
  if (!INDEXES[name]) throw new Error(`unknown index ${name}`);
  return async (db) => {
    await db.query(`DROP INDEX IF EXISTS ${name}`);
    await db.query(`ANALYZE ${INDEXES[name].table}`);
  };
}

// Remediation: recreate the index with its healthy definition, refresh statistics.
function ensureIndex(name) {
  if (!INDEXES[name]) throw new Error(`unknown index ${name}`);
  return async (db) => {
    await db.query(INDEXES[name].definition);
    await db.query(`ANALYZE ${INDEXES[name].table}`);
  };
}

// Severity with a default and bounds; severities are validated when the variant runs.
function severity(variant, fallback, min, max) {
  const value = variant.severity ?? fallback;
  if (!Number.isFinite(value) || value < min || value > max) {
    throw new Error(`severity must be between ${min} and ${max}`);
  }
  return value;
}

// CPU_BOTTLENECK: a response "signature" derived with a password-hashing function on every
// request. pbkdf2Sync runs on the event loop, so it blocks all other requests meanwhile.
function expensiveSignature(payload, kiloIterations) {
  return crypto.pbkdf2Sync(JSON.stringify(payload), "response-signature", Math.round(kiloIterations * 1000), 32, "sha256").toString("hex");
}

// Remediation: an ordinary content hash, microseconds instead of milliseconds.
function cheapSignature(payload) {
  return crypto.createHash("sha256").update(JSON.stringify(payload)).digest("hex");
}

// CACHE fault: the cache key accidentally includes a per-request value, so every lookup
// misses (and every response is written to Redis under a new key).
function brokenCacheKey(key) {
  return `${key}:${Date.now()}:${crypto.randomUUID()}`;
}

module.exports = { INDEXES, dropIndex, ensureIndex, severity, expensiveSignature, cheapSignature, brokenCacheKey };
