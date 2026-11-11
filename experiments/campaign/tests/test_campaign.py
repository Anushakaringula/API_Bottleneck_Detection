"""Campaign planning, orchestration (with a fake lab and runner) and summary. No Docker needed."""
import json
import tempfile
import unittest
from pathlib import Path

from campaign.plan import FAULT_CLASSES, expand, label_for, runner_config, validate_config
from campaign.run import run_campaign
from campaign.summarize import repeats_for, summarize

BASE = {
    "name": "test",
    "random_seed": 7,
    "seed_scale": "large",
    "workload": {"request_rate_per_second": 10, "duration_seconds": 5, "warmup_requests": 5},
    "operation_workload": {"reports": {"request_rate_per_second": 1}},
    "conditions": [
        {"operation": "users.list", "variants": ["healthy"], "repeats": 3},
        {"operation": "users.list", "variants": ["nplus1:20", "nplus1-fixed:20"], "repeats": 2},
        {"operation": "reports", "variants": ["healthy", "cache"], "repeats": 2},
    ],
}


def config(**changes):
    return validate_config({**json.loads(json.dumps(BASE)), **changes})


class PlanTests(unittest.TestCase):
    def test_expansion_is_complete_shuffled_and_reproducible(self):
        runs = expand(config())
        self.assertEqual(len(runs), 3 + 4 + 4)
        self.assertEqual([r["run_index"] for r in runs], list(range(1, 12)))
        self.assertEqual(runs, expand(config()))
        self.assertNotEqual([r["condition_id"] for r in runs], [r["condition_id"] for r in sorted(runs, key=lambda r: r["condition_id"])])
        self.assertNotEqual([r["condition_id"] for r in runs], [r["condition_id"] for r in expand(config(random_seed=8))])
        # Same workload seed for every run of one operation (paired comparisons), different across operations.
        seeds = {(r["operation"], r["workload_seed"]) for r in runs}
        self.assertEqual(len(seeds), 2)

    def test_labels(self):
        self.assertEqual(label_for("healthy")["fault_class"], "NONE")
        nplus1 = label_for("nplus1:50")
        self.assertEqual((nplus1["fault"], nplus1["fault_class"], nplus1["severity"], nplus1["is_fixed"]), ("nplus1", "N_PLUS_ONE", 50.0, False))
        fixed = label_for("noindex-fixed")
        self.assertEqual((fixed["fault_class"], fixed["remediation_of"], fixed["is_fixed"]), ("NONE", "noindex", True))
        self.assertEqual(set(FAULT_CLASSES.values()), {"NONE", "DB_BOTTLENECK", "N_PLUS_ONE", "LARGE_PAYLOAD", "CACHE_OPPORTUNITY", "CPU_BOTTLENECK"})
        with self.assertRaises(ValueError):
            label_for("mystery")

    def test_validation(self):
        with self.assertRaises(ValueError):
            config(name="Bad Name")
        with self.assertRaises(ValueError):
            config(seed_scale="huge")
        with self.assertRaises(ValueError):
            config(conditions=[{"operation": "nope", "variants": ["healthy"]}])
        with self.assertRaises(ValueError):
            config(workload={"request_rate_per_second": 1, "duration_seconds": 1, "bogus": 1})
        with self.assertRaises(ValueError):
            config(conditions=[{"operation": "users.list", "variants": ["healthy"], "repeats": 0}])

    def test_duplicate_conditions_are_rejected(self):
        cfg = config(conditions=[{"operation": "users.list", "variants": ["healthy"]}, {"operation": "users.list", "variants": ["healthy"]}])
        with self.assertRaises(ValueError):
            expand(cfg)

    def test_runner_config_carries_no_ground_truth(self):
        cfg = config()
        for run in expand(cfg):
            raw = runner_config(cfg, run, "test-campaign")
            text = json.dumps(raw)
            for secret in ("nplus1", "cache", "healthy", "fixed", run["condition_id"]):
                self.assertNotIn(secret, text)
            self.assertEqual(raw["load_model"], "open")
            expected_rate = 1 if run["operation"] == "reports" else 10
            self.assertEqual(raw["request_rate_per_second"], expected_rate)


class FakeLab:
    def __init__(self, fail_runs=()):
        self.calls = []
        self.fail_runs = set(fail_runs)
        self.api_running = False

    def reset(self):
        self.calls.append("reset")

    def api_up(self, variants, run_id):
        self.calls.append(("up", json.dumps(variants)))
        self.api_running = True
        if any(token in run_id for token in self.fail_runs):
            raise RuntimeError("simulated start failure")

    def api_down(self):
        self.calls.append("down")
        self.api_running = False

    def db_statistics(self):
        return {"statements": [], "indexes": ["orders_user_id_idx"]}

    def api_logs_tail(self, lines=20):
        return "log tail"


def fake_execute(counter):
    def execute(config, *, output_root, span_file, repeat_group_id, repeat_index):
        counter.append(config)
        experiment_id = f"exp-{len(counter):03d}"
        run_dir = output_root / experiment_id
        run_dir.mkdir(parents=True)
        p50 = 100.0 if "/api/reports" in config["target_url"] else 5.0 + len(counter) % 3
        summary = {
            "experiment_id": experiment_id,
            "measured": {"total_requests": 50, "failed_requests": 0, "median_ms": p50, "p95_ms": p50 * 2, "p99_ms": p50 * 3,
                         "latency_from_intended_ms": {"median_ms": p50}, "send_lag_ms": {"p99_ms": 1.0},
                         "achieved_send_rate_per_second": 10.0, "maximum_observed_in_flight": 2},
            "telemetry_correlation": {"correlation_status": "complete", "request_level": {"client_requests": 55, "requests_matched_to_one_span": 55}},
        }
        (run_dir / "summary.json").write_text(json.dumps(summary))
        return summary
    return execute


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = config()
        self.campaign_dir = self.root / "campaigns" / "test-c1"
        self.campaign_dir.mkdir(parents=True)
        self.runs = expand(self.cfg)
        (self.campaign_dir / "manifest.json").write_text(json.dumps({"campaign_id": "test-c1", "config": self.cfg, "runs": self.runs}))
        self.labels = self.root / "labels" / "test-c1.jsonl"
        self.labels.parent.mkdir()

    def run_campaign(self, lab, executed, limit=None):
        return run_campaign(self.cfg, self.campaign_dir, self.labels, lab, execute=fake_execute(executed), limit=limit, log=lambda _: None)

    def test_every_run_resets_starts_with_its_variant_and_stops(self):
        lab, executed = FakeLab(), []
        counts = self.run_campaign(lab, executed)
        self.assertEqual(counts, {"completed": 11, "failed": 0, "skipped": 0, "warmup": 0, "rerun": 0})
        ups = [c for c in lab.calls if isinstance(c, tuple)]
        self.assertEqual(len(ups), 11)
        self.assertEqual(lab.calls.count("reset"), 11)
        self.assertEqual(lab.calls.count("down"), 11)
        self.assertFalse(lab.api_running)
        self.assertEqual([json.loads(u[1]) for u in ups], [{r["operation"]: r["variant"]} for r in self.runs])
        labels = [json.loads(line) for line in self.labels.read_text().splitlines()]
        self.assertEqual(len(labels), 11)
        self.assertEqual({l["fault_class"] for l in labels}, {"NONE", "N_PLUS_ONE", "CACHE_OPPORTUNITY"})
        self.assertTrue((self.campaign_dir / "db-stats" / "exp-001.json").exists())

    def test_failed_runs_are_recorded_and_resume_continues(self):
        lab, executed = FakeLab(fail_runs={"-r000002"}), []
        counts = self.run_campaign(lab, executed, limit=4)
        self.assertEqual(counts, {"completed": 3, "failed": 1, "skipped": 0, "warmup": 0, "rerun": 0})
        self.assertFalse(lab.api_running)
        progress = [json.loads(line) for line in (self.campaign_dir / "progress.jsonl").read_text().splitlines()]
        failed = [p for p in progress if p["status"] == "failed"]
        self.assertEqual(failed[0]["run_index"], 2)
        self.assertIn("simulated start failure", failed[0]["error"])
        # Resume: completed runs are skipped, the failed one is retried.
        counts = self.run_campaign(FakeLab(), executed)
        self.assertEqual(counts, {"completed": 8, "failed": 0, "skipped": 3, "warmup": 0, "rerun": 0})
        self.assertEqual(len(self.labels.read_text().splitlines()), 11)

    def test_summary_reports_noise_and_effects(self):
        self.run_campaign(FakeLab(), [])
        report = summarize(self.campaign_dir, self.labels)
        self.assertEqual(report["completed_runs"], 11)
        self.assertEqual(report["telemetry"]["request_match_rate"], 1.0)
        noise = {n["operation"]: n for n in report["healthy_noise"]}
        self.assertEqual(noise["users.list"]["runs"], 3)
        self.assertEqual(noise["reports"]["cv"], 0.0)
        conditions = {c["condition_id"]: c for c in report["conditions"]}
        self.assertAlmostEqual(conditions["reports/cache"]["ratio_to_healthy"], 1.0)
        self.assertIn("separated_from_healthy", conditions["users.list/nplus1:20"])

    def test_repeats_estimate(self):
        self.assertIsNone(repeats_for(None))
        self.assertEqual(repeats_for(0.0), 2)
        self.assertEqual(repeats_for(0.1), 4)
        self.assertGreater(repeats_for(0.3), repeats_for(0.1))


if __name__ == "__main__":
    unittest.main()
