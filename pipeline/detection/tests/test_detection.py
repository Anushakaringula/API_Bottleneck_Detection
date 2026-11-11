"""Detector of record, leave-one-out scoring, and the pipeline's import boundaries."""
import ast
import unittest
from pathlib import Path

from detection.rule import baseline, fit, verdict
from evaluation.detection import detect, score, wilson

PIPELINE = Path(__file__).resolve().parents[2]


def run(eid, route, value, coverage=1.0, excluded=()):
    return {"features": {"experiment_id": eid, "server_latency_p50_ms": value},
            "run": {"experiment_id": eid, "route": route, "span_coverage": coverage, "excluded_reasons": list(excluded)}}


class RuleTests(unittest.TestCase):
    def test_threshold_is_max_of_p99_and_min_slowdown(self):
        tight = baseline([5.0, 5.0, 5.1, 5.0])
        self.assertAlmostEqual(tight["threshold"], 6.0)  # 1.2 x median dominates when noise is small
        wide = baseline([5.0, 5.0, 5.0, 9.0])
        self.assertGreater(wide["threshold"], 8.8)  # P99 dominates when a healthy run was slow
        self.assertIsNone(baseline([5.0]))

    def test_verdicts(self):
        base = baseline([5.0, 5.0, 5.0])
        self.assertEqual(verdict(run("a", "/r", 5.5)["run"], {"server_latency_p50_ms": 5.5}, base)["verdict"], "normal")
        anomalous = verdict(run("a", "/r", 9.0)["run"], {"server_latency_p50_ms": 9.0}, base)
        self.assertEqual(anomalous["verdict"], "anomalous")
        self.assertAlmostEqual(anomalous["ratio_to_baseline"], 1.8)
        self.assertEqual(verdict(run("a", "/r", 9.0, coverage=0.5)["run"], {"server_latency_p50_ms": 9.0}, base)["verdict"], "inconclusive")
        self.assertEqual(verdict(run("a", "/r", 9.0, excluded=["lag"])["run"], {"server_latency_p50_ms": 9.0}, base)["verdict"], "inconclusive")
        self.assertEqual(verdict(run("a", "/r", 9.0)["run"], {"server_latency_p50_ms": 9.0}, None)["verdict"], "inconclusive")
        self.assertEqual(set(fit([("/a", 1.0), ("/a", 1.2), ("/b", 3.0)])), {"/a"})

    def test_wilson_interval(self):
        low, high = wilson(0, 80)
        self.assertEqual(low, 0.0)
        self.assertLess(high, 0.05)
        self.assertIsNone(wilson(0, 0))


class EvaluationTests(unittest.TestCase):
    def test_leave_one_out_and_scoring(self):
        runs = {f"h{i}": run(f"h{i}", "/r", v) for i, v in enumerate([5.0, 5.1, 4.9, 5.0, 8.0])}  # h4: a slow healthy run
        runs |= {"f1": run("f1", "/r", 20.0), "f2": run("f2", "/r", 5.2), "x1": run("x1", "/r", 5.0), "e1": run("e1", "/r", 30.0, excluded=["lag"])}
        healthy = {f"h{i}" for i in range(5)}
        verdicts = detect(runs, runs, healthy, same_campaign=True)
        self.assertTrue(all(verdicts[h]["leave_one_out"] for h in healthy))
        self.assertFalse(verdicts["f1"]["leave_one_out"])
        # Scored against the other four, the slow healthy run is a false alarm; with itself included it would not be.
        self.assertEqual(verdicts["h4"]["verdict"], "anomalous")
        self.assertEqual(verdicts["h4"]["baseline_runs"], 4)
        labels = {**{h: {"variant": "healthy", "is_fixed": False, "fault_class": "NONE", "condition_id": "r/healthy"} for h in healthy},
                  "f1": {"variant": "noindex", "is_fixed": False, "fault_class": "DB_BOTTLENECK", "condition_id": "r/noindex"},
                  "f2": {"variant": "cpu:20", "is_fixed": False, "fault_class": "CPU_BOTTLENECK", "condition_id": "r/cpu:20"},
                  "x1": {"variant": "noindex-fixed", "is_fixed": True, "fault_class": "NONE", "condition_id": "r/noindex-fixed"},
                  "e1": {"variant": "noindex", "is_fixed": False, "fault_class": "DB_BOTTLENECK", "condition_id": "r/noindex"}}
        s = score(verdicts, labels)
        self.assertEqual((s["false_alarms_on_healthy"]["anomalous"], s["false_alarms_on_healthy"]["decided"]), (1, 5))
        self.assertEqual((s["detection_on_faults"]["anomalous"], s["detection_on_faults"]["decided"], s["detection_on_faults"]["inconclusive"]), (1, 2, 1))
        self.assertEqual(s["flagged_fixed_variants"]["anomalous"], 0)
        self.assertFalse(s["false_alarm_rate_within_limit"])  # 1/5 = 20 % > 5 %
        self.assertEqual(s["by_fault_class"]["CPU_BOTTLENECK"]["rate"], 0.0)  # a weak fault can be missed

    def test_baseline_from_another_campaign(self):
        reference = {f"m{i}": run(f"m{i}", "/r", 5.0) for i in range(3)}
        evaluated = {"h1": run("h1", "/r", 5.1), "f1": run("f1", "/r", 9.0)}
        verdicts = detect(evaluated, reference, set(reference), same_campaign=False)
        self.assertEqual((verdicts["h1"]["verdict"], verdicts["f1"]["verdict"]), ("normal", "anomalous"))
        self.assertFalse(any(v["leave_one_out"] for v in verdicts.values()))


class BoundaryTests(unittest.TestCase):
    def imports(self, package):
        found = set()
        for path in (PIPELINE / package).rglob("*.py"):
            if "tests" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    found |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    found.add(node.module.split(".")[0] + ("." + node.module.split(".")[1] if node.module.count(".") else ""))
        return found

    def test_pipeline_never_imports_experiments_or_the_target_api(self):
        for package in ("features", "detection", "evaluation", "telemetry_analysis"):
            names = {n.split(".")[0] for n in self.imports(package)}
            self.assertFalse(names & {"experiment_runner", "campaign"}, package)

    def test_only_evaluation_reads_labels(self):
        for package in ("features", "detection", "telemetry_analysis"):
            self.assertNotIn("evaluation", {n.split(".")[0] for n in self.imports(package)}, package)
            for path in (PIPELINE / package).rglob("*.py"):
                if "tests" not in path.parts:
                    self.assertNotIn("data/labels", path.read_text(encoding="utf-8").replace("\\", "/"), str(path))


if __name__ == "__main__":
    unittest.main()
