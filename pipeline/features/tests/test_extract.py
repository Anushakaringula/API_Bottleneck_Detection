"""Feature extraction (Analyzer v2): mechanisms, telemetry levels, leakage. Synthetic telemetry."""
import json
import tempfile
import unittest
from pathlib import Path

from features.extract import covered_ms, extract, request_metrics
from features.schema import FEATURES, LEVELS, features_at_level


def span(name, trace, start_ms, duration, kind=2, **attrs):
    seconds, ms = divmod(start_ms, 1000)
    return {"span_name": name, "trace_id": trace, "span_kind": kind, "duration_ms": duration,
            "timestamp": f"2026-11-11T10:00:{int(seconds):02d}.{int(ms):03d}Z", "attributes": attrs}


def experiment(trace, server_ms, route="/api/things", start=0):
    return span("experiment.http.request", trace, start, server_ms, kind=0, **{
        "experiment.latency_ms": server_ms, "experiment.cpu_user_ms": 2.0, "experiment.cpu_system_ms": 0.0,
        "experiment.heap_delta_mb": 0.1, "http.route": route})


def query(trace, start, duration, text="SELECT 1"):
    return span("pg.query:SELECT lab", trace, start, duration, **{"db.system.name": "postgresql", "db.query.text": text})


def cache(trace, op, start, duration=0.5):
    return span(f"redis-{op}", trace, start, duration, **{"db.system.name": "redis", "db.operation.name": op})


class MechanismTests(unittest.TestCase):
    def test_nplus1_signature(self):
        trace = [experiment("t", 30)] + [query("t", 1, 1, "SELECT users")] + [query("t", 2 + i, 1, "SELECT count WHERE user_id=$1") for i in range(5)]
        m = request_metrics(trace)
        self.assertEqual((m["db_queries"], m["repeated_query_max"], m["distinct_queries"]), (6, 5, 2))

    def test_parallel_queries_are_counted_once(self):
        self.assertEqual(covered_ms([query("t", 0, 10), query("t", 0, 10)]), 10)
        self.assertEqual(covered_ms([query("t", 0, 10), query("t", 5, 10)]), 15)
        self.assertEqual(covered_ms([query("t", 0, 4), query("t", 10, 4)]), 8)
        m = request_metrics([experiment("t", 20), query("t", 0, 10), query("t", 0, 10)])
        self.assertAlmostEqual(m["db_share"], 0.5)
        self.assertAlmostEqual(m["app_ms"], 10)

    def test_cache_reads_hits_and_writes(self):
        hit = request_metrics([experiment("a", 3), cache("a", "GET", 0)])
        miss = request_metrics([experiment("b", 9), cache("b", "GET", 0), query("b", 1, 5), cache("b", "SET", 7)])
        self.assertTrue(hit["cache_read"])
        self.assertEqual((hit["db_queries"], miss["db_queries"], miss["cache_writes"]), (0, 1, 1))

    def test_levels_are_cumulative_and_complete(self):
        self.assertEqual(set(level for level, _ in FEATURES.values()), set(LEVELS))
        sets = [set(features_at_level(level)) for level in LEVELS]
        self.assertTrue(all(a < b for a, b in zip(sets, sets[1:])))
        self.assertEqual(sets[-1], set(FEATURES))
        self.assertTrue(all(name.startswith("client_") or name in ("response_bytes_median", "error_rate", "achieved_rate_rps") for name in features_at_level(1)))


class ExtractTests(unittest.TestCase):
    def test_campaign_extraction_separates_features_from_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            campaign = root / "campaigns" / "main-x"
            spans_dir = root / "spans"
            spans_dir.mkdir(parents=True)
            runs = {"run-ok": "completed", "run-excluded": "completed", "run-warmup": "discarded_warmup"}
            for i, (run_id, status) in enumerate(runs.items()):
                run_dir = campaign / "experiments" / run_id
                run_dir.mkdir(parents=True)
                (run_dir / "configuration.json").write_text(json.dumps({"execution": {"measured_phase_elapsed_seconds": 2.0},
                                                                         "workload_configuration": {"user_metadata": {"run_index": i + 1}}}))
                requests, spans = [], []
                for k in range(4):
                    trace = f"{run_id}-{k}"
                    requests.append({"phase": "measured", "trace_id": trace, "latency_ms": 12.0, "latency_from_intended_ms": 12.5,
                                     "outcome": "success", "response_bytes": 900})
                    spans += [experiment(trace, 8.0, route="/api/orders"), query(trace, 1, 3.0), cache(trace, "GET", 0)]
                requests.append({"phase": "warmup", "trace_id": "w", "latency_ms": 99, "outcome": "success"})
                (run_dir / "requests.jsonl").write_text("".join(json.dumps(r) + "\n" for r in requests))
                (spans_dir / f"{run_id}.jsonl").write_text("".join(json.dumps(s) + "\n" for s in spans))
            (campaign / "progress.jsonl").write_text("".join(json.dumps({"experiment_id": r, "status": s}) + "\n" for r, s in runs.items()))
            (campaign / "exclusions.json").write_text(json.dumps({"excluded": [{"experiment_id": "run-excluded", "reasons": ["client send-lag p99 12 ms > 10 ms"]}]}))
            runtime = root / "runtime.jsonl"
            runtime.write_text(json.dumps({"run_ids": ["run-ok"], "window_ms": 1000, "event_loop_delay_p99_ms": 11.0, "event_loop_utilization": 0.2,
                                           "gc_total_ms": 2.0, "rss_mb": 100.0, "requests_in_flight_max": 3}) + "\n")
            out = extract(campaign, spans_dir, runtime, root / "features")

            features = [json.loads(l) for l in (out / "features.jsonl").read_text().splitlines()]
            meta = {r["experiment_id"]: r for r in map(json.loads, (out / "runs.jsonl").read_text().splitlines())}
            self.assertEqual(sorted(f["experiment_id"] for f in features), ["run-excluded", "run-ok"])  # warm-up skipped
            ok = next(f for f in features if f["experiment_id"] == "run-ok")
            self.assertEqual(ok["client_latency_p50_ms"], 12.0)  # warm-up request not counted
            self.assertEqual(ok["server_latency_p50_ms"], 8.0)
            self.assertEqual(ok["client_server_gap_p50_ms"], 4.0)
            self.assertEqual(ok["achieved_rate_rps"], 2.0)
            self.assertEqual(ok["cache_hit_ratio"], 0.0)  # every read was followed by a query
            self.assertEqual(ok["event_loop_delay_p99_ms"], 11.0)
            self.assertEqual(ok["gc_ms_per_second"], 2.0)
            # leakage: predictors are numeric, allowlisted, and carry no route or IDs
            text = (out / "features.jsonl").read_text()
            self.assertNotIn("/api/orders", text)
            for f in features:
                self.assertTrue(set(f) - {"experiment_id"} <= set(FEATURES))
                self.assertTrue(all(v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)) for k, v in f.items() if k != "experiment_id"))
            self.assertEqual(meta["run-ok"]["route"], "/api/orders")
            self.assertEqual(meta["run-ok"]["span_coverage"], 1.0)
            self.assertEqual(meta["run-excluded"]["excluded_reasons"], ["client send-lag p99 12 ms > 10 ms"])
            self.assertEqual(json.loads((out / "manifest.json").read_text())["excluded_runs"], 1)


if __name__ == "__main__":
    unittest.main()
