"""Protocol rules (experiments/protocol.md §6–7): warm-up run, exclusions, re-runs, clean git."""
import json
import tempfile
import unittest
from pathlib import Path

from campaign.plan import expand, validate_config
from campaign.quality import DEFAULT_EXCLUSION, exclusion_reasons
from campaign.run import RERUN_INDEX_BASE, WARMUP_INDEX_BASE, preflight, run_campaign
from campaign.summarize import summarize

from test_campaign import BASE, FakeLab

GOOD = {"correlation_status": "complete", "measured_requests": 150, "failed_requests": 0, "send_lag_p99_ms": 2.0, "image_id": "img-1"}
ENV = {"git_dirty": False, "target_api_image_id": "img-1"}


class ExclusionRuleTests(unittest.TestCase):
    def test_good_run_is_kept(self):
        self.assertEqual(exclusion_reasons(GOOD, ENV, DEFAULT_EXCLUSION), [])

    def test_each_rule(self):
        cases = {
            "telemetry": {**GOOD, "correlation_status": "partial"},
            "failed requests": {**GOOD, "failed_requests": 2},
            "send-lag": {**GOOD, "send_lag_p99_ms": 10.5},
            "image": {**GOOD, "image_id": "img-2"},
        }
        for word, record in cases.items():
            reasons = exclusion_reasons(record, ENV, DEFAULT_EXCLUSION)
            self.assertEqual(len(reasons), 1, word)
            self.assertIn(word, reasons[0])
        self.assertEqual(exclusion_reasons({**GOOD, "failed_requests": 1}, ENV, DEFAULT_EXCLUSION), [])  # 1/150 <= 1 %
        self.assertIn("git_dirty", exclusion_reasons(GOOD, {**ENV, "git_dirty": True}, DEFAULT_EXCLUSION)[0])
        self.assertTrue(exclusion_reasons({**GOOD, "send_lag_p99_ms": None}, ENV, DEFAULT_EXCLUSION))


def executor_with_lag(lagging_calls):
    """Fake runner: the n-th call (1-based) in `lagging_calls` reports a send lag of 25 ms."""
    calls = []

    def execute(config, *, output_root, span_file, repeat_group_id, repeat_index):
        calls.append(config)
        experiment_id = f"exp-{len(calls):03d}"
        (output_root / experiment_id).mkdir(parents=True)
        lag = 25.0 if len(calls) in lagging_calls else 1.5
        summary = {"experiment_id": experiment_id,
                   "measured": {"total_requests": 150, "failed_requests": 0, "median_ms": 5.0, "p95_ms": 9.0, "p99_ms": 12.0,
                                "latency_from_intended_ms": {"median_ms": 5.0}, "send_lag_ms": {"p99_ms": lag},
                                "achieved_send_rate_per_second": 5.0, "maximum_observed_in_flight": 2},
                   "telemetry_correlation": {"correlation_status": "complete", "request_level": {"client_requests": 170, "requests_matched_to_one_span": 170}}}
        (output_root / experiment_id / "summary.json").write_text(json.dumps(summary))
        return summary
    return execute, calls


class ProtocolOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.cfg = validate_config({**json.loads(json.dumps(BASE)), "discard_warmup_runs": 1, "rerun_excluded": True,
                                    "apply_exclusions": True, "require_clean_git": True})
        self.runs = expand(self.cfg)
        self.campaign_dir = root / "campaigns" / "main-test"
        self.campaign_dir.mkdir(parents=True)
        (self.campaign_dir / "manifest.json").write_text(json.dumps({"campaign_id": "main-test", "config": self.cfg, "runs": self.runs,
                                                                      "environment": {"git_dirty": False, "target_api_image_id": None}}))
        self.labels = root / "labels" / "main-test.jsonl"
        self.labels.parent.mkdir()

    def test_warmup_is_discarded_and_excluded_runs_are_rerun_once(self):
        # call 1 = warm-up, calls 2..12 = planned runs; planned run at call 4 lags -> excluded -> re-run
        execute, calls = executor_with_lag({1, 4})
        counts = run_campaign(self.cfg, self.campaign_dir, self.labels, FakeLab(), execute=execute, log=lambda _: None)
        self.assertEqual((counts["warmup"], counts["completed"], counts["rerun"]), (1, 11, 1))
        progress = [json.loads(l) for l in (self.campaign_dir / "progress.jsonl").read_text().splitlines()]
        warmups = [p for p in progress if p["status"] == "discarded_warmup"]
        self.assertEqual(len(warmups), 1)
        self.assertGreaterEqual(warmups[0]["run_index"], WARMUP_INDEX_BASE)
        labels = [json.loads(l) for l in self.labels.read_text().splitlines()]
        self.assertEqual(len(labels), 12)  # 11 planned + 1 re-run; the warm-up is never labelled
        self.assertNotIn("exp-001", {l["experiment_id"] for l in labels})
        rerun = [l for l in labels if l.get("rerun_of")]
        self.assertEqual(len(rerun), 1)
        original = self.runs[2]  # third planned run = fourth call
        self.assertEqual(rerun[0]["rerun_of"], original["run_index"])
        self.assertEqual(rerun[0]["run_index"], RERUN_INDEX_BASE + original["run_index"])
        self.assertEqual(rerun[0]["condition_id"], original["condition_id"])
        exclusions = json.loads((self.campaign_dir / "exclusions.json").read_text())["excluded"]
        self.assertEqual([e["run_index"] for e in exclusions], [original["run_index"]])
        self.assertEqual(exclusions[0]["rerun_experiment_id"], "exp-013")
        # Resume after completion: nothing to do, no second re-run.
        execute2, calls2 = executor_with_lag(set())
        counts2 = run_campaign(self.cfg, self.campaign_dir, self.labels, FakeLab(), execute=execute2, log=lambda _: None)
        self.assertEqual((counts2["completed"], counts2["rerun"], counts2["warmup"]), (0, 0, 0))
        self.assertEqual(calls2, [])

    def test_summary_leaves_excluded_runs_out_of_the_statistics(self):
        execute, _ = executor_with_lag({1, 4})
        run_campaign(self.cfg, self.campaign_dir, self.labels, FakeLab(), execute=execute, log=lambda _: None)
        report = summarize(self.campaign_dir, self.labels)
        self.assertTrue(report["exclusions"]["applied"])
        self.assertEqual(report["completed_runs"], 12)
        self.assertEqual(report["analysed_runs"], 11)
        self.assertEqual(len(report["exclusions"]["excluded_runs"]), 1)

    def test_clean_git_is_required(self):
        lab = FakeLab()
        lab.seed_info = lambda: {"seed_scale": "large"}
        lab.registered_variants = lambda: {"users.list": ["healthy", "nplus1", "nplus1-fixed"], "reports": ["healthy", "cache"]}
        with self.assertRaises(SystemExit) as raised:
            preflight(self.cfg, self.runs, lab, git_dirty=True)
        self.assertIn("committed", str(raised.exception))
        preflight(self.cfg, self.runs, lab, git_dirty=False)


if __name__ == "__main__":
    unittest.main()
