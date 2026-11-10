// Helpers shared by the domain routes.
const variants = require("../lab/variants");
const { database, cache } = require("../db");

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
    this.expose = true;
  }
}

// Registers an operation with its implementations ({ healthy, ...faults }) and returns the
// Express handler that runs the variant selected at startup. Implementations receive
// (req, res, { db, variant }) and send the response themselves.
function operation(name, implementations) {
  variants.register(name, Object.keys(implementations));
  return async (req, res, next) => {
    const db = database();
    if (!db) {
      res.status(503).json({ error: "database not configured" });
      return;
    }
    try {
      const variant = variants.variantFor(name);
      await implementations[variant.name](req, res, { db, variant });
    } catch (error) {
      next(error);
    }
  };
}

function intParam(value, fallback, min, max) {
  if (value === undefined || value === "") return fallback;
  const number = Number(value);
  if (!Number.isInteger(number) || number < min || number > max) {
    throw new HttpError(400, `parameter must be an integer between ${min} and ${max}`);
  }
  return number;
}

function idParam(value) {
  return intParam(value, undefined, 1, Number.MAX_SAFE_INTEGER);
}

// Cache-aside with a TTL. A Redis failure falls back to the database (counted as a miss),
// so cache problems show up as latency, not as errors.
async function cached(key, ttlSeconds, load) {
  let client = null;
  try {
    client = await cache();
    if (client) {
      const hit = await client.get(key);
      if (hit !== null) return JSON.parse(hit);
    }
  } catch {
    client = null;
  }
  const value = await load();
  if (client) {
    try {
      await client.set(key, JSON.stringify(value), { EX: ttlSeconds });
    } catch {
      // ignore: the response is still correct
    }
  }
  return value;
}

module.exports = { HttpError, operation, intParam, idParam, cached };
