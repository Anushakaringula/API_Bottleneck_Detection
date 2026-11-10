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
let active = new Map(); // operation -> { name, severity }

function register(operation, variantNames) {
  if (registry.has(operation)) throw new Error(`operation ${operation} registered twice`);
  if (!variantNames.includes("healthy")) throw new Error(`operation ${operation} needs a healthy variant`);
  registry.set(operation, new Set(variantNames));
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
  active = new Map();
}

module.exports = { register, configure, variantFor, parseVariant, operations, resetForTests };
