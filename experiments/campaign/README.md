# Experiment campaigns (Phase 3.5D)

A campaign is a planned set of labelled runs against the local lab. Each run tests one
operation of the target API with one implementation variant (healthy, a fault, or a fix).
This package plans the runs, executes them, keeps the ground truth apart, and summarizes
the result.

## Run a campaign

The lab must be up and seeded at the campaign's scale; Docker Desktop must be running.

```sh
source scripts/env.sh
scripts/lab-up.sh && scripts/lab-seed.sh large                         # once
python -m campaign.run --config experiments/configs/campaigns/pilot.json --dry-run
python -m campaign.run --config experiments/configs/campaigns/pilot.json
python -m campaign.run --resume <campaign-id>                          # after an interruption
python -m campaign.summarize <campaign-id>
```

Before the first run, it checks that the lab has the right seed scale and that every
variant exists in the target API's registry. A failed run is recorded and the campaign
continues; `--resume` retries failed runs and skips completed ones. Ctrl+C stops the target
API cleanly.

## Protocol rules (experiments/protocol.md)

Campaign configs switch the protocol's rules on. `main.json` and `heldout.json` use all of
them:

| Key | Effect |
|---|---|
| `require_clean_git` | refuse to start unless `git status` is clean (results must map to one commit) |
| `discard_warmup_runs` | N unlabelled healthy runs at the start of every session (cold start) |
| `exclusion` | thresholds of the exclusion rules (`quality.py`): complete correlation, failed requests ≤ 1 %, client send-lag p99 ≤ 10 ms; plus uncommitted code and a changed target-API image |
| `rerun_excluded` | after all planned runs, re-run each excluded run once (new position, same condition) |
| `apply_exclusions` | `campaign.summarize` leaves excluded runs out of all statistics and lists them with reasons |

`data/raw/campaigns/<id>/exclusions.json` lists every excluded run, its reasons and its re-run.
The target-API image is built once before the campaign, and its ID is recorded in the
manifest and per run.

## What one run does

1. `scripts/lab-reset.sh`: database `lab` recreated from the seeded template; query
   statistics and Redis cleared.
2. `scripts/lab-api.sh up` with `ABR_VARIANTS={"<operation>": "<variant>"}`: target API in
   the lab network; the variant's startup step (e.g. dropping an index) runs.
3. `experiment_runner`: open-loop workload. Warm-up, then the measured phase, then a check
   that every request matched its span.
4. Database statistics snapshot (`pg_stat_statements`, `pg_stat_database`, index list).
5. `scripts/lab-api.sh down`: the target API stops and flushes its telemetry.
6. A ground-truth label is written to `data/labels/<campaign-id>.jsonl`.

## Design choices

| Choice | Why |
|---|---|
| Run order shuffled with the campaign seed | Slow drifts (machine temperature, background load) do not line up with conditions |
| One workload seed per operation | Every run of an operation sends the same timetable and request mix; only the variant differs (paired comparisons) |
| Same rate for all runs of an operation | Healthy and faulty runs see the same load |
| Per-operation rate where a fault is heavy (`reports`: 0.5 rps, 60 s) | The faulty variant must slow down, not collapse: at 1 rps the cache fault's 0.8–2.4 s queries overlapped and latency grew without bound (smoke campaign, 11 Nov 2026) |
| No ground truth in run artifacts | The runner config says only `campaign-run`; the variant is in the labels file alone |
| Fixed variants labelled `NONE` with `remediation_of` | A fix should behave like healthy; verification (Phase 7) compares them |

## Files

| Path | Contents |
|---|---|
| `experiments/configs/campaigns/*.json` | Campaign definitions: workload, per-operation overrides, conditions × repeats, seed |
| `data/raw/campaigns/<id>/manifest.json` | Config, full run plan in execution order, environment (versions, image ID, git commit, seed) |
| `data/raw/campaigns/<id>/progress.jsonl` | One line per finished run: status, wall time, requests, correlation |
| `data/raw/campaigns/<id>/experiments/<run>/` | Runner artifacts (`configuration.json`, `requests.jsonl`, `summary.json`) |
| `data/raw/campaigns/<id>/db-stats/<run>.json` | Database-side statistics of the run |
| `data/raw/spans/runs/<run>.jsonl` | The run's spans (written by the target API) |
| `data/labels/<id>.jsonl` | Ground truth per run: operation, variant, fault, class, severity, fix |
| `data/results/campaigns/<id>/report.md` | Summary: healthy noise, effect per condition, telemetry completeness, run time |

## Campaign config

```json
{
  "name": "pilot",
  "random_seed": 20261111,
  "seed_scale": "large",
  "workload": {"load_model": "open", "arrival_process": "poisson", "request_rate_per_second": 5,
               "duration_seconds": 30, "warmup_requests": 20, "concurrency": 50, "timeout_seconds": 30},
  "operation_workload": {"reports": {"request_rate_per_second": 0.5, "duration_seconds": 60, "warmup_requests": 3}},
  "conditions": [
    {"operation": "users.list", "variants": ["healthy"], "repeats": 3},
    {"operation": "users.list", "variants": ["nplus1:20", "nplus1-fixed:20"], "repeats": 2}
  ]
}
```

The requests sent to each operation (paths, parameters) are fixed in `plan.py`
(`OPERATION_REQUESTS`).

## Tests

```sh
python -m unittest discover -s experiments/campaign/tests    # fake lab and runner; no Docker
```
