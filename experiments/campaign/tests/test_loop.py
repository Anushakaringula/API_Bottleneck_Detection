"""Closed loop end to end with a fake lab: real extraction, detection, diagnosis, recommendation, verification."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from campaign.loop import closed_loop
from campaign.plan import validate_config

from test_campaign import BASE


class LoopLab:
    def __init__(self):
        self.variant = None

    def reset(self):
        pass

    def api_up(self, variants, run_id):
        self.variant = next(iter(variants.values()))

    def api_down(self):
        pass

    def registered_variants(self):
        return {"users.list": ["healthy", "nplus1", "nplus1-fixed"]}


def fake_execute(lab, spans_dir, counter):
    """Writes runner artifacts and spans shaped like the variant the lab is running."""
    def execute(config, *, output_root, span_file, repeat_group_id, repeat_index):
        counter.append(1)
        faulty = lab.variant.startswith("nplus1:")
        experiment_id = str(uuid.uuid4())
        run_dir = output_root / experiment_id
        run_dir.mkdir(parents=True)
        (run_dir / "configuration.json").write_text(json.dumps({"execution": {"measured_phase_elapsed_seconds": 10.0}}))
        requests, spans = [], []
        for k in range(20):
            trace = f"{experiment_id[:8]}{k:024d}"
            server = (24.0 if faulty else 5.0) + (k % 3) * 0.1 + len(counter) * 0.01
            requests.append({"phase": "measured", "trace_id": trace, "latency_ms": server + 10, "outcome": "success", "response_bytes": 1300})
            spans.append({"span_name": "experiment.http.request", "trace_id": trace, "span_kind": 0, "duration_ms": server,
                          "timestamp": "2026-11-11T10:00:00.000Z",
                          "attributes": {"experiment.latency_ms": server, "http.route": "/api/users", "experiment.cpu_user_ms": 1.0, "experiment.cpu_system_ms": 0.0}})
            queries = [("SELECT users", 0.5)] + ([("SELECT count WHERE user_id=$1", 0.8)] * 20 if faulty else [])
            t = 0
            for text, d in queries:
                spans.append({"span_name": "pg.query:SELECT lab", "trace_id": trace, "span_kind": 2, "duration_ms": d,
                              "timestamp": f"2026-11-11T10:00:00.{t:03d}Z", "attributes": {"db.system.name": "postgresql", "db.query.text": text}})
                t += 1
        (run_dir / "requests.jsonl").write_text("".join(json.dumps(r) + "\n" for r in requests))
        spans_dir.mkdir(parents=True, exist_ok=True)
        (spans_dir / f"{experiment_id}.jsonl").write_text("".join(json.dumps(s) + "\n" for s in spans))
        return {"experiment_id": experiment_id, "measured": {"median_ms": requests[10]["latency_ms"]}}
    return execute


class ClosedLoopTests(unittest.TestCase):
    def test_injected_nplus1_is_found_fixed_and_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            base_id = "main-test"
            (data / "raw" / "campaigns" / base_id).mkdir(parents=True)
            (data / "raw" / "campaigns" / base_id / "manifest.json").write_text(json.dumps({"config": validate_config(json.loads(json.dumps(BASE)))}))
            features, runs, labels = [], [], []
            for i, value in enumerate([5.0, 5.1, 4.9, 5.05, 4.95]):
                eid = f"h{i}"
                features.append({"experiment_id": eid, "server_latency_p50_ms": value, "db_queries_per_request_mean": 1.0, "repeated_query_max_median": 1.0,
                                 "db_time_p50_ms": 0.5, "db_time_share_p50": 0.1, "response_bytes_median": 1300.0, "app_time_p50_ms": 4.5})
                runs.append({"experiment_id": eid, "route": "/api/users", "span_coverage": 1.0, "excluded_reasons": []})
                labels.append({"experiment_id": eid, "variant": "healthy"})
            (data / "features" / base_id).mkdir(parents=True)
            for name, rows in (("features.jsonl", features), ("runs.jsonl", runs)):
                (data / "features" / base_id / name).write_text("".join(json.dumps(r) + "\n" for r in rows))
            (data / "labels").mkdir()
            (data / "labels" / f"{base_id}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in labels))

            lab, counter = LoopLab(), []
            report = closed_loop("users.list", "nplus1:20", base_id, 3, lab, data_dir=data,
                                 execute=fake_execute(lab, data / "raw" / "spans" / "runs", counter), log=lambda _: None)
            self.assertTrue(report["detection"]["anomalous"])
            self.assertEqual(report["diagnosis"]["class"], "N_PLUS_ONE")
            self.assertEqual(report["recommendation"]["variant"], "nplus1-fixed")
            self.assertEqual(report["verification"]["fix_variant"], "nplus1-fixed:20")  # same parameters as the injected variant
            self.assertTrue(report["verification"]["improved"])
            self.assertEqual(report["verification"]["after_verdicts"], ["normal"] * 3)
            self.assertTrue(report["outcome"].startswith("improvement verified"))
            self.assertEqual(len(counter), 6)  # 3 runs before, 3 after
            self.assertTrue((data / "results" / "loops" / report["loop_id"] / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
