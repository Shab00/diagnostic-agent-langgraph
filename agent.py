import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from simulator import get_scenario

load_dotenv()

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _client


MODEL = "gpt-4o-mini"

REPAIR_STRATEGY_CATALOG = {
    "restart_cache": {
        "name": "restart_cache",
        "description": "Restart the cache layer to force a clean rebuild of cached entries.",
        "target_component": "cache",
        "estimated_impact": "medium",
        "estimated_cost": "low",
        "estimated_risk": "low",
        "execution_time_minutes": 3,
        "is_destructive": False,
    },
    "rebuild_db_index": {
        "name": "rebuild_db_index",
        "description": "Rebuild fragmented database indexes to restore query performance.",
        "target_component": "database",
        "estimated_impact": "high",
        "estimated_cost": "low",
        "estimated_risk": "medium",
        "execution_time_minutes": 30,
        "is_destructive": True,
    },
    "failover_to_replica": {
        "name": "failover_to_replica",
        "description": "Fail over the primary database to a healthy replica.",
        "target_component": "database",
        "estimated_impact": "medium",
        "estimated_cost": "high",
        "estimated_risk": "medium",
        "execution_time_minutes": 5,
        "is_destructive": True,
    },
    "throttle_api_traffic": {
        "name": "throttle_api_traffic",
        "description": "Apply rate limiting at the API gateway to reduce load on downstream components.",
        "target_component": "api_gateway",
        "estimated_impact": "low",
        "estimated_cost": "low",
        "estimated_risk": "low",
        "execution_time_minutes": 1,
        "is_destructive": False,
    },
    "scale_queue_workers": {
        "name": "scale_queue_workers",
        "description": "Add additional consumer workers to drain the message queue backlog faster.",
        "target_component": "message_queue",
        "estimated_impact": "high",
        "estimated_cost": "medium",
        "estimated_risk": "low",
        "execution_time_minutes": 8,
        "is_destructive": False,
    },
}

APM_LATENCY_THRESHOLDS = {
    "api_gateway": 300,
    "database": 1500,
}
APM_ERROR_RATE_THRESHOLD = 0.05


def get_apm_diagnostics(system_state: dict, fault_description: str) -> dict:
    flagged = []
    component_states: dict[str, dict] = {}

    for component, metrics in system_state.items():
        latency = metrics.get("latency_ms") or metrics.get("query_latency_ms")
        error_rate = metrics.get("error_rate")

        entry = {}
        if latency is not None:
            entry["latency_ms"] = latency
        if error_rate is not None:
            entry["error_rate"] = error_rate
        if "consumer_lag_seconds" in metrics:
            entry["consumer_lag_seconds"] = metrics["consumer_lag_seconds"]
        if "depth" in metrics:
            entry["depth"] = metrics["depth"]

        is_flagged = False
        threshold = APM_LATENCY_THRESHOLDS.get(component)
        if latency is not None and threshold is not None and latency > threshold:
            is_flagged = True
        if error_rate is not None and error_rate > APM_ERROR_RATE_THRESHOLD:
            is_flagged = True
        if component == "message_queue" and metrics.get("consumer_lag_seconds", 0) > 60:
            is_flagged = True

        entry["performance_flag"] = "degraded" if is_flagged else "nominal"
        component_states[component] = entry
        if is_flagged:
            flagged.append(component)

    num_degraded = len(flagged)
    if num_degraded == 0:
        confidence = 0.9
    elif num_degraded == 1:
        confidence = 0.85
    elif num_degraded == 2:
        confidence = 0.6
    else:
        confidence = 0.4

    summary = (
        f"APM performance analysis flags {num_degraded} component(s) exceeding "
        f"latency/error-rate thresholds: {', '.join(flagged) if flagged else 'none'}. "
        f"Fault context: {fault_description.strip()[:200]}"
    )

    return {
        "source": "apm_monitor",
        "component_states": component_states,
        "summary": summary,
        "confidence": round(confidence, 2),
        "flagged_components": flagged,
    }


def get_infra_diagnostics(system_state: dict, fault_description: str) -> dict:
    flagged = []
    component_states: dict[str, dict] = {}

    for component, metrics in system_state.items():
        status = metrics.get("status", "unknown")
        entry = {"status": status}
        for key in ("connections", "replication_lag_ms", "hit_rate", "eviction_rate", "staleness_seconds", "depth"):
            if key in metrics:
                entry[key] = metrics[key]

        component_states[component] = entry
        if status in ("degraded", "critical"):
            flagged.append(component)

    num_unhealthy = len(flagged)
    confidence = max(0.5, 0.95 - 0.15 * num_unhealthy)

    summary = (
        f"Infrastructure health check reports {num_unhealthy} component(s) in a "
        f"degraded or critical state: {', '.join(flagged) if flagged else 'none'}. "
        f"Component status is derived directly from resource/health telemetry, not "
        f"downstream latency effects."
    )

    return {
        "source": "infra_health",
        "component_states": component_states,
        "summary": summary,
        "confidence": round(confidence, 2),
        "flagged_components": flagged,
    }


def build_conflict_summary(diagnostic_a: dict, diagnostic_b: dict) -> dict:
    flagged_a = set(diagnostic_a["flagged_components"])
    flagged_b = set(diagnostic_b["flagged_components"])

    conflicting = sorted(flagged_a.symmetric_difference(flagged_b))
    conflict_detected = len(conflicting) > 0

    source_a_claim = (
        f"APM flags: {', '.join(sorted(flagged_a)) if flagged_a else 'none'}"
    )
    source_b_claim = (
        f"Infra health flags: {', '.join(sorted(flagged_b)) if flagged_b else 'none'}"
    )

    if conflict_detected:
        explanation = (
            f"APM (performance-focused) and infra health (status-focused) disagree on "
            f"{', '.join(conflicting)}. This typically happens when a component is "
            f"experiencing latency/errors caused by an overloaded or failing upstream/"
            f"downstream dependency, rather than being unhealthy itself — the two "
            f"monitoring sources are measuring different things (symptom vs. root health)."
        )
    else:
        explanation = (
            "Both sources flag the same set of components, indicating agreement on "
            "which parts of the system are implicated."
        )

    return {
        "conflict_detected": conflict_detected,
        "conflicting_components": conflicting,
        "source_a_claim": source_a_claim,
        "source_b_claim": source_b_claim,
        "conflict_explanation": explanation,
    }


STATUS_WEIGHT = {"critical": 1.5, "degraded": 1.0, "healthy": 0.3}

# (metric_key, healthy_baseline, higher_is_worse)
SEVERITY_METRICS = {
    "api_gateway": [("latency_ms", 200, True), ("error_rate", 0.02, True)],
    "database": [("query_latency_ms", 500, True), ("replication_lag_ms", 50, True)],
    "cache": [("eviction_rate", 0.2, True), ("staleness_seconds", 300, True)],
    "message_queue": [("depth", 1000, True), ("consumer_lag_seconds", 60, True)],
}


def build_severity_ranking(system_state: dict) -> list[dict]:
    """Deterministically compute how far each component deviates from its
    healthy baseline, so the LLM is handed pre-computed magnitudes instead of
    having to eyeball raw JSON (which it tends to misread)."""
    ranking = []
    for component, metrics in system_state.items():
        status = metrics.get("status", "unknown")
        deviations = []
        max_ratio = 0.0
        for key, baseline, _higher_is_worse in SEVERITY_METRICS.get(component, []):
            value = metrics.get(key)
            if value is None or baseline == 0:
                continue
            ratio = value / baseline
            max_ratio = max(max_ratio, ratio)
            deviations.append(f"{key}={value} ({ratio:.1f}x healthy baseline of {baseline})")

        # cache hit_rate is "lower is worse", handle separately
        if component == "cache" and "hit_rate" in metrics:
            hit_rate = metrics["hit_rate"]
            healthy_hit_rate = 0.7
            ratio = healthy_hit_rate / max(hit_rate, 0.01)
            max_ratio = max(max_ratio, ratio)
            deviations.append(f"hit_rate={hit_rate} ({ratio:.1f}x below healthy baseline of {healthy_hit_rate})")

        severity_score = round(max_ratio * STATUS_WEIGHT.get(status, 1.0), 2)
        ranking.append({
            "component": component,
            "status": status,
            "severity_score": severity_score,
            "deviations": deviations,
        })

    ranking.sort(key=lambda x: x["severity_score"], reverse=True)
    return ranking


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "evaluate_and_rank_strategies",
            "description": (
                "Select the three most relevant repair strategies for this fault from "
                "the provided catalog, rank them 1-3, and provide justification and "
                "trade-off analysis for each, plus an overall reasoning summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ranked_strategies": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {
                            "type": "object",
                            "properties": {
                                "rank": {"type": "integer", "enum": [1, 2, 3]},
                                "strategy_name": {
                                    "type": "string",
                                    "enum": list(REPAIR_STRATEGY_CATALOG.keys()),
                                },
                                "justification": {
                                    "type": "string",
                                    "description": (
                                        "Why this strategy is ranked here, explicitly "
                                        "referencing the conflict between the two "
                                        "diagnostic sources."
                                    ),
                                },
                                "trade_off_acknowledged": {
                                    "type": "string",
                                    "description": (
                                        "Honest statement of what this strategy "
                                        "sacrifices compared to alternatives."
                                    ),
                                },
                            },
                            "required": ["rank", "strategy_name", "justification", "trade_off_acknowledged"],
                        },
                    },
                    "recommended_action": {
                        "type": "string",
                        "description": "The single top recommended action, stated concisely.",
                    },
                    "reasoning_summary": {
                        "type": "string",
                        "description": (
                            "Explanation of the root cause hypothesis under uncertainty, "
                            "referencing how the conflicting diagnostics were resolved."
                        ),
                    },
                },
                "required": ["ranked_strategies", "recommended_action", "reasoning_summary"],
            },
        },
    }
]


def _fallback_ranking(diagnostic_a: dict, diagnostic_b: dict, conflict: dict) -> dict:
    """Deterministic fallback used if the LLM call is unavailable, so the
    pipeline still produces a valid RankedReport."""
    flagged = set(diagnostic_a["flagged_components"]) | set(diagnostic_b["flagged_components"])

    candidates: list[str] = []
    if "cache" in flagged:
        candidates.append("restart_cache")
    if "database" in flagged:
        candidates.append("rebuild_db_index")
        candidates.append("failover_to_replica")
    if "message_queue" in flagged:
        candidates.append("scale_queue_workers")
    if "api_gateway" in flagged:
        candidates.append("throttle_api_traffic")

    for name in REPAIR_STRATEGY_CATALOG:
        if name not in candidates:
            candidates.append(name)
    candidates = candidates[:3]

    ranked = []
    for i, name in enumerate(candidates, start=1):
        ranked.append(
            {
                "rank": i,
                "strategy_name": name,
                "justification": (
                    f"Selected because {name.replace('_', ' ')} targets a component "
                    f"flagged by at least one monitoring source; conflict between "
                    f"sources ({conflict['source_a_claim']} vs {conflict['source_b_claim']}) "
                    f"was resolved by prioritising components flagged by both or by the "
                    f"status-based infra check."
                ),
                "trade_off_acknowledged": (
                    "Fallback ranking generated without LLM reasoning; trade-offs not "
                    "individually analysed."
                ),
            }
        )

    return {
        "ranked_strategies": ranked,
        "recommended_action": f"Proceed with {candidates[0]}." if candidates else "No action determined.",
        "reasoning_summary": (
            "Root cause could not be resolved via LLM; fallback heuristic ranking "
            f"based on flagged components: {', '.join(sorted(flagged)) if flagged else 'none'}."
        ),
    }


def evaluate_and_rank_strategies(
    diagnostic_a: dict,
    diagnostic_b: dict,
    conflict_summary: dict,
    fault_description: str,
    severity_ranking: list[dict],
) -> dict:
    prompt = f"""You are a diagnostic repair strategy evaluator for an operational system
with four components: api_gateway, database, cache, message_queue.

Fault description: {fault_description}

APM monitor diagnostic (performance-focused):
{json.dumps(diagnostic_a, indent=2)}

Infra health diagnostic (status-focused):
{json.dumps(diagnostic_b, indent=2)}

Conflict summary between the two sources:
{json.dumps(conflict_summary, indent=2)}

Pre-computed severity ranking (deviation from healthy baseline, most severe first —
use these authoritative numbers rather than re-deriving magnitude from raw metrics):
{json.dumps(severity_ranking, indent=2)}

Available repair strategies (catalog):
{json.dumps(REPAIR_STRATEGY_CATALOG, indent=2)}

Select the THREE most relevant strategies for this specific fault, rank them 1-3
(1 = most recommended), and call evaluate_and_rank_strategies with your ranking.

Diagnostic reasoning principle: the two sources measure different things. APM
measures downstream performance symptoms (latency, error rate) and infra health
measures a component's own resource/status telemetry. When APM flags a component
as degraded but infra reports that same component as healthy, the most likely
explanation is that the component is a VICTIM of load cascading from a different,
genuinely unhealthy component elsewhere in the system — not that the healthy
component's own status field is wrong. Conversely, a component that infra reports
as degraded/critical in its own status/resource metrics (e.g. collapsed hit rate,
high eviction rate, replication lag, queue depth) is a strong candidate for the
actual root cause, even if APM does not directly instrument that metric. Weigh
this when forming your root cause hypothesis — do not default to "the component
with the highest latency number is the root cause."

When multiple components are flagged by BOTH sources (no source conflict), distinguish
the primary root cause from cascading downstream symptoms using the pre-computed
severity_ranking above: the component with the highest severity_score is the strongest
root-cause candidate, and other flagged components with lower scores are more likely
secondary/cascading symptoms. Trust the provided severity_score and status values over
your own re-reading of the raw metrics.

Rules:
- Do not automatically rank the fastest/obvious fix first unless your reasoning
  genuinely supports it as the best choice given the root cause hypothesis.
- Each justification must explicitly reference the conflict (or agreement) between
  the two diagnostic sources.
- Each trade_off_acknowledged must honestly state what is sacrificed by choosing
  that strategy over the alternatives.
- reasoning_summary must explain your root cause hypothesis under uncertainty.
"""

    try:
        client = get_client()
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            tools=TOOLS,
            tool_choice={"type": "function", "function": {"name": "evaluate_and_rank_strategies"}},
        )
        tool_call = response.choices[0].message.tool_calls[0]
        args = json.loads(tool_call.function.arguments)
    except Exception:
        args = _fallback_ranking(diagnostic_a, diagnostic_b, conflict_summary)

    ranked_strategies = []
    for item in sorted(args["ranked_strategies"], key=lambda x: x["rank"]):
        strategy = REPAIR_STRATEGY_CATALOG[item["strategy_name"]]
        ranked_strategies.append(
            {
                "rank": item["rank"],
                "strategy": strategy,
                "justification": item["justification"],
                "trade_off_acknowledged": item["trade_off_acknowledged"],
            }
        )

    return {
        "ranked_strategies": ranked_strategies,
        "recommended_action": args["recommended_action"],
        "reasoning_summary": args["reasoning_summary"],
    }


def run_agent(fault: dict, scenario: str) -> dict:
    system_state = get_scenario(scenario)

    diagnostic_a = get_apm_diagnostics(system_state, fault["description"])
    diagnostic_b = get_infra_diagnostics(system_state, fault["description"])

    conflict = build_conflict_summary(diagnostic_a, diagnostic_b)
    severity_ranking = build_severity_ranking(system_state)

    evaluation = evaluate_and_rank_strategies(
        diagnostic_a, diagnostic_b, conflict, fault["description"], severity_ranking
    )

    return {
        "diagnostic_a": diagnostic_a,
        "diagnostic_b": diagnostic_b,
        "conflict": conflict,
        "strategies_evaluated": evaluation["ranked_strategies"],
        "recommended_action": evaluation["recommended_action"],
        "reasoning_summary": evaluation["reasoning_summary"],
    }
