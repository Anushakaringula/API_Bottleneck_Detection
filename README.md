# Automated API Bottleneck Detection and Verified Remediation

Final-year B.Tech CSE major project, RGUKT Basar.
Guide: Prof. G. Sagar. Team: P. Dhanush, G. Vardhan, K. Anusha.

**Research question:** Is request-level observability telemetry sufficient to
identify and distinguish different types of API performance bottlenecks? Where it
is not, the system should say "insufficient evidence" instead of guessing.

## Workflow and where it lives

| Step | What | Location | Status |
|---|---|---|---|
| 1 | API workload generation | `experiments/experiment_runner/` | Built (closed and open loop, rate profiles, calibration: 3.5B) |
| 2 | OpenTelemetry instrumentation and telemetry collection | `target-api/` (emits), runner and analyzer (per-request correlation by trace ID) | Built (3.5A telemetry fixes implemented) |
| 3 | Experiment execution and data collection | `experiments/` → `data/raw/` | Built |
| 4 | Telemetry processing and feature engineering | `pipeline/telemetry_analysis/` → `data/processed/` | Built |
| 5 | Detection (abnormal vs own healthy baseline) and diagnosis (likely bottleneck type, with evidence) | `pipeline/detection/`, `pipeline/diagnosis/` | Planned (Phases 4–5) |
| 6 | Remediation recommendation or execution | `pipeline/remediation/` | Planned (Phase 6) |
| 7 | Before-and-after verification | `pipeline/verification/` | Planned (Phase 7) |
| 8 | Dashboard | `dashboard/` | Planned (Phase 8) |

Planned folders are created only when their code is written. See
[docs/architecture.md](docs/architecture.md) for the design,
[PROJECT_STATUS.md](PROJECT_STATUS.md) for what is done and what is known to be
wrong, and [docs/README.md](docs/README.md) for the full documentation.

## Repository layout

```
target-api/        system under test: Express API + OpenTelemetry instrumentation (Node.js)
experiments/       workload generation and experiment execution (Python) + experiment configs
pipeline/          offline investigation system (Python): telemetry analysis now, detection etc. later
data/              all recorded data; stages exchange work through files here (see data/README.md)
scripts/           environment setup, test runner, legacy-data verification
docs/              overview, architecture, how to run, roadmap
```

## Quick start

Requires Node.js 20 and Python 3.10 on Ubuntu (Windows: see [docs/02-development-setup.md](docs/02-development-setup.md)). No other installs are needed for
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
