# 9. Glossary and FAQ

## Glossary

| Term | Meaning in this project |
|---|---|
| **Run** | one 30–60 s experiment: one operation, one condition, fresh database and API. The statistical unit |
| **Condition** | operation + variant, e.g. `users.list/nplus1:20` |
| **Variant** | which implementation of an operation is active: `healthy`, a fault (`nplus1:20`) or a fix (`nplus1-fixed:20`) |
| **Severity** | the number after the colon: rows (`nplus1`, `payload`) or thousands of hash iterations (`cpu`) |
| **Campaign** | a planned, shuffled set of runs with a config, e.g. the main campaign (220 runs) |
| **Held-out session** | 68 runs on another day, analysed once, for the final numbers |
| **Pilot** | 48-run first campaign, used to measure noise and set thresholds; not used for final claims |
| **Ground truth / label** | which variant really ran. Stored in `data/labels/`, read only for evaluation |
| **Span / trace** | OpenTelemetry records of one request (trace) and each step inside it (spans: the request, each SQL query, each Redis call) |
| **traceparent** | W3C header carrying the trace ID from the client, so each request matches exactly one server trace |
| **p50 / p95 / p99** | median / 95th / 99th percentile. "Server p50" = the median server-side latency of a run |
| **CV** | coefficient of variation = standard deviation / mean, a measure of noise |
| **Open loop** | requests are sent on a fixed timetable regardless of responses (like real traffic) |
| **Send lag** | how late the client sent a request versus its timetable; > 10 ms p99 excludes a run |
| **Baseline** | the healthy runs of the same operation; everything is compared to it |
| **Leave-one-out** | when scoring a healthy run, it is left out of its own baseline |
| **Telemetry levels L1–L4** | cumulative feature sets: L1 client, L2 + server timing, L3 + runtime, L4 + DB/cache tracing |
| **Relative features** | features expressed against the baseline (log₂ ratio, or difference for shares) |
| **INSUFFICIENT_EVIDENCE** | the diagnosis abstains: no mechanism matches the evidence |
| **Abstention / coverage** | an ML model abstains below a confidence threshold; coverage = share of runs it answers |
| **Macro-F1** | average F1 over the five classes; every class counts equally regardless of size |
| **Precision** | share of flagged runs that really were faults |
| **Wilson 95 % CI** | confidence interval for a rate (e.g. 103/110); honest for small counts |
| **Grouped CV** | cross-validation where all repeats of a condition stay in one fold |
| **Leave-one-operation-out** | train on 7 operations, test on the 8th; checks transfer to unseen endpoints |
| **Welch t-test on log latency** | compares faulty vs fixed runs with unequal variances; logs make it about ratios |
| **Holm correction** | adjusts p-values when testing 10 fixes at once, so 1 in 20 chance hits do not count |
| **Mann–Whitney** | rank-based test used as a check on Welch |
| **TOST** | two one-sided tests: proves *equivalence* (fixed within ±10 % of healthy), not just "no difference found" |
| **Exclusion / re-run** | a run breaking a protocol rule is excluded and re-run once at the end |
| **Amendment** | a recorded, dated change to the frozen protocol, with its reason |
| **Content identity** | hash of the Docker image's layers + config; stable across rebuilds of the same code |

## FAQ

**What does the project do, in one sentence?**
It automatically detects that an API endpoint is slow, explains which of five bottleneck
types causes it (with evidence), recommends the fix, re-runs the endpoint with the fix and
proves statistically whether it worked.

**What is the main finding?**
Latency alone can tell *that* an API is slow (93.6 % detected, 5 % false alarms) but not
*why*. Diagnosing the cause needs tracing of database and cache calls: macro-F1 0.29 with
client data versus 0.95 with full tracing.

**Is the controlled data only 220 records?**
220 is the number of experiments (runs) in the main campaign. They contain 35,612 traced
requests, the spans of every query inside them, and runtime samples every second. Requests
within a run are not independent, so results are counted in runs. See
[06-experiments-and-data.md](06-experiments-and-data.md).

**Is 220 runs enough?**
Yes. The size came from the pilot's measured noise: server-side noise was 0.2–8.3 %, so 5
repeats per fault condition separate the effects with margin. The detection confidence
interval is 87–97 %.

**Why a synthetic API instead of a real one?**
To know the ground truth. In a real system nobody knows for sure what caused a slowdown,
so accuracy cannot be measured. Here each fault is injected deliberately and labelled.
Each fault is a realistic, documented mistake, appears in two different operations, and
comes at several severities. Generalising to other applications is listed as a threat to
validity.

**Why rules and ML both?**
The rules are the interpretable baseline (they give reasons); ML tests whether learning
does better. So far it does not (rules 1.000 vs Random Forest 0.946), and the rules explain
themselves.

**Does the system change code?**
No. It switches the operation to a pre-written, reviewed fixed variant, as a developer
would deploy a reviewed fix.

**Why is the held-out session needed?**
The rule thresholds were set on the pilot, and the main campaign was used during
development. Final claims must come from data that played no part in building the
methods. It runs on another day with its own seed and is analysed once.

**Is the charger / battery issue important?**
It is a measurement-noise issue, not part of the research question. On battery the laptop
CPU is slower, which widened the verification intervals. It was traced via the Windows
power log, measured, reported as a threat, and prevented by rule 5. Diagnosis was
unaffected. See [07-results-explained.md](07-results-explained.md#what-happened-during-the-main-campaign-incident-log).

**Why didn't any fix prove "back to healthy"?**
Proving equivalence (TOST, ±10 %) needs tight intervals. With 3 fixed runs and power-state
noise, the intervals were too wide, although the fixed medians are close to healthy
(×0.81–1.16).

**Is the project complete?**
The system and the main campaign are complete. Remaining: the held-out session (~75 min,
charger plugged in), the guide's review, the thesis and the viva demo.
[PROGRESS.md](../PROGRESS.md) is always current.
