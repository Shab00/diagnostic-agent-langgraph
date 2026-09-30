from fastapi import APIRouter, HTTPException

import store
from models import RankedReport

router = APIRouter()


@router.get("/report/{report_id}")
def get_report(report_id: str) -> RankedReport:
    report = store.reports.get(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.get("/fault/{fault_id}/report")
def get_report_for_fault(fault_id: str) -> RankedReport:
    if fault_id not in store.faults:
        raise HTTPException(status_code=404, detail="Fault not found")

    for report in store.reports.values():
        if report["fault_id"] == fault_id:
            return report

    raise HTTPException(status_code=404, detail="Report not found for this fault")
