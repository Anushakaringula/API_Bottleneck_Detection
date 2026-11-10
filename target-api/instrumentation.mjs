import { NodeSDK } from "@opentelemetry/sdk-node";
import { getNodeAutoInstrumentations } from "@opentelemetry/auto-instrumentations-node";
import { PrometheusExporter } from "@opentelemetry/exporter-prometheus";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const directory = path.dirname(fileURLToPath(import.meta.url));
const dataDirectory = path.resolve(process.env.ABR_DATA_DIR || path.resolve(directory, "../data"));
const outputFile = path.resolve(process.env.ABR_SPAN_FILE || path.join(dataDirectory, "raw/spans/otel-spans.jsonl"));
await fs.mkdir(path.dirname(outputFile), { recursive: true });

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
    const content = records.map((record) => JSON.stringify(record)).join("\n") + "\n";
    writeQueue = writeQueue.then(() => fs.appendFile(outputFile, content));
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
  traceExporter: new FileSpanExporter(),
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
console.log(`Span dataset: ${outputFile}`);

let stopping;
async function shutdown() {
  if (!stopping) stopping = sdk.shutdown().catch((error) => console.error("OpenTelemetry shutdown failed:", error));
  await stopping;
}
globalThis.__otelShutdown = shutdown;
