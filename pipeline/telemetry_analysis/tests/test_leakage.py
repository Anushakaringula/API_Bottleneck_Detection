"""Automated checks that labels, scenario names, endpoints and IDs never reach predictor features."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from telemetry_analysis import analyzer
from telemetry_analysis.analyzer import analyze
from telemetry_analysis.leakage_audit import direct_leakage_problems, forbidden_predictor_names, proxy_leakage_report
from telemetry_analysis.schema import JOIN_KEY_FIELDS, PREDICTOR_FIELDS

# Distinctive text that must never appear in the predictor file.
SCENARIO_TOKEN = "zz-scenario-token-cpu-intensive"
ENDPOINT_TOKEN = "http://127.0.0.1:3000/zz-endpoint-token"
GROUP_TOKEN = "zz-repeat-group-token"


def write_run(root: Path, run_id: str, scenario: str, latency: float) -> None:
    run = root / "experiments" / run_id
    run.mkdir(parents=True)
    config = {
        "experiment_id": run_id, "repeat_group_id": GROUP_TOKEN, "repeat_index": 1,
        "scenario": scenario, "target_endpoint": ENDPOINT_TOKEN, "http_method": "GET",
        "execution": {"measured_attempted_requests": 2, "measured_phase_elapsed_seconds": 2.0, "maximum_observed_in_flight": 1},
        "workload_configuration": {"concurrency": 1}, "environment": {},
    }
    summary = {"measured": {"total_requests": 2, "phase_elapsed_seconds": 2.0}, "warmup": {"total_requests": 0}}
    rows = [{"phase": "measured", "latency_ms": latency, "status_code": 200, "outcome": "success", "response_bytes": 10}] * 2
    (run / "configuration.json").write_text(json.dumps(config))
    (run / "summary.json").write_text(json.dumps(summary))
    (run / "requests.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


class LeakageTests(unittest.TestCase):
    def run_analysis(self, root: Path) -> Path:
        spans = root / "spans.jsonl"
        spans.write_text("")
        return analyze(root / "experiments", spans, root / "analysis")

    def test_1_allowlist_contains_no_forbidden_names(self):
        self.assertEqual(forbidden_predictor_names(), [])
        # the pattern itself must catch typical leaks
        self.assertEqual(forbidden_predictor_names({"scenario", "target_endpoint", "run_id", "p95_latency_ms"}),
                         ["run_id", "scenario", "target_endpoint"])

    def test_2_feature_output_has_no_label_text_and_only_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_run(root, "zz-run-id-token-1", SCENARIO_TOKEN, 10.0)
            out = self.run_analysis(root)
            text = (out / "run_features.jsonl").read_text()
            for token in (SCENARIO_TOKEN, ENDPOINT_TOKEN, GROUP_TOKEN, "zz-run-id-token-1"):
                self.assertNotIn(token, text)
            for row in map(json.loads, text.splitlines()):
                self.assertTrue(set(row) <= PREDICTOR_FIELDS | JOIN_KEY_FIELDS)
                for key, value in row.items():
                    if key not in JOIN_KEY_FIELDS:
                        self.assertTrue(value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)), key)
            self.assertEqual(direct_leakage_problems(out), [])

    def test_3_features_and_metadata_are_separated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_run(root, "run-a", SCENARIO_TOKEN, 10.0)
            out = self.run_analysis(root)
            feature = json.loads((out / "run_features.jsonl").read_text())
            metadata = json.loads((out / "run_metadata.jsonl").read_text())
            self.assertEqual(set(feature) & set(metadata), JOIN_KEY_FIELDS)
            self.assertEqual(metadata["scenario"], SCENARIO_TOKEN)  # labels kept, but only in metadata

    def test_4_serialization_guard_blocks_unapproved_fields(self):
        original = analyzer._run_features

        def leaking(*args, **kwargs):
            feature, meta = original(*args, **kwargs)
            feature["scenario"] = meta["scenario"]
            return feature, meta

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_run(root, "run-a", SCENARIO_TOKEN, 10.0)
            with mock.patch.object(analyzer, "_run_features", leaking):
                with self.assertRaisesRegex(ValueError, "unapproved feature fields"):
                    self.run_analysis(root)
            self.assertFalse(any((root / "analysis").glob("*/run_features.jsonl")))

    def test_proxy_audit_flags_a_separating_feature(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_run(root, "run-a", "slow", 100.0)
            write_run(root, "run-b", "fast", 1.0)
            out = self.run_analysis(root)
            flagged = {item["predictor"] for item in proxy_leakage_report(out)}
            self.assertIn("median_latency_ms", flagged)


if __name__ == "__main__":
    unittest.main()
