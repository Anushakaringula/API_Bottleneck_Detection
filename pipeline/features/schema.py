"""Feature definitions: name -> (telemetry level, meaning). The only allowlist of predictors.

Levels are cumulative (RQ3): L1 ⊂ L2 ⊂ L3 ⊂ L4.
  L1  client only: what the workload runner sees
  L2  + server request timing (the API's experiment span)
  L3  + process / runtime signals (CPU, heap, event loop, GC)
  L4  + tracing of database and cache calls
All values are per run (measured requests only); "per request" values are aggregated over
the run's requests. No feature names or values may carry operation, route, variant or IDs.
"""

SCHEMA_VERSION = "1.0.0"

FEATURES = {
    # L1: client
    "client_latency_p50_ms": (1, "median client latency"),
    "client_latency_p95_ms": (1, "95th percentile client latency"),
    "client_latency_p99_ms": (1, "99th percentile client latency"),
    "client_latency_mean_ms": (1, "mean client latency"),
    "client_latency_from_intended_p50_ms": (1, "median latency from the intended send time (includes client-side queueing)"),
    "response_bytes_median": (1, "median response body size seen by the client"),
    "error_rate": (1, "share of measured requests that failed"),
    "achieved_rate_rps": (1, "requests per second actually sent"),
    # L2: server timing
    "server_latency_p50_ms": (2, "median server-side latency (experiment span)"),
    "server_latency_p95_ms": (2, "95th percentile server-side latency"),
    "server_latency_p99_ms": (2, "99th percentile server-side latency"),
    "client_server_gap_p50_ms": (2, "median client minus server latency per request (network, client, queueing)"),
    # L3: process and runtime
    "cpu_ms_per_request_mean": (3, "mean process CPU time (user + system) during a request; process-level, coarse"),
    "heap_delta_mb_median": (3, "median process heap change during a request; process-level, coarse"),
    "event_loop_delay_p99_ms": (3, "median over the run's runtime windows of the event-loop delay p99"),
    "event_loop_utilization_mean": (3, "mean event-loop utilization over the run's runtime windows"),
    "gc_ms_per_second": (3, "garbage-collection pause time per second"),
    "rss_mb_mean": (3, "mean resident memory"),
    "in_flight_max": (3, "most requests in flight in any runtime window"),
    # L4: database and cache tracing
    "db_queries_per_request_mean": (4, "mean number of database queries per request"),
    "db_queries_per_request_max": (4, "largest number of database queries in one request"),
    "db_time_p50_ms": (4, "median wall-clock time per request spent waiting on database queries (union of query spans)"),
    "db_time_share_p50": (4, "median share of server latency spent in database queries"),
    "repeated_query_max_median": (4, "median over requests of the largest count of one identical query text in a request (N+1 signature)"),
    "distinct_queries_per_request_mean": (4, "mean number of distinct query texts per request"),
    "cache_calls_per_request_mean": (4, "mean number of cache (Redis) calls per request"),
    "cache_writes_per_request_mean": (4, "mean number of cache writes (SET) per request"),
    "cache_hit_ratio": (4, "share of requests with a cache read that needed no database query; empty if no cache reads"),
    "app_time_p50_ms": (4, "median server latency minus the time covered by database and cache calls (time in application code)"),
    "app_time_share_p50": (4, "median share of server latency spent outside database and cache calls"),
}

LEVELS = (1, 2, 3, 4)


def features_at_level(level: int) -> list[str]:
    """Cumulative feature set for a telemetry level (L1 ⊂ L2 ⊂ L3 ⊂ L4)."""
    return [name for name, (lvl, _) in FEATURES.items() if lvl <= level]
