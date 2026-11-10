import datetime as dt
import http.server
import io
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from experiment_runner.runner import (
    execute_experiment,
    latency_summary,
    resolve_configuration,
    span_correlation,
)


class WorkloadHandler(http.server.BaseHTTPRequestHandler):
    active = 0
    max_active = 0
    seen_headers = []
    lock = threading.Lock()

    def do_GET(self):
        self._respond()

    def do_POST(self):
        self._respond()

    def _respond(self):
        with self.lock:
            type(self).seen_headers.append({key.lower(): value for key, value in self.headers.items()})
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
        try:
            if self.path == "/slow":
                time.sleep(0.25)
            status = 503 if self.path == "/error" else 200
            body = b"error" if status != 200 else b"ok"
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            with self.lock:
                type(self).active -= 1

    def log_message(self, *_args):
        pass


class QuietThreadingHTTPServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], BrokenPipeError):
            return
        super().handle_error(request, client_address)

    def log_message(self, *_args):
        pass


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WorkloadHandler.active = 0
        WorkloadHandler.max_active = 0
        cls.server = QuietThreadingHTTPServer(("127.0.0.1", 0), WorkloadHandler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def make_config(self, **overrides):
        values = {
            "target_url": self.base_url + "/ok",
            "scenario": "custom",
            "scenario_label": "test-ground-truth",
            "request_count": 2,
            "duration_seconds": None,
            "concurrency": 2,
            "warmup_requests": 0,
            "request_rate_per_second": None,
            "request_interval_seconds": None,
            "timeout_seconds": 2,
            "repeats": 1,
            "span_wait_seconds": 0,
        }
        values.update(overrides)
        return resolve_configuration(values)

    def run_config(self, config):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        summary = execute_experiment(
            config,
            output_root=root / "experiments",
            span_file=root / "otel-spans.jsonl",
            repeat_group_id="test-repeat-group",
            repeat_index=1,
        )
        run_dir = root / "experiments" / summary["experiment_id"]
        return summary, run_dir

    def test_percentile_calculation(self):
        result = latency_summary([1.0, 2.0, 3.0, 4.0])
        self.assertEqual(result["average_ms"], 2.5)
        self.assertEqual(result["median_ms"], 2.5)
        self.assertAlmostEqual(result["p95_ms"], 3.85)
        self.assertAlmostEqual(result["p99_ms"], 3.97)
        self.assertIsNone(latency_summary([])["p95_ms"])

    def test_warmup_is_excluded_from_measured_statistics_and_correlated(self):
        config = self.make_config(request_count=2, warmup_requests=1, concurrency=1)
        summary, run_dir = self.run_config(config)
        rows = [json.loads(line) for line in (run_dir / "requests.jsonl").read_text().splitlines()]
        warmup = [row for row in rows if row["phase"] == "warmup"]
        measured = [row for row in rows if row["phase"] == "measured"]
        self.assertEqual(len(warmup), 1)
        self.assertEqual(len(measured), 2)
        self.assertEqual(summary["warmup"]["total_requests"], 1)
        self.assertEqual(summary["measured"]["total_requests"], 2)
        self.assertEqual(summary["measured"]["average_ms"], sum(row["latency_ms"] for row in measured) / 2)
        self.assertTrue(all("scenario" not in row for row in rows))
        self.assertEqual(summary["telemetry_correlation"]["expected_spans"], 3)
        self.assertEqual(summary["telemetry_correlation"]["missing_spans"], 3)
        saved_config = json.loads((run_dir / "configuration.json").read_text())
        self.assertEqual(saved_config["experiment_id"], summary["experiment_id"])
        self.assertTrue(saved_config["environment"]["python_version"])
        self.assertFalse(saved_config["scenario_is_model_feature"])
        self.assertLessEqual(saved_config["ended_at"], saved_config["artifacts_finalized_at"])

    def test_duration_mode_launches_within_the_configured_window(self):
        config = self.make_config(request_count=None, duration_seconds=0.16, request_rate_per_second=20)
        summary, _ = self.run_config(config)
        self.assertGreaterEqual(summary["measured"]["total_requests"], 2)
        self.assertEqual(summary["measured"]["stop_reason"], "duration_elapsed")
        self.assertGreaterEqual(summary["measured"]["phase_elapsed_seconds"], 0.15)

    def test_concurrency_is_bounded_and_pacing_is_applied(self):
        WorkloadHandler.max_active = 0
        config = self.make_config(target_url=self.base_url + "/slow", request_count=8, concurrency=3)
        summary, run_dir = self.run_config(config)
        self.assertLessEqual(summary["measured"]["maximum_observed_in_flight"], 3)
        self.assertLessEqual(WorkloadHandler.max_active, 3)

        paced = self.make_config(request_count=4, concurrency=4, request_rate_per_second=5)
        _, paced_dir = self.run_config(paced)
        rows = [json.loads(line) for line in (paced_dir / "requests.jsonl").read_text().splitlines()]
        starts = [dt.datetime.fromisoformat(row["started_at"].replace("Z", "+00:00")).timestamp() for row in rows]
        gaps = [right - left for left, right in zip(starts, starts[1:])]
        self.assertTrue(all(gap >= 0.15 for gap in gaps), gaps)

    def test_http_errors_and_connection_failures_are_recorded(self):
        http_config = self.make_config(target_url=self.base_url + "/error", request_count=1)
        http_summary, http_dir = self.run_config(http_config)
        http_row = json.loads((http_dir / "requests.jsonl").read_text().strip())
        self.assertEqual(http_row["status_code"], 503)
        self.assertEqual(http_row["outcome"], "http_error")
        self.assertEqual(http_summary["measured"]["failed_requests"], 1)

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        unused_port = probe.getsockname()[1]
        probe.close()
        failed_config = self.make_config(target_url=f"http://127.0.0.1:{unused_port}/", request_count=1)
        failed_summary, failed_dir = self.run_config(failed_config)
        failed_row = json.loads((failed_dir / "requests.jsonl").read_text().strip())
        self.assertEqual(failed_row["outcome"], "connection_error")
        self.assertEqual(failed_summary["measured"]["failed_requests"], 1)
        self.assertEqual(failed_summary["telemetry_correlation"]["missing_spans"], 1)

    def test_timeout_is_recorded_as_a_connection_failure(self):
        config = self.make_config(target_url=self.base_url + "/slow", request_count=1, timeout_seconds=0.1)
        summary, run_dir = self.run_config(config)
        row = json.loads((run_dir / "requests.jsonl").read_text().strip())
        self.assertEqual(row["outcome"], "connection_error")
        self.assertEqual(summary["measured"]["failed_requests"], 1)

    def test_repeats_have_distinct_ids_and_shared_group_id(self):
        from experiment_runner.runner import main

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "config.json"
            config = self.make_config(request_count=1, repeats=2)
            config_path.write_text(json.dumps(config))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = main(["--config", str(config_path), "--output-dir", str(root / "experiments")])
            self.assertEqual(result, 0)
            runs = [json.loads(line) for line in stdout.getvalue().splitlines()]
            self.assertEqual(len(runs), 2)
            self.assertNotEqual(runs[0]["experiment_id"], runs[1]["experiment_id"])
            saved = [json.loads((Path(run["summary"]).parent / "configuration.json").read_text()) for run in runs]
            self.assertEqual(saved[0]["repeat_group_id"], saved[1]["repeat_group_id"])
            self.assertEqual([item["repeat_index"] for item in saved], [1, 2])

    def test_span_correlation_reports_duplicates_and_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "spans.jsonl"
            span = {"span_name": "experiment.http.request", "span_id": "one", "attributes": {"experiment.run_id": "run-1"}}
            path.write_text("\n".join(json.dumps(row) for row in [span, span, {**span, "span_id": "two"}]) + "\n")
            result = span_correlation(path, "run-1", expected_spans=4, wait_seconds=0)
        self.assertEqual(result["observed_spans"], 3)
        self.assertEqual(result["missing_spans"], 2)
        self.assertEqual(result["duplicate_spans"], 1)
        self.assertEqual(result["correlation_status"], "partial")

    def test_requests_carry_traceparent_and_never_the_scenario_label(self):
        WorkloadHandler.seen_headers = []
        config = self.make_config(request_count=3, concurrency=1, headers={"traceparent": "00-" + "f" * 32 + "-" + "f" * 16 + "-01"})
        _, run_dir = self.run_config(config)
        rows = [json.loads(line) for line in (run_dir / "requests.jsonl").read_text().splitlines()]
        headers = WorkloadHandler.seen_headers
        self.assertEqual(len(headers), 3)
        sent = {h["traceparent"].split("-")[1]: h["traceparent"].split("-")[2] for h in headers}
        for row in rows:
            self.assertRegex(row["trace_id"], r"^[0-9a-f]{32}$")
            self.assertRegex(row["client_span_id"], r"^[0-9a-f]{16}$")
            self.assertEqual(sent[row["trace_id"]], row["client_span_id"])
        self.assertEqual(len({row["trace_id"] for row in rows}), 3, "one trace per request")
        self.assertNotIn("f" * 32, sent, "a configured traceparent header is replaced")
        for h in headers:
            self.assertNotIn("x-experiment-scenario", h)
            self.assertNotIn("test-ground-truth", json.dumps(h))
            self.assertIn("x-experiment-run-id", h)

    def test_span_correlation_matches_each_request_by_trace_id(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "spans.jsonl"
            def span(span_id, trace_id, run="run-1"):
                return {"span_name": "experiment.http.request", "span_id": span_id, "trace_id": trace_id, "attributes": {"experiment.run_id": run}}
            rows = [span("s1", "a" * 32), span("s2", "b" * 32), span("s3", "b" * 32), span("s4", "d" * 32), span("s5", "a" * 32, run="other")]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            partial = span_correlation(path, "run-1", expected_spans=3, wait_seconds=0, trace_ids=["a" * 32, "b" * 32, "c" * 32])
            path.write_text(json.dumps(span("s1", "a" * 32)) + "\n" + json.dumps(span("s2", "b" * 32)) + "\n")
            complete = span_correlation(path, "run-1", expected_spans=2, wait_seconds=0, trace_ids=["a" * 32, "b" * 32])
        level = partial["request_level"]
        self.assertEqual(level["requests_matched_to_one_span"], 1)
        self.assertEqual(level["requests_with_multiple_spans"], 1)
        self.assertEqual(level["requests_without_span"], 1)
        self.assertEqual(level["spans_without_client_request"], 1)
        self.assertEqual(partial["correlation_status"], "partial")
        self.assertEqual(complete["request_level"]["requests_matched_to_one_span"], 2)
        self.assertEqual(complete["correlation_status"], "complete")

    def test_hard_limits_are_validated(self):
        with self.assertRaises(ValueError):
            self.make_config(concurrency=21)
        with self.assertRaises(ValueError):
            self.make_config(request_rate_per_second=51)
        with self.assertRaises(ValueError):
            self.make_config(request_count=5000, warmup_requests=1)


if __name__ == "__main__":
    unittest.main()
