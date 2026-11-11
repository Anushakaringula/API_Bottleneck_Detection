# Detection (Phase 4, RQ1)

Answers "is this run's behaviour abnormal for its operation?" Diagnosis (which bottleneck)
is Phase 5.

**Detector of record** (`rule.py`, protocol v1 §9): a run is *anomalous* when its server-side
run p50 exceeds its operation's threshold:

    threshold = max(P99 of the healthy run p50s, 1.2 × healthy median)

The verdict is *inconclusive* when the protocol excluded the run, when less than 95 % of its
requests have spans, or when its operation has no baseline. Every verdict carries its
evidence: value, threshold, baseline median and size, and the ratio to the baseline.

```sh
python -m features.extract <campaign-id>                          # features first
python -m evaluation.detection --campaign <main-id>               # in-campaign, leave-one-out
python -m evaluation.detection --campaign <heldout-id> --baseline-campaign <main-id>   # final
```

Output: `data/results/detection/<campaign-id>/` with `verdicts.jsonl` (no ground truth),
`summary.json` and `report.md`. The report gives:
- the false-alarm rate on healthy runs (protocol limit ≤ 5 %);
- the detection rate per fault class and per condition, with 95 % Wilson intervals;
- the share of fixed variants flagged;
- the precision.

**Separation of concerns:**
- `detection/` never reads labels.
- `evaluation/labels.py` is the only module that does: it chooses the healthy reference runs
  and scores the verdicts.
- A healthy run scored against its own campaign's baseline is left out of that baseline.
- `pipeline/` never imports `experiments/`.
- `tests/test_detection.py` enforces these rules.

**Pilot (development check, leave-one-out):**
- 0/24 healthy runs flagged (95 % CI 0–13.8 %).
- 20/20 fault runs detected, including the weakest (×2.0, ×2.2).
- 0/4 fixed variants flagged.
