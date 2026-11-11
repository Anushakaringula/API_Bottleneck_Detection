# 6. Experiments and data

What the controlled data is, how much of it there is, how it was produced and where it
lives. Folder rules (who writes, who reads) are in [../data/README.md](../data/README.md).
The binding rules are in the [protocol](../experiments/protocol.md).

## The unit is the run, not the request

A **run** is one 30–60 s experiment. It covers one operation in one condition (healthy, a
fault at one severity, or a fix), against a freshly reset database and a freshly started
API.

Requests inside a run share the same process, database state and moment in time, so they
are **not independent samples**. Counting 35,612 requests as 35,612 data points would
overstate the evidence. Every result is therefore counted in runs ("103/110 fault runs
detected"). This was decided on 10 Nov 2026 (PROGRESS.md, Decisions).

## Levels of data, from coarse to fine

| Level | Main campaign | What it holds | Where |
|---|---|---|---|
| Campaign | 1 | plan, environment (git commit, image, power plan, versions), progress | `data/raw/campaigns/<id>/manifest.json`, `progress.jsonl` |
| **Run** | **220 used** (244 executed: 24 re-runs replaced excluded runs) | one experiment; one row of features | `data/raw/campaigns/<id>/experiments/<run>/` |
| Request | **35,612** (0 failed, 100 % matched to a trace) | client latency, status, bytes, intended/actual send time, trace ID | `.../experiments/<run>/requests.jsonl` |
| Span | many per request (e.g. 21 query spans for one N+1 request) | server timing of the request, every SQL query, every Redis call | `data/raw/spans/runs/<run>.jsonl` |
| Runtime sample | 1 per second | event-loop delay, CPU, heap, GC, RSS, requests in flight | `data/raw/runtime/runtime-metrics.jsonl` |
| Database statistics | 1 per run | per-query calls and time (`pg_stat_statements`) | `data/raw/campaigns/<id>/db-stats/` |
| Features | 244 rows × 30 | run summary used by detection and diagnosis | `data/features/<id>/features.jsonl` |
| Labels (ground truth) | 244 rows | operation, variant, class, severity; read only for evaluation | `data/labels/<id>.jsonl` |

Size on disk: about 430 MB of span files (all campaigns) plus 27 MB of campaign records for
the main campaign. None of it is in git. **Back up the `data/` folder separately.**

## All controlled datasets

| Dataset | ID | Runs | Purpose | Status |
|---|---|---|---|---|
| Smoke | `smoke-…` | a few | tooling check | done |
| Pilot | `pilot-20261111T004216-0b0138` | 48 | measure noise and effect sizes; set thresholds and campaign size | done; never used for final claims |
| **Main campaign** | `main-20261111T100109-be3c28` | **220** (+24 re-runs) | development results for all research questions | done |
| **Held-out session** | (not yet run) | **68** | **final** numbers on data the methods have never seen; different day, own seed | open |
| Closed-loop demos | `loop-20261111T142329-ae2410`, `loop-20261111T142948-d33e74` | 2 × 6 | end-to-end demonstration | done |

Total, once the held-out session is done: about 350 controlled runs and over 50,000 traced
requests.

## What is in the main campaign (220 runs)

Config: `experiments/configs/campaigns/main.json` (seed 2026111201).

| Group | Conditions | Repeats | Runs |
|---|---|---|---|
| Healthy | 8 operations | 10 each | 80 |
| Faults | 22 conditions (below) | 5 each | 110 |
| Fixed variants | 10 fixes | 3 each | 30 |
| **Total** | | | **220** |

Fault conditions:

| Operation | Faults (severities) |
|---|---|
| users.list | nplus1:5, nplus1:20, nplus1:50 |
| users.get | nplus1:3, nplus1:10, nplus1:30 |
| products.list | payload:200, payload:2000, payload:5000 |
| search | payload:100, payload:1000, payload:3000, noindex |
| products.get | cpu:20, cpu:100, cpu:300, cache |
| dashboard | cpu:20, cpu:100, cpu:300 |
| orders.list | noindex |
| reports | cache |

By class: N_PLUS_ONE 30, LARGE_PAYLOAD 30, CPU_BOTTLENECK 30, DB_BOTTLENECK 10,
CACHE_OPPORTUNITY 10 runs. DB and cache faults have no severity levels, so they have fewer
runs; the ML uses balanced class weights, and macro-F1 weights every class equally.

Fixed variants: nplus1-fixed:20 (users.list), nplus1-fixed:10 (users.get), payload-fixed:2000
(products.list), payload-fixed:1000 and noindex-fixed (search), cpu-fixed:100 and
cache-fixed (products.get), cpu-fixed:100 (dashboard), noindex-fixed (orders.list),
cache-fixed (reports).

## Why 220 runs is enough

The size was derived from the pilot's measured noise (protocol §5):
- server-side run-to-run variation (CV) of healthy runs was 0.2–8.3 %, against 4–24 %
  client-side;
- at that noise, 2–3 runs separate a 20 % slowdown, so 5 repeats per fault and 10 per
  healthy operation (for a stable baseline P99) leave a safety margin;
- result: detection rate 93.6 % with a 95 % CI of 87.4–96.9 %. Tight enough to answer the
  research questions.

## The lab (fixed for all data)

| Part | Setting |
|---|---|
| Machine | one Windows 11 laptop, Docker Desktop (WSL2) |
| PostgreSQL | 16.15, pinned image digest; autovacuum off, JIT off, no parallel workers; `pg_stat_statements` on |
| Redis | 7.4.12, pinned image digest |
| Target API | Docker image `abr-target-api:lab` (Node 20), 2 CPUs / 1 GB, in the lab network (avoids ~1 ms per query of port forwarding) |
| Database seed | deterministic (`setseed`); scale *large*: 100k users, 50k products, 1M orders, ~3.0M order items, 549 MB; reset from a template database in ~3 s |
| Workload | open loop, Poisson, 5 requests/s, 30 s measured, 20 warm-up requests (`reports`: 0.5/s, 60 s, 3 warm-up) |
| Client calibration | timetable kept up to 100 requests/s, send-lag p99 ≤ 3.9 ms |

## Protocol rules that protect the data

| Rule | Why |
|---|---|
| Clean git tree before a campaign | every run is traceable to committed code |
| One discarded warm-up run per session | the first run after a cold start is the noisiest |
| Run order shuffled by seed | time effects (e.g. machine state) cannot line up with a condition |
| Labels stored apart; only `evaluation/labels.py` reads them | no leakage into detection or diagnosis |
| Exclude a run if: correlation incomplete, > 1 % failed requests, client send-lag p99 > 10 ms, uncommitted code, different image content, **on battery** | the run did not measure what it should; each excluded run is re-run once at the end |
| Held-out session on another day, analysed once | final claims are not tuned on their own data |
