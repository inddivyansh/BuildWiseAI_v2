"""Deterministic compliance checker — pure Python, the ONLY component that
decides compliance. No LLM performs arithmetic or renders judgment here."""

from __future__ import annotations

from planlint.config import settings
from planlint.models import (
    CheckResult,
    Constraint,
    Operator,
    Parameter,
    PhysicalAsset,
    VerdictType,
)

_TO_INCHES: dict[str, float] = {
    "in": 1.0,
    "inch": 1.0,
    "inches": 1.0,
    "ft": 12.0,
    "feet": 12.0,
    "mm": 1.0 / 25.4,
    "cm": 1.0 / 2.54,
    "m": 1.0 / 0.0254,
}


def to_inches(value: float, unit: str) -> float | None:
    """Convert a value in `unit` to inches; None for unknown units."""
    factor = _TO_INCHES.get(unit.strip().lower())
    if factor is None:
        return None
    return value * factor


_TO_SQUARE_METRES: dict[str, float] = {
    "m²": 1.0,
    "m2": 1.0,
    "sqm": 1.0,
    "sq m": 1.0,
    "square metres": 1.0,
    "square meters": 1.0,
    "ft²": 0.09290304,
    "ft2": 0.09290304,
    "sqft": 0.09290304,
    "sq ft": 0.09290304,
    "square feet": 0.09290304,
}


def to_square_metres(value: float, unit: str) -> float | None:
    """Convert an area in `unit` to square metres; None for unknown units."""
    factor = _TO_SQUARE_METRES.get(unit.strip().lower())
    if factor is None:
        return None
    return value * factor


# Natural-language phrase for each measurable parameter, so a user-facing reason
# reads "Clear width is 30 inches…", never the raw field name "clear_width".
_PARAM_PHRASE: dict[Parameter, str] = {
    Parameter.AREA: "floor area",
    Parameter.CLEAR_WIDTH: "clear width",
    Parameter.OPENING_HEIGHT: "opening height",
    Parameter.THRESHOLD_HEIGHT: "threshold height",
    Parameter.MANEUVERING_CLEARANCE: "maneuvering clearance",
    Parameter.SLOPE: "slope",
    Parameter.RISER_HEIGHT: "riser height",
    Parameter.TREAD_DEPTH: "tread depth",
    Parameter.LANDING_LENGTH: "landing length",
}


def _param_phrase(parameter: Parameter) -> str:
    """Human phrase for a parameter; falls back to de-underscoring unknown keys."""
    return _PARAM_PHRASE.get(parameter, parameter.value.replace("_", " "))


def _review(reason: str, measured: float | None = None, required: str | None = None) -> CheckResult:
    return CheckResult(
        verdict=VerdictType.NEEDS_REVIEW, measured=measured, required=required, reason=reason
    )


def check(asset: PhysicalAsset, constraint: Constraint) -> CheckResult | None:
    """Compare one asset against one constraint.

    Returns None when the constraint does not apply to this asset type
    (no edge is written at all).
    """
    if constraint.applies_to != asset.type:
        return None

    if constraint.operator in (Operator.QUALITATIVE, Operator.BOOLEAN):
        kind = constraint.operator.value
        return _review(
            f"Constraint is {kind} and cannot be auto-verdicted: {constraint.summary or 'see clause text'}"
        )

    if constraint.extraction_confidence < settings.extraction_confidence_floor:
        return _review(
            f"Constraint extraction confidence {constraint.extraction_confidence:.2f} is below "
            f"the {settings.extraction_confidence_floor:.2f} threshold"
        )

    if constraint.parameter is None or constraint.value is None:
        return _review("Constraint is missing a parameter or value")

    if constraint.unit is None:
        return _review("Constraint has no unit")

    if constraint.parameter == Parameter.AREA:
        return _check_area(asset, constraint)

    if constraint.parameter == Parameter.SLOPE:
        return _check_slope(asset, constraint)

    required_in = to_inches(constraint.value, constraint.unit)
    if required_in is None:
        return _review(f"Unknown unit '{constraint.unit}'")

    measured = asset.measurements.get(constraint.parameter)
    if measured is None:
        return _review(
            f"Asset has no '{constraint.parameter.value}' measurement",
        )

    phrase = _param_phrase(constraint.parameter).capitalize()

    if constraint.operator == Operator.MIN:
        required_str = f">= {required_in:g} in"
        if measured >= required_in:
            return CheckResult(
                verdict=VerdictType.COMPLIES_WITH,
                measured=measured,
                required=required_str,
                reason=f"{phrase} is {measured:g} in; code requires {required_str}",
            )
        return CheckResult(
            verdict=VerdictType.VIOLATES,
            measured=measured,
            required=required_str,
            reason=(
                f"{phrase} is {measured:g} inches, "
                f"code requires {required_in:g} inches minimum"
            ),
        )

    if constraint.operator == Operator.MAX:
        required_str = f"<= {required_in:g} in"
        if measured <= required_in:
            return CheckResult(
                verdict=VerdictType.COMPLIES_WITH,
                measured=measured,
                required=required_str,
                reason=f"{phrase} is {measured:g} in; code requires {required_str}",
            )
        return CheckResult(
            verdict=VerdictType.VIOLATES,
            measured=measured,
            required=required_str,
            reason=(
                f"{phrase} is {measured:g} inches, "
                f"code allows {required_in:g} inches maximum"
            ),
        )

    if constraint.operator == Operator.RANGE:
        if constraint.value_high is None:
            return _review("Range constraint is missing its upper bound")
        high_in = to_inches(constraint.value_high, constraint.unit)
        if high_in is None:
            return _review(f"Unknown unit '{constraint.unit}'")
        required_str = f"{required_in:g}..{high_in:g} in"
        if required_in <= measured <= high_in:
            return CheckResult(
                verdict=VerdictType.COMPLIES_WITH,
                measured=measured,
                required=required_str,
                reason=f"{phrase} is {measured:g} in, within {required_str}",
            )
        return CheckResult(
            verdict=VerdictType.VIOLATES,
            measured=measured,
            required=required_str,
            reason=(
                f"{phrase} is {measured:g} inches, "
                f"code requires between {required_in:g} and {high_in:g} inches"
            ),
        )

    return _review(f"Unsupported operator '{constraint.operator.value}'")


def _check_area(asset: PhysicalAsset, constraint: Constraint) -> CheckResult:
    """Floor-area comparison — measured values are stored in square metres."""
    assert constraint.value is not None and constraint.unit is not None
    required = to_square_metres(constraint.value, constraint.unit)
    if required is None:
        return _review(f"Unknown area unit '{constraint.unit}'")
    measured = asset.measurements.get(Parameter.AREA)
    if measured is None:
        return _review("Asset has no 'area_m2' measurement")

    if constraint.operator == Operator.MIN:
        required_str = f">= {required:g} m²"
        ok = measured >= required
        reason = (
            f"floor area is {measured:g} m²; code requires {required_str}"
            if ok
            else f"floor area is {measured:g} m², code requires {required:g} m² minimum"
        )
    elif constraint.operator == Operator.MAX:
        required_str = f"<= {required:g} m²"
        ok = measured <= required
        reason = (
            f"floor area is {measured:g} m²; code requires {required_str}"
            if ok
            else f"floor area is {measured:g} m², code allows {required:g} m² maximum"
        )
    elif constraint.operator == Operator.RANGE:
        if constraint.value_high is None:
            return _review("Range constraint is missing its upper bound")
        high = to_square_metres(constraint.value_high, constraint.unit)
        if high is None:
            return _review(f"Unknown area unit '{constraint.unit}'")
        required_str = f"{required:g}..{high:g} m²"
        ok = required <= measured <= high
        reason = (
            f"floor area is {measured:g} m², within {required_str}"
            if ok
            else f"floor area is {measured:g} m², code requires between {required:g} and {high:g} m²"
        )
    else:
        return _review(f"Unsupported operator '{constraint.operator.value}'")

    return CheckResult(
        verdict=VerdictType.COMPLIES_WITH if ok else VerdictType.VIOLATES,
        measured=measured,
        required=required_str,
        reason=reason,
    )


def _slope_str(grade: float) -> str:
    """Grade fraction (rise/run) as a drawing-style ratio: 0.083 -> '1:12'."""
    return f"1:{round(1 / grade)}" if grade > 0 else "flat"


def _check_slope(asset: PhysicalAsset, constraint: Constraint) -> CheckResult:
    """Slope comparison — measured and constraint are grade fractions (rise/run),
    dimensionless, so no unit conversion. `measured` is left off the result to
    keep the inches-formatting UI honest; the reason carries the ratio."""
    assert constraint.value is not None
    measured = asset.measurements.get(Parameter.SLOPE)
    if measured is None:
        return _review("Asset has no 'slope' measurement")
    required = constraint.value

    if constraint.operator == Operator.MAX:
        required_str = f"<= {_slope_str(required)}"
        ok = measured <= required
        reason = (
            f"slope is {_slope_str(measured)}; code allows {required_str}"
            if ok
            else f"slope is {_slope_str(measured)}, steeper than the {_slope_str(required)} maximum"
        )
    elif constraint.operator == Operator.MIN:
        required_str = f">= {_slope_str(required)}"
        ok = measured >= required
        reason = (
            f"slope is {_slope_str(measured)}; code requires {required_str}"
            if ok
            else f"slope is {_slope_str(measured)}, shallower than {_slope_str(required)}"
        )
    elif constraint.operator == Operator.RANGE:
        if constraint.value_high is None:
            return _review("Range constraint is missing its upper bound")
        required_str = f"{_slope_str(required)}..{_slope_str(constraint.value_high)}"
        ok = required <= measured <= constraint.value_high
        reason = f"slope is {_slope_str(measured)}, {'within' if ok else 'outside'} {required_str}"
    else:
        return _review(f"Unsupported operator '{constraint.operator.value}'")

    return CheckResult(
        verdict=VerdictType.COMPLIES_WITH if ok else VerdictType.VIOLATES,
        measured=None,
        required=required_str,
        reason=reason,
    )
