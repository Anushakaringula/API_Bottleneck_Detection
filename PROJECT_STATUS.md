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
- **Phase 0 (housekeeping) and Phase 3.5A (telemetry fixes) are committed** (`15c1c39`)
  on `develop`. See [Phase 0 and 3.5A](#phase-0-and-35a-implemented-10-november-2026).
  Still open: `verify-legacy.sh`, because the legacy data is not on this PC.
- **Phase 3.5B (open-loop workload generation) is committed** (`60d185a`). See
  [Phase 3.5B](#phase-35b-implemented-10-november-2026).
- **Phase 3.5C-1 (local experiment lab) is implemented** (10 Nov 2026); the whole project
  uses the local lab for experiments. See [Phase 3.5C-1](#phase-35c-1-local-experiment-lab-implemented-10-november-2026).
  3.5C-2 (domain API, schema, seed) and 3.5C-3 (faults) and 3.5D are not
  started.

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

### Phase 3.5B (implemented 10 November 2026)

1. **Open-loop scheduler** (`load_model: "open"`). Requests are released at
   timetable slots, whether or not earlier requests have finished.
   - Arrivals: `constant` or `poisson`, reproducible from `random_seed` (always
     saved).
   - The closed loop stays the default and is unchanged.
2. **Intended vs actual send time.** Each request records
   `intended_send_offset_ms`, `sent_offset_ms`, `send_lag_ms` and
   `latency_from_intended_ms`. The summary adds lag and latency distributions and
   the achieved send rate.
3. **Real concurrency.** In the open loop, `concurrency` is the number of workers.
   Caps raised: 200 workers, 200 rps, 50,000 requests, 900 s.
4. **Workload profiles.** Rate `stages` (steady, linear ramp, pause) and
   `request_variants` (weighted query-string variants per request). Example
   configs: `open-loop-steady.json`, `open-loop-ramp.json`, `open-loop-burst.json`.
5. **Client calibration.**
   - The target API has `/__calib/noop`, `/__calib/sleep` and `/__calib/cpu`
     (only with `ABR_CALIBRATION=1`).
   - `python3 -m experiment_runner.calibrate` writes
     `data/results/calibration/<id>/report.md`, with send lag and achieved rate
     per rate and the highest rate the client sustains.

**Verification status**

- Target API: **11/11 tests pass** (Windows, Node 24.15). The calibration test
  shows 5 concurrent 300 ms sleeps finish together and the runtime sampler sees
  them in flight.
- All suites pass on Windows (Python 3.12.10, Node 24.15): **target API 11, runner 22,
  analysis 14** (`scripts/test-all.sh`). This also confirms the Phase 0/3.5A Python
  code. Two original `test_paths.py` tests were fixed for Windows paths.
- **Calibration on the Windows development PC (10 Nov 2026,
  `data/results/calibration/20261110T223824-36389f56/`): gate passed.**
  - All 10 runs pass: noop and 50 ms sleep at 5, 10, 20, 50 and 100 rps, with at
    least 500 requests per run.
  - Send lag p99 0.5–3.9 ms, achieved rate within 0.03 % of target, 0 failures.
  - The sleep runs reached 9 requests in flight. Highest sustained rate: **100 rps**
    (the top of the tested range).
- Observations from that calibration:
  - Isolated send-lag spikes of 18–28 ms occur on Windows (lag max). A first run
    with only 75 requests at 5 rps failed because of one such spike, hence
    `--min-requests 500`.
  - Client-observed latency for a no-op is 5–15 ms at p50 and about 28 ms at p99
    on this PC (Python `urllib` opens a new connection per request, plus Windows
    timer granularity). This floor is the same for every condition, so
    baseline-relative detection is unaffected, but it must be re-measured on the
    experiment machine.
- **To do:** repeat the calibration on the machine that will collect the main
  dataset, if that is not this PC.

**Finding (10 Nov 2026).** With the current synchronous endpoints, at most one
request is ever in flight on the server, even at client concurrency 10. Load
queues in the socket backlog and appears as event-loop delay. Real server-side
concurrency needs asynchronous work: database and cache calls in 3.5C, or
`/__calib/sleep`.

### Phase 3.5C-1: local experiment lab (implemented 10 November 2026)

**Decision (10 Nov 2026):** the whole project uses a local lab for measured experiments,
with this PC as the experiment machine. The cloud services (Neon, Redis Cloud) stay
configured in `.env` for application use only. Measured cloud round trips were 113–203 ms
(Neon) and 317 ms p50 (Redis Cloud).

- **Installed:** WSL2 and Docker Desktop 4.99. `%USERPROFILE%\.wslconfig` gives Docker
  6 CPUs, 8 GB and 2 GB swap. `pg` and `redis` (node-redis) were added to `target-api`.
- **`infrastructure/`:** Docker Compose with PostgreSQL 16.15 and Redis 7.4.12, both
  pinned by digest and bound to 127.0.0.1.
  - Fixed container limits (PostgreSQL 2 CPU / 2 GB; Redis 1 CPU / 512 MB).
  - PostgreSQL: `autovacuum` and `jit` off; `pg_stat_statements` and `track_io_timing` on.
  - Redis: no persistence.
- **Template databases:** `lab` is recreated from the template `lab_seed`, which accepts
  no connections.
- **Scripts:** `scripts/lab-up / lab-down / lab-reset / lab-status / lab-probe.sh`.
- **Environment profiles:**
  - `.env.lab` (lab) and `.env` (cloud), both git-ignored; `.env.lab.example` is
    committed.
  - `npm run start:lab`; `ABR_ENV_FILE=.env.lab source scripts/env.sh`.
  - `scripts/env.sh` now parses the env file instead of executing it, and values already
    set in the environment win over the file (this fixed `.env` blanking `ABR_PYTHON`).

**Verification**

- **Gate passed:** `scripts/lab-probe.sh` (from the lab network, 2,000 samples).
  PostgreSQL `SELECT 1` p50 0.12 / p99 0.33 ms; Redis `PING` p50 0.12 / p99 0.32 ms;
  both hosts local.
- **Reset reproducibility passed:** after changing data and Redis, `lab-reset.sh`
  restored identical table checksums and an empty cache.
- **Lab configuration confirmed live:** `autovacuum=off`, `jit=off`,
  `shared_buffers=512MB`, `pg_stat_statements` loaded; Redis has no persistence and
  `maxmemory` 256 MB.
- `scripts/test-all.sh`: 11 / 22 / 14 pass.

**Finding → decision.** From the Windows host shell, Docker Desktop's port forwarding
adds 0.4–1.2 ms and jitter per call (p99 1.4–3.7 ms instead of 0.33 ms). It would be
paid by every query, which inflates N+1. Therefore the **target API runs inside the lab
network in experiments** (to be built in 3.5C-2). The runner stays on the host, so the
hop lies only between client and server and is the same for every condition.

**Bug fixed along the way:** Redis rejects comments at the end of a line
(`requirepass labpass  # ...`); `redis.conf` keeps comments on their own lines.

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
7. **Queueing hidden by the closed loop: fixed in 3.5B.** The open loop keeps to a
   timetable and records send lag. The legacy data was collected closed-loop.
8. **Runtime metrics never collected: fixed in 3.5A.** They are stored in
   `data/raw/runtime/runtime-metrics.jsonl`.
9. **Mixed span file.** Two span record formats share the legacy span file. New
   data also gets clean per-run span files (3.5A).
10. **Seven CSV rows have no span** in the legacy data.
11. **No real database.** `/api/query-sim` filters an in-memory array. It is not a
    database experiment.

## Proposed (planned, not built)

- **Phase 3.5C**: a reference API with healthy, faulty and remediated versions;
  PostgreSQL (run as a user-owned cluster, no sudo needed); Redis through
  `node-redis`.
- **Phase 3.5D**: a pilot campaign, then a repeated, randomized experiment campaign
  with healthy-vs-healthy runs.
- **Phases 4–9**: detection, diagnosis (rules, then Random Forest and XGBoost as
  comparators), remediation, verification, dashboard, final evaluation.
  See [docs/04-roadmap.md](docs/04-roadmap.md).

## Unverified assumptions

- The Python runner can send the planned request rates accurately. (Confirmed up to 100 rps on the Windows PC on 10 Nov 2026 with `experiment_runner.calibrate`; repeat on the experiment machine.)
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
