# Project status (10 November 2026)

> **Progress at a glance: [PROGRESS.md](PROGRESS.md)** (single tracker, updated every phase).
> This file keeps the detailed notes of each phase.

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
- **Phase 3.5C-1 (local experiment lab) is committed** (`8af1f81`); the whole project
  uses the local lab for experiments. See [Phase 3.5C-1](#phase-35c-1-local-experiment-lab-implemented-10-november-2026).
- **Phase 3.5C-2 (domain API, schema and seed, target API in the lab network) is committed**
  (10 Nov 2026). See [Phase 3.5C-2](#phase-35c-2-domain-api-database-and-target-api-in-the-lab-implemented-10-november-2026).
- **Phase 3.5C-3 (injected faults and fixes) is implemented** (11 Nov 2026); fault-activation
  gate passed. See [Phase 3.5C-3](#phase-35c-3-injected-faults-and-fixes-implemented-11-november-2026).
- **Phase 3.5C-3 is committed** (`7c2f018`). **Phase 3.5D-1 (campaign tool, pilot,
  protocol draft) is implemented** (11 Nov 2026). See [Phase 3.5D-1](#phase-35d-1-campaign-tool-and-pilot-implemented-11-november-2026).
  3.5D-2 (main campaign) waits for the protocol approval.

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

### Phase 3.5C-2: domain API, database and target API in the lab (implemented 10 November 2026)

- **Schema and seed** (`target-api/db/`):
  - 5 tables (users, categories, products, orders, order_items).
  - `seed.sql` is deterministic: setseed, single-stream random, no parallel workers,
    fixed dates.
  - `constraints.sql` adds foreign keys and 6 named healthy indexes.
  - `scripts/lab-seed.sh small|medium|large` builds the `lab_seed` template.
  - Large = 100k users, 50k products, 1M orders, 2,997,667 order items, 549 MB, built in
    34–37 s.
- **Domain API** (`src/routes/domain.js`): 9 operations (`users.list/get`,
  `products.list/get`, `orders.list/create`, `search`, `reports`, `dashboard`) on PostgreSQL
  (`pg`) and Redis (node-redis, cache-aside). Without a database the domain routes
  answer 503.
- **Variant registry** (`src/lab/variants.js`): `ABR_VARIANTS` selects the implementation
  per operation at startup.
  - Unknown operations or variants stop the server.
  - Only `healthy` exists so far; 3.5C-3 adds the faults.
- **Safety:** under `ABR_ENV_PROFILE=lab` the server refuses a non-local database or
  cache. The smoke test now clears the database variables, so it can never reach the lab
  or the cloud.
- **Target API in the lab network:**
  - `target-api/Dockerfile` (Node 20, pinned by digest).
  - Compose service `api` (2 CPUs / 1 GB), managed with `scripts/lab-api.sh up|down|restart|logs`.
  - Telemetry goes to `data/` through a bind mount and is flushed on `docker stop`.
- **Lab config:** `max_parallel_workers_per_gather = 0`, because parallel workers on 2 CPUs
  make query speed depend on concurrent load.

**Verification (10 Nov 2026)**

- `scripts/test-all.sh`: target API 16 pass + 5 skipped; runner 22; analysis 14.
- `ABR_LAB_TESTS=1 npm run test:lab`, at small and at large scale: **5/5**.
  - All operations answer with data; 404 and 400 carry their reasons.
  - PostgreSQL spans sit under the experiment span for all 9 operations.
  - Redis cache-aside: the first read queries the database, the second makes no database
    query.
  - Variant names never reach telemetry.
- **Seed determinism:** re-seeding gives byte-identical checksums for all 5 data tables.
- **Healthy query plans at large scale:** all use indexes. A reset takes about 3 s.
- **API in Docker vs on the host** (300 identical requests, 801 query spans): PostgreSQL
  span p50 **0.47 vs 0.90 ms**, p95 1.70 vs 2.45 ms. This confirms the 3.5C-1 decision.
- **Healthy baseline at large scale** (API in Docker, 60 sequential requests per
  operation):
  - p50 4.1–5.4 ms for all operations except `orders.create` (7.5 ms) and `dashboard`
    (12.2 ms).
  - The only outliers are first, uncached requests: `reports` 1,024 ms, `dashboard` 337 ms.

**Fixed during verification:**

- Search took 42 ms (a sort of 6,310 rows), because a `text_pattern_ops` index supports
  matching but not ordering. It is now a `(lower(name) COLLATE "C")` index plus a matching
  `ORDER BY`: 0.09 ms.
- `POST /api/orders` stamps `now()`, which would have shifted report windows mid-run.
  Reports now use the fixed reference time 2026-11-01.

### Phase 3.5C-3: injected faults and fixes (implemented 11 November 2026)

- **Five fault classes, each in two operations, each with a `-fixed` variant**
  (`src/routes/domain.js`, `src/lab/faults.js`):

  | Fault (class) | Operations |
  |---|---|
  | `noindex` (DB_BOTTLENECK) | orders.list, search |
  | `nplus1` (N_PLUS_ONE) | users.list, users.get |
  | `payload` (LARGE_PAYLOAD) | products.list, search |
  | `cache` (CACHE_OPPORTUNITY) | products.get, reports |
  | `cpu` (CPU_BOTTLENECK) | products.get, dashboard |

  - Severities: N+1 rows, payload rows, CPU work.
  - Queueing stays a run setting (`PG_POOL_MAX`).
- **Variant startup steps:** database faults drop or recreate their index before the
  first request.
- **Change to the healthy `users.get`:** it now returns item counts per order (JOIN), so
  its N+1 fix returns the healthy response exactly.
- **Gate passed:** `npm run lab:fault-checks` (large seed,
  `data/results/fault-checks/2026-11-10T18-39-58-859Z/`).
  - All 10 fault checks and all 3 severity ladders pass. The mechanism is present when
    faulty, gone when fixed, and the effect grows with severity.
  - Fixed variants return byte-identical responses to their faulty variants where
    required.
  - Server time, faulty vs fixed:

    | Fault / operation | Faulty | Fixed |
    |---|---|---|
    | nplus1 / users.list | 25.2 ms | 4.0 ms |
    | noindex / orders.list | 53.3 ms | 6.1 ms |
    | payload / products.list | 46.6 ms | 3.0 ms (3.09 MB vs 1.7 kB) |
    | cache / reports | 791.4 ms | 2.5 ms |
    | cpu / products.get | 24.6 ms | 2.6 ms |

  - **Weak effects, kept on purpose:** the products.get cache fault (5.3 vs 2.5 ms, a
    cheap query) and the users.get N+1 fault (23.4 vs 15.9 ms). They are realistic hard
    cases for detection.
- **Variants in the Docker API verified:** `noindex` removed the index (count 1 → 0); a
  bad variant stops the container with a visible reason; the reset restores the index.
- `scripts/test-all.sh` 16 (+5 skipped) / 22 / 14; `npm run test:lab` 5/5.

**Fixed during verification:**

- The CPU fault's default severity of 20 added only about 4 ms on this PC, below the
  check's 10 ms threshold. It was recalibrated to 100 (+21–22 ms).
- In Docker, `console.error` before `process.exit` lost the "Refusing to start" message,
  because stderr is an asynchronous pipe there. Refusals are now written synchronously.

**Telemetry note for later phases:** on Windows, `experiment.cpu_*_ms` comes in steps of
about 15.6 ms (values 0/8/16/47 ms), the Windows timer granularity. It is finer inside the
Linux container. Process CPU stays a coarse signal (see TELEMETRY.md).

### Phase 3.5D-1: campaign tool and pilot (implemented 11 November 2026)

- **`experiments/campaign/`:**
  - `plan.py`: config validation, expansion into runs, run order shuffled with the
    campaign seed, one workload seed per operation, ground-truth labels per variant.
  - `run.py`: orchestrator. Per run: reset → API in Docker with the variant → open-loop
    workload → database statistics → API down. It is resumable, stops the API cleanly on
    Ctrl+C, and checks the seed scale and variant registry before starting.
  - `lab.py`: lab control. It uses Git Bash explicitly on Windows.
  - `summarize.py`: healthy noise, effect sizes, telemetry completeness, client- and
    server-side.
  - 9 tests with a fake lab and runner.
- **Runner:** request variants can set the path (`/api/users/17`). 23 tests.
- **Configs:** `experiments/configs/campaigns/smoke.json` (3 runs) and `pilot.json` (48 runs).
- **Ground truth** only in `data/labels/<campaign>.jsonl`. Runner artifacts carry no variant
  (tested).

**Smoke campaign** (`smoke-20261111T003553-c4f896`): 3/3 runs end to end, complete
correlation. Two problems found and fixed:

- a column name in the database-statistics query;
- the reports cache fault collapsed at 1 rps (client p50 16.9 s, queries 3.5–15 s
  overlapping on 2 CPUs). Reports now run at 0.5 rps for 60 s with days ∈ {7, 14, 30}. A
  check run then gave healthy p50 16 ms and cache 441 ms: a stable slowdown.

**Pilot campaign** (`pilot-20261111T004216-0b0138`, 48 runs, 47.5 min, report in
`data/results/campaigns/pilot-20261111T004216-0b0138/`):

- 48/48 completed, 0 failed; 7,459/7,459 requests matched to exactly one span; 0 failed
  requests.
- **Healthy noise:** server-side run p50 CV 0.2–8.3 %, so 2–3 repeats detect a 20 %
  slowdown. Client-side CV is 4–24 % (up to 23 repeats), because of the ~15 ms client and
  forwarding floor.
- **Faults vs healthy (server run p50):** all 10 fully separated.
  - Ratios: reports cache ×87.9, products.list payload ×18.7, orders.list noindex ×16.0,
    products.get cpu ×12.9, search noindex ×11.1, search payload ×9.3, users.list nplus1 ×4.8,
    dashboard cpu ×3.1, users.get nplus1 ×2.2, products.get cache ×2.0.
  - Client-side: 9/10; the products.get cache fault was not separated.
- **Fixed variants back at healthy:** ×0.90 and ×0.99.
- **Send lag:** worst p99 41.5 ms in run 1 (cold start) and 32.8 ms in run 19; all other
  runs ≤ 16.7 ms. The protocol therefore adds a discarded warm-up run and an exclusion rule
  (> 10 ms).
- The pilot ran with uncommitted code (`git_dirty: true`). That is acceptable for a pilot;
  the main campaign must start from a committed tree.

**Protocol draft:** `experiments/protocol.md`, waiting for Prof. Sagar's approval. It sets
main campaign 220 runs (~3.7 h) and held-out 68 runs, severities including weak levels,
exclusion rules, the analysis plan, and the decisions requested (§9).

### Phase 3.5D-2 preparation: protocol v1 and enforced rules (11 November 2026)

- **Protocol frozen as v1 by team decision** (`experiments/protocol.md`): the pilot-supported
  values of §9 were adopted as proposed, to be presented to Prof. Sagar. Any change he requests
  becomes an amendment.
  - Anomalous: server-side run p50 > max(P99 of healthy run p50s, 1.2 × healthy median),
    from the main campaign's 10 healthy runs per operation.
  - False alarms ≤ 5 %; back-to-healthy margin ± 10 %.
  - Preliminary pilot thresholds 4.42–23.59 ms per operation. Every pilot fault run lies
    above its threshold; both fixed variants lie below.
- **Rules enforced in code:**
  - `require_clean_git`;
  - `discard_warmup_runs` (one unlabelled run per session);
  - exclusion rules in `experiments/campaign/quality.py`;
  - `rerun_excluded` (one re-run, recorded as `rerun_of`);
  - `apply_exclusions` in the summary;
  - the target-API image is built before the manifest; its ID is recorded per run.
  - Re-summarizing the pilot under the rules (informational) excludes all 48 runs, because
    the pilot ran with uncommitted code. As intended, the pilot is not analysis data.
- **Configs:** `main.json` (220 runs: healthy 80, faults 110, fixed 30; about 4 h) and
  `heldout.json` (68 runs, own seed, another day).
  - Class counts in main: N_PLUS_ONE, LARGE_PAYLOAD, CPU_BOTTLENECK 30 each;
    DB_BOTTLENECK and CACHE_OPPORTUNITY 10 each (no severity levels); NONE 110.
  - Diagnosis needs class weighting.
- **Heavy-severity check** (scratch, not data): payload 5000/3000, cpu 300 and N+1 50/30 all
  ran with send-lag p99 ≤ 4.6 ms, 0 failures and complete correlation.
- Tests: campaign 14 (5 new protocol-rule tests).

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

### Main campaign and analysis (11 Nov 2026)

Campaign `main-20261111T100109-be3c28`: 220 planned runs, shuffled, from commit `4c0f60d`.
Results: `docs/results.md` (generated) and `data/results/*/main-20261111T100109-be3c28/`.

- **Incidents, all handled by protocol rules and recorded as amendments:**
  - Docker Desktop gave the image a new ID on every rebuild of identical code, so all runs
    were falsely excluded. Fixed in `810966a`: content hash, no rebuild per run.
  - PostgreSQL restarted once during a reset, so 2 runs failed. They were completed with
    `--resume`, and the reset now retries.
  - 24 runs exceeded the client send-lag limit, mostly while analysis ran on the same PC.
    They were re-run once and all re-runs passed.
- **Laptop power state.** The charger was connected and disconnected five times. Healthy
  latency on battery is ×1.6–2.3 higher; within one power state the CV is 1–8 %. The team
  kept the campaign and reports this as a threat. Rule 5 (no runs on battery) applies from
  the held-out session on.
- **Detection:** false alarms 4/80 (5.0 %), detection 103/110 (93.6 %). Misses are the weakest
  severities (users.get nplus1:3, dashboard cpu:20).
- **Diagnosis:**
  - Rules: 110/110.
  - Random Forest: L1 0.29, L3 0.55, L4 0.95 macro-F1. Database and cache tracing (L4) is
    what makes ML work (RQ3).
  - XGBoost L4: 0.67, weak on CACHE_OPPORTUNITY.
- **Remediation and verification:**
  - Right fix for 110/110 runs.
  - 7/10 fixes significantly improved latency; 0/10 were shown back to healthy (wide
    intervals).
  - Two closed loops (users.list N+1, orders.list missing index) were each verified end to end.
- **Dashboard:** built with `next build` and checked against these results.

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
