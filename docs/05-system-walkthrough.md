# 5. How the finished system works

This guide follows one degraded API endpoint through the whole system, stage by stage. For
each stage it says what the stage does, how it decides, where the code is, and what it
writes. Numbers are from the main campaign (`main-20261111T100109-be3c28`) unless stated
otherwise.

```
            ┌───────────── target-api (system under test) ─────────────┐
 workload → │ Express routes ── PostgreSQL / Redis    OpenTelemetry spans │ → data/raw/
            └──────────────────────────────────────────────────────────┘
                                                                     │
 data/raw → features (L1–L4) → detection → diagnosis → remediation → re-run → verification
                                    │            │            │                    │
                                    └────────────┴──── data/results/ ──────────────┴→ dashboard, docs/results.md
```

The two halves never import each other's code. `target-api/` only produces telemetry, and
`pipeline/` only reads files. Tests enforce this boundary (`pipeline/detection/tests/`).

---

## Stage 0: the system under test (`target-api/`)

An Express 5 API on Node 20. It runs in Docker inside the lab network, next to PostgreSQL 16
and Redis 7, which both run on this PC. It has 9 domain operations over a seeded shop
database:

| Operation | Route | What it does |
|---|---|---|
| `users.list` | `GET /api/users` | page of users with their order counts (1 query) |
| `users.get` | `GET /api/users/:id` | one user with latest orders and their item counts |
| `products.list` | `GET /api/products` | page of products, summary columns |
| `products.get` | `GET /api/products/:id` | product details, Redis cache-aside (60 s) |
| `orders.list` | `GET /api/orders?user_id=` | a user's orders with item counts |
| `orders.create` | `POST /api/orders` | place an order in a transaction (not part of the campaigns) |
| `search` | `GET /api/search?q=` | product name prefix search |
| `reports` | `GET /api/reports?days=` | sales by category, Redis cache (60 s); fixed reference date, so results never drift |
| `dashboard` | `GET /api/dashboard` | summary figures (parallel queries) |

**Variants.** Each operation has a `healthy` implementation. Fault operations also have a
**faulty** one and a **fixed** one, both reviewed in advance. The variant is chosen at startup
with `ABR_VARIANTS='{"users.list":"nplus1:20"}'` (registry: `src/lab/variants.js`, building
blocks: `src/lab/faults.js`). The API never reports which variant is active, so the analysis
cannot cheat.

**The five injected bottlenecks**, each in two operations:

| Class | Variant | What the faulty code does wrong | Fixed variant |
|---|---|---|---|
| DB_BOTTLENECK | `noindex` (orders.list, search) | the index the query needs is dropped (`orders.user_id`; product name), so PostgreSQL scans the table | `noindex-fixed`: index recreated |
| N_PLUS_ONE | `nplus1:N` (users.list, users.get) | one query for the page, then one count query per row (N rows) | `nplus1-fixed`: one query with a subquery / JOIN |
| LARGE_PAYLOAD | `payload:N` (products.list, search) | pagination ignored: N rows with every column (description, specs) | `payload-fixed`: one page, summary columns |
| CACHE_OPPORTUNITY | `cache` (products.get, reports) | the cache key changes on every request, so the cache never hits | `cache-fixed`: stable key, 60 s TTL |
| CPU_BOTTLENECK | `cpu:N` (products.get, dashboard) | an expensive key-derivation "signature" (PBKDF2, N thousand iterations) on every response | `cpu-fixed`: ordinary hash |

The number after the colon is the **severity**. For example, `nplus1:5`, `:20` and `:50`
are mild, medium and strong. A fixed variant returns the same response as its faulty
version, so a before/after comparison measures only the fix.

**Telemetry.** OpenTelemetry instruments HTTP, Express, `pg` and Redis. Each request
carries a W3C `traceparent` from the client, so every request can be matched to exactly one
server-side trace (100 % matched in the main campaign). An experiment span wraps each
request and records server latency, response bytes, and CPU and heap deltas. A runtime
sampler records event-loop delay, GC, CPU, heap and requests in flight once per second.
Output: `data/raw/spans/runs/<run-id>.jsonl` and `data/raw/runtime/`.

---

## Stage 1: generating controlled load (`experiments/`)

**Workload runner** (`experiment_runner/runner.py`): *open loop*. Requests are sent on a
seeded Poisson timetable (5 requests/s, 30 s measured, after 20 warm-up requests; `reports`
uses 0.5 requests/s for 60 s), whether or not earlier requests have finished. This is how
real traffic behaves, and it keeps slow responses from slowing the load down. The runner
records how late each request was sent (*send lag*). If the client itself falls behind, the
run is excluded.

**Campaign orchestrator** (`campaign/run.py`) runs many labelled runs automatically. For
each run:

1. reset the database from a template (`lab-reset.sh`, about 3 s; same data every time);
2. start the API container with one variant (`lab-api.sh up`);
3. send warm-up and measured requests (runner);
4. save database statistics (`pg_stat_statements`);
5. stop the API (telemetry is flushed);
6. record progress: requests, failures, send lag, image identity, power state.

The run order is shuffled with a fixed seed. Ground truth (which variant ran) is written
**only** to `data/labels/<campaign>.jsonl`, which the analysis reads only for evaluation.

Protocol rules are enforced in code: a clean git tree, a discarded warm-up run, exclusion
rules, one re-run of each excluded run, and no runs on battery
([protocol](../experiments/protocol.md) §6–7).

---

## Stage 2: features (`pipeline/features/`)

`python -m features.extract <campaign>` turns each run into **30 numeric features**,
grouped in four cumulative **telemetry levels**. These levels are the core of RQ3: how much
telemetry do you need?

| Level | Adds | Examples |
|---|---|---|
| **L1** client only | what any load tester sees | client latency p50/p95/p99, response size, error rate |
| **L2** + server timing | the API's own request span | server latency p50/p95/p99, client–server gap |
| **L3** + runtime | process signals | CPU per request, event-loop delay, GC, heap, in-flight |
| **L4** + DB/cache tracing | spans of every query and Redis call | queries per request, repeated identical queries, DB time and share, cache hit ratio, time in application code |

Full list with meanings: `pipeline/features/schema.py`. Database time is the union of
overlapping query spans, so parallel queries are not double-counted. Features never contain
the route, variant or any ID (leakage tests).

---

## Stage 3: detection (`pipeline/detection/rule.py`)

**Question:** is this run slower than normal for its operation?

**Rule of record** (protocol v1, decided before the main campaign):

> A run is **anomalous** if its server-side median latency (`server_latency_p50_ms`) is
> above **max(P99 of the healthy runs' medians, 1.2 × healthy median)** for that operation.

- The P99 term absorbs normal run-to-run noise; the 1.2× term ignores slowdowns under 20 %.
- Healthy baselines come from the healthy runs of the same operation. When a healthy run is
  itself being scored, it is left out of its own baseline (*leave-one-out*).
- **Inconclusive** if the run was excluded by the protocol, less than 95 % of its requests
  have a trace, or fewer than 2 healthy runs exist.
- Output per run: verdict, value, threshold, ratio to baseline, and the reason, in
  `data/results/detection/<campaign>/verdicts.jsonl`.

Example: `users.list` healthy median 4.45 ms, threshold 5.34 ms. An N+1 run at 24.05 ms is
anomalous (×5.4).

Scoring (`pipeline/evaluation/detection.py`) joins verdicts with labels: false-alarm rate on
healthy runs, detection rate on fault runs, Wilson 95 % confidence intervals.

---

## Stage 4: diagnosis (`pipeline/diagnosis/`)

**Question:** given a slow run, which bottleneck caused it?

First, every feature is made **relative to the operation's healthy baseline**
(`relative.py`): log₂ ratios for amounts, differences for shares and ratios. "21 queries" means
nothing on its own; "20 more queries than healthy" is the signal. This is also what lets a
model trained on some operations work on an operation it has never seen.

**Method A: mechanism rules** (`rules.py`, the interpretable baseline). There is one rule
per class, built from how each fault works. Each rule returns **evidence** in plain words:

| Class | Fires when (relative to healthy) | Kept apart from |
|---|---|---|
| N_PLUS_ONE | ≥ 2 more queries per request **and** one identical query repeated ≥ 2 more times | — |
| DB_BOTTLENECK | DB time ≥ 2× and ≥ 2 ms more, DB share +15 points, **same** query count | N+1 and cache (which add queries) |
| LARGE_PAYLOAD | response ≥ 4× larger | — |
| CACHE_OPPORTUNITY | cache hit ratio ≥ 0.5 lower than a cache-using baseline | — |
| CPU_BOTTLENECK | time in application code ≥ 2× and ≥ 3 ms more, **same** queries, response < 2× | payload (which also costs CPU) |

No rule fires → `INSUFFICIENT_EVIDENCE`: the system says it doesn't know instead of guessing.
Several fire → the strongest wins (confidence "medium"). The thresholds were set from the
mechanisms and checked on the pilot only, never tuned on the main or held-out data.

Example evidence: *"21.0 queries per request vs 1.0 healthy; one query repeated 20x per
request vs 1x"* → N_PLUS_ONE.

**Method B: machine learning** (`ml.py`). Random Forest (300 trees) and XGBoost (200
trees, depth 4) are trained on the same relative features, with balanced class weights. Each
is trained at each telemetry level L1–L4 to answer RQ3. They can **abstain** when their top
probability is below 0.5 or 0.7 (RQ5).

**Fair evaluation** (`pipeline/evaluation/diagnosis.py`). All methods use the same runs and
splits:
- `grouped`: all repeats of a condition are in the same fold, so the model never sees the
  exact condition it is tested on;
- `operation`: a whole operation is held out (*leave-one-operation-out*);
- final: train on the main campaign, test once on the held-out session.

---

## Stage 5: remediation (`pipeline/remediation/catalogue.py`)

**Question:** what should the developer change?

A catalogue maps each class to one reviewed fix: what to change in plain words, the fixed
variant that implements it, and which metric must move if the fix works:

| Class | Recommended fix | Must improve |
|---|---|---|
| DB_BOTTLENECK | add the missing index (then ANALYZE) | DB time ↓, DB share ↓ |
| N_PLUS_ONE | replace per-row queries with one JOIN / batched query | queries per request ↓, repeats ↓ |
| LARGE_PAYLOAD | paginate and select only needed fields | response size ↓ |
| CACHE_OPPORTUNITY | stable cache key with a TTL | hit ratio ↑, queries ↓ |
| CPU_BOTTLENECK | remove the expensive computation from the request path | app time ↓, event-loop delay ↓ |

`INSUFFICIENT_EVIDENCE` → "collect more evidence (re-run with full tracing, L4)". The system
**never edits code**. It switches the operation to the pre-written fixed variant, as a
developer would deploy a reviewed fix.

---

## Stage 6: re-run and verification (`pipeline/verification/stats.py`)

**Question:** did the fix really help, and is the endpoint back to normal?

The same workload (same seed) runs again with the fixed variant. Then three checks:

1. **Improved?** One-sided Welch t-test on log server latency (faulty vs fixed runs), with
   Holm correction across all fixes, α = 0.05. An exact Mann–Whitney test is the check.
2. **Back to healthy?** TOST equivalence: the 90 % confidence interval of fixed / healthy
   must lie within ±10 %.
3. **Right mechanism?** The metric the catalogue names must have moved in the expected
   direction (e.g. queries per request went down for an N+1 fix).

Verdicts: `improved, back to healthy` / `improved, not shown equivalent to healthy` /
`improvement not shown`.

---

## Stage 7: the closed loop (`experiments/campaign/loop.py`)

All stages in one command, on a live API:

```
python -m campaign.loop --operation users.list --variant nplus1:20 --baseline-campaign main-20261111T100109-be3c28
```

1. **Observe:** 3 runs with a fault the system is not told about.
2. **Detect:** anomalous? (baseline from the main campaign's healthy runs)
3. **Diagnose:** rules plus evidence.
4. **Remediate:** pick the fix from the catalogue and restart the operation with it.
5. **Re-run:** 3 runs with the fix.
6. **Verify:** improved? back to healthy?

The injected variant is revealed only at the end. Output:
`data/results/loops/<loop-id>/report.json`, shown on the dashboard.

---

## Stage 8: outputs

- **Dashboard** (`dashboard/`, Next.js, read-only): see [08-dashboard-guide.md](08-dashboard-guide.md).
- **Results report** (`python -m evaluation.report` → [results.md](results.md)): one section
  per research question plus threats to validity, generated from the result files.

One command runs stages 2–6 for a campaign: `scripts/analyze-campaign.sh <campaign-id>`.
