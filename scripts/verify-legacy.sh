#!/usr/bin/env bash
# Verifies the frozen Phase 1-3 data and reproduces the 9 Nov 2026 analysis.
#  1. checksums of the 35 raw files and the 5 analysis files
#  2. re-runs the analyzer on the legacy data into a temporary folder
#  3. the 4 data files must be byte-identical to the 9 Nov output
#  4. manifest: identical except analysis_created_at and the (mapped) input paths
#  5. direct-leakage checks on the 9 Nov output (hard failure) + proxy audit (warning)
# Nothing is written under data/.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
cd "$ABR_ROOT"
export PYTHONDONTWRITEBYTECODE=1

RAW=data/raw/legacy-2026-11-09
REF=data/processed/legacy/20261109T180648-7cd51a3e

echo "== 1. checksums =="
(cd "$RAW" && sha256sum --quiet -c SHA256SUMS) && echo "raw: $(wc -l < "$RAW/SHA256SUMS") files OK"
(cd data/processed/legacy && sha256sum --quiet -c SHA256SUMS) && echo "analysis: $(wc -l < data/processed/legacy/SHA256SUMS) files OK"

echo "== 2. re-run analyzer on legacy data =="
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
"$ABR_PYTHON" -m telemetry_analysis.analyzer --experiments-dir "$RAW/experiments" --span-file "$RAW/otel-spans.jsonl" --output-dir "$OUT" > /dev/null
NEW="$(find "$OUT" -mindepth 1 -maxdepth 1 -type d)"

echo "== 3. byte-identical data files =="
for f in run_features.jsonl run_metadata.jsonl telemetry_observations.jsonl data_quality.jsonl; do
  cmp "$REF/$f" "$NEW/$f"
  echo "identical: $f"
done

echo "== 4. manifest =="
"$ABR_PYTHON" "$ABR_ROOT/scripts/compare_manifest.py" "$REF/manifest.json" "$NEW/manifest.json" "$RAW/MIGRATION_INVENTORY.tsv"

echo "== 5. leakage checks on the 9 Nov output =="
"$ABR_PYTHON" -m telemetry_analysis.leakage_audit "$REF"

echo "LEGACY VERIFICATION PASSED"
