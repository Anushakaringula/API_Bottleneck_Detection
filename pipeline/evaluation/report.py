#!/usr/bin/env python3
"""Results report (Phase 9): one section per research question, generated from result files.

  python -m evaluation.report --main <main-id> [--heldout <heldout-id>] [--output docs/results.md]

Every number is read from data/results/... and each section names its source, so the report
cannot drift from the data. Re-run it after the held-out session for the final numbers. The
interpretation sentences are generated from the numbers and the protocol's criteria
(experiments/protocol.md v1).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .labels import DATA_DIR

ROOT = DATA_DIR.parent


def _load(kind: str, campaign: str) -> dict[str, Any] | None:
    path = DATA_DIR / "results" / kind / campaign / "summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def pct(v, d=1):
    return "–" if v is None else f"{100 * v:.{d}f} %"


def num(v, d=3):
    return "–" if v is None else f"{v:.{d}f}"


def ci(c):
    return "" if not c else f" (95 % CI {100 * c[0]:.1f}–{100 * c[1]:.1f} %)"


def src(kind, campaign):
    return f"`data/results/{kind}/{campaign}/`"


def dataset_section(main: str, heldout: str | None) -> list[str]:
    lines = ["## Data", ""]
    for name, cid in (("Main campaign", main), ("Held-out session", heldout)):
        s = _load("campaigns", cid) if cid else None
        if not s:
            lines.append(f"- {name}: {'not run yet' if not cid else 'no summary'}")
            continue
        t, ex = s["telemetry"], s.get("exclusions", {})
        n_ex = len(ex.get("excluded_runs", []))
        lines.append(f"- **{name}** `{cid}`: {s['planned_runs']} planned runs; {s['completed_runs']} runs completed, of which "
                     f"{n_ex} were excluded by the protocol and replaced by a re-run ({'applied' if ex.get('applied') else 'informational'}); "
                     f"{t['requests_matched_to_one_span']}/{t['requests_attempted_incl_warmup']} requests matched to their spans "
                     f"({pct(t['request_match_rate'], 2)}); {t['failed_requests']} failed requests. Source: {src('campaigns', cid)}")
    return lines + [""]


def rq1(main: str, heldout: str | None) -> list[str]:
    lines = ["## RQ1 Detection: can telemetry tell a degraded API apart from normal variation?", "",
             "Detector of record (protocol v1): server-side run p50 > max(P99 of healthy runs, 1.2 × healthy median), per operation.", ""]
    rows = [("Main (healthy runs leave-one-out)", main)] + ([("Held-out (baselines from main)", heldout)] if heldout else [])
    lines += ["| Data | False alarms on healthy runs | Faults detected | Fixed variants flagged | Precision |", "|---|---|---|---|---|"]
    final = None
    for label, cid in rows:
        d = _load("detection", cid)
        if not d:
            continue
        final = d
        h, f, x = d["false_alarms_on_healthy"], d["detection_on_faults"], d["flagged_fixed_variants"]
        lines.append(f"| {label} | {h['anomalous']}/{h['decided']} = {pct(h['rate'])}{ci(h['ci95'])} | {f['anomalous']}/{f['decided']} = {pct(f['rate'])}{ci(f['ci95'])} | "
                     f"{x['anomalous']}/{x['decided']} | {pct(d['precision'])} |")
    if final:
        weakest = sorted(final["by_condition"].items(), key=lambda kv: (kv[1]["rate"] if kv[1]["rate"] is not None else 1, kv[1]["median_ratio_to_baseline"] or 0))[:3]
        lines += ["", f"False-alarm rate within the protocol limit (≤ 5 %): **{'yes' if final['false_alarm_rate_within_limit'] else 'no'}**. "
                  "Hardest conditions: " + "; ".join(f"{k} {pct(v['rate'])} (median ×{num(v['median_ratio_to_baseline'], 2)})" for k, v in weakest) + ".",
                  f"Source: {src('detection', rows[-1][1])}"]
    return lines + [""]


def rq2_to_rq5(main: str, heldout: str | None) -> list[str]:
    cid = heldout if heldout and _load("diagnosis", heldout) else main
    d = _load("diagnosis", cid)
    if not d:
        return ["## RQ2–RQ5 Diagnosis", "", "No diagnosis results yet.", ""]
    final = d["train_campaign_id"] != d["campaign_id"]
    scheme = "final" if final else "grouped"
    rules = d["rules"]["metrics"]
    def ml(level, kind, s=scheme, t="0.0"):
        return d["ml"].get(f"L{level}/{kind}", {}).get(s, {}).get(f"abstain_{t}", {})
    setting = (f"models trained on `{d['train_campaign_id']}`, tested once on `{cid}`" if final else
               f"cross-validation on `{cid}` with all repeats of a condition in one fold (`grouped`) and, separately, whole operations held out (`operation`)")
    lines = ["## RQ2 Diagnosis: once a slowdown is detected, which bottleneck caused it?", "",
             f"{d['fault_runs']} fault runs; {setting}. Abstentions count as wrong.", "",
             f"- Rules (mechanism evidence, L4): macro-F1 **{num(rules['macro_f1'])}**, accuracy {pct(rules['accuracy'])}, coverage {pct(rules['coverage'])}.",
             f"- Per class (rules): " + ", ".join(f"{k} {num(v)}" for k, v in rules["f1_per_class"].items()) + ".", "",
             "## RQ3 Sufficiency: which telemetry is needed?", "",
             "| Telemetry level | Random Forest macro-F1 | XGBoost macro-F1 |" + ("" if final else " RF, operation held out | XGB, operation held out |"),
             "|---|---|---|" + ("" if final else "---|---|")]
    names = {1: "L1 client only", 2: "L2 + server timing", 3: "L3 + runtime signals", 4: "L4 + database/cache tracing"}
    for level in (1, 2, 3, 4):
        extra = "" if final else f" {num(ml(level, 'random_forest', 'operation').get('macro_f1'))} | {num(ml(level, 'xgboost', 'operation').get('macro_f1'))} |"
        lines.append(f"| {names[level]} | {num(ml(level, 'random_forest').get('macro_f1'))} | {num(ml(level, 'xgboost').get('macro_f1'))} |{extra}")
    best_ml = max((ml(4, k).get("macro_f1") or 0) for k in ("random_forest", "xgboost"))
    lines += ["", "## RQ4 Rules vs ML", "",
              f"At L4: rules {num(rules['macro_f1'])} vs best ML {num(best_ml)} (macro-F1). "
              + ("ML does better than the rules." if best_ml > rules["macro_f1"] + 0.02 else
                 "ML does not do better than the interpretable rules." if best_ml < rules["macro_f1"] - 0.02 else "Rules and ML perform about the same."),
              "", "## RQ5 Uncertainty: abstention", "",
              f"Models evaluated {'once on the held-out session' if final else 'with grouped cross-validation (condition unseen)'}.", "",
              "| Method (L4) | Confidence threshold | Coverage | Accuracy when answering |", "|---|---|---|---|",
              f"| Rules | – | {pct(rules['coverage'])} | {pct(rules['selective_accuracy'])} |"]
    for kind in ("random_forest", "xgboost"):
        for t in ("0.0", "0.5", "0.7"):
            m = ml(4, kind, t=t)
            lines.append(f"| {kind.replace('_', ' ').title()} | {t} | {pct(m.get('coverage'))} | {pct(m.get('selective_accuracy'))} |")
    return lines + ["", f"Source: {src('diagnosis', cid)}", ""]


def rq6(main: str) -> list[str]:
    v = _load("verification", main)
    if not v:
        return ["## RQ6 Remediation", "", "No verification results yet.", ""]
    m, pairs = v["remediation_mapping"], v["pairs"]
    improved = sum(p["improved"] for p in pairs)
    back = sum(p["verdict"] == "improved, back to healthy" for p in pairs)
    lines = ["## RQ6 Remediation: does the recommended fix work, and does it restore normal performance?", "",
             f"- The fix recommended from the rules' diagnosis matches the injected fault for **{m['recommendation_matches_fault']}/{m['fault_runs']}** fault runs ({pct(m['rate'])}).",
             f"- **{improved}/{len(pairs)}** fixes improved server-side latency significantly (one-sided Welch on log latency, Holm, α 0.05); "
             f"**{back}/{len(pairs)}** were shown back to healthy (TOST, ±10 %).", "",
             "| Fix | Faulty → fixed (healthy) | Ratio | p (Holm) | Fixed / healthy [90 % CI] | Verdict |", "|---|---|---|---|---|---|"]
    for p in pairs:
        b = p["back_to_healthy"]
        lines.append(f"| {p['pair']} | {num(p['median_ms']['faulty'], 2)} → {num(p['median_ms']['fixed'], 2)} ({num(p['median_ms']['healthy'], 2)}) ms | "
                     f"×{num(p['improvement']['ratio'])} | {num(p['improvement']['p_holm'], 4)} | ×{num(b['ratio'])} "
                     f"[{num(b['ci90'][0]) if b['ci90'] else '–'}, {num(b['ci90'][1]) if b['ci90'] else '–'}] | {p['verdict']} |")
    lines += ["", f"Source: {src('verification', main)}", ""]
    loops_dir = DATA_DIR / "results" / "loops"
    loops = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(loops_dir.glob("*/report.json"))] if loops_dir.exists() else []
    loops = [r for r in loops if r.get("baseline_campaign") == main]
    if loops:
        lines += ["### Closed loop (observe → detect → diagnose → remediate → re-run → verify)", "",
                  "The fault is injected without telling the system; it is revealed only after the loop.", "",
                  "| Loop | Injected | Detected | Diagnosis | Fix applied | Server p50 before → after (healthy) | Outcome |", "|---|---|---|---|---|---|---|"]
        for r in loops:
            v = r.get("verification") or {}
            imp = v.get("improvement") or {}
            med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
            lines.append(f"| `{r['loop_id']}` | {r['operation']} `{r['injected_variant']}` | {'yes' if r['detection']['anomalous'] else 'no'} | "
                         f"{(r.get('diagnosis') or {}).get('class', '–')} | `{v.get('fix_variant', '–')}` | "
                         f"{num(med(v.get('before_ms')), 2)} → {num(med(v.get('after_ms')), 2)} ({num(v.get('healthy_median_ms'), 2)}) ms, ×{num(imp.get('ratio'))} | {r['outcome']} |")
        lines += ["", "Source: `data/results/loops/<loop-id>/report.json`", ""]
    return lines


THREATS = """## Threats to validity

- **Synthetic application and faults.** The reference API and its faults are built for the
  study. Each fault is a realistic, documented mistake, and each appears in two operations
  and at several severities. Generalization to other applications is not shown.
- **One machine.** All data comes from one Windows PC (Docker Desktop, WSL2), at a fixed
  load of 5 rps (0.5 rps for reports). Calibration (send-lag p99 ≤ 3.9 ms up to 100 rps) and
  the protocol's exclusion rules guard the client side. Server-side background noise is not
  controlled beyond running nothing else heavy.
- **Rule thresholds were set on the pilot.** The rules were designed from the fault
  mechanisms and checked on the pilot campaign. They were not tuned on the main or held-out
  data. Final claims use the held-out session.
- **Process-level CPU.** CPU and heap per request are process-level and coarse (Windows timer
  granularity on the host; finer inside the Linux container).
- **Class balance.** DB and cache faults have fewer runs (no severity levels). The ML models
  use balanced class weights, and macro-F1 weights every class equally.
- **Docker image identity.** In the main campaign, Docker's image ID changed on every rebuild
  of identical code (containerd image store), so the image rule could not be applied from
  the recorded IDs. Layers, config and creation time were verified identical, and
  `target-api/` had no commits during the campaign (protocol amendment 4).
"""


def power_threat(campaigns: list[str]) -> list[str]:
    """Power state of the runs (charger / battery), from data/raw/campaigns/<id>/power.json."""
    lines = []
    for cid in campaigns:
        path = DATA_DIR / "raw" / "campaigns" / cid / "power.json"
        if not path.exists():
            continue
        states = json.loads(path.read_text(encoding="utf-8"))["runs"].values()
        counts = {s: sum(1 for x in states if x == s) for s in ("ac", "battery", "mixed", "unknown")}
        lines.append(f"`{cid}`: {counts['ac']} runs on the charger, {counts['battery']} on battery, {counts['mixed']} switched during the run")
    if not lines:
        return []
    return ["- **Laptop power state.** The experiment PC is a laptop; on battery Windows limits CPU boost, and healthy runs on "
            "battery were ×1.6–2.3 slower than on the charger, while runs within one power state varied by 1–8 % (CV). "
            + "; ".join(lines) + ". The run order was randomized, so the power state is not tied to any condition: it adds "
            "noise (wider healthy baselines, weaker low-severity detection, wide verification intervals) rather than bias. "
            "Since protocol amendment 5, runs on battery are excluded and campaigns refuse to start on battery. "
            "Source: `data/raw/campaigns/<id>/power.json` (Windows Kernel-Power events).", ""]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--main", required=True)
    parser.add_argument("--heldout")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "results.md")
    args = parser.parse_args(argv)
    lines = ["# Results", "",
             f"Generated by `python -m evaluation.report --main {args.main}" + (f" --heldout {args.heldout}" if args.heldout else "") + "` from the result files.",
             "Do not edit by hand: re-run the command instead. Protocol: [experiments/protocol.md](../experiments/protocol.md).", ""]
    lines += dataset_section(args.main, args.heldout) + rq1(args.main, args.heldout) + rq2_to_rq5(args.main, args.heldout) + rq6(args.main) + [THREATS.rstrip("\n")]
    lines += power_threat([c for c in (args.main, args.heldout) if c])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"results report: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
