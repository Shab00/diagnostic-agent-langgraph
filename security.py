import re

from fastapi import HTTPException

INJECTION_PATTERNS = [
    "ignore previous instructions",
    "you are now",
    "disregard",
    "forget",
    "new instructions",
    "system prompt",
    "jailbreak",
]

VALID_COMPONENTS = ["api_gateway", "database", "cache", "message_queue", "unknown"]

_TAG_RE = re.compile(r"<[^>]*>")


def sanitise_fault_report(description: str) -> str:
    lowered = description.lower()
    for pattern in INJECTION_PATTERNS:
        if pattern in lowered:
            raise HTTPException(
                status_code=400,
                detail="Invalid fault report: potentially unsafe input detected",
            )

    stripped = _TAG_RE.sub("", description)
    return stripped[:500]


def validate_component(component: str) -> str:
    if component not in VALID_COMPONENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid component: must be one of {VALID_COMPONENTS}",
        )
    return component
