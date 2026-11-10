// Periodic process/runtime samples: event-loop delay, GC pauses, CPU, heap and RSS.
// These are process-level signals over a time window, never per-request values.
// Each sample lists the run IDs whose requests were active in that window, so a
// run can be joined to the samples that overlap it.
const fs = require("node:fs/promises");
const path = require("node:path");
const { monitorEventLoopDelay, performance, PerformanceObserver } = require("node:perf_hooks");

const NS_PER_MS = 1e6;

function startRuntimeMetrics({ file, intervalMs, inFlight }) {
  if (!intervalMs || intervalMs <= 0) return { noteRequest() {}, async stop() {} };

  const loopDelay = monitorEventLoopDelay({ resolution: 10 });
  loopDelay.enable();
  let gcCount = 0;
  let gcTotalMs = 0;
  let gcMaxMs = 0;
  const gcObserver = new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      gcCount += 1;
      gcTotalMs += entry.duration;
      gcMaxMs = Math.max(gcMaxMs, entry.duration);
    }
  });
  gcObserver.observe({ entryTypes: ["gc"] });

  let runIds = new Set();
  let maxInFlight = 0;
  let windowStart = Date.now();
  let cpuStart = process.cpuUsage();
  let eluStart = performance.eventLoopUtilization();
  let writeQueue = Promise.resolve();
  let initialized = false;

  function sample() {
    const now = Date.now();
    const elapsedMs = Math.max(now - windowStart, 1);
    const cpu = process.cpuUsage(cpuStart);
    const elu = performance.eventLoopUtilization(eluStart);
    const memory = process.memoryUsage();
    const record = {
      window_start: new Date(windowStart).toISOString(),
      window_end: new Date(now).toISOString(),
      window_ms: elapsedMs,
      run_ids: [...runIds].sort(),
      event_loop_delay_mean_ms: loopDelay.count ? loopDelay.mean / NS_PER_MS : null,
      event_loop_delay_p50_ms: loopDelay.count ? loopDelay.percentile(50) / NS_PER_MS : null,
      event_loop_delay_p99_ms: loopDelay.count ? loopDelay.percentile(99) / NS_PER_MS : null,
      event_loop_delay_max_ms: loopDelay.count ? loopDelay.max / NS_PER_MS : null,
      event_loop_utilization: elu.utilization,
      gc_count: gcCount,
      gc_total_ms: gcTotalMs,
      gc_max_ms: gcMaxMs,
      process_cpu_user_ms: cpu.user / 1000,
      process_cpu_system_ms: cpu.system / 1000,
      process_cpu_pct: ((cpu.user + cpu.system) / 1000 / elapsedMs) * 100,
      heap_used_mb: memory.heapUsed / (1024 * 1024),
      heap_total_mb: memory.heapTotal / (1024 * 1024),
      rss_mb: memory.rss / (1024 * 1024),
      // A sample can only run when the event loop is free, so the instant value is
      // usually low; the window maximum is the useful concurrency signal.
      requests_in_flight: inFlight(),
      requests_in_flight_max: Math.max(maxInFlight, inFlight()),
    };
    loopDelay.reset();
    gcCount = 0;
    gcTotalMs = 0;
    gcMaxMs = 0;
    runIds = new Set();
    maxInFlight = inFlight();
    windowStart = now;
    cpuStart = process.cpuUsage();
    eluStart = performance.eventLoopUtilization();
    writeQueue = writeQueue.then(async () => {
      if (!initialized) {
        await fs.mkdir(path.dirname(file), { recursive: true });
        initialized = true;
      }
      await fs.appendFile(file, JSON.stringify(record) + "\n");
    }).catch((error) => console.error("Runtime metrics write failed:", error));
  }

  const timer = setInterval(sample, intervalMs);
  timer.unref();

  return {
    // Called when a request starts.
    noteRequest(runId) {
      runIds.add(runId);
      maxInFlight = Math.max(maxInFlight, inFlight());
    },
    async stop() {
      clearInterval(timer);
      sample();
      gcObserver.disconnect();
      loopDelay.disable();
      await writeQueue;
    },
  };
}

module.exports = { startRuntimeMetrics };
