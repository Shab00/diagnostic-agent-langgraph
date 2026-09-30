from typing import Literal, Optional
from pydantic import BaseModel, Field


class FaultReport(BaseModel):
    fault_id: str
    description: str = Field(max_length=500)
    severity: Literal["low", "medium", "high", "critical"]
    component: Literal["api_gateway", "database", "cache", "message_queue", "unknown"]
    reported_at: str


class DiagnosticResult(BaseModel):
    source: Literal["apm_monitor", "infra_health"]
    component_states: dict[str, dict]
    summary: str
    confidence: float
    flagged_components: list[str]


class ConflictSummary(BaseModel):
    conflict_detected: bool
    conflicting_components: list[str]
    source_a_claim: str
    source_b_claim: str
    conflict_explanation: str


class RepairStrategy(BaseModel):
    name: str
    description: str
    target_component: str
    estimated_impact: Literal["low", "medium", "high"]
    estimated_cost: Literal["low", "medium", "high"]
    estimated_risk: Literal["low", "medium", "high"]
    execution_time_minutes: int
    is_destructive: bool


class RankedStrategy(BaseModel):
    rank: int
    strategy: RepairStrategy
    justification: str
    trade_off_acknowledged: str


class RankedReport(BaseModel):
    report_id: str
    fault_id: str
    generated_at: str
    diagnostic_a: DiagnosticResult
    diagnostic_b: DiagnosticResult
    conflict: ConflictSummary
    strategies_evaluated: list[RankedStrategy]
    recommended_action: str
    reasoning_summary: str


class AuditEntry(BaseModel):
    event: str
    fault_id: str
    at: str
    detail: Optional[str] = None
