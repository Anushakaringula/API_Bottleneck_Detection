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
| Status | ✅ | ✅ | ✅ | ✅ | ✅ | 🟡 | ✅ |

Every stage is built and has run on the main campaign (220 runs), including two real closed
loops (inject → detect → diagnose → fix → re-run → verified). **Verify 🟡:** 7/10 fixes shown
to improve, none shown back to healthy within ±10 %; the laptop's charger/battery switching
widened the intervals (see Key measurements). Final numbers: held-out session (another day,
on the charger).

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
| 3.5D-1 | Campaign orchestrator, ground-truth labels, pilot campaign + pilot report, protocol draft | `0275bbb` | pilot 48/48 runs, 100 % requests matched; noise and effects measured | ✅ |
| 3.5D-2 | Protocol v1 frozen (team decision), rules enforced in code, main campaign (220 runs) + held-out session (68 runs, another day) | `4c0f60d`, `810966a` | data-quality report clean; exclusions reported | 🔄 main campaign done (`main-20261111T100109-be3c28`, 220 + 24 re-runs, 0 failed requests); held-out session open |
| Analyzer v2 | Run-level features from request telemetry, 30 features in levels L1–L4, label-free (`pipeline/features/`) | committed | every pilot fault visible in its mechanism feature; tests 5/5 | ✅ |
| 4 | Detection: detector of record (protocol rule), verdicts with evidence, leave-one-out scoring, label access isolated (`pipeline/detection/`, `pipeline/evaluation/`) | committed | main: false alarms 4/80 = 5.0 % (limit ≤ 5 %), detected 103/110 = 93.6 % | ✅ dev numbers; final on held-out |
| 5 | Diagnosis: mechanism rules with evidence vs Random Forest / XGBoost on baseline-relative features, L1–L4, grouped + leave-one-operation-out CV, abstention (`pipeline/diagnosis/`) | `6aa367a` | main: rules macro-F1 1.000; RF L4 0.946 (0.955 operation unseen); L1 ≈ 0.29 | ✅ dev numbers; final on held-out |
| 6 | Remediation catalogue + recommender (class → fix variant, expected effect, evidence) (`pipeline/remediation/`) | `c4855f5` | main: right fix 110/110 | ✅ |
| 7 | Verification: Welch on log latency (Holm), TOST back-to-healthy (±10 %), mechanism check; closed-loop demo `campaign.loop` (`pipeline/verification/`) | `c4855f5` | main: 7/10 improved (Holm), 0/10 back to healthy; 2/2 closed loops verified | 🟡 power noise (amendment 5) |
| 8 | Dashboard (Next.js 16, read-only over result files): overview, endpoint evidence, closed-loop page; `scripts/analyze-campaign.sh` (one command: summary → features → detection → diagnosis → verification) | `43608ed` | every number traceable to a file | ✅ built; pages checked against the main results |
| 9 | Results report generated from result files, one section per RQ + threats to validity (`python -m evaluation.report` → `docs/results.md`) | `99466a5` + (to commit) | every claim points to a result file | 🔄 `docs/results.md` with main numbers; final after held-out |

Legend: ✅ done and committed · 🔄 in progress · ⬜ not started.

## What exists

| Component | Path | Summary |
|---|---|---|
| Target API | `target-api/` | Express 5 + OpenTelemetry. 9 domain operations on PostgreSQL + Redis. Variants per operation (`ABR_VARIANTS`). Docker image (Node 20). [README](target-api/README.md), [TELEMETRY](target-api/TELEMETRY.md) |
| Database | `target-api/db/` | 5 tables, deterministic seed: small, medium, or large (1M orders, 549 MB, ~35 s), 6 named indexes |
| Lab | `infrastructure/`, `scripts/lab-*.sh` | Pinned containers on 127.0.0.1 with fixed limits; up / seed / reset (~3 s) / status / probe / api. [README](infrastructure/README.md) |
| Workload runner | `experiments/experiment_runner/` | Closed and open loop, Poisson, rate stages, request variants, send lag, traceparent, calibration |
| Telemetry analysis | `pipeline/telemetry_analysis/` | Run-level client features, labels kept apart, leakage checks, request-to-span join |
| Dashboard | `dashboard/` | Next.js read-only views of all results; [README](dashboard/README.md) |
| Remediation + verification | `pipeline/remediation/`, `pipeline/verification/`, `experiments/campaign/loop.py` | Fix catalogue, recommender, before/after statistics, closed-loop demo; [README](pipeline/verification/README.md) |
| Diagnosis | `pipeline/diagnosis/` | Baseline-relative features, mechanism rules with evidence, RF / XGBoost with abstention; evaluated per level L1–L4; [README](pipeline/diagnosis/README.md) |
| Detection | `pipeline/detection/`, `pipeline/evaluation/` | Protocol rule per operation, verdicts with evidence, false-alarm / detection rates with 95 % CIs; only `evaluation/labels.py` reads labels; [README](pipeline/detection/README.md) |
| Features (Analyzer v2) | `pipeline/features/` | 30 run features in L1–L4 (client, server, runtime, database/cache), interval-union database time, metadata kept apart; [README](pipeline/features/README.md) |
| Results | `docs/results.md` (generated), `data/results/` | One section per RQ + threats to validity; `python -m evaluation.report --main <id> [--heldout <id>]` |
| Tests | `scripts/test-all.sh`, `npm run test:lab`, `npm run lab:fault-checks` | 16 / 23 / 18 / 5 / 7 / 7 / 6 / 14 pass; lab 5/5; fault checks 10/10 |

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
| **Main campaign** (220 runs + 24 re-runs, ~4.5 h) | 35,612/35,612 requests matched to spans; 0 failed requests; 24 runs excluded (send lag) and re-run; 2 runs failed once (PostgreSQL restart) and completed on resume | `data/results/campaigns/main-20261111T100109-be3c28/` |
| Main: detection (RQ1) | false alarms **4/80 = 5.0 %** (CI 2.0–12.2 %), detected **103/110 = 93.6 %** (CI 87.4–96.9 %), precision 96.3 %; misses: users.get nplus1:3, dashboard cpu:20 | `data/results/detection/main-20261111T100109-be3c28/` |
| Main: diagnosis (RQ2–5) | rules **110/110** (macro-F1 1.000); RF L1 0.29 → L3 0.55 → L4 0.95; XGB L4 0.67; RF at confidence 0.5: 84 % coverage, 100 % accurate | `data/results/diagnosis/main-20261111T100109-be3c28/` |
| Main: remediation + verification (RQ6) | right fix 110/110; **7/10** fixes improved (Holm α 0.05); 0/10 shown within ±10 % of healthy | `data/results/verification/main-20261111T100109-be3c28/` |
| Closed loops | users.list nplus1:20 → N_PLUS_ONE → fixed: 24.05 → 5.11 ms (healthy 4.45); orders.list noindex → DB_BOTTLENECK → fixed: 86.20 → 5.31 ms (healthy 5.05); both verified | `data/results/loops/` |
| Power state (main) | 100 runs on charger, 137 on battery, 7 mixed; healthy on battery ×1.6–2.3 slower; within one state CV 1–8 % | `data/raw/campaigns/main-20261111T100109-be3c28/power.json` |

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
| 11 Nov | **Protocol v1 frozen by team decision** with the pilot-supported values: anomalous = server-side run p50 > max(P99 healthy, 1.2 × healthy median); false alarms ≤ 5 %; back-to-healthy ± 10 %; preliminary thresholds 4.4–23.6 ms per operation | Pilot: every fault run above its threshold, both fixed variants below; to be presented to Prof. Sagar |
| 11 Nov | Protocol rules enforced in code (clean git, discarded warm-up run, exclusions with one re-run) | Rules applied automatically, not by hand |
| 11 Nov | Image rule compares a content hash (layers + config); API no longer rebuilt per run; reset retried after a PostgreSQL restart (amendment 4) | Docker Desktop changes the image ID on every build of identical code: all 218 runs were falsely excluded |
| 11 Nov | Runs on battery excluded, campaigns refuse to start on battery (rule 5, amendment 5); main campaign kept as is and the power mix reported as a threat (team decision) | Healthy latency ×1.6–2.3 higher on battery; a 4 h re-run was not worth the delay |
| 11 Nov | Diagnosis evaluated on fault runs only (detection handles healthy vs faulty); CI and the experiment PC on Python 3.12 (`requirements.txt`: scikit-learn 1.9.1, XGBoost 3.4.1) | Detection → diagnosis is the architecture; numpy 2.5 needs Python 3.11+ |

## Open items

| Item | Owner | Status |
|---|---|---|
| Copy the legacy data from the laptop backup; run `scripts/verify-legacy.sh` | team | open |
| Push `develop` and check the GitHub Actions run | team | open |
| Rotate Neon and Redis Cloud passwords (pasted in chat) | team | open |
| Present protocol v1 (`experiments/protocol.md` §9, adopted by the team on 11 Nov) to Prof. Sagar; record any requested change as an amendment | team | open |
| Run the held-out session on a different day, **charger plugged in the whole time** (~75 min) | team | open |

## Next steps

1. Commit this step.
2. **Held-out session** (another day, charger plugged in; the campaign refuses to start on battery):
   `python -m campaign.run --config experiments/configs/campaigns/heldout.json` (68 runs, ~75 min).
3. `scripts/analyze-campaign.sh <heldout-id> --train main-20261111T100109-be3c28` → final detection and diagnosis.
4. `python -m evaluation.report --main main-20261111T100109-be3c28 --heldout <heldout-id>` → final `docs/results.md`.
5. Thesis chapters from `docs/results.md`; related work (GA-Prof, RCA surveys); demo with the dashboard
   (`cd dashboard && npm run dev`) and `python -m campaign.loop`.
