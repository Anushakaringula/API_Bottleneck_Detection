# 8. Dashboard guide

The dashboard is the visual front end of the project. It shows what the system found:
where the API was slow, why, what fix it recommends, and whether the fix worked. It is
**read-only**. It has no database and copies no pipeline logic; every number comes from a
file under `data/results/`, and each section names its source.

```sh
scripts/analyze-campaign.sh <campaign-id>     # produce the results first
cd dashboard
npm install                                   # once
npm run dev                                   # http://localhost:3001
```

The campaign selector at the top lists every analysed campaign: main first, then held-out,
then pilot.

## Page 1: Overview (`/`)

The summary of one campaign.

**Headline cards:**

| Card | Meaning | Main campaign |
|---|---|---|
| Runs / telemetry coverage | experiments run; share of requests matched to their trace | 244 runs, 100 % |
| False alarms | healthy runs wrongly flagged as slow (95 % CI) | 5.0 % (4/80) |
| Faults detected | fault runs correctly flagged (95 % CI) | 93.6 % (103/110) |
| Diagnosis | macro-F1 of the rules and the best ML model | rules 1.000 |
| Fixes verified | fixes shown to improve; how many back to healthy | 7/10; 0 back to healthy |

**Sections:**

1. **Endpoints:** one row per route (`/api/users`, `/api/orders`, …): healthy server p50,
   faults tested, detected, correctly diagnosed. Click a route to open page 2.
2. **Detection by fault class:** detection rate per bottleneck type.
3. **Diagnosis: rules vs Random Forest vs XGBoost:** macro-F1 per telemetry level L1–L4.
   This is the RQ3/RQ4 table: client data alone is not enough; full tracing is.
4. **Remediation verified:** per fix: faulty → fixed median, healthy median, ratio,
   p-value, verdict.
5. **Closed-loop runs:** every automatic loop with its diagnosis, fix and outcome. Click one
   to open page 3.

Sources: `data/results/{campaigns,detection,diagnosis,verification,loops}/`.

## Page 2: Endpoint evidence (`/endpoint?campaign=…&operation=…`)

Every run of one route, one row per run:

| Column | Meaning |
|---|---|
| Condition (injected) | what was secretly injected, e.g. `nplus1:20 #3`. Shown only for checking; the system never sees it. "excluded" marks runs the protocol excluded |
| Server p50 | the run's median server latency, and its ratio to healthy |
| vs threshold | bar: the run's latency; the tick is the anomaly threshold; red when above |
| Detection | anomalous / normal / inconclusive. **Green = correct** (fault flagged, or healthy left alone), **red = wrong** |
| Diagnosis | the bottleneck the rules concluded (green = matches the injected fault) |
| Evidence | why, in plain words, e.g. "21.0 queries per request vs 1.0 healthy" |
| Recommended fix | e.g. "replace the per-row queries with one JOIN / batched query" |

This page shows that the system **explains** its decisions. Sources:
`detection/<id>/verdicts.jsonl`, `diagnosis/<id>/summary.json`, `campaigns/<id>/summary.json`.

## Page 3: Closed loop (`/loops/<loop-id>`)

The whole project on one page: one automatic run of the full cycle.

```
1 Observe    users.list ran with a fault the system was not told about (revealed after: nplus1:20)
2 Detect     ANOMALOUS: server p50 24.05 ms > threshold 5.34 ms (×5.40)
3 Diagnose   N_PLUS_ONE (high): 21.0 queries per request vs 1.0; one query repeated 20x
4 Remediate  replace the per-row queries with one JOIN; applied variant nplus1-fixed:20
5 Verify     ×0.208 (p < 0.0001), healthy 4.45 ms → improvement verified
```

Below that, bars show the server latency of each run before and after the fix. Source:
`data/results/loops/<loop-id>/report.json`.

## A 3-minute demo

1. **Overview:** "220 controlled experiments: 93.6 % of faults detected at 5 % false alarms,
   and diagnosis needs full tracing (L1 0.29 → L4 0.95)."
2. **Click `/api/users`:** "For every run the system says what is wrong and why."
3. **Click a closed loop:** "Here it found the problem, applied the fix and proved it worked."

For a live loop during the demo (about 6 min, charger plugged in):
`python -m campaign.loop --operation users.list --variant nplus1:20 --baseline-campaign main-20261111T100109-be3c28`,
then refresh the overview.

## Troubleshooting

| Problem | Fix |
|---|---|
| `EADDRINUSE ... 3001` | another dashboard is still running. Close it, or find it with `Get-NetTCPConnection -LocalPort 3001` and stop that process; or use `npx next dev -p 3002` |
| "Loop not found" / empty campaign list | run `scripts/analyze-campaign.sh <id>` first; the dashboard only reads `data/results/` |
| Another data folder | `ABR_DATA_DIR=/path/to/data npm run dev` |
