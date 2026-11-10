import datetime as dt
import http.server
import io
import json
import math
import random
import socket
import statistics
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from experiment_runner.runner import (
    MAX_CONCURRENCY,
    MAX_REQUEST_COUNT,
    MAX_REQUEST_RATE,
    arrival_offsets,
    execute_experiment,
    latency_summary,
    resolve_configuration,
    span_correlation,
)


class WorkloadHandler(http.server.BaseHTTPRequestHandler):
    active = 0
    max_active = 0
    seen_headers = []
    seen_paths = []
    lock = threading.Lock()

    def do_GET(self):
        self._respond()

    def do_POST(self):
        self._respond()

    def _respond(self):
        with self.lock:
            type(self).seen_headers.append({key.lower(): value for key, value in self.headers.items()})
            type(self).seen_paths.append(self.path)
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
        # A client that timed out closes the socket: BrokenPipeError on Linux,
        # ConnectionAbortedError/ConnectionResetError on Windows.
        if isinstance(sys.exc_info()[1], ConnectionError):
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
            self.make_config(concurrency=MAX_CONCURRENCY + 1)
        with self.assertRaises(ValueError):
            self.make_config(request_rate_per_second=MAX_REQUEST_RATE + 1)
        with self.assertRaises(ValueError):
            self.make_config(request_count=MAX_REQUEST_COUNT, warmup_requests=1)
        with self.assertRaises(ValueError):
            # 200 rps for 300 s plus warm-up is above the request cap.
            self.make_config(load_model="open", request_count=None, duration_seconds=300, request_rate_per_second=200, warmup_requests=1)

    def test_open_loop_options_are_validated(self):
        with self.assertRaises(ValueError):
            self.make_config(arrival_process="poisson")  # needs load_model open
        with self.assertRaises(ValueError):
            self.make_config(load_model="open", request_rate_per_second=None)
        with self.assertRaises(ValueError):
            self.make_config(load_model="open", stages=[{"duration_seconds": 1, "rate_per_second": 5}])  # request_count still set
        with self.assertRaises(ValueError):
            self.make_config(load_model="open", request_count=None, stages=[{"duration_seconds": 1, "rate_per_second": 0}])
        with self.assertRaises(ValueError):
            self.make_config(request_variants=[{"query": {"limit": True}}])
        config = self.make_config(load_model="open", request_count=None,
                                  stages=[{"duration_seconds": 2, "rate_per_second": 5}, {"duration_seconds": 1, "rate_per_second_start": 5, "rate_per_second_end": 15}])
        self.assertEqual(config["duration_seconds"], 3)
        self.assertEqual(config["stages"][0], {"duration_seconds": 2.0, "rate_per_second_start": 5.0, "rate_per_second_end": 5.0})
        self.assertIsInstance(config["random_seed"], int)
        # A resolved configuration resolves to itself (saved configs can be re-run).
        self.assertEqual(resolve_configuration(json.loads(json.dumps(config))), config)

    def test_constant_timetable_and_rate_stages(self):
        rng = random.Random(1)
        self.assertEqual(arrival_offsets([{"duration_seconds": math.inf, "rate_per_second_start": 10, "rate_per_second_end": 10}], "constant", rng, count=4),
                         [0.0, 0.1, 0.2, 0.3])
        steady = arrival_offsets([{"duration_seconds": 2, "rate_per_second_start": 5, "rate_per_second_end": 5}], "constant", rng)
        self.assertEqual(len(steady), 10)
        self.assertLess(steady[-1], 2)
        # Ramp 0 -> 10 rps over 10 s: 50 expected arrivals, getting denser.
        ramp = arrival_offsets([{"duration_seconds": 10, "rate_per_second_start": 0, "rate_per_second_end": 10}], "constant", rng)
        self.assertEqual(len(ramp), 50)
        self.assertTrue(all(b > a for a, b in zip(ramp, ramp[1:])))
        gaps = [b - a for a, b in zip(ramp, ramp[1:])]
        self.assertGreater(gaps[0], gaps[-1] * 5)
        # A zero-rate stage is a pause.
        paused = arrival_offsets([{"duration_seconds": 1, "rate_per_second_start": 10, "rate_per_second_end": 10},
                                  {"duration_seconds": 1, "rate_per_second_start": 0, "rate_per_second_end": 0},
                                  {"duration_seconds": 1, "rate_per_second_start": 10, "rate_per_second_end": 10}], "constant", rng)
        self.assertEqual(len(paused), 20)
        self.assertFalse(any(1.0 <= t < 2.0 for t in paused))

    def test_poisson_timetable_is_seeded_and_has_the_configured_rate(self):
        stage = [{"duration_seconds": math.inf, "rate_per_second_start": 20, "rate_per_second_end": 20}]
        first = arrival_offsets(stage, "poisson", random.Random("seed"), count=4000)
        again = arrival_offsets(stage, "poisson", random.Random("seed"), count=4000)
        self.assertEqual(first, again)
        mean_gap = first[-1] / (len(first) - 1)
        self.assertAlmostEqual(mean_gap, 1 / 20, delta=0.05 / 20 * 3)
        gaps = [b - a for a, b in zip(first, first[1:])]
        cv = statistics.pstdev(gaps) / statistics.fmean(gaps)
        self.assertAlmostEqual(cv, 1.0, delta=0.1)  # exponential gaps

    def test_open_loop_sends_on_schedule_while_requests_are_outstanding(self):
        WorkloadHandler.max_active = 0
        # Each request takes 250 ms; at 20 rps the open loop must not wait for responses.
        config = self.make_config(target_url=self.base_url + "/slow", load_model="open", request_count=10,
                                  request_rate_per_second=20, concurrency=20)
        summary, run_dir = self.run_config(config)
        rows = [json.loads(line) for line in (run_dir / "requests.jsonl").read_text().splitlines()]
        intended = [row["intended_send_offset_ms"] for row in rows]
        self.assertEqual([round(x) for x in intended], [0, 50, 100, 150, 200, 250, 300, 350, 400, 450])
        self.assertGreater(summary["measured"]["maximum_observed_in_flight"], 3)
        self.assertGreater(WorkloadHandler.max_active, 3)
        self.assertLess(summary["measured"]["send_lag_ms"]["p99_ms"], 50)  # lenient for shared CI machines
        self.assertLess(summary["measured"]["phase_elapsed_seconds"], 1.5)  # closed loop with 1 worker: 2.5 s
        self.assertAlmostEqual(summary["measured"]["achieved_send_rate_per_second"], 20, delta=4)
        for row in rows:
            self.assertGreaterEqual(row["latency_from_intended_ms"], row["latency_ms"] - 1e-6)
        saved = json.loads((run_dir / "configuration.json").read_text())
        self.assertEqual(saved["workload_configuration"]["load_model"], "open")
        self.assertEqual(saved["execution"]["planned_measured_requests"], 10)

    def test_open_loop_saturated_workers_show_up_as_send_lag(self):
        # One worker, 250 ms requests, 20 rps: requests queue at the client and the lag is recorded.
        config = self.make_config(target_url=self.base_url + "/slow", load_model="open", request_count=4,
                                  request_rate_per_second=20, concurrency=1)
        summary, _ = self.run_config(config)
        self.assertEqual(summary["measured"]["maximum_observed_in_flight"], 1)
        self.assertGreater(summary["measured"]["send_lag_ms"]["maximum_ms"], 400)

    def test_request_variants_are_drawn_reproducibly(self):
        WorkloadHandler.seen_paths = []
        variants = [{"query": {"limit": 10}, "weight": 3}, {"query": {"limit": 100, "fields": "id"}, "weight": 1}]
        config = self.make_config(request_count=40, concurrency=1, request_variants=variants, random_seed=7)
        _, run_dir = self.run_config(config)
        rows = [json.loads(line) for line in (run_dir / "requests.jsonl").read_text().splitlines()]
        _, again_dir = self.run_config(config)
        again = [json.loads(line) for line in (again_dir / "requests.jsonl").read_text().splitlines()]
        self.assertEqual([r["variant_index"] for r in rows], [r["variant_index"] for r in again])
        self.assertEqual({r["variant_index"] for r in rows}, {0, 1})
        self.assertIn("/ok?limit=10", WorkloadHandler.seen_paths)
        self.assertIn("/ok?limit=100&fields=id", WorkloadHandler.seen_paths)

    def test_request_variants_can_replace_the_path(self):
        WorkloadHandler.seen_paths = []
        variants = [{"path": "/items/1"}, {"path": "/items/2", "query": {"full": 1}}]
        config = self.make_config(target_url=self.base_url + "/ignored?x=1", request_count=12, concurrency=1, request_variants=variants, random_seed=3)
        self.run_config(config)
        self.assertEqual(set(WorkloadHandler.seen_paths), {"/items/1", "/items/2?full=1"})
        with self.assertRaises(ValueError):
            self.make_config(request_variants=[{"path": "relative"}])
        with self.assertRaises(ValueError):
            self.make_config(request_variants=[{"path": "/a?b=1"}])

    def test_closed_loop_records_send_timing(self):
        config = self.make_config(request_count=3, concurrency=1, request_rate_per_second=10)
        summary, run_dir = self.run_config(config)
        rows = [json.loads(line) for line in (run_dir / "requests.jsonl").read_text().splitlines()]
        for row in rows:
            self.assertGreaterEqual(row["send_lag_ms"], 0)
            self.assertIsNotNone(row["sent_offset_ms"])
            self.assertIsNone(row["variant_index"])
        self.assertIsNotNone(summary["measured"]["send_lag_ms"]["p99_ms"])

    def test_calibration_evaluation(self):
        from experiment_runner.calibrate import evaluate, max_sustainable_rate

        def summary(p99, achieved, failed=0):
            return {"measured": {"send_lag_ms": {"median_ms": 1.0, "p95_ms": 2.0, "p99_ms": p99, "maximum_ms": p99},
                                 "achieved_send_rate_per_second": achieved, "failed_requests": failed, "total_requests": 100,
                                 "median_ms": 3.0, "p99_ms": 9.0, "maximum_observed_in_flight": 2}}
        ok = evaluate(summary(3.0, 49.5), 50, 5.0, 0.02)
        self.assertTrue(ok["pass"])
        self.assertFalse(evaluate(summary(8.0, 50), 50, 5.0, 0.02)["checks"]["send_lag_p99_within_limit"])
        self.assertFalse(evaluate(summary(3.0, 45), 50, 5.0, 0.02)["checks"]["achieved_rate_within_tolerance"])
        self.assertFalse(evaluate(summary(3.0, 50, failed=1), 50, 5.0, 0.02)["pass"])
        results = [{"target_rate_per_second": r, "pass": p} for r, p in [(5, True), (5, True), (20, True), (50, False), (100, True)]]
        self.assertEqual(max_sustainable_rate(results), 20)


if __name__ == "__main__":
    unittest.main()
