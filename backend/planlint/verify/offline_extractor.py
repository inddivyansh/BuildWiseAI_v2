"""Deterministic constraint extraction for PLANLINT_OFFLINE_SAMPLE mode.

Regex-parses simple numeric requirements ('… 32 inches (815 mm) minimum')
so the full pipeline — including the golden end-to-end test and the offline
demo — runs with zero API calls. Never used in real deployments."""

from __future__ import annotations

import re

from planlint.models import AssetType, Constraint, Operator, Parameter

_REQ = re.compile(
    r"(?P<value>\d+(?:[.½]\d*)?)\s*inch(?:es)?[^.]*?(?P<kind>minimum|maximum)",
    re.IGNORECASE,
)
_HALF = re.compile(r"1/2\s*inch[^.]*?(?P<kind>minimum|maximum)", re.IGNORECASE)


def _applies_to(text: str) -> AssetType:
    lowered = text.lower()
    if "door" in lowered or "gate" in lowered:
        return AssetType.DOOR
    if "window" in lowered:
        return AssetType.WINDOW
    if "route" in lowered or "corridor" in lowered or "walking surface" in lowered:
        return AssetType.CORRIDOR
    if "ramp" in lowered:
        return AssetType.RAMP
    if "stair" in lowered:
        return AssetType.STAIR
    return AssetType.OTHER


def _parameter(text: str) -> Parameter:
    lowered = text.lower()
    if "threshold" in lowered:
        return Parameter.THRESHOLD_HEIGHT
    if "riser" in lowered:
        return Parameter.RISER_HEIGHT
    if "tread" in lowered:
        return Parameter.TREAD_DEPTH
    return Parameter.CLEAR_WIDTH


def offline_extract(clause: dict) -> list[Constraint]:
    text = clause["text"]
    constraints: list[Constraint] = []
    m = _HALF.search(text)
    value: float | None = None
    kind: str | None = None
    if m:
        value, kind = 0.5, m.group("kind")
    else:
        m = _REQ.search(text)
        if m:
            value, kind = float(m.group("value")), m.group("kind")
    if value is not None and kind is not None:
        constraints.append(
            Constraint(
                regulation_id=clause["id"],
                applies_to=_applies_to(text),
                parameter=_parameter(text),
                operator=Operator.MIN if kind.lower() == "minimum" else Operator.MAX,
                value=value,
                unit="in",
                extraction_confidence=1.0,
                summary=f"{_parameter(text).value} {kind.lower()} of {value:g} inches",
            )
        )
    elif "shall" in text.lower():
        constraints.append(
            Constraint(
                regulation_id=clause["id"],
                applies_to=_applies_to(text),
                parameter=None,
                operator=Operator.QUALITATIVE,
                extraction_confidence=0.8,
                summary=text.splitlines()[0][:100],
            )
        )
    return constraints
