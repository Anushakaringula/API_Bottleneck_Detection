# 7. Results explained

[results.md](results.md) is the generated report: exact numbers, each with its source file.
This guide explains what those numbers mean, how strong they are, and what happened while
collecting them.

**Status (11 Nov 2026):** these are **development** numbers from the main campaign. The
**final** numbers come from the held-out session (68 runs, another day), with models and
baselines taken from the main campaign. Re-run `python -m evaluation.report --main <main-id>
--heldout <heldout-id>` to update `results.md`.

## The research question

> Is request-level observability telemetry sufficient to identify and distinguish different
> types of API performance bottlenecks? Where it is not, the system should say "insufficient
> evidence" instead of guessing.

## Answers so far

### RQ1 Detection: can it tell a slow API apart from normal variation?
**Yes.**
- False alarms on 4/80 healthy runs (**5.0 %**, 95 % CI 2.0–12.2 %), exactly at the protocol
  limit of ≤ 5 %.
- Faults detected in **103/110** runs (**93.6 %**, CI 87.4–96.9 %); precision 96.3 %.
- Detection rate per class: DB 100 %, payload 100 %, cache 100 %, CPU 90 %, N+1 87 %.
- The misses are the **mildest severities**: `users.get nplus1:3` (1/5 detected, median ×0.90
  of healthy) and `dashboard cpu:20` (2/5, ×0.69). At these settings the slowdown is smaller
  than the 20 % the rule is designed to ignore, so missing them is expected, not a failure.

### RQ2 Diagnosis: which bottleneck caused it?
**Yes, with full tracing.** The mechanism rules diagnosed **110/110** fault runs correctly
(macro-F1 1.000), each with written evidence. Caveat: the rule thresholds were set on the
pilot, so a perfect score on development data must be confirmed on the held-out session.

### RQ3 Sufficiency: how much telemetry is needed?
**This is the central finding.** Random Forest macro-F1 by telemetry level:

| Level | What it sees | Macro-F1 |
|---|---|---|
| L1 | client only (what a load tester sees) | 0.29 |
| L2 | + server request timing | 0.27 |
| L3 | + runtime (CPU, event loop, GC) | 0.55 |
| L4 | + database and cache tracing | **0.95** |

Latency alone, whether from the client or the server, can say *that* an API is slow but not
*why*. Runtime signals help, but distinguishing the five causes needs tracing of database
and cache calls. With an operation held out completely, Random Forest at L4 still reaches
0.955: the baseline-relative features transfer to an endpoint the model has never seen.

### RQ4 Rules vs ML
**The interpretable rules are not beaten.** Rules 1.000; Random Forest 0.946; XGBoost 0.666
(XGBoost never predicts CACHE_OPPORTUNITY correctly: F1 0.000, with only 10 cache runs).
Rules give their reasons; ML needs more data and gives probabilities.

### RQ5 Uncertainty: can it say "I don't know"?
**Yes.** Random Forest at confidence ≥ 0.5 answers 84 % of runs and is right on 100 % of
those. At ≥ 0.7 it answers 57 %, also 100 % correct. The rules abstain
(`INSUFFICIENT_EVIDENCE`) when no mechanism matches; in the main campaign every fault run
matched.

### RQ6 Remediation: does the fix work?
- **Right fix recommended:** 110/110 fault runs.
- **Improvement shown:** 7/10 fixes (Holm-corrected, α 0.05). The other 3 also improved
  (×0.31 to ×0.50) but with only 5 vs 3 runs and noisy data the test fell just short (p 0.051).
- **Back to healthy (±10 %):** 0/10 shown. The fixed runs' medians are close to healthy
  (×0.81–1.16), but the confidence intervals are too wide to *prove* equivalence. The cause
  is the laptop's power-state noise (below).
- **Closed loops:** both demos fully succeeded:

| Injected | Detected | Diagnosis | Server p50 before → after (healthy) |
|---|---|---|---|
| users.list `nplus1:20` | yes (×5.4) | N_PLUS_ONE: 21 vs 1 queries/request | 24.05 → 5.11 ms (4.45), ×0.208 |
| orders.list `noindex` | yes (×17.1) | DB_BOTTLENECK: DB time 83.8 vs 2.1 ms, same queries | 86.20 → 5.31 ms (5.05), ×0.060 |

## What happened during the main campaign (incident log)

Every incident was caught by the protocol's own checks, handled by its rules, and recorded
as an amendment ([protocol](../experiments/protocol.md), Amendments).

| # | What happened | Effect | How it was handled |
|---|---|---|---|
| 1 | **Docker image ID changed on every run.** Docker Desktop's containerd store gives each build a new ID, even for identical code, and the API was rebuilt before every run | the image rule excluded 218 runs, all false positives | Verified identical layers, config and creation time; `target-api/` unchanged. The rule now compares a content hash; no rebuild per run (amendment 4, commit `810966a`) |
| 2 | **PostgreSQL restarted once** during a database reset (11:03) | 2 runs failed | Completed with `--resume`; the reset now retries once after a restart |
| 3 | **24 runs had client send lag > 10 ms**, mostly while analysis ran on the same PC | 24 excluded (11 %) | Re-run once at the end, as the protocol says; all 24 re-runs passed. Lesson: run nothing else during a campaign |
| 4 | **Charger plugged and unplugged** five times | on battery, healthy latency ×1.6–2.3 higher; within one power state only 1–8 % CV. 100 runs on charger, 137 on battery, 7 mixed | Found from the Windows power log (Kernel-Power event 105). The team kept the campaign and reports the noise as a threat to validity. Rule 5 (amendment 5) now excludes runs on battery and refuses to start a campaign on battery |

**Why incident 4 does not invalidate the results.** The run order was shuffled, so battery
runs are spread evenly across conditions. That adds noise but no bias. Diagnosis was
unaffected (110/110), because its features are relative to the baseline. Detection was
slightly affected (wider baselines). Verification was the most affected (wide intervals).
The held-out session runs entirely on the charger.

## Strength of the evidence

| Claim | Strength now | After held-out |
|---|---|---|
| Detection works with few false alarms | strong (CI 87–97 %) | final number |
| Deep tracing (L4) is needed to diagnose | strong (0.29 → 0.95, consistent across models and schemes) | confirmed or not on unseen data |
| Rules are as good as ML | development only (thresholds from the pilot) | the real test |
| Fixes improve performance | 7/10 shown, all 10 in the right direction | verification stays on main data |
| Fixes restore healthy performance | not shown (noise) | not re-tested (held-out has no fixes) |

## Threats to validity (summary)

A synthetic API and faults (realistic, documented mistakes, but generalisation not shown);
one machine; rule thresholds set on the pilot; coarse process-level CPU measurement; class
imbalance; the Docker image ID issue; laptop power state. Details:
[results.md](results.md#threats-to-validity).
