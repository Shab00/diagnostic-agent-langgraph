import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import store
from agent import run_agent
from models import AuditEntry, FaultReport, RankedReport
from security import sanitise_fault_report, validate_component
from simulator import list_scenarios

router = APIRouter()


class FaultRequest(BaseModel):
    description: str = Field(max_length=500)
    severity: Literal["low", "medium", "high", "critical"]
    component: str
    scenario: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/fault")
def submit_fault(payload: FaultRequest) -> RankedReport:
    clean_description = sanitise_fault_report(payload.description)
    clean_component = validate_component(payload.component)

    if payload.scenario not in list_scenarios():
        raise HTTPException(
            status_code=400,
            detail=f"Invalid scenario: must be one of {list_scenarios()}",
        )

    fault_id = str(uuid.uuid4())
    reported_at = _now_iso()

    fault = FaultReport(
        fault_id=fault_id,
        description=clean_description,
        severity=payload.severity,
        component=clean_component,
        reported_at=reported_at,
    )
    store.faults[fault_id] = fault.model_dump()

    store.audit_log.append(
        AuditEntry(
            event="fault_received",
            fault_id=fault_id,
            at=_now_iso(),
            detail=f"scenario={payload.scenario} severity={payload.severity} component={clean_component}",
        ).model_dump()
    )

    agent_output = run_agent(fault.model_dump(), payload.scenario)

    report_id = str(uuid.uuid4())
    report = RankedReport(
        report_id=report_id,
        fault_id=fault_id,
        generated_at=_now_iso(),
        diagnostic_a=agent_output["diagnostic_a"],
        diagnostic_b=agent_output["diagnostic_b"],
        conflict=agent_output["conflict"],
        strategies_evaluated=agent_output["strategies_evaluated"],
        recommended_action=agent_output["recommended_action"],
        reasoning_summary=agent_output["reasoning_summary"],
    )
    store.reports[report_id] = report.model_dump()

    store.audit_log.append(
        AuditEntry(
            event="report_generated",
            fault_id=fault_id,
            at=_now_iso(),
            detail=f"report_id={report_id}",
        ).model_dump()
    )

    return report
