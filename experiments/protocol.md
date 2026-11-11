# Experiment protocol (v1, frozen)

**Status: frozen on 11 Nov 2026 by team decision, before the main campaign.**

- The values in §9 are the ones the pilot data supports. The team adopted them as proposed;
  they are to be presented to Prof. G. Sagar.
- If he asks for changes before the main data is analysed, they are recorded under
  "Amendments" and applied to the analysis.
- Any other change is also recorded under "Amendments", with its date and reason.

The protocol fixes, before the main data is collected, what will be run, how often, in which
order, and which runs are excluded. This prevents choosing the analysis after seeing the
results. The figures come from the pilot campaign `pilot-20261111T004216-0b0138` (48 runs,
`data/results/campaigns/pilot-20261111T004216-0b0138/report.md`).

## 1. Question and unit

- Research questions RQ1–RQ6: [docs/01-project-overview.md](../docs/01-project-overview.md).
- **Unit of analysis: the run.** One run is one operation under one implementation variant,
  with a fresh lab state, a warm-up, then a measured phase. Requests within a run are not
  independent samples.
- **Ground truth:** the variant of each run, stored only in `data/labels/<campaign>.jsonl`.
  Detection and diagnosis never read it. ML training reads it for training-fold runs only.

## 2. Environment (fixed for all data)

| Item | Value |
|---|---|
| Machine | Project PC: Windows 11 Pro, 8 logical CPUs, 16 GB, Docker Desktop 4.99 (WSL2: 6 CPUs, 8 GB) |
| Lab | PostgreSQL 16.15, Redis 7.4.12 (pinned by digest), fixed container limits (`infrastructure/README.md`) |
| Target API | Docker image `abr-target-api:lab` (Node 20), in the lab network, 2 CPUs / 1 GB; image ID recorded per campaign |
| Data | `large` seed (1M orders, deterministic); reset to the seed before every run |
| Client | Python workload runner on the host; calibration passed up to 100 rps |
| Code | One git commit for the whole campaign (recorded in the manifest; `git_dirty` must be false) |

During a campaign the PC runs nothing else heavy. The campaign starts with Docker Desktop
freshly started and the lab up for at least 5 minutes.

## 3. Workload (identical for every run of an operation)

| Parameter | Value | Reason (pilot) |
|---|---|---|
| Load model | open loop, Poisson arrivals | queueing stays visible; arrivals realistic |
| Rate | 5 rps; `reports` 0.5 rps | below the capacity of every faulty variant. At 1 rps the reports cache fault collapsed (latency grew without bound) instead of slowing down |
| Measured phase | 30 s; `reports` 60 s | about 150 requests per run (30 for reports) |
| Warm-up | 20 requests; `reports` 3 | caches and connections warm |
| Request mix | fixed per operation (`experiments/campaign/plan.py`), same workload seed for all runs of an operation | runs of one operation differ only in the variant |
| Max in flight / timeout | 50 workers / 30 s | |

## 4. Conditions

| Fault (class) | Operation | Severities | Notes |
|---|---|---|---|
| `nplus1` (N_PLUS_ONE) | users.list | 5, 20, 50 rows | 5 = weak |
| `nplus1` | users.get | 3, 10, 30 orders | 10 = weak in the pilot (server ×2.2) |
| `payload` (LARGE_PAYLOAD) | products.list | 200, 2000, 5000 rows | |
| `payload` | search | 100, 1000, 3000 rows | |
| `cpu` (CPU_BOTTLENECK) | products.get | 20, 100, 300 k iterations | 20 = weak (about +4 ms) |
| `cpu` | dashboard | 20, 100, 300 k iterations | |
| `noindex` (DB_BOTTLENECK) | orders.list, search | – (scales with data size) | |
| `cache` (CACHE_OPPORTUNITY) | products.get, reports | – | products.get = weak (server ×2.0) |

This gives 22 fault conditions (18 with three severities, plus 4 without a severity), every
fault in two operations, and weak, medium and strong effects.

**Healthy (A/A) runs:** each of the 8 operations runs healthy many times. These runs define
each operation's baseline and measure false alarms; anything flagged in a healthy run is a
false alarm.

**Fixed variants:** each of the 10 fault/operation pairs at its default severity. These
feed verification (Phase 7) and the "back to healthy" check.

## 5. Repeats and campaign size

From the pilot's healthy runs (server-side run p50; client-side in brackets):

| | CV of healthy run p50 | Runs for a 20 % slowdown (α 0.05, power 0.8) |
|---|---|---|
| Server-side | 0.2–8.3 % | 2–3 |
| Client-side | 4–24 % | 2–23 |

Detection and diagnosis use server-side telemetry (L2 and above). Client-only telemetry (L1)
is evaluated as the weakest level in RQ3. The proposed repeats leave margin for that, and
give enough runs to train and test the ML models:

| Block | Runs |
|---|---|
| Healthy: 8 operations × 10 repeats | 80 |
| Faults: 22 conditions × 5 repeats | 110 |
| Fixed: 10 pairs × 3 repeats | 30 |
| **Main campaign** | **220 runs, about 3.7 h** (pilot: 59 s per run) |
| **Held-out session** (separate day, new campaign seed): healthy 8 × 3, faults 22 × 2 | **68 runs, about 70 min** |

The held-out session is analysed only once, at the end (final evaluation). It is never used
to choose features, thresholds or models.

## 6. Order and control

- **Run order is shuffled** with the campaign seed. Runs are executed one at a time; every
  run starts from the reset lab.
- One **warm-up run** at the start of each campaign session is executed and discarded. In
  the pilot, run 1 (the cold start) had the worst client send lag, 41.5 ms.
- If a campaign is interrupted, it is resumed with `--resume`. Runs already completed are
  kept.

## 7. Exclusion rules (decided now, applied automatically, reported)

A run is **excluded** if any of the following holds:
1. request-to-span correlation is not complete;
2. more than 1 % of measured requests failed;
3. client send-lag p99 > 10 ms (the client did not keep the timetable);
4. the run's `git_dirty` flag is true, or its image content identity differs from the campaign's (amendment of 11 Nov).
5. the machine was on battery at the start or end of the run (`exclusion.require_ac_power`; amendment of 11 Nov). A campaign refuses to start on battery.

Excluded runs are listed with their reason. They are re-run once at the end of the
campaign, with the same condition and a new position. The number excluded per condition is
reported, so a reader can check that exclusions are not related to a condition (selection
bias).

Pilot check:
- Rules 1 and 2 would have excluded no run.
- Rule 3 would have excluded 2 of 48 runs (run 1, cold start, 41.5 ms; run 19, 32.8 ms).
- Rule 4 would have excluded all 48. The pilot ran on commit `7c2f018` with the new
  campaign code not yet committed (`git_dirty: true`). That is acceptable for a pilot,
  whose data is not part of the analysis. The main campaign must start from a committed tree.

## 8. Analysis plan (fixed now, run later)

- **Detection (RQ1):** per operation, a run is anomalous if its server-side run p50
  (later: its baseline-relative features) exceeds a threshold derived from that operation's
  healthy runs in the main campaign. Reported: false-alarm rate on healthy runs and
  detection rate per fault and severity, with confidence intervals, on the held-out session.
- **Diagnosis (RQ2, RQ4, RQ5):** rules versus Random Forest versus XGBoost, on the same runs
  and splits.
  - Splits keep all repeats of a condition together; the other split is
    leave-one-operation-out.
  - Final numbers come from the held-out session.
  - Macro-F1, per-class precision and recall, confusion matrix, abstention coverage
    against accuracy.
- **Telemetry levels (RQ3):** L1 (client only), L2 (+ server timing), L3 (+ runtime signals),
  L4 (+ database and cache spans), all on identical runs.
- **Verification (RQ6):** fixed versus faulty runs per pair, a paired comparison (Wilcoxon)
  with effect size and a bootstrap CI; fixed versus healthy with an equivalence test within
  the margin below.

## 9. Decisions (team, 11 Nov 2026; to be presented to Prof. Sagar)

| # | Decision | Value adopted | Basis in the pilot |
|---|---|---|---|
| 1 | Minimum meaningful slowdown for "anomalous" | **20 %** over the operation's healthy baseline (server-side run p50) | healthy server CV ≤ 8.3 %, so 20 % is ≥ 2.4 CV above normal variation |
| 2 | Detection threshold per operation | **threshold = max(P99 of the healthy run p50s, 1.2 × healthy median)**, from the main campaign's 10 healthy runs per operation; run is *anomalous* if its server-side run p50 > threshold | all 10 pilot fault conditions above it in every run; both fixed variants below |
| 3 | Acceptable false-alarm rate | **≤ 5 %** of healthy runs (held-out session) | |
| 4 | Equivalence margin for "back to healthy" | **± 10 %** of the healthy median (server-side run p50) | pilot fixed variants at ×0.90 and ×0.99 |
| 5 | Repeats | **main 220 runs, held-out 68 runs** (§5) | server-side 2–3 repeats suffice for 20 %; extra repeats for ML and margin |
| 6 | Severities | **§4**, three levels incl. weak ones | heaviest levels checked on 11 Nov: send-lag p99 ≤ 4.6 ms, 0 failures (scratch check, not data) |
| 7 | Exclusion rules | **§7**, enforced by `experiments/campaign/quality.py` | |

**Preliminary thresholds from the pilot** (3 healthy runs per operation; for orientation
only, because the final values come from the main campaign as in decision 2):

| Operation | Healthy median (ms) | Healthy max (ms) | 1.2 × median (ms) | Preliminary threshold (ms) | Pilot fault runs above |
|---|---|---|---|---|---|
| dashboard | 19.66 | 21.62 | 23.59 | 23.59 | 2/2 |
| orders.list | 5.49 | 5.75 | 6.59 | 6.59 | 2/2 |
| products.get | 3.69 | 3.75 | 4.42 | 4.42 | 4/4 |
| products.list | 4.98 | 5.02 | 5.97 | 5.97 | 2/2 |
| reports | 4.62 | 4.77 | 5.54 | 5.54 | 2/2 (cache-fixed below) |
| search | 4.61 | 4.82 | 5.53 | 5.53 | 4/4 |
| users.get | 6.70 | 6.70 | 8.04 | 8.04 | 2/2 |
| users.list | 5.05 | 5.15 | 6.06 | 6.06 | 2/2 (nplus1-fixed below) |

Detection (Phase 4) may later use baseline-relative features instead of the server-side
run p50 alone. The rule above remains the detector of record, and any new detector is
compared against it.

## Amendments

| Date | Change | Reason |
|---|---|---|
| 11 Nov 2026 | Draft → v1 frozen; §9 values adopted by the team as proposed; preliminary thresholds added | team decision to proceed with the pilot-supported values; to be presented to Prof. Sagar |
| 11 Nov 2026 | §8 verification statistics: unpaired one-sided Welch t-test on log server-side run p50 (Holm across pairs) with exact Mann-Whitney as a check, and TOST equivalence (90 % CI within ±10 %), instead of a paired Wilcoxon test | faulty and fixed runs are independent runs, not pairs; with 5 vs 3 runs the exact rank test's smallest p (1/56) cannot pass a Holm correction over 10 pairs |
| 11 Nov 2026 | §6/§7 enforced in code: `require_clean_git`, `discard_warmup_runs`, `rerun_excluded`, `apply_exclusions` (`experiments/campaign/run.py`, `quality.py`); campaign configs `main.json`, `heldout.json` | the rules are applied automatically and reported, not by hand |
| 11 Nov 2026 | §7 rule 4 compares the image's **content identity** (hash of layers + config, `lab.image_id`) instead of Docker's image ID; the API is no longer rebuilt before each run; the lab reset is retried once after a PostgreSQL restart | In the main campaign every run recorded a different Docker image ID although the code was unchanged: with Docker Desktop's containerd store the ID changes on every build (build metadata), while layers, config and creation time (2026-11-10 18:46 UTC) stayed identical; verified by rebuilding twice. `target-api/` had no commits during the campaign. For campaigns recorded with Docker IDs the rule cannot be applied from the IDs and is skipped. Separately, PostgreSQL restarted once during a reset (11:03), failing 2 runs; they are completed with `--resume`. |
| 11 Nov 2026 | §7 rule 5: runs on battery are excluded; the power state is recorded per run and the power plan per campaign (`campaign/power.py`); `main.json` and `heldout.json` set `require_ac_power` | The laptop switched between charger and battery five times during the main campaign (Windows Kernel-Power events). Healthy runs on battery were x1.6-2.3 slower than on the charger, while runs within one power state varied by 1-8 % (CV). Of 244 completed runs, 100 were on the charger, 137 on battery, 7 mixed (`data/raw/campaigns/<id>/power.json`, from the event log). Decided after looking at the main campaign, before the held-out session. |
