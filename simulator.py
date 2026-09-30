SCENARIOS: dict[str, dict] = {
    "cache_collapse": {
        "api_gateway": {
            "latency_ms": 380, "error_rate": 0.03, "status": "degraded",
        },
        "database": {
            "query_latency_ms": 2400, "connections": 94,
            "replication_lag_ms": 12, "status": "healthy",
        },
        "cache": {
            "hit_rate": 0.08, "staleness_seconds": 7200,
            "eviction_rate": 0.94, "status": "critical",
        },
        "message_queue": {
            "depth": 820, "consumer_lag_seconds": 45, "status": "healthy",
        },
    },
    "db_degradation": {
        "api_gateway": {
            "latency_ms": 510, "error_rate": 0.06, "status": "degraded",
        },
        "database": {
            "query_latency_ms": 4100, "connections": 99,
            "replication_lag_ms": 340, "status": "critical",
        },
        "cache": {
            "hit_rate": 0.81, "staleness_seconds": 42,
            "eviction_rate": 0.12, "status": "healthy",
        },
        "message_queue": {
            "depth": 1200, "consumer_lag_seconds": 90, "status": "degraded",
        },
    },
    "queue_backup": {
        "api_gateway": {
            "latency_ms": 920, "error_rate": 0.12, "status": "critical",
        },
        "database": {
            "query_latency_ms": 1800, "connections": 76,
            "replication_lag_ms": 28, "status": "degraded",
        },
        "cache": {
            "hit_rate": 0.61, "staleness_seconds": 180,
            "eviction_rate": 0.34, "status": "healthy",
        },
        "message_queue": {
            "depth": 48000, "consumer_lag_seconds": 1800, "status": "critical",
        },
    },
}


def get_scenario(scenario: str) -> dict:
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario}")
    return SCENARIOS[scenario]


def list_scenarios() -> list[str]:
    return list(SCENARIOS.keys())
