"""
LangGraph-based diagnostic repair ranking agent.
Replaces custom OpenAI loop with StateGraph workflow.
Preserves all pure Python logic (thresholds, diagnostics, conflict detection).
"""

import json
import logging
import os
from typing import Literal
from typing_extensions import TypedDict

from dotenv import load_dotenv
from openai import OpenAI
from langgraph.graph import StateGraph, START, END

from simulator import get_scenario

load_dotenv()

# ============================================================================
# STEP 2a: STATE SCHEMA
# ============================================================================

class DiagnosticAgentState(TypedDict):
    """Immutable state dict passed between graph nodes."""
    # Input
    fault_description: str
    system_state: dict[str, dict]
    
    # Pure Python diagnostics
    diagnostic_a: dict | None
    diagnostic_b: dict | None
    conflict: dict | None
    severity_ranking: list[dict] | None
    
    # LLM outputs (set by rank_repairs_* nodes)
    ranked_strategies: list[dict] | None
    recommended_action: str | None
    
    # LLM output (set by write_reasoning node only)
    reasoning_summary: str | None


# ============================================================================
# CONSTANTS (BYTE-IDENTICAL TO ORIGINAL agent.py)
# ============================================================================

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

STATUS_WEIGHT = {"critical": 1.5, "degraded": 1.0, "healthy": 0.3}
SEVERITY_METRICS = {
    "api_gateway": [("latency_ms", 200, True), ("error_rate", 0.02, True)],
    "database": [("query_latency_ms", 500, True), ("replication_lag_ms", 50, True)],
    "cache": [("eviction_rate", 0.2, True), ("staleness_seconds", 300, True)],
    "message_queue": [("depth", 1000, True), ("consumer_lag_seconds", 60, True)],
}


# ============================================================================
# PURE PYTHON FUNCTIONS (BYTE-IDENTICAL TO ORIGINAL agent.py)
# ============================================================================

def get_apm_diagnostics(system_state: dict, fault_description: str) -> dict:
    """Compute APM-based diagnostics (performance thresholds)."""
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
    """Compute infra health-based diagnostics (status telemetry)."""
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
    """Detect disagreement between APM and infra diagnostics."""
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


def build_severity_ranking(system_state: dict) -> list[dict]:
    """Deterministically compute deviation magnitude for each component."""
    ranking = []
    for component, metrics in system_state.items():
        status = metrics.get("status", "healthy")
        status_contribution = STATUS_WEIGHT.get(status, 0)

        severity_score = status_contribution
        metric_deviations = []

        if component in SEVERITY_METRICS:
            for metric_key, healthy_baseline, higher_is_worse in SEVERITY_METRICS[component]:
                if metric_key in metrics:
                    current_val = metrics[metric_key]
                    if higher_is_worse:
                        deviation = max(0, (current_val - healthy_baseline) / max(healthy_baseline, 1))
                    else:
                        deviation = max(0, (healthy_baseline - current_val) / max(healthy_baseline, 1))
                    severity_score += deviation
                    metric_deviations.append({
                        "metric": metric_key,
                        "baseline": healthy_baseline,
                        "current": current_val,
                        "deviation": round(deviation, 2),
                    })

        ranking.append({
            "component": component,
            "severity_score": round(severity_score, 2),
            "status": status,
            "metric_deviations": metric_deviations,
        })

    ranking.sort(key=lambda x: x["severity_score"], reverse=True)
    return ranking


def _fallback_ranking(diagnostic_a: dict, diagnostic_b: dict, conflict: dict) -> dict:
    """Deterministic fallback if LLM unavailable."""
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


# ============================================================================
# STEP 2a: NODE FUNCTION STUBS (IMPLEMENTATION IN STEP 2b)
# ============================================================================

def fetch_system_state(state: DiagnosticAgentState) -> DiagnosticAgentState:
    """STEP 2b: Load scenario metrics into state.
    
    Pure Python node; no external calls.
    State already contains system_state and fault_description from initial invocation.
    This node is a pass-through; it exists to clearly mark the start of the workflow.
    """
    # State already initialized with system_state and fault_description
    return state


def compute_apm_diagnostics(state: DiagnosticAgentState) -> DiagnosticAgentState:
    """STEP 2b: Compute APM diagnostics (performance thresholds).
    
    Calls get_apm_diagnostics() with system_state and fault_description.
    Sets state["diagnostic_a"].
    """
    diagnostic_a = get_apm_diagnostics(state["system_state"], state["fault_description"])
    return {"diagnostic_a": diagnostic_a}


def compute_infra_diagnostics(state: DiagnosticAgentState) -> DiagnosticAgentState:
    """STEP 2b: Compute infra diagnostics (status telemetry).
    
    Calls get_infra_diagnostics() with system_state and fault_description.
    Sets state["diagnostic_b"].
    """
    diagnostic_b = get_infra_diagnostics(state["system_state"], state["fault_description"])
    return {"diagnostic_b": diagnostic_b}


def compute_severity_ranking(state: DiagnosticAgentState) -> DiagnosticAgentState:
    """STEP 2b: Compute severity ranking (deviation magnitudes).
    
    Calls build_severity_ranking() with system_state.
    Sets state["severity_ranking"].
    """
    severity_ranking = build_severity_ranking(state["system_state"])
    return {"severity_ranking": severity_ranking}


def detect_conflict(state: DiagnosticAgentState) -> DiagnosticAgentState:
    """STEP 2b: Detect conflict between APM and infra diagnostics.
    
    Calls build_conflict_summary().
    Sets state["conflict"].
    Pure Python only; routing signal is handled by graph.add_conditional_edges().
    """
    conflict = build_conflict_summary(state["diagnostic_a"], state["diagnostic_b"])
    return {**state, "conflict": conflict}


# ============================================================================
# STEP 2c: LLM CLIENT & HELPER FUNCTIONS
# ============================================================================

def _get_llm_client() -> OpenAI:
    """Create OpenAI client pointing to local endpoint.
    
    Endpoint: http://localhost:20128/v1 (OpenAI-compatible)
    Auth: OPENAI_API_KEY environment variable
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable not set")
    
    return OpenAI(api_key=api_key, base_url="http://localhost:20128/v1")


def _parse_json_response(text: str) -> dict:
    """Robustly extract a JSON object from an LLM response.
    
    Handles: bare JSON, fenced JSON, JSON with preamble prose.
    """
    if not text:
        raise ValueError("Empty LLM response")
    
    # Strip code fences if present
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()
    
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # Fall back: first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end+1])
    
    raise ValueError(f"Could not parse JSON from response: {text[:200]}")

def rank_repairs_conflict(state: DiagnosticAgentState) -> DiagnosticAgentState:
    """STEP 2c: Rank repairs when APM and infra conflict.
    
    LLM node. Prompt emphasizes source disagreement, explains which to trust.
    Selects top 3 strategies from REPAIR_STRATEGY_CATALOG.
    
    Returns state with ranked_strategies and recommended_action set.
    On LLM error, uses _fallback_ranking().
    """
    prompt = f"""You are a diagnostic repair strategy evaluator for an operational system
with four components: api_gateway, database, cache, message_queue.

CONFLICT DETECTION CONTEXT:
Two monitoring sources report DIFFERENT flagged components. This indicates that APM 
(which measures downstream performance symptoms like latency/error rates) and infrastructure 
health monitoring (which measures a component's own resource/status telemetry) are seeing 
different things.

**Key Principle:** When sources disagree, the component flagged by INFRA HEALTH as 
degraded/critical is likely the ROOT CAUSE. The component flagged by APM but NOT infra 
is likely a SYMPTOM (victim of load from the actual root cause).

Fault description: {state.get("fault_description", "")}

APM diagnostic (performance-focused, flags latency/error symptoms):
{json.dumps(state.get("diagnostic_a", {}), indent=2)}

Infra health diagnostic (status-focused, flags resource/status problems):
{json.dumps(state.get("diagnostic_b", {}), indent=2)}

Conflict summary:
{json.dumps(state.get("conflict", {}), indent=2)}

Pre-computed severity ranking (authoritative deviation magnitudes):
{json.dumps(state.get("severity_ranking", []), indent=2)}

Available repair strategies:
{json.dumps(REPAIR_STRATEGY_CATALOG, indent=2)}

CRITICAL OUTPUT FORMAT:
Your entire response must be a single valid JSON object.
- Start response with {{
- End response with }}
- Do not write any prose before or after JSON
- Do not use markdown code fences (no ```json)
- Do not write headers, bullet points, or commentary outside JSON structure
- first character of output must be {{, last character must be }}

TASK: Select the THREE most relevant strategies to address the ROOT CAUSE (not symptoms).
Explain why you trust infra or APM more in this case, and rank strategies 1-3 (1 = most recommended).

Return a JSON object with:
{{
  "ranked_strategies": [
    {{"rank": 1, "strategy_name": "...", "justification": "...", "trade_off_acknowledged": "..."}},
    ...
  ],
  "recommended_action": "...",
  "reasoning_summary": "Root cause is X because Y. Strategy selection balances..."
}}
"""

    try:
        client = _get_llm_client()
        response = client.chat.completions.create(
            model="free_first",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        response_text = response.choices[0].message.content
        ranking_data = _parse_json_response(response_text)
    except Exception as e:
        logging.warning(f"rank_repairs_conflict LLM call failed, using fallback: {e}")
        # Fallback to deterministic ranking on any error
        ranking_data = _fallback_ranking(
            state.get("diagnostic_a", {}),
            state.get("diagnostic_b", {}),
            state.get("conflict", {})
        )

    # Ensure we have exactly the fields we need
    ranked_strategies = ranking_data.get("ranked_strategies", [])
    recommended_action = ranking_data.get("recommended_action", "No action determined.")
    
    return {
        **state,
        "ranked_strategies": ranked_strategies,
        "recommended_action": recommended_action,
    }


def rank_repairs_agreement(state: DiagnosticAgentState) -> DiagnosticAgentState:
    """STEP 2c: Rank repairs when APM and infra agree.
    
    LLM node. Prompt treats sources as aligned (no conflict).
    Selects top 3 strategies from REPAIR_STRATEGY_CATALOG.
    
    Returns state with ranked_strategies and recommended_action set.
    On LLM error, uses _fallback_ranking().
    """
    prompt = f"""You are a diagnostic repair strategy evaluator for an operational system
with four components: api_gateway, database, cache, message_queue.

AGREEMENT CONTEXT:
Both APM (performance metrics) and infrastructure health monitoring agree on which 
components are flagged. This alignment is a strong signal. Select repairs targeting 
the highest-severity flagged components.

Fault description: {state.get("fault_description", "")}

APM diagnostic (performance-focused):
{json.dumps(state.get("diagnostic_a", {}), indent=2)}

Infra health diagnostic (status-focused):
{json.dumps(state.get("diagnostic_b", {}), indent=2)}

Both sources agree on flagged components:
{json.dumps(state.get("conflict", {}), indent=2)}

Pre-computed severity ranking (authoritative deviation magnitudes):
{json.dumps(state.get("severity_ranking", []), indent=2)}

Available repair strategies:
{json.dumps(REPAIR_STRATEGY_CATALOG, indent=2)}

CRITICAL OUTPUT FORMAT:
Your entire response must be a single valid JSON object.
- Start response with {{
- End response with }}
- Do not write any prose before or after JSON
- Do not use markdown code fences (no ```json)
- Do not write headers, bullet points, or commentary outside JSON structure
- first character of output must be {{, last character must be }}

TASK: Select the THREE most relevant strategies ranked by severity and repair effectiveness.
Rank strategies 1-3 (1 = most recommended). For each, explain the trade-off (why this over alternatives).

Return a JSON object with:
{{
  "ranked_strategies": [
    {{"rank": 1, "strategy_name": "...", "justification": "...", "trade_off_acknowledged": "..."}},
    ...
  ],
  "recommended_action": "...",
  "reasoning_summary": "Root cause analysis and repair ranking rationale..."
}}
"""

    try:
        client = _get_llm_client()
        response = client.chat.completions.create(
            model="free_first",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        response_text = response.choices[0].message.content
        ranking_data = _parse_json_response(response_text)
    except Exception as e:
        logging.warning(f"rank_repairs_agreement LLM call failed, using fallback: {e}")
        # Fallback to deterministic ranking on any error
        ranking_data = _fallback_ranking(
            state.get("diagnostic_a", {}),
            state.get("diagnostic_b", {}),
            state.get("conflict", {})
        )

    # Ensure we have exactly the fields we need
    ranked_strategies = ranking_data.get("ranked_strategies", [])
    recommended_action = ranking_data.get("recommended_action", "No action determined.")
    
    return {
        **state,
        "ranked_strategies": ranked_strategies,
        "recommended_action": recommended_action,
    }


def write_reasoning(state: DiagnosticAgentState) -> DiagnosticAgentState:
    """STEP 2c: Synthesize root-cause narrative.
    
    LLM node. Outputs reasoning_summary as coherent multi-sentence story.
    Does NOT polish strategy justifications (those come from rank_repairs nodes).
    
    Task: Tie root cause hypothesis to ranked repair strategy selection.
    """
    prompt = f"""Based on the diagnostics and repair strategy selection below, 
write a coherent multi-sentence narrative explaining:
1) What is the root cause of the system fault?
2) Why did we select these three repair strategies?
3) How do they address the root cause?

Do NOT bullet-point or re-summarize each strategy's details. Write a flowing paragraph 
that tells the diagnostic story.

Fault description: {state.get("fault_description", "")}

APM diagnostic (performance metrics):
{json.dumps(state.get("diagnostic_a", {}), indent=2)}

Infra health diagnostic (resource/status telemetry):
{json.dumps(state.get("diagnostic_b", {}), indent=2)}

Conflict/agreement between sources:
{json.dumps(state.get("conflict", {}), indent=2)}

Selected repair strategies (ranked 1-3):
{json.dumps(state.get("ranked_strategies", []), indent=2)}

Recommended action:
{state.get("recommended_action", "")}

Write a 2-3 sentence narrative as the root-cause analysis summary. Be specific about 
which component is the root cause and why (reference the metrics and severity ranking).
"""

    try:
        client = _get_llm_client()
        response = client.chat.completions.create(
            model="free_first",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        reasoning_summary = response.choices[0].message.content.strip()
    except Exception as e:
        # Fallback generic summary on LLM error
        flagged = set(state.get("diagnostic_a", {}).get("flagged_components", [])) | set(state.get("diagnostic_b", {}).get("flagged_components", []))
        reasoning_summary = (
            "Root cause could not be determined via LLM; fallback narrative based on "
            f"flagged components: {', '.join(sorted(flagged)) if flagged else 'none'}. "
            f"Repair strategies selected from severity ranking and component status."
        )

    return {**state, "reasoning_summary": reasoning_summary}


def finalize_report(state: DiagnosticAgentState) -> dict:
    """STEP 2a STUB: Assemble final report.
    
    STEP 2d: Build output dict matching old agent.run_agent() shape.
    """
    # Transform ranked_strategies: LLM outputs strategy_name, expand to full object
    strategies_evaluated = []
    for item in state.get("ranked_strategies", []):
        strategy_name = item.get("strategy_name")
        if strategy_name and strategy_name in REPAIR_STRATEGY_CATALOG:
            strategies_evaluated.append({
                "rank": item.get("rank"),
                "strategy": REPAIR_STRATEGY_CATALOG[strategy_name],
                "justification": item.get("justification"),
                "trade_off_acknowledged": item.get("trade_off_acknowledged"),
            })
    
    return {
        "diagnostic_a": state.get("diagnostic_a"),
        "diagnostic_b": state.get("diagnostic_b"),
        "conflict": state.get("conflict"),
        "strategies_evaluated": strategies_evaluated,
        "recommended_action": state.get("recommended_action"),
        "reasoning_summary": state.get("reasoning_summary"),
    }


# ============================================================================
# STEP 2d: ROUTING FUNCTION FOR CONDITIONAL EDGE
# ============================================================================

def _route_on_conflict(state: DiagnosticAgentState) -> Literal["conflict", "agreement"]:
    """Route to rank_repairs variant based on conflict detection.
    
    STEP 2d: Used by graph.add_conditional_edges() after detect_conflict.
    Returns string matching node names in conditional routing map.
    """
    if state.get("conflict", {}).get("conflict_detected"):
        return "conflict"
    else:
        return "agreement"


# ============================================================================
# STEP 2d: GRAPH BUILDER
# ============================================================================

def build_graph():
    """Construct the LangGraph StateGraph.
    
    STEP 2d: Add all nodes and edges, including parallel fan-out and conditional routing.
    """
    builder = StateGraph(DiagnosticAgentState)

    # Add all 8 nodes
    builder.add_node("fetch_system_state", fetch_system_state)
    builder.add_node("compute_apm_diagnostics", compute_apm_diagnostics)
    builder.add_node("compute_infra_diagnostics", compute_infra_diagnostics)
    builder.add_node("compute_severity_ranking", compute_severity_ranking)
    builder.add_node("detect_conflict", detect_conflict)
    builder.add_node("rank_repairs_conflict", rank_repairs_conflict)
    builder.add_node("rank_repairs_agreement", rank_repairs_agreement)
    builder.add_node("write_reasoning", write_reasoning)
    builder.add_node("finalize_report", finalize_report)

    # Linear: START → fetch_system_state
    builder.add_edge(START, "fetch_system_state")

    # Parallel: fetch → {apm, infra, severity} (all three start)
    builder.add_edge("fetch_system_state", "compute_apm_diagnostics")
    builder.add_edge("fetch_system_state", "compute_infra_diagnostics")
    builder.add_edge("fetch_system_state", "compute_severity_ranking")

    # Join: all three parallel nodes → detect_conflict
    builder.add_edge("compute_apm_diagnostics", "detect_conflict")
    builder.add_edge("compute_infra_diagnostics", "detect_conflict")
    builder.add_edge("compute_severity_ranking", "detect_conflict")

    # Conditional routing: detect_conflict → rank_repairs_conflict OR rank_repairs_agreement
    # "conflict" maps to rank_repairs_conflict, "agreement" maps to rank_repairs_agreement
    builder.add_conditional_edges(
        "detect_conflict",
        _route_on_conflict,
        {
            "conflict": "rank_repairs_conflict",
            "agreement": "rank_repairs_agreement",
        }
    )

    # Convergence: both rank_repairs variants → write_reasoning
    builder.add_edge("rank_repairs_conflict", "write_reasoning")
    builder.add_edge("rank_repairs_agreement", "write_reasoning")

    # Linear: write_reasoning → finalize_report → END
    builder.add_edge("write_reasoning", "finalize_report")
    builder.add_edge("finalize_report", END)

    return builder.compile()


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

_graph = None

def get_graph():
    """Lazy-load compiled graph."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph

def run_agent(fault: dict, scenario: str) -> dict:
    """Execute diagnostic workflow on fault + scenario."""
    system_state = get_scenario(scenario)

    initial_state: DiagnosticAgentState = {
        "fault_description": fault.get("description", ""),
        "system_state": system_state,
        "diagnostic_a": None,
        "diagnostic_b": None,
        "conflict": None,
        "severity_ranking": None,
        "ranked_strategies": None,
        "recommended_action": None,
        "reasoning_summary": None,
    }

    graph = get_graph()
    result = graph.invoke(initial_state)

    # Expand strategy_name into full catalog objects for the API schema
    raw = result.get("ranked_strategies") or []
    expanded = []
    for item in raw:
        name = item.get("strategy_name")
        if name and name in REPAIR_STRATEGY_CATALOG:
            expanded.append({
                "rank": item.get("rank"),
                "strategy": REPAIR_STRATEGY_CATALOG[name],
                "justification": item.get("justification", ""),
                "trade_off_acknowledged": item.get("trade_off_acknowledged", ""),
            })

    return {
        "diagnostic_a": result.get("diagnostic_a"),
        "diagnostic_b": result.get("diagnostic_b"),
        "conflict": result.get("conflict"),
        "strategies_evaluated": expanded,
        "recommended_action": result.get("recommended_action"),
        "reasoning_summary": result.get("reasoning_summary"),
    }
