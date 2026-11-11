"""Diagnosis rules, baseline-relative features, ML wrapper and evaluation metrics (synthetic data)."""
import math
import unittest

from diagnosis import INSUFFICIENT, ml
from diagnosis.relative import DIFFERENCE_FEATURES, relative, relative_table
from diagnosis.rules import diagnose
from evaluation.diagnosis import abstain, metrics

BASE = {"db_queries_per_request_mean": 1.0, "repeated_query_max_median": 1.0, "db_time_p50_ms": 2.0, "db_time_share_p50": 0.4,
        "response_bytes_median": 1500.0, "app_time_p50_ms": 3.0, "cache_hit_ratio": None, "event_loop_delay_p99_ms": 11.0}
CACHED_BASE = {**BASE, "db_queries_per_request_mean": 0.05, "db_time_p50_ms": 0.0, "db_time_share_p50": 0.0, "cache_hit_ratio": 0.95}


def run(**changes):
    return {**BASE, **changes}


class RuleTests(unittest.TestCase):
    def test_each_class_from_its_mechanism(self):
        cases = {
            "N_PLUS_ONE": (run(db_queries_per_request_mean=21, repeated_query_max_median=20, db_time_p50_ms=18, db_time_share_p50=0.7), BASE),
            "DB_BOTTLENECK": (run(db_time_p50_ms=50, db_time_share_p50=0.95), BASE),
            "LARGE_PAYLOAD": (run(response_bytes_median=3_000_000, app_time_p50_ms=40), BASE),
            "CACHE_OPPORTUNITY": ({**CACHED_BASE, "cache_hit_ratio": 0.0, "db_queries_per_request_mean": 1.0, "db_time_p50_ms": 2.0}, CACHED_BASE),
            "CPU_BOTTLENECK": (run(app_time_p50_ms=25, event_loop_delay_p99_ms=55), BASE),
        }
        for expected, (features, base) in cases.items():
            result = diagnose(features, base)
            self.assertEqual(result["diagnosis"], expected, result)
            self.assertTrue(result["evidence"])

    def test_classes_stay_apart(self):
        # payload costs CPU too, but the CPU rule needs an unchanged response size
        self.assertEqual(diagnose(run(response_bytes_median=3_000_000, app_time_p50_ms=40), BASE)["fired"], ["LARGE_PAYLOAD"])
        # N+1 raises database time, but the DB rule needs an unchanged query count
        self.assertNotIn("DB_BOTTLENECK", diagnose(run(db_queries_per_request_mean=21, repeated_query_max_median=20, db_time_p50_ms=18, db_time_share_p50=0.7), BASE)["fired"])
        # a cache fault adds a query per request, but neither N+1 nor DB fires
        cache = diagnose({**CACHED_BASE, "cache_hit_ratio": 0.0, "db_queries_per_request_mean": 1.0, "db_time_p50_ms": 2.0}, CACHED_BASE)
        self.assertEqual(cache["fired"], ["CACHE_OPPORTUNITY"])

    def test_insufficient_evidence(self):
        result = diagnose(run(app_time_p50_ms=4.0), BASE)  # a small change: no rule may fire
        self.assertEqual((result["diagnosis"], result["confidence"]), (INSUFFICIENT, "none"))


class RelativeTests(unittest.TestCase):
    def test_transform(self):
        rel = relative({"server_latency_p50_ms": 20.0, "db_time_share_p50": 0.9, "cache_hit_ratio": None}, {"server_latency_p50_ms": 5.0, "db_time_share_p50": 0.4, "cache_hit_ratio": None})
        self.assertAlmostEqual(rel["server_latency_p50_ms"], math.log2(20.01 / 5.01))
        self.assertAlmostEqual(rel["db_time_share_p50"], 0.5)
        self.assertEqual(rel["cache_hit_ratio"], 0.0)
        self.assertIn("db_time_share_p50", DIFFERENCE_FEATURES)
        self.assertNotIn("server_latency_p50_ms", DIFFERENCE_FEATURES)

    def test_leave_one_out_baseline(self):
        def r(eid, value):
            return {"features": {"server_latency_p50_ms": value}, "run": {"route": "/x", "excluded_reasons": []}}
        data = {"h1": r("h1", 4.0), "h2": r("h2", 6.0), "h3": r("h3", 8.0), "f1": r("f1", 30.0)}
        table = relative_table(data, data, {"h1", "h2", "h3"}, same_campaign=True)
        self.assertEqual(table["f1"]["base"]["server_latency_p50_ms"], 6.0)   # all healthy runs
        self.assertEqual(table["h1"]["base"]["server_latency_p50_ms"], 7.0)   # without itself


class MlAndMetricTests(unittest.TestCase):
    def test_models_learn_a_separable_problem_and_abstain(self):
        names = ["a", "b"]
        rows = [{"a": 5.0 + i * 0.1, "b": 0.0} for i in range(10)] + [{"a": 0.0, "b": 5.0 + i * 0.1} for i in range(10)]
        y = ["N_PLUS_ONE"] * 10 + ["CPU_BOTTLENECK"] * 10
        x = ml.matrix(rows, names)
        for kind in ml.MODELS:
            pred, proba, classes = ml.fit_predict(kind, x, y, ml.matrix([{"a": 5.5, "b": 0.0}, {"a": 0.0, "b": 5.5}], names))
            self.assertEqual(pred, ["N_PLUS_ONE", "CPU_BOTTLENECK"], kind)
            self.assertEqual(proba.shape, (2, 2))
        self.assertEqual(abstain(["A", "B"], [0.9, 0.4], 0.5), ["A", INSUFFICIENT])

    def test_metrics_count_abstentions_as_wrong(self):
        m = metrics(["N_PLUS_ONE", "N_PLUS_ONE", "CPU_BOTTLENECK", "CPU_BOTTLENECK"], ["N_PLUS_ONE", INSUFFICIENT, "CPU_BOTTLENECK", "N_PLUS_ONE"])
        self.assertEqual(m["accuracy"], 0.5)
        self.assertEqual(m["coverage"], 0.75)
        self.assertAlmostEqual(m["selective_accuracy"], 2 / 3)
        self.assertEqual(m["confusion"]["matrix"][1][-1], 1)  # the N+1 run that abstained


if __name__ == "__main__":
    unittest.main()
