# Features (Analyzer v2)

Turns one campaign's telemetry into one feature row per run, grouped by telemetry level for
RQ3. **It reads no ground truth.** Baseline-relative features ("how much slower than this
operation's healthy runs") are computed in detection (Phase 4), because choosing the
healthy runs uses labels.

```sh
source scripts/env.sh
python -m features.extract <campaign-id>        # -> data/features/<campaign-id>/
python -m unittest discover -s pipeline/features/tests
```

| Output | Contents |
|---|---|
| `features.jsonl` | `experiment_id` + numeric predictors only (allowlist: `schema.py`) |
| `runs.jsonl` | metadata, never predictors: `route` (operation identity, taken from telemetry), span coverage, protocol exclusion reasons |
| `schema.json` | feature → level and meaning |

| Level | Adds | Examples |
|---|---|---|
| L1 | client view | client latency p50/p95/p99, response size, error rate |
| L2 | server timing | server latency p50/p95/p99, client–server gap |
| L3 | process / runtime | CPU per request, event-loop delay, event-loop utilization, GC |
| L4 | database and cache tracing | queries per request, repeated identical query (N+1 signature), database time share, cache hit ratio, app-code time share |

Each client request is joined to its server spans by trace ID. Database and cache time is
the wall-clock time covered by their spans (the union of intervals), so parallel queries
count once. Only runs with status `completed` are extracted; failed runs and discarded
warm-up runs are skipped. Excluded runs keep their features, and their reasons are listed in
`runs.jsonl`.

Pilot check (48 runs): every fault shows the feature its mechanism predicts.
- N+1: 21 vs 1 queries per request; one query repeated 20×.
- Missing index: database time share 0.97 vs 0.43.
- Payload: 3.09 MB vs 1.7 kB.
- Cache: hit ratio 0 vs 0.955.
- CPU: app-code share 0.98 vs 0.72, event-loop delay p99 55 vs 11.5 ms.
