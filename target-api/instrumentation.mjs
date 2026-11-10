import { NodeSDK, tracing } from "@opentelemetry/sdk-node";
import { getNodeAutoInstrumentations } from "@opentelemetry/auto-instrumentations-node";
import { PrometheusExporter } from "@opentelemetry/exporter-prometheus";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const directory = path.dirname(fileURLToPath(import.meta.url));
const dataDirectory = path.resolve(process.env.ABR_DATA_DIR || path.resolve(directory, "../data"));
const outputFile = path.resolve(process.env.ABR_SPAN_FILE || path.join(dataDirectory, "raw/spans/otel-spans.jsonl"));
const runSpanDirectory = path.resolve(process.env.ABR_SPAN_RUN_DIR || path.join(path.dirname(outputFile), "runs"));
await fs.mkdir(path.dirname(outputFile), { recursive: true });

// Shared with src/server.js: the request entry point stores the run ID under this
// context key, and RunIdSpanProcessor copies it onto every span started below it.
const RUN_ID_CONTEXT_KEY = Symbol.for("abr.experiment.run_id");
const RUN_ID_ATTRIBUTE = "experiment.run_id";

// Run IDs come from a request header, so only safe characters reach a file name.
function runFileName(runId) {
  return /^[A-Za-z0-9._-]{1,128}$/.test(runId) && !/^\.+$/.test(runId) ? `${runId}.jsonl` : null;
}

class RunIdSpanProcessor {
  onStart(span, parentContext) {
    const runId = parentContext?.getValue(RUN_ID_CONTEXT_KEY);
    if (runId) span.setAttribute(RUN_ID_ATTRIBUTE, runId);
  }
  onEnd() {}
  async forceFlush() {}
  async shutdown() {}
}

// BatchSpanProcessor may call export repeatedly. Serialize appends so concurrently
// completed batches cannot interleave or reorder JSONL records.
let writeQueue = Promise.resolve();
class FileSpanExporter {
  export(spans, callback) {
    const records = spans.map((span) => {
      const startMs = span.startTime[0] * 1000 + span.startTime[1] / 1e6;
      const endMs = span.endTime[0] * 1000 + span.endTime[1] / 1e6;
      return {
        timestamp: new Date(startMs).toISOString(),
        trace_id: span.spanContext().traceId,
        span_id: span.spanContext().spanId,
        parent_span_id: span.parentSpanContext?.spanId || null,
        span_name: span.name,
        duration_ms: Number((endMs - startMs).toFixed(3)),
        span_kind: span.kind,
        attributes: span.attributes,
        status_code: span.status.code,
        status_message: span.status.message || "",
      };
    });
    if (!records.length) {
      callback({ code: 0 });
      return;
    }
    // Every span goes to the combined file; spans carrying a run ID also go to that
    // run's own file, so one run's telemetry can be read without filtering.
    const byRun = new Map();
    for (const record of records) {
      const fileName = runFileName(String(record.attributes?.[RUN_ID_ATTRIBUTE] ?? ""));
      if (!fileName) continue;
      if (!byRun.has(fileName)) byRun.set(fileName, []);
      byRun.get(fileName).push(record);
    }
    const lines = (rows) => rows.map((record) => JSON.stringify(record)).join("\n") + "\n";
    writeQueue = writeQueue.then(async () => {
      await fs.appendFile(outputFile, lines(records));
      if (byRun.size) await fs.mkdir(runSpanDirectory, { recursive: true });
      for (const [fileName, rows] of byRun) await fs.appendFile(path.join(runSpanDirectory, fileName), lines(rows));
    });
    writeQueue.then(() => callback({ code: 0 }), (error) => {
      console.error("Span export failed:", error);
      callback({ code: 1, error });
    });
  }

  async forceFlush() {
    await writeQueue;
  }

  async shutdown() {
    await writeQueue;
  }
}

const sdk = new NodeSDK({
  spanProcessors: [new RunIdSpanProcessor(), new tracing.BatchSpanProcessor(new FileSpanExporter())],
  metricReader: new PrometheusExporter({ port: Number(process.env.METRICS_PORT || 9464) }),
  instrumentations: [
    getNodeAutoInstrumentations({
      "@opentelemetry/instrumentation-http": { enabled: true },
      "@opentelemetry/instrumentation-express": { enabled: true },
    }),
  ],
});

await sdk.start();
console.log("OpenTelemetry initialized successfully");
console.log(`Span dataset: ${outputFile} (per-run copies in ${runSpanDirectory})`);

let stopping;
async function shutdown() {
  if (!stopping) stopping = sdk.shutdown().catch((error) => console.error("OpenTelemetry shutdown failed:", error));
  await stopping;
}
globalThis.__otelShutdown = shutdown;
