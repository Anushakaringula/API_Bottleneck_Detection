# 1. Project overview

## The problem

When a web API becomes slow, monitoring graphs show *that* it is slow. They rarely
show *why*. Common causes include:

- a slow database query, for example because an index is missing
- an N+1 query pattern, where one request runs many small queries instead of one
- CPU-heavy code, such as an inefficient algorithm
- a response that is too large to build and send
- a cache that is not working
- too many requests at once (queueing)
- memory pressure (garbage-collection pauses)

Each cause needs a different fix. Picking the wrong one wastes effort.

OpenTelemetry can record a lot of information about every request: how long it
took, which database queries it ran, how big the response was, and how busy the
process was. This project asks:

> **Is request-level observability telemetry sufficient to identify and
> distinguish different types of API performance bottlenecks?**

Where the telemetry is *not* sufficient, the system should say "not enough
evidence" instead of guessing.

## Research questions

| ID | Question |
|---|---|
| RQ1 Detection | Can telemetry, compared with a proper baseline, tell a degraded API apart from normal run-to-run variation? |
| RQ2 Diagnosis | Once a slowdown is detected, can telemetry identify which kind of bottleneck caused it? |
| RQ3 Sufficiency | Which telemetry is actually needed? Diagnosis is compared across four telemetry levels (below). |
| RQ4 Rules vs ML | Do Random Forest and XGBoost do better than an interpretable rule-based method, once data leakage is removed? |
| RQ5 Uncertainty | Can the system recognise when its evidence is insufficient, and is it more accurate when it does commit to an answer? |
| RQ6 Remediation | Does the recommended fix produce an improvement that holds up statistically, and does it return performance to normal? |

The four telemetry levels for RQ3. Each level includes everything in the level
before it:

| Level | Signals |
|---|---|
| L1 | Client-side only: latency, time to first byte, status, response size |
| L2 | + server request timing |
| L3 | + process and runtime signals: CPU, heap, event-loop delay, garbage collection |
| L4 | + full tracing: database, cache and handler spans |

## How the finished system is meant to work

1. A workload runner sends controlled traffic to a reference API.
2. OpenTelemetry records spans and metrics for every request.
3. An analyzer joins client measurements with server spans into a request-level dataset.
4. **Detection** compares the API with its own healthy baseline and answers
   *normal*, *anomalous* or *inconclusive*. It does not name a cause.
5. **Diagnosis** runs only after a detection. It looks at where the extra time
   went and names a likely cause, with supporting evidence, contradicting
   evidence, missing evidence and a confidence level, or it reports
   *insufficient evidence*.
6. A remediation engine recommends a fix.
7. The fix is applied by switching the API to a pre-written, reviewed
   "remediated" version. The system never edits code by itself.
8. The same workload runs again.
9. Statistics decide whether the improvement is real or just noise.
10. A dashboard shows the results.

## How the experiments work

The reference API has operations that can each run in three versions:

- **healthy**: the normal implementation
- **faulty**: a known problem switched on, at several severity levels
- **remediated**: the fix for that problem

Because the team knows which version was running in each experiment, it can measure
how often detection and diagnosis are right, how often they raise false alarms, and
how often they correctly say "not sure".

Planned fault types: database query (missing index), CPU, N+1 queries, large
payload, cache (Redis), and high concurrency. Memory is a stretch goal.

## Key ideas you need to understand the methodology

- **Baseline.** The healthy version of the *same* operation under the *same*
  traffic. "Slow" always means "slower than its own baseline", never "slower than
  another endpoint".
- **Run.** One server start, a warm-up period, a measured period, then shutdown.
  The run is the experimental unit. Requests inside one run share the same process
  and machine state, so they are not independent samples.
- **Healthy-vs-healthy (A/A) runs.** Comparing healthy runs with other healthy
  runs. Anything flagged there is a false alarm. These runs are how false alarms
  are measured.
- **Data leakage.** A model can learn a shortcut instead of real evidence: the
  endpoint name, the scenario name, or a response size that only one endpoint
  produces. To prevent this:
  - ground-truth labels are stored separately from predictor features,
  - each fault is injected into at least two different operations,
  - models are also tested on operations they never saw during training.
- **Open-loop traffic.** Requests are sent on a fixed timetable even if the server
  falls behind, so queueing delay becomes visible instead of hidden.
- **Detection vs diagnosis.** Detection answers "is something wrong?" and diagnosis
  answers "what is wrong?". They fail in different ways, so they are evaluated
  separately.

## Technology

| Area | Tools |
|---|---|
| Target API | Node.js 20, Express 5 |
| Observability | OpenTelemetry (traces and metrics), Prometheus exporter |
| Workload and analysis | Python 3.12 (runner and analyzer: standard library; diagnosis ML: numpy, scikit-learn, XGBoost, SciPy from `requirements.txt`) |
| Database and cache (planned) | PostgreSQL 14, Redis with the `node-redis` client |
| ML (planned) | scikit-learn (Random Forest), XGBoost |
| Dashboard (planned) | Next.js / React |
| Load tool | Python runner; k6 only if calibration shows the Python runner cannot keep up |

Everything runs on one Ubuntu 22.04 laptop.
