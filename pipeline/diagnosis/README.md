# Diagnosis (Phase 5; RQ2–RQ5)

After a slowdown is detected: which bottleneck caused it? The answer is one of
`DB_BOTTLENECK`, `N_PLUS_ONE`, `LARGE_PAYLOAD`, `CACHE_OPPORTUNITY` or `CPU_BOTTLENECK`, or
`INSUFFICIENT_EVIDENCE`.

| File | Role |
|---|---|
| `relative.py` | features relative to the operation's healthy baseline (log2 ratio; difference for shares); leave-one-out for reference runs; shared by rules and ML |
| `rules.py` | interpretable baseline method: one mechanism rule per class, with evidence and exclusions that keep the classes apart; no rule gives insufficient evidence |
| `ml.py` | Random Forest and XGBoost, balanced class weights, abstention below a confidence threshold |
| `../evaluation/diagnosis.py` | rules vs RF vs XGB, per telemetry level L1–L4; macro-F1, per-class F1, confusion matrix, abstention |

```sh
python -m pip install -r requirements.txt                                          # once
python -m evaluation.diagnosis --campaign <main-id>                                 # development: grouped and leave-one-operation-out CV
python -m evaluation.diagnosis --campaign <heldout-id> --train-campaign <main-id>   # final, once
```

**Evaluation design:**
- Diagnosis is scored on fault runs. Runs excluded by the protocol are left out.
- The `grouped` scheme keeps every repeat of a condition (operation × fault × severity) in
  one fold, so the tested condition is never seen in training.
- The `operation` scheme holds out a whole operation.
- Final numbers come from training on the main campaign and testing once on the held-out
  session.
- Abstentions count as wrong in accuracy and macro-F1. Coverage and selective accuracy are
  reported separately.

**Rule thresholds** were set from the fault mechanisms and checked on the pilot campaign,
which is development data. On the pilot the rules diagnose 20/20 runs. That figure is a
consistency check, not a result.
