// Implementation variants per operation (Phase 3.5C). Every domain operation has a
// `healthy` implementation; Phase 3.5C-3 adds faulty variants (with a severity) and the
// remediated one. The active variants are fixed when the server starts:
//
//   ABR_VARIANTS='{"users.list": "nplus1:100", "reports": "healthy"}'
//
// Operations not listed run `healthy`. The variant is ground truth for evaluation: it is
// never written to telemetry, logs or responses (enforced by tests). The campaign
// orchestrator restarts the server with a new ABR_VARIANTS for every run.

const registry = new Map(); // operation -> Set of variant base names
const preparations = new Map(); // "operation/variant" -> async (db, variant) => void
let active = new Map(); // operation -> { name, severity }

// `prepare` optionally maps variant names to a startup step (e.g. a database fault drops an
// index). Steps run once, before the server accepts requests; the lab is reset to the seed
// before every run, so they never leak into the next run.
function register(operation, variantNames, prepare = {}) {
  if (registry.has(operation)) throw new Error(`operation ${operation} registered twice`);
  if (!variantNames.includes("healthy")) throw new Error(`operation ${operation} needs a healthy variant`);
  registry.set(operation, new Set(variantNames));
  for (const [name, step] of Object.entries(prepare)) preparations.set(`${operation}/${name}`, step);
}

// Runs the startup steps of the active variants. Returns how many ran.
async function prepareActive(db) {
  let count = 0;
  for (const [operation, variant] of active) {
    const step = preparations.get(`${operation}/${variant.name}`);
    if (!step) continue;
    if (!db) throw new Error(`variant of ${operation} needs a database to prepare`);
    await step(db, variant);
    count += 1;
  }
  return count;
}

// "nplus1:100" -> { name: "nplus1", severity: 100 }; "healthy" -> { name: "healthy", severity: null }
function parseVariant(text) {
  const match = /^([a-z][a-z0-9_-]*)(?::([0-9]+(?:\.[0-9]+)?))?$/.exec(String(text));
  if (!match) throw new Error(`invalid variant "${text}"`);
  return { name: match[1], severity: match[2] === undefined ? null : Number(match[2]) };
}

// Validates the configuration against the registered operations; call after all routes
// are registered. Throws on unknown operations or variants, so a typo cannot silently run
// the healthy implementation.
function configure(raw) {
  const config = raw && raw.trim() ? JSON.parse(raw) : {};
  if (typeof config !== "object" || Array.isArray(config) || config === null) throw new Error("ABR_VARIANTS must be a JSON object");
  const next = new Map();
  for (const [operation, text] of Object.entries(config)) {
    if (!registry.has(operation)) throw new Error(`ABR_VARIANTS: unknown operation "${operation}"`);
    const variant = parseVariant(text);
    if (!registry.get(operation).has(variant.name)) {
      throw new Error(`ABR_VARIANTS: operation "${operation}" has no variant "${variant.name}" (available: ${[...registry.get(operation)].join(", ")})`);
    }
    next.set(operation, variant);
  }
  active = next;
}

function variantFor(operation) {
  return active.get(operation) || { name: "healthy", severity: null };
}

function operations() {
  return Object.fromEntries([...registry].map(([operation, names]) => [operation, [...names]]));
}

function resetForTests() {
  registry.clear();
  preparations.clear();
  active = new Map();
}

module.exports = { register, configure, prepareActive, variantFor, parseVariant, operations, resetForTests };
