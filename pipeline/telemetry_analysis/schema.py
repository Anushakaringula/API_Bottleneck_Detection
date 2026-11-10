"""Versioned output fields and model-predictor governance."""

SCHEMA_VERSION = "1.0.0"

# analysis_record_id is a join key, never a model predictor.
JOIN_KEY_FIELDS = {"analysis_record_id"}
PREDICTOR_FIELDS = {
    "measured_attempts", "successful_responses", "failed_requests",
    "error_rate", "average_latency_ms", "median_latency_ms",
    "p95_latency_ms", "p99_latency_ms", "minimum_latency_ms",
    "maximum_latency_ms", "attempt_throughput_rps",
    "successful_throughput_rps", "measured_phase_duration_seconds",
    "responses_with_size", "average_response_bytes", "median_response_bytes",
    "p95_response_bytes", "maximum_response_bytes", "runner_max_in_flight",
    "status_2xx_count", "status_3xx_count", "status_4xx_count",
    "status_5xx_count", "status_other_count", "requests_without_status",
}
