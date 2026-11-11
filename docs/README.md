# Project documentation

**Automated API Bottleneck Detection and Verified Remediation**
Final-year B.Tech CSE major project, RGUKT Basar.
Guide: Prof. G. Sagar. Team: P. Dhanush (B210768), G. Vardhan (B210911), K. Anusha (B210430).

If you are new to the project, read these files in order:

| File | What it tells you |
|---|---|
| [01-project-overview.md](01-project-overview.md) | What problem the project studies, the research questions, and how the finished system is meant to work |
| [../PROGRESS.md](../PROGRESS.md) | Progress at a glance: phases, gates, key results, decisions, open items |
| [../PROJECT_STATUS.md](../PROJECT_STATUS.md) | What is built and working, what is known to be wrong, what is planned, and what is still an untested assumption |
| [architecture.md](architecture.md) | How the repository is organised, how the parts connect, and how a request flows through the system |
| [02-development-setup.md](02-development-setup.md) | Installing the tools on Ubuntu or Windows (WSL2 / Git Bash), legacy data, CI |
| [03-running-the-existing-code.md](03-running-the-existing-code.md) | How to start the API, run an experiment, analyze the results and run the tests |
| [04-roadmap.md](04-roadmap.md) | The plan from here to the final evaluation, phase by phase |
| [05-system-walkthrough.md](05-system-walkthrough.md) | **How the finished system works**, stage by stage: faults, telemetry, features L1–L4, detection rule, diagnosis rules and ML, remediation, verification statistics, closed loop |
| [06-experiments-and-data.md](06-experiments-and-data.md) | **What the controlled data is**: runs vs requests vs spans, every dataset, the 220 main-campaign runs, why 220 is enough, lab settings, protocol rules |
| [07-results-explained.md](07-results-explained.md) | **What the results mean**, per research question; strength of the evidence; incident log of the main campaign |
| [08-dashboard-guide.md](08-dashboard-guide.md) | What every dashboard page and column shows; a 3-minute demo; troubleshooting |
| [09-glossary-and-faq.md](09-glossary-and-faq.md) | Terms (p50, macro-F1, TOST, Holm, L1–L4, …) and answers to common questions (viva preparation) |
| [results.md](results.md) | The generated results report (do not edit; re-run `python -m evaluation.report`) |
| [../experiments/protocol.md](../experiments/protocol.md) | The frozen experiment protocol (v1) and its amendments |

Quick path for a newcomer: 01 → 05 → 06 → 07, then 08 for the demo.

Files 01–04 were written on 10 November 2026, before the analysis phases; files 05–09
describe the finished system as of **11 November 2026**. All documents report only what has
actually been built or measured. [../PROGRESS.md](../PROGRESS.md) is always current.
