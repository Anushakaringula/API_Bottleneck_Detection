// Data access for the dashboard: reads the pipeline's result files, nothing else.
// Every number on screen comes from a file under data/ (path shown on each page).
import fs from "node:fs";
import path from "node:path";

export const DATA_DIR = path.resolve(/*turbopackIgnore: true*/ process.env.ABR_DATA_DIR || path.join(process.cwd(), "..", "data"));

function readJson(...parts) {
  const file = path.join(/*turbopackIgnore: true*/ DATA_DIR, ...parts);
  return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : null;
}

function readJsonl(...parts) {
  const file = path.join(/*turbopackIgnore: true*/ DATA_DIR, ...parts);
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line));
}

function listDirs(...parts) {
  const dir = path.join(/*turbopackIgnore: true*/ DATA_DIR, ...parts);
  return fs.existsSync(dir) ? fs.readdirSync(dir, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name) : [];
}

/** Campaigns with detection results, newest first (main campaigns before pilots). */
export function campaigns() {
  const ids = listDirs("results", "detection");
  const rank = (id) => (id.startsWith("main") ? 0 : id.startsWith("heldout") ? 1 : 2);
  return ids.sort((a, b) => rank(a) - rank(b) || b.localeCompare(a));
}

export function campaign(id) {
  const summary = readJson("results", "campaigns", id, "summary.json");
  const detection = readJson("results", "detection", id, "summary.json");
  const verdicts = readJsonl("results", "detection", id, "verdicts.jsonl");
  const diagnosis = readJson("results", "diagnosis", id, "summary.json");
  const verification = readJson("results", "verification", id, "summary.json");
  const runs = summary?.runs ?? [];
  const verdictById = Object.fromEntries(verdicts.map((v) => [v.experiment_id, v]));
  const diagnosisById = Object.fromEntries((diagnosis?.rules?.per_run ?? []).map((d) => [d.experiment_id, d]));
  const routeByOperation = {};
  for (const run of runs) {
    const route = verdictById[run.experiment_id]?.route;
    if (route) routeByOperation[run.operation] = route;
  }
  return { id, summary, detection, verdicts, verdictById, diagnosis, diagnosisById, verification, runs, routeByOperation };
}

export function loops() {
  return listDirs("results", "loops").sort().reverse().map((id) => readJson("results", "loops", id, "report.json")).filter(Boolean);
}

export function loop(id) {
  return readJson("results", "loops", id, "report.json");
}

export const fmt = {
  ms: (v) => (v == null ? "–" : v >= 100 ? `${v.toFixed(0)} ms` : `${v.toFixed(2)} ms`),
  pct: (v, d = 1) => (v == null ? "–" : `${(100 * v).toFixed(d)} %`),
  num: (v, d = 3) => (v == null ? "–" : Number(v).toFixed(d)),
  ratio: (v) => (v == null ? "–" : `×${v.toFixed(2)}`),
  ci: (c) => (!c ? "" : `${(100 * c[0]).toFixed(1)}–${(100 * c[1]).toFixed(1)} %`),
  cls: (c) => (c ? c.replaceAll("_", " ").toLowerCase().replace(/^\w/, (m) => m.toUpperCase()) : "–"),
};
