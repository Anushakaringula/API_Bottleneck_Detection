#!/usr/bin/env bash
# Full analysis of one campaign, in order: summary -> features -> detection -> diagnosis -> verification.
#   scripts/analyze-campaign.sh <campaign-id>                          # development (main campaign)
#   scripts/analyze-campaign.sh <heldout-id> --train <main-id>         # final: baselines and models from main
# Results: data/results/{campaigns,detection,diagnosis,verification}/<campaign-id>/ (read by the dashboard).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
cd "$ABR_ROOT"
campaign="${1:?usage: scripts/analyze-campaign.sh <campaign-id> [--train <main-id>]}"
train=""
if [ "${2:-}" = "--train" ]; then
  train="${3:?--train needs a campaign id}"
  [ -f "data/features/$train/features.jsonl" ] || { echo "analyze: run scripts/analyze-campaign.sh $train first" >&2; exit 2; }
fi
echo "== 1/5 campaign summary";  "$ABR_PYTHON" -m campaign.summarize "$campaign"
echo "== 2/5 features";          "$ABR_PYTHON" -m features.extract "$campaign"
if [ -n "$train" ]; then
  echo "== 3/5 detection (baselines from $train)"; "$ABR_PYTHON" -m evaluation.detection --campaign "$campaign" --baseline-campaign "$train"
  echo "== 4/5 diagnosis (models trained on $train)"; "$ABR_PYTHON" -m evaluation.diagnosis --campaign "$campaign" --train-campaign "$train"
  echo "== 5/5 verification: the held-out session has no fixed variants (verification uses the main campaign)"
else
  echo "== 3/5 detection";      "$ABR_PYTHON" -m evaluation.detection --campaign "$campaign"
  echo "== 4/5 diagnosis";      "$ABR_PYTHON" -m evaluation.diagnosis --campaign "$campaign"
  echo "== 5/5 verification";   "$ABR_PYTHON" -m evaluation.verification --campaign "$campaign"
fi
echo "done: data/results/*/$campaign/ (dashboard: cd dashboard && npm run dev -> http://localhost:3001)"
