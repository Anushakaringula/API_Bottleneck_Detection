# Automated API Bottleneck Detection and Verified Remediation

Final-year B.Tech CSE major project, RGUKT Basar.
Guide: Prof. G. Sagar. Team: P. Dhanush, G. Vardhan, K. Anusha.

**Research question:** Is request-level observability telemetry sufficient to
identify and distinguish different types of API performance bottlenecks? Where it
is not, the system should say "insufficient evidence" instead of guessing.

## Workflow and where it lives

| Step | What | Location | Status |
|---|---|---|---|
| 1 | API workload generation | `experiments/experiment_runner/` | Built (open loop, Poisson, calibration) |
| 2 | OpenTelemetry instrumentation and telemetry collection | `target-api/` (emits), per-request correlation by trace ID | Built (100 % of requests matched) |
| 3 | Experiment execution: campaigns with labels and protocol rules | `experiments/campaign/` → `data/raw/campaigns/` | Built; main campaign done (220 runs) |
| 4 | Features in telemetry levels L1–L4 | `pipeline/features/` → `data/features/` | Built |
| 5 | Detection (vs own healthy baseline) and diagnosis (bottleneck type, with evidence) | `pipeline/detection/`, `pipeline/diagnosis/` | Built: 93.6 % detected at 5.0 % false alarms; rules 110/110 |
| 6 | Remediation recommendation | `pipeline/remediation/` | Built: right fix 110/110 |
| 7 | Re-run and before-and-after verification; closed loop | `pipeline/verification/`, `experiments/campaign/loop.py` | Built: 7/10 fixes shown to improve; 2/2 closed loops verified |
| 8 | Dashboard and results report | `dashboard/`, `pipeline/evaluation/report.py` → `docs/results.md` | Built |

**Main finding so far:** latency alone shows *that* an API is slow, not *why*. Diagnosing the
cause needs database and cache tracing (macro-F1 0.29 with client data, 0.95 with full
tracing). Final numbers come from the held-out session. Explained in
[docs/07-results-explained.md](docs/07-results-explained.md).

**Progress: [PROGRESS.md](PROGRESS.md).** Planned folders are created only when their code is written. See
[docs/architecture.md](docs/architecture.md) for the design,
[PROJECT_STATUS.md](PROJECT_STATUS.md) for what is done and what is known to be
wrong, and [docs/README.md](docs/README.md) for the full documentation.

## Repository layout

```
target-api/        system under test: Express API + OpenTelemetry instrumentation (Node.js), injectable faults
infrastructure/    local lab: PostgreSQL 16 + Redis 7 + the API in Docker Compose
experiments/       workload runner, campaign orchestrator, closed loop (Python); configs; protocol.md
pipeline/          investigation system (Python): features, detection, diagnosis, remediation, verification, evaluation
dashboard/         read-only Next.js dashboard over data/results
data/              all recorded data; stages exchange work through files here (see data/README.md); not in git
scripts/           environment, lab control, tests, one-command campaign analysis
docs/              guides (start at docs/README.md) and the generated results.md
```

## Quick start

Requires Node.js 20 and Python 3.12 (3.11+; `pip install -r requirements.txt`) on Ubuntu (Windows: see [docs/02-development-setup.md](docs/02-development-setup.md)). No other installs are needed for
the current phases.

```sh
cd ~/api-bottleneck-research
(cd target-api && npm ci)       # once: install the locked dependencies
cp .env.example .env            # optional; defaults work without it
source scripts/env.sh           # loads .env, sets PYTHONPATH

scripts/test-all.sh             # all test suites
scripts/verify-legacy.sh        # checks the frozen historical data and reproduces the 9 Nov analysis

(cd target-api && npm start)    # terminal 1: API on :3000, metrics on :9464
python3 -m experiment_runner.runner --config experiments/configs/example-config.json   # terminal 2
python3 -m telemetry_analysis.analyzer
```

Details: [docs/03-running-the-existing-code.md](docs/03-running-the-existing-code.md).

## Research rules

- Scenario labels, endpoint names, experiment IDs and implementation variants are
  never model inputs. Automated leakage checks enforce this.
- Ground-truth labels are stored apart from features and are never read at
  prediction time.
- Raw data is never edited or deleted. Historical data is checksummed.
- No fabricated results. "Insufficient evidence" is a valid answer.
