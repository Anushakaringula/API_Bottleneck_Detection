# 4. Roadmap

Each phase ends at a **gate**: a check that must pass, and be reviewed by the team,
before the next phase starts. Nothing below Phase 3 has been built yet.

| Phase | Goal | Gate | Status |
|---|---|---|---|
| 1–3 | Instrumented API, workload runner, telemetry analyzer | Tests pass | Completed |
| Repo reorganization | Folder structure organised by workflow role, without breaking existing code | All existing tests and checks still pass | Done 10 Nov 2026 (gates G0–G6 passed); not yet committed |
| 3.5A Telemetry fixes | Span-parenting fix; per-request trace IDs (W3C `traceparent`) and exact client-to-span matching, with tests; per-run span files; runtime metrics | 100% of requests matched to their spans; no lost spans; parenting test no longer marked `todo` | Proposed |
| 3.5B Workload generation | Open-loop timetable; intended vs actual send time; real concurrency; client calibration | Client keeps to the timetable at every planned rate | Proposed |
| 3.5C Reference API | Healthy, faulty and remediated versions; PostgreSQL; Redis; each fault in at least two operations | Every fault is confirmed active when switched on; no labels leak into telemetry | Proposed |
| 3.5D Experiments | Pilot campaign, then repeated randomized campaign with healthy-vs-healthy runs and a held-out session | Protocol approved by Prof. Sagar; data-quality report clean | Proposed |
| 4 Detection | Normal / anomalous / inconclusive verdicts, each with evidence; thresholds derived from healthy runs | False-alarm and detection rates measured on held-out data | Proposed |
| 5 Diagnosis | Rules first, then Random Forest and XGBoost; four-level telemetry study (RQ3); abstention ("not sure") analysis | All methods evaluated on identical runs and splits | Proposed |
| 6 Remediation | Recommend a fix from the evidence; apply it by switching to the reviewed remediated version | Every recommendation traceable to its evidence | Proposed |
| 7 Verification | Same workload before and after the fix; run-level statistics; check whether performance is back to healthy | Results for every fault, including fixes that did not help | Proposed |
| 8 Dashboard | Next.js dashboard that reads the analysis outputs | Every number on screen traceable to a run | Proposed |
| 9 Evaluation and report | Methodology, dataset, results per research question, limitations | Every claim points to a result file | Proposed |

## Rules the team follows throughout

- Scenario labels, endpoint names, experiment IDs and implementation versions are
  never used as model inputs.
- Detection thresholds come from repeated healthy runs. They are agreed with the
  guide and frozen before the main data is analyzed.
- All four telemetry levels are evaluated on the same runs, labels and splits.
- Runs excluded for missing telemetry are reported, with checks for selection bias.
- Process-level CPU is never presented as the CPU usage of one request.
- A deliberately injected fault does not prove that detection works. Only
  measured results count.
- Raw data is never deleted or edited.
- No results are fabricated. "Not enough evidence" is an acceptable answer.

## Rough size of the main experiment campaign (estimate)

Roughly 400–550 runs and 11–15 hours of machine time over several days. The pilot
campaign will replace this estimate with measured values.
