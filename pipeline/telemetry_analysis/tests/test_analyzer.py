import json
import tempfile
import unittest
from pathlib import Path

from telemetry_analysis.analyzer import analyze, percentile
from telemetry_analysis.schema import JOIN_KEY_FIELDS, PREDICTOR_FIELDS


class AnalyzerTests(unittest.TestCase):
    def test_percentile_uses_linear_n_minus_one_rank(self):
        self.assertEqual(percentile([1, 2, 3, 4], 0.5), 2.5)
        self.assertEqual(percentile([], .95), None)

    def test_analysis_excludes_warmup_and_separates_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exp = root / "experiments" / "run-1"
            exp.mkdir(parents=True)
            config = {
                "experiment_id": "run-1", "repeat_group_id": "repeat-1", "repeat_index": 1,
                "scenario": "cpu-intensive", "target_endpoint": "http://localhost/api/cpu",
                "http_method": "GET", "schema_version": 1, "started_at": "t0", "ended_at": "t1",
                "execution": {"measured_attempted_requests": 2, "measured_phase_elapsed_seconds": 4.0,
                              "maximum_observed_in_flight": 2},
                "workload_configuration": {"concurrency": 2}, "environment": {"python_version": "test"},
            }
            summary = {"measured": {"total_requests": 2, "phase_elapsed_seconds": 4.0},
                       "warmup": {"total_requests": 1}}
            requests = [
                {"phase": "warmup", "latency_ms": 900, "status_code": 200, "outcome": "success", "response_bytes": 999},
                {"phase": "measured", "latency_ms": 10, "status_code": 200, "outcome": "success", "response_bytes": 10},
                {"phase": "measured", "latency_ms": 30, "status_code": 503, "outcome": "http_error", "response_bytes": 20},
            ]
            (exp / "configuration.json").write_text(json.dumps(config))
            (exp / "summary.json").write_text(json.dumps(summary))
            (exp / "requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in requests))
            span_file = root / "spans.jsonl"
            span_file.write_text(json.dumps({
                "span_name": "experiment.http.request", "trace_id": "1" * 32,
                "span_id": "1" * 16, "duration_ms": 8,
                "attributes": {"experiment.run_id": "run-1", "http.response.status_code": 503,
                               "experiment.cpu_user_ms": 2.0, "experiment.heap_delta_mb": .5},
            }) + "\n")
            out = analyze(root / "experiments", span_file, root / "analysis")
            feature = json.loads((out / "run_features.jsonl").read_text())
            metadata = json.loads((out / "run_metadata.jsonl").read_text())
            observation = json.loads((out / "telemetry_observations.jsonl").read_text())
            self.assertEqual(feature["measured_attempts"], 2)
            self.assertEqual(feature["average_latency_ms"], 20)
            self.assertEqual(feature["attempt_throughput_rps"], .5)
            self.assertEqual(feature["successful_throughput_rps"], .25)
            self.assertEqual(feature["maximum_response_bytes"], 20)
            self.assertEqual(feature["status_5xx_count"], 1)
            self.assertNotIn("scenario", feature)
            self.assertNotIn("repeat_group_id", feature)
            self.assertTrue(set(feature) - JOIN_KEY_FIELDS <= PREDICTOR_FIELDS)
            self.assertEqual(metadata["analysis_record_id"], feature["analysis_record_id"])
            self.assertEqual(metadata["repeat_group_id"], "repeat-1")
            self.assertEqual(observation["failed_requests_with_status_correspondence_count_level"], 1)
            self.assertEqual(observation["resource_observations"]["scope"], "process-level deltas sampled around request span; not per-request consumption")
            self.assertEqual(observation["span_phase_coverage"], "warmup_and_measured_unseparated")

    def test_transport_failure_and_unexplained_correlation_are_not_overclaimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); exp = root / "experiments" / "run-2"; exp.mkdir(parents=True)
            config = {"experiment_id": "run-2", "execution": {"measured_attempted_requests": 1,
                       "measured_phase_elapsed_seconds": 1}, "workload_configuration": {}}
            summary = {"measured": {"total_requests": 1, "phase_elapsed_seconds": 1}, "warmup": {"total_requests": 0}}
            row = {"phase": "measured", "latency_ms": 50, "status_code": None, "outcome": "connection_error"}
            (exp / "configuration.json").write_text(json.dumps(config)); (exp / "summary.json").write_text(json.dumps(summary))
            (exp / "requests.jsonl").write_text(json.dumps(row) + "\n")
            spans = root / "spans.jsonl"; spans.write_text("")
            out = analyze(root / "experiments", spans, root / "analysis")
            obs = json.loads((out / "telemetry_observations.jsonl").read_text())
            self.assertEqual(obs["failed_connection_requests_without_span_count"], 1)
            self.assertEqual(obs["failed_connection_requests_with_possible_span_count"], 0)
            self.assertTrue(obs["unexplained_correlation_discrepancy"])
            quality = [json.loads(x) for x in (out / "data_quality.jsonl").read_text().splitlines()]
            self.assertIn("span_count_discrepancy", {x["code"] for x in quality})

    def test_invalid_jsonl_duplicate_span_and_missing_duration_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); exp = root / "experiments" / "run-3"; exp.mkdir(parents=True)
            config = {"experiment_id": "run-3", "execution": {}, "workload_configuration": {}}
            summary = {"measured": {"total_requests": 1}, "warmup": {"total_requests": 0}}
            request = {"phase": "measured", "latency_ms": 2, "status_code": 200,
                       "outcome": "success", "response_bytes": 3}
            (exp / "configuration.json").write_text(json.dumps(config))
            (exp / "summary.json").write_text(json.dumps(summary))
            (exp / "requests.jsonl").write_text(json.dumps(request) + "\nnot-json\n")
            span = {"trace_id": "a" * 32, "span_id": "b" * 16, "span_name": "experiment.http.request",
                    "duration_ms": 1, "attributes": {"experiment.run_id": "run-3", "http.response.status_code": 200}}
            spans = root / "spans.jsonl"
            spans.write_text(json.dumps(span) + "\n" + json.dumps(span) + "\n")
            out = analyze(root / "experiments", spans, root / "analysis")
            feature = json.loads((out / "run_features.jsonl").read_text())
            self.assertIsNone(feature["attempt_throughput_rps"])
            self.assertIsNone(feature["successful_throughput_rps"])
            quality = [json.loads(x) for x in (out / "data_quality.jsonl").read_text().splitlines()]
            codes = {x["code"] for x in quality}
            self.assertIn("malformed_jsonl_record", codes)
            self.assertIn("duplicate_span_id", codes)
            self.assertIn("throughput_duration_unavailable", codes)


    def test_requests_are_joined_to_spans_by_trace_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); exp = root / "experiments" / "run-4"; exp.mkdir(parents=True)
            config = {"experiment_id": "run-4", "execution": {"measured_attempted_requests": 3,
                       "measured_phase_elapsed_seconds": 1}, "workload_configuration": {}}
            summary = {"measured": {"total_requests": 3, "phase_elapsed_seconds": 1}, "warmup": {"total_requests": 0}}
            requests = [
                {"request_index": 0, "phase": "measured", "trace_id": "a" * 32, "latency_ms": 12.0, "status_code": 200, "outcome": "success", "response_bytes": 50},
                {"request_index": 1, "phase": "measured", "trace_id": "b" * 32, "latency_ms": 9.0, "status_code": 200, "outcome": "success", "response_bytes": 50},
                {"request_index": 2, "phase": "measured", "trace_id": "c" * 32, "latency_ms": 7.0, "status_code": None, "outcome": "connection_error"},
            ]
            (exp / "configuration.json").write_text(json.dumps(config)); (exp / "summary.json").write_text(json.dumps(summary))
            (exp / "requests.jsonl").write_text("".join(json.dumps(r) + "\n" for r in requests))

            def experiment_span(trace, span_id, parent, latency):
                return {"span_name": "experiment.http.request", "trace_id": trace, "span_id": span_id, "parent_span_id": parent,
                        "span_kind": 0, "duration_ms": latency,
                        "attributes": {"experiment.telemetry_schema": "2", "experiment.run_id": "run-4", "http.request.method": "GET",
                                       "url.path": "/x", "experiment.latency_ms": latency, "experiment.response_size_bytes": 50,
                                       "http.response.status_code": 200}}
            # Server spans have the client's span as parent, which is never exported.
            server_a = {"span_name": "GET", "trace_id": "a" * 32, "span_id": "1" * 16, "parent_span_id": "9" * 16, "span_kind": 1, "duration_ms": 11, "attributes": {}}
            spans = [server_a, experiment_span("a" * 32, "2" * 16, "1" * 16, 10.0), experiment_span("d" * 32, "3" * 16, None, 5.0)]
            span_file = root / "spans.jsonl"; span_file.write_text("".join(json.dumps(s) + "\n" for s in spans))
            out = analyze(root / "experiments", span_file, root / "analysis")
            records = [json.loads(x) for x in (out / "request_records.jsonl").read_text().splitlines()]
            report = json.loads((out / "request_join_summary.json").read_text())
            quality = [json.loads(x) for x in (out / "data_quality.jsonl").read_text().splitlines()]
            manifest = json.loads((out / "manifest.json").read_text())

            by_index = {r["request_index"]: r for r in records}
            self.assertEqual(by_index[0]["match_status"], "matched")
            self.assertEqual(by_index[0]["server_latency_ms"], 10.0)
            self.assertEqual(by_index[0]["client_minus_server_ms"], 2.0)
            self.assertTrue(by_index[0]["status_agrees"])
            self.assertEqual(by_index[1]["match_status"], "no_server_span")
            self.assertEqual(by_index[2]["match_status"], "no_server_span")
            run = report["runs"][0]
            self.assertEqual(run["matched_requests"], 1)
            self.assertEqual(run["server_spans_without_client_request"], 1)
            self.assertAlmostEqual(report["totals"]["exact_match_rate"], 1 / 3)
            codes = {q["code"] for q in quality}
            self.assertIn("request_level_correlation_incomplete", codes)
            self.assertNotIn("parent_span_not_in_input", codes)
            self.assertNotIn("dedicated_span_attribute_missing", codes)
            self.assertNotIn("request_records", json.dumps(manifest))
            # Request-level rows never reach the predictor file.
            self.assertNotIn("a" * 32, (out / "run_features.jsonl").read_text())

    def test_pre_traceparent_requests_are_reported_as_not_joinable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); exp = root / "experiments" / "run-5"; exp.mkdir(parents=True)
            config = {"experiment_id": "run-5", "execution": {}, "workload_configuration": {}}
            summary = {"measured": {"total_requests": 1}, "warmup": {"total_requests": 0}}
            (exp / "configuration.json").write_text(json.dumps(config)); (exp / "summary.json").write_text(json.dumps(summary))
            (exp / "requests.jsonl").write_text(json.dumps({"phase": "measured", "latency_ms": 3, "status_code": 200, "outcome": "success"}) + "\n")
            span = {"span_name": "experiment.http.request", "trace_id": "e" * 32, "span_id": "4" * 16, "duration_ms": 1,
                    "attributes": {"experiment.run_id": "run-5", "http.response.status_code": 200}}
            span_file = root / "spans.jsonl"; span_file.write_text(json.dumps(span) + "\n")
            out = analyze(root / "experiments", span_file, root / "analysis")
            record = json.loads((out / "request_records.jsonl").read_text())
            report = json.loads((out / "request_join_summary.json").read_text())
            quality = [json.loads(x) for x in (out / "data_quality.jsonl").read_text().splitlines()]
            self.assertEqual(record["match_status"], "no_client_trace_id")
            self.assertIsNone(report["totals"]["exact_match_rate"])
            self.assertIsNone(report["runs"][0]["server_spans_without_client_request"])
            self.assertNotIn("request_level_correlation_incomplete", {q["code"] for q in quality})
            # Old-format spans are still checked against the old attribute list, scenario included.
            missing = {q.get("attribute") for q in quality if q["code"] == "dedicated_span_attribute_missing"}
            self.assertIn("experiment.scenario", missing)


if __name__ == "__main__":
    unittest.main()
