# Progress tracker

**Automated API Bottleneck Detection, Diagnosis and Verified Remediation**. B.Tech CSE
major project, RGUKT Basar. This is the single place for project progress. It is updated at
the end of every phase. Detailed notes per phase: [PROJECT_STATUS.md](PROJECT_STATUS.md).
Plan: [docs/04-roadmap.md](docs/04-roadmap.md).

Last updated: **11 Nov 2026**. Branch `develop`. Git is managed by the team: one commit
per phase, merged to `main` after review.

## Research loop

| Stage | Observe | Detect | Diagnose | Remediate | Re-run | Verify | Dashboard |
|---|---|---|---|---|---|---|---|
| Status | ✅ | ⬜ | ⬜ | 🟡 | ✅ | ⬜ | ⬜ |

- **Observe ✅:** telemetry, workload generator, lab and faults are built and verified.
- **Remediate 🟡:** the fixed variants exist; the recommendation engine does not.
- **Re-run ✅:** any workload can be repeated exactly from its seed.
- **Detect, Diagnose, Verify, Dashboard ⬜:** not started.

The data-generation platform is complete. The analysis side (detection, diagnosis, ML,
remediation engine, verification, dashboard) has not started.

## Phases

| Phase | Content | Commit | Gate | Status |
|---|---|---|---|---|
| 1–3 | Instrumented API, workload runner, telemetry analyzer (original) | `911af77` | tests pass | ✅ |
| 0 | Windows/WSL setup, LF line endings, CI workflow, docs | `15c1c39` | `test-all.sh` passes | ✅ |
| 3.5A | Span parenting fixed, W3C traceparent, per-request join, per-run span files, runtime metrics | `15c1c39` | 300/300 requests matched to their spans | ✅ |
| 3.5B | Open-loop workload (constant/Poisson, stages, variants), send-lag recording, calibration | `60d185a` | timetable kept up to 100 rps, lag p99 ≤ 3.9 ms | ✅ |
| 3.5C-1 | Local lab: Docker PostgreSQL 16.15 + Redis 7.4.12, env profiles, lab probe | `8af1f81` | round trip p99 0.33 ms; reset reproducible | ✅ |
| 3.5C-2 | Domain API (9 operations), deterministic seed up to 1M orders, variant registry, API in Docker | `cfa029e` | lab tests 5/5; seed byte-identical | ✅ |
| 3.5C-3 | Faults × 2 operations each, with fixed variants and severities | `7c2f018` | 10/10 fault checks, 3/3 severity ladders | ✅ |
| 3.5D-1 | Campaign orchestrator, ground-truth labels, pilot campaign + pilot report, protocol draft | (to commit) | pilot 48/48 runs, 100 % requests matched; noise and effects measured | ✅ verified, to commit |
| 3.5D-2 | Protocol approval (Prof. Sagar), main campaign (220 runs) + held-out session (68 runs) | — | protocol approved; data-quality report clean | ⬜ waiting for approval |
| Analyzer v2 | Server-side features, baseline-relative features, telemetry levels L1–L4 | — | no single feature separates operations | ⬜ |
| 4 | Detection: normal / anomalous / inconclusive against healthy baselines | — | false-alarm and detection rates on held-out data | ⬜ |
| 5 | Diagnosis: rules → Random Forest / XGBoost, abstention, L1–L4 | — | all methods on identical runs and splits | ⬜ |
| 6 | Remediation engine (fault → fix), applied by switching to the fixed variant | — | every recommendation traceable to its evidence | ⬜ |
| 7 | Verification: paired before/after runs, statistics, back to healthy | — | results for every fault | ⬜ |
| 8 | Dashboard (Next.js) | — | every number traceable to a run | ⬜ |
| 9 | Evaluation and report | — | every claim points to a result file | ⬜ |

Legend: ✅ done and committed · 🔄 in progress · ⬜ not started.

## What exists

| Component | Path | Summary |
|---|---|---|
| Target API | `target-api/` | Express 5 + OpenTelemetry. 9 domain operations on PostgreSQL + Redis. Variants per operation (`ABR_VARIANTS`). Docker image (Node 20). [README](target-api/README.md), [TELEMETRY](target-api/TELEMETRY.md) |
| Database | `target-api/db/` | 5 tables, deterministic seed: small, medium, or large (1M orders, 549 MB, ~35 s), 6 named indexes |
| Lab | `infrastructure/`, `scripts/lab-*.sh` | Pinned containers on 127.0.0.1 with fixed limits; up / seed / reset (~3 s) / status / probe / api. [README](infrastructure/README.md) |
| Workload runner | `experiments/experiment_runner/` | Closed and open loop, Poisson, rate stages, request variants, send lag, traceparent, calibration |
| Telemetry analysis | `pipeline/telemetry_analysis/` | Run-level client features, labels kept apart, leakage checks, request-to-span join |
| Tests | `scripts/test-all.sh`, `npm run test:lab`, `npm run lab:fault-checks` | 16 (+5 lab) / 22 / 14 pass; lab 5/5; fault checks 10/10 |

### Injected faults (Phase 3.5C-3)

| Fault (class) | Operations | Faulty vs fixed (server time, median) |
|---|---|---|
| `noindex` (DB_BOTTLENECK) | orders.list, search | 53.3 vs 6.1 ms; index absent |
| `nplus1` (N_PLUS_ONE) | users.list, users.get | 25.2 vs 4.0 ms; 21 vs 1 queries per request |
| `payload` (LARGE_PAYLOAD) | products.list, search | 3.09 MB vs 1.7 kB |
| `cache` (CACHE_OPPORTUNITY) | products.get, reports | 791 vs 2.5 ms (reports) |
| `cpu` (CPU_BOTTLENECK) | products.get, dashboard | 24.6 vs 2.6 ms, same queries |

## Key measurements

| Measurement | Result | Source |
|---|---|---|
| Client calibration | 5–100 rps kept; lag p99 0.5–3.9 ms; 0 failures | `data/results/calibration/20261110T223824-36389f56/` |
| Lab round trip (Docker network) | PostgreSQL p99 0.33 ms, Redis p99 0.32 ms (cloud: 113–317 ms) | `data/results/lab-probe/` |
| API in Docker vs host | DB span p50 0.47 vs 0.90 ms | 3.5C-2 notes |
| Healthy baseline (large) | all operations p50 4–12 ms | 3.5C-2 notes |
| Fault activation | 10/10 checks, 3/3 ladders | `data/results/fault-checks/2026-11-10T18-39-58-859Z/` |
| **Pilot campaign** (48 runs, 47.5 min) | 48/48 completed; 7,459/7,459 requests matched to their spans; 0 failed requests | `data/results/campaigns/pilot-20261111T004216-0b0138/report.md` |
| Pilot: healthy noise (CV of run p50) | **server-side 0.2–8.3 %** vs client-side 4–24 % → 2–3 vs up to 23 repeats for a 20 % slowdown | same |
| Pilot: faults vs healthy (server run p50) | **10/10 fully separated**, ×2.0 (products.get cache) to ×87.9 (reports cache); client-side 9/10 | same |
| Pilot: fixed variants | back at healthy level: ×0.90 (reports cache-fixed), ×0.99 (users.list nplus1-fixed) | same |

## Decisions

| Date | Decision | Reason |
|---|---|---|
| 10 Nov | Local lab for all experiments; cloud (Neon, Redis Cloud) for application use only | Cloud round trips (113–317 ms) exceed the effects studied |
| 10 Nov | This PC is the experiment machine | Calibration and lab gate passed here |
| 10 Nov | Target API runs inside the lab's Docker network during experiments | Host port forwarding added ~1 ms per query (inflates N+1) |
| 10 Nov | The experimental unit is the run, not the request | Requests in one run share process and machine state |
| 10 Nov | Python runner kept; no k6 | Calibration passed up to 100 rps |
| 11 Nov | Fixed variants return the same responses as faulty ones | Before/after comparisons measure only the fix |
| 11 Nov | Weak faults kept (products.get cache, users.get N+1) | Realistic hard cases for detection |
| 11 Nov | `reports` workload: 0.5 rps, 60 s, days ∈ {7, 14, 30} | At 1 rps (days up to 90) the cache fault collapsed instead of slowing down (smoke campaign) |
| 11 Nov | Detection uses server-side telemetry (L2+); client-only (L1) is the weakest level in RQ3 | Pilot: client floor (~15 ms) makes client CV up to 3× larger; server separates all 10 faults |
| 11 Nov | Protocol draft: main 220 runs, held-out 68 runs, exclusion rules, discarded warm-up run | Pilot noise and effect sizes (`experiments/protocol.md`) |

## Open items

| Item | Owner | Status |
|---|---|---|
| Copy the legacy data from the laptop backup; run `scripts/verify-legacy.sh` | team | open |
| Push `develop` and check the GitHub Actions run | team | open |
| Rotate Neon and Redis Cloud passwords (pasted in chat) | team | open |
| **Approve the protocol** (`experiments/protocol.md`, §9: thresholds, false-alarm rate, equivalence margin, repeats, severities, exclusions) | Prof. Sagar | draft ready (11 Nov) |
| Commit before the main campaign (`git_dirty` must be false; the pilot ran dirty) | team | open |

## Next steps

1. Commit 3.5D-1. Prof. Sagar reviews `experiments/protocol.md`.
2. **Analyzer v2 (can start now, on the pilot data):** server-side and database/cache features
   per request and run, baseline-relative features, telemetry levels L1–L4, extended leakage
   audit.
3. 3.5D-2: main campaign (220 runs, about 3.7 h) and held-out session (68 runs) once the
   protocol is approved.
4. Detection → diagnosis → remediation → verification → dashboard → report.
