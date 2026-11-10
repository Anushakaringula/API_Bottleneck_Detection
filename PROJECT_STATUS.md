# Project status (10 November 2026)

Labels used in this file:

- **Completed**: built, and checked against the code and data.
- **Known issue**: found in the code audit of 9 November 2026 or later; not yet fixed.
- **Proposed**: planned, not built.
- **Unverified**: an assumption that experiments still have to test.

## Summary

- Phases 1–3 (instrumentation, workload runner, telemetry analysis) are built and their
  tests pass.
- On 10 November 2026 the repository was **reorganised by workflow role** (see
  [docs/architecture.md](docs/architecture.md)). All existing behaviour was
  preserved, and the 9 Nov analysis was reproduced byte-for-byte from the moved data.
  The reorganisation is committed (`911af77`).
- An audit found that the data collected so far is **not good enough to evaluate
  bottleneck detection**. The team therefore added a preparation phase (3.5) before
  detection (Phase 4).
- **Phase 0 (housekeeping) and Phase 3.5A (telemetry fixes) are implemented**
  (10 Nov 2026, not yet committed). See [Phase 0 and 3.5A](#phase-0-and-35a-implemented-10-november-2026)
  for what still has to be verified. Phases 3.5B–3.5D are not started.

## Completed

### Phase 1: instrumented target API (`target-api/`)

- Express API with experimental endpoints: `/api/normal`, `/api/cpu`,
  `/api/payload`, `/api/query-sim`, `/health`.
- OpenTelemetry automatic HTTP and Express instrumentation.
- One `experiment.http.request` span per request, recording latency, HTTP status,
  response body size, and process CPU and heap deltas.
- Prometheus metrics on `http://localhost:9464/metrics`: request count, latency,
  response size, errors, in-flight requests.
- Spans are written to `data/raw/spans/otel-spans.jsonl` and a CSV log to
  `data/raw/spans/telemetry.csv`. Both paths can be changed with environment
  variables (see `.env.example`).
- **Smoke test** (as of Phase 1–3): 5 checks passed, plus a 6th marked TODO for the span-parenting defect. Since 3.5A: 10 checks, all passing.

### Phase 2: experiment runner (`experiments/experiment_runner/`)

- Configurable request count or duration, concurrency, request rate, warm-up
  requests and timeouts.
- Unique experiment IDs and shared repeat-group IDs.
- Per-request results, run summaries, and **run-level** span correlation: counts of
  expected, missing and duplicate spans per run ID.
- Safety limits on load.
- **12 unit tests pass** (the original 9, plus 3 path tests); 14 since 3.5A (traceparent and request-level correlation).

### Phase 3: telemetry analysis (`pipeline/telemetry_analysis/`)

- Reads all experiment artifacts and the span file, validates them and recomputes
  statistics.
- Writes predictor features separately from metadata (scenario labels, endpoints,
  IDs) and enforces an allowlist of permitted features.
- Writes a data-quality report and a manifest with SHA-256 hashes of the inputs.
- **Automated leakage checks**:
  - allowlist names,
  - label and ID text in the feature output,
  - numeric-only values,
  - feature and metadata separation,
  - the serialization guard,
  - a proxy-leakage audit (`leakage_audit.py`).
- **12 unit tests pass** (the original 4, plus 5 leakage tests and 3 path tests); 14 since 3.5A (request-level join).

### Phase 0 and 3.5A (implemented 10 November 2026)

**Phase 0: housekeeping**

- `docs/02-development-setup.md`: Ubuntu, WSL2 and Git Bash setup, legacy data, CI.
- `scripts/env.sh` builds `PYTHONPATH` with Windows paths and `;` under Git Bash, and
  picks a working Python 3.10+ as `$ABR_PYTHON`. The scripts use `$ABR_PYTHON`.
- `.gitattributes` keeps `*.sh`, `SHA256SUMS` and `*.tsv` at LF. CRLF copies broke
  `sha256sum -c` and bash outside Git Bash.
- `target-api/package.json`: name `target-api`, `main` points to `src/server.js`, and
  `npm test` runs `node --test`. `node --test test/` fails on Node 22+.
- CI: `.github/workflows/test.yml` runs `scripts/test-all.sh` on Ubuntu 22.04,
  Node 20, Python 3.10.

**Phase 3.5A: telemetry fixes**

1. **Span parenting fixed.** Cause: `@opentelemetry/instrumentation-express`
   deliberately restores each layer's parent span when a middleware calls `next()`,
   discarding a context set inside that middleware. The experiment span is now
   started in the `http.createServer` callback, before Express. All Express spans
   are its descendants. The `todo` mark is removed and the test passes.
2. **Per-request correlation.** The runner sends a W3C `traceparent` per request and
   records `trace_id` and `client_span_id`. `summary.json` reports
   `telemetry_correlation.request_level`. The analyzer writes
   `request_records.jsonl` and `request_join_summary.json`.
3. **Scenario label removed from telemetry.** It is no longer sent as a header or
   recorded in spans or the CSV. Tests check this. Spans now carry
   `experiment.telemetry_schema = "2"`.
4. **Per-run span files**: `data/raw/spans/runs/<run-id>.jsonl`. The run ID is copied
   onto every span of a request by `RunIdSpanProcessor`.
5. **Runtime metrics collected**: `data/raw/runtime/runtime-metrics.jsonl`, sampled
   every second. It records event-loop delay, ELU, GC pauses, process CPU, heap, RSS,
   in-flight requests and the active run IDs.
6. **Windows-safe shutdown**: the server also shuts down on an IPC `"shutdown"`
   message. Without it, the smoke test could not flush spans on Windows.

**Verification status**

- Target API: **10/10 tests pass** (Windows, Node 24.15).
- Not yet run, because no Python was available on the development machine:
  - the runner and analyzer suites (expected: 14 and 14),
  - `verify-legacy.sh` (the analyzer is designed to keep the legacy output
    byte-identical; this must be confirmed),
  - a live experiment run.

### Historical data (frozen)

- Raw: `data/raw/legacy-2026-11-09/`. Analysis: `data/processed/legacy/`.
- All 40 files are checked by `SHA256SUMS` and `MIGRATION_INVENTORY.tsv`, and
  `scripts/verify-legacy.sh` reproduces the 9 Nov analysis byte-for-byte.

All numbers below were recounted directly from the raw files during the audit.

| Item | Value |
|---|---|
| Experiment runs | 11 |
| Measured requests | 320 (all returned HTTP 200) |
| Warm-up requests | 38 |
| Dedicated experiment spans | 408 |
| Spans matched to experiment runs | 358 |
| Spans from manual tests, not linked to any experiment run | 50, across 9 run IDs |
| Analysis output | `data/processed/legacy/20261109T180648-7cd51a3e/` |

These numbers show that the pipeline works end to end. **They are not evidence
that bottleneck detection works.**

## Known issues

1. **Too little data.**
   - Only 3 of the 11 runs have 100 measured requests; the other 8 have 2–3 each.
   - No condition was repeated.
   - There is no full-size run for the payload endpoint.
2. **No real load.** At most 1 request was ever in flight, so queueing and
   saturation have never been observed.
3. **Features identify the endpoint (proxy leakage).**
   - Response size is constant per endpoint (53, 74 or 1,161,826 bytes).
   - Throughput just equals the configured rate.
   - The automated audit (10 Nov) shows that `p95_latency_ms` and
     `p99_latency_ms` *each alone* perfectly separate the 4 scenarios in the 11
     legacy runs.
   - A classifier trained on these features would learn which endpoint was called,
     not why it was slow.
4. **No healthy-vs-faulty comparison of the same API.** Each endpoint has one fixed
   behaviour.
5. **Span-parenting defect: fixed in 3.5A** for new data. In the legacy data the
   experiment span never has a child; that data is unchanged.
6. **Correlation per run only: fixed in 3.5A** for new data (W3C `traceparent`). The
   legacy data can still only be matched per run.
7. **Queueing is hidden.** The runner waits for free workers instead of keeping
   to a timetable (closed-loop), so delays caused by load do not show up in the
   latency figures.
8. **Runtime metrics never collected: fixed in 3.5A.** They are stored in
   `data/raw/runtime/runtime-metrics.jsonl`.
9. **Mixed span file.** Two span record formats share the legacy span file. New
   data also gets clean per-run span files (3.5A).
10. **Seven CSV rows have no span** in the legacy data.
11. **No real database.** `/api/query-sim` filters an in-memory array. It is not a
    database experiment.

## Proposed (planned, not built)

- **Phase 3.5B**: open-loop workload generation that records the intended send
  time, the actual send time and the delay between them; real concurrency;
  client calibration.
- **Phase 3.5C**: a reference API with healthy, faulty and remediated versions;
  PostgreSQL (run as a user-owned cluster, no sudo needed); Redis through
  `node-redis`.
- **Phase 3.5D**: a pilot campaign, then a repeated, randomized experiment campaign
  with healthy-vs-healthy runs.
- **Phases 4–9**: detection, diagnosis (rules, then Random Forest and XGBoost as
  comparators), remediation, verification, dashboard, final evaluation.
  See [docs/04-roadmap.md](docs/04-roadmap.md).

## Unverified assumptions

- The Python runner can send the planned request rates accurately.
- No spans are lost at the highest planned load.
- Each injected fault produces a measurable slowdown at the chosen severities.
- Laptop noise is small enough for useful detection.
- Database time share separates database faults from CPU faults.
- The time accounting (splitting extra latency into database, cache, CPU work,
  transfer and queueing) recovers known values on a calibration operation.
- Random Forest or XGBoost beat the rules once leakage is removed. "No
  improvement" is an acceptable research answer.
- Remediated versions bring performance back to the healthy level.

## Decisions already taken by the team

- The memory bottleneck experiments are a stretch goal.
- Redis is accessed through `node-redis`.
- The Python runner stays. k6 is added only if calibration shows it is needed.
- Detection thresholds come from repeated healthy experiments and are agreed with
  Prof. Sagar before being finalized.
- No sudo commands or software installs happen without the team reviewing the
  exact command first.
- Folders for future phases are created only when their code exists.

## Still to be decided

These are to be agreed with Prof. Sagar after the pilot:

- the threshold percentiles and the minimum meaningful slowdown,
- the acceptable false-alarm rate,
- the limits on lost telemetry.

Also still open:

- the git author identity for commits,
- whether to use OTel Collector, Prometheus and Grafana for the demo.
