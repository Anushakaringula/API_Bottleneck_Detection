"""Verification statistics and the remediation catalogue."""
import math
import unittest

from diagnosis import CLASSES, INSUFFICIENT
from remediation.catalogue import CATALOGUE, recommend
from verification.stats import equivalent, holm, mann_whitney_less, welch_log


class StatsTests(unittest.TestCase):
    def test_clear_improvement(self):
        before, after = [24.0, 25.0, 23.5, 24.8, 24.2], [5.0, 5.1, 4.9]
        w = welch_log(before, after, confidence=0.95)
        self.assertAlmostEqual(w["ratio"], math.exp(sum(map(math.log, after)) / 3 - sum(map(math.log, before)) / 5))
        self.assertLess(w["ci"][1], 0.25)
        self.assertLess(w["p_one_sided"], 0.001)
        self.assertAlmostEqual(mann_whitney_less(before, after), 1 / 56)  # exact: smallest possible p for 5 vs 3

    def test_no_improvement(self):
        w = welch_log([5.0, 5.2, 4.8, 5.1], [5.1, 4.9, 5.0])
        self.assertGreater(w["p_one_sided"], 0.2)
        self.assertIsNone(welch_log([5.0], [4.0])["p_one_sided"])

    def test_holm(self):
        adjusted = holm({"a": 0.01, "b": 0.04, "c": 0.03, "d": None})
        self.assertAlmostEqual(adjusted["a"], 0.03)
        self.assertAlmostEqual(adjusted["c"], 0.06)
        self.assertAlmostEqual(adjusted["b"], 0.06)  # monotone: never below the previous adjusted value
        self.assertIsNone(adjusted["d"])

    def test_equivalence(self):
        healthy = [5.0, 5.05, 4.95, 5.02, 4.98, 5.01, 4.99, 5.03, 4.97, 5.0]
        self.assertTrue(equivalent(healthy, [5.0, 5.02, 4.98])["equivalent"])
        self.assertFalse(equivalent(healthy, [6.0, 6.1, 5.9])["equivalent"])     # 20 % slower
        self.assertFalse(equivalent(healthy, [4.0, 6.0, 5.0])["equivalent"])     # too noisy to show equivalence


class CatalogueTests(unittest.TestCase):
    def test_every_class_has_a_fix_and_a_checkable_effect(self):
        self.assertEqual(set(CATALOGUE), set(CLASSES))
        for cls, entry in CATALOGUE.items():
            self.assertTrue(entry["variant"].endswith("-fixed"), cls)
            self.assertTrue(entry["expected_change"], cls)
            self.assertTrue(set(entry["expected_change"].values()) <= {"up", "down"})

    def test_recommend(self):
        rec = recommend({"diagnosis": "N_PLUS_ONE", "confidence": "high", "evidence": ["21 queries per request vs 1"]})
        self.assertEqual(rec["variant"], "nplus1-fixed")
        self.assertEqual(rec["based_on"], ["21 queries per request vs 1"])
        none = recommend({"diagnosis": INSUFFICIENT, "evidence": []})
        self.assertIsNone(none["variant"])
        self.assertEqual(none["recommendation"], "collect more evidence")


if __name__ == "__main__":
    unittest.main()
