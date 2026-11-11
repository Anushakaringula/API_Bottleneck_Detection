# Dashboard (Phase 8)

A read-only Next.js app over the pipeline's result files, laid out like the concept note's
dashboard (§13). It has no database and copies no pipeline logic: every number comes from a
file under `data/`, and each section names its source.

```sh
scripts/analyze-campaign.sh <campaign-id>     # produce the results first (summary, features, detection, diagnosis, verification)
cd dashboard
npm install                                   # once
npm run dev                                   # http://localhost:3001
```

| Page | Shows | Sources |
|---|---|---|
| `/` overview | runs, telemetry coverage, false-alarm and detection rates (with 95 % CIs), diagnosis macro-F1 (rules vs RF vs XGBoost per telemetry level), verified fixes, per-endpoint table, closed-loop runs | `data/results/{campaigns,detection,diagnosis,verification,loops}/` |
| `/endpoint?campaign=…&operation=…` | every run of one endpoint: injected condition, server p50 against the threshold, detection verdict, diagnosis with evidence, recommended fix | detection verdicts, diagnosis per-run results, campaign summary |
| `/loops/<loop-id>` | one closed loop: observe → detect → diagnose → remediate → verify, with before/after latency per run | `data/results/loops/<loop-id>/report.json` |

The campaign selector lists every campaign that has detection results: main first, then
held-out, then pilot. `ABR_DATA_DIR` points the dashboard at another data folder.
