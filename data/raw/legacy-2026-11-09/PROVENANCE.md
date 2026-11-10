# Provenance of the legacy data

These files are the Phase 1–3 telemetry and experiment artifacts collected on
9 November 2026, before the repository was reorganised. They were moved here on
10 November 2026 with `mv` (a rename on the same filesystem). No bytes were changed.

| Old path (before 10 Nov 2026) | New path |
|---|---|
| `datasets/otel-spans.jsonl` | `data/raw/legacy-2026-11-09/otel-spans.jsonl` |
| `datasets/telemetry.csv` | `data/raw/legacy-2026-11-09/telemetry.csv` |
| `datasets/experiments/<run-id>/` (11 runs × 3 files) | `data/raw/legacy-2026-11-09/experiments/<run-id>/` |
| `datasets/analysis/20261109T180648-7cd51a3e/` (5 files) | `data/processed/legacy/20261109T180648-7cd51a3e/` |

## Integrity files

- `MIGRATION_INVENTORY.tsv` lists, for all 40 files, the old path, new path, size,
  modification time and SHA-256. All 40 matched their pre-move values exactly.
- `SHA256SUMS` (35 raw files, here) and `../../processed/legacy/SHA256SUMS`
  (5 analysis files) can be checked with `sha256sum -c SHA256SUMS`.
- A full backup taken before the move, with a passed restore test, is at
  `~/abr-backups/2026-11-10/` on the project laptop.

## Old paths inside historical files

Some historical files mention the old locations and were deliberately **not**
edited:

- the 9 Nov analysis `manifest.json` lists its inputs as `datasets/...`;
- each `summary.json` stores the absolute path of the old span file.

Use the table above to map them. `scripts/verify-legacy.sh` re-runs the analyzer on
these files and confirms the 9 Nov output is reproduced byte-for-byte.

## Known characteristics

This data has the limitations recorded in `PROJECT_STATUS.md`: few requests per
run, no repeats, and one endpoint per scenario (proxy leakage). It also contains
50 spans from manual tests that belong to no experiment run, and 7 CSV rows with
no matching span. It is a pipeline test dataset, not a research dataset.
