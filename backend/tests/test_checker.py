"""Exhaustive tests for the deterministic checker — the only component that
decides compliance."""

from planlint.models import (
    AssetType,
    CheckResult,
    Constraint,
    Operator,
    Parameter,
    PhysicalAsset,
    VerdictType,
)
from planlint.verify.checker import check, to_inches


def make_asset(
    type_: AssetType = AssetType.DOOR,
    measurements: dict | None = None,
) -> PhysicalAsset:
    return PhysicalAsset(
        id="asset-test",
        type=type_,
        bbox=(0, 0, 10, 10),
        measurements=measurements if measurements is not None else {Parameter.CLEAR_WIDTH: 30.0},
    )


def make_constraint(
    operator: Operator = Operator.MIN,
    value: float | None = 32.0,
    value_high: float | None = None,
    unit: str | None = "in",
    applies_to: AssetType = AssetType.DOOR,
    parameter: Parameter | None = Parameter.CLEAR_WIDTH,
    confidence: float = 1.0,
) -> Constraint:
    return Constraint(
        regulation_id="reg-test",
        applies_to=applies_to,
        parameter=parameter,
        operator=operator,
        value=value,
        value_high=value_high,
        unit=unit,
        extraction_confidence=confidence,
        summary="test constraint",
    )


# ---------------------------------------------------------------- to_inches

def test_to_inches_identity():
    assert to_inches(32.0, "in") == 32.0


def test_to_inches_feet():
    assert to_inches(3.0, "ft") == 36.0


def test_to_inches_mm():
    assert abs(to_inches(815.0, "mm") - 32.086) < 0.01


def test_to_inches_cm():
    assert abs(to_inches(91.5, "cm") - 36.02) < 0.01


def test_to_inches_m():
    assert abs(to_inches(0.915, "m") - 36.02) < 0.01


def test_to_inches_unknown_unit():
    assert to_inches(1.0, "furlong") is None


# ------------------------------------------------------------- min operator

def test_min_violates():
    result = check(make_asset(), make_constraint(Operator.MIN, 32.0))
    assert result is not None
    assert result.verdict == VerdictType.VIOLATES
    assert result.measured == 30.0
    assert result.required == ">= 32 in"
    assert "30" in result.reason and "32" in result.reason


def test_min_complies():
    asset = make_asset(measurements={Parameter.CLEAR_WIDTH: 36.0})
    result = check(asset, make_constraint(Operator.MIN, 32.0))
    assert result.verdict == VerdictType.COMPLIES_WITH


def test_min_boundary_complies():
    """Exactly meeting a minimum complies."""
    asset = make_asset(measurements={Parameter.CLEAR_WIDTH: 32.0})
    result = check(asset, make_constraint(Operator.MIN, 32.0))
    assert result.verdict == VerdictType.COMPLIES_WITH


# ------------------------------------------------------------- max operator

def test_max_complies():
    asset = make_asset(measurements={Parameter.THRESHOLD_HEIGHT: 0.25})
    result = check(
        asset,
        make_constraint(Operator.MAX, 0.5, parameter=Parameter.THRESHOLD_HEIGHT),
    )
    assert result.verdict == VerdictType.COMPLIES_WITH


def test_max_violates():
    asset = make_asset(measurements={Parameter.THRESHOLD_HEIGHT: 0.75})
    result = check(
        asset,
        make_constraint(Operator.MAX, 0.5, parameter=Parameter.THRESHOLD_HEIGHT),
    )
    assert result.verdict == VerdictType.VIOLATES


def test_max_boundary_complies():
    asset = make_asset(measurements={Parameter.THRESHOLD_HEIGHT: 0.5})
    result = check(
        asset,
        make_constraint(Operator.MAX, 0.5, parameter=Parameter.THRESHOLD_HEIGHT),
    )
    assert result.verdict == VerdictType.COMPLIES_WITH


# ----------------------------------------------------------- range operator

def test_range_complies():
    asset = make_asset(measurements={Parameter.RISER_HEIGHT: 5.0})
    result = check(
        asset,
        make_constraint(Operator.RANGE, 4.0, value_high=7.0, parameter=Parameter.RISER_HEIGHT),
    )
    assert result.verdict == VerdictType.COMPLIES_WITH


def test_range_violates_below():
    asset = make_asset(measurements={Parameter.RISER_HEIGHT: 3.0})
    result = check(
        asset,
        make_constraint(Operator.RANGE, 4.0, value_high=7.0, parameter=Parameter.RISER_HEIGHT),
    )
    assert result.verdict == VerdictType.VIOLATES


def test_range_violates_above():
    asset = make_asset(measurements={Parameter.RISER_HEIGHT: 8.0})
    result = check(
        asset,
        make_constraint(Operator.RANGE, 4.0, value_high=7.0, parameter=Parameter.RISER_HEIGHT),
    )
    assert result.verdict == VerdictType.VIOLATES


def test_range_missing_high_bound_needs_review():
    asset = make_asset(measurements={Parameter.RISER_HEIGHT: 5.0})
    result = check(
        asset,
        make_constraint(Operator.RANGE, 4.0, value_high=None, parameter=Parameter.RISER_HEIGHT),
    )
    assert result.verdict == VerdictType.NEEDS_REVIEW


# ----------------------------------------------------- unit conversion path

def test_constraint_in_mm_converted():
    """815 mm ≈ 32.09 in, so a 30-in door violates and a 33-in door complies."""
    violating = check(make_asset(), make_constraint(Operator.MIN, 815.0, unit="mm"))
    assert violating.verdict == VerdictType.VIOLATES
    passing = check(
        make_asset(measurements={Parameter.CLEAR_WIDTH: 33.0}),
        make_constraint(Operator.MIN, 815.0, unit="mm"),
    )
    assert passing.verdict == VerdictType.COMPLIES_WITH


def test_unknown_unit_needs_review():
    result = check(make_asset(), make_constraint(Operator.MIN, 32.0, unit="cubit"))
    assert result.verdict == VerdictType.NEEDS_REVIEW
    assert "unit" in result.reason.lower()


# ------------------------------------------------------------- skip / review

def test_applies_to_mismatch_returns_none():
    """A ramp constraint checked against a door produces no edge at all."""
    result = check(make_asset(), make_constraint(applies_to=AssetType.RAMP))
    assert result is None


def test_qualitative_needs_review():
    result = check(make_asset(), make_constraint(Operator.QUALITATIVE, value=None, unit=None))
    assert result.verdict == VerdictType.NEEDS_REVIEW
    assert "qualitative" in result.reason.lower()


def test_boolean_needs_review():
    result = check(make_asset(), make_constraint(Operator.BOOLEAN, value=None, unit=None))
    assert result.verdict == VerdictType.NEEDS_REVIEW


def test_missing_measurement_needs_review():
    asset = make_asset(measurements={})
    result = check(asset, make_constraint())
    assert result.verdict == VerdictType.NEEDS_REVIEW
    assert "measurement" in result.reason.lower()


def test_low_extraction_confidence_needs_review():
    result = check(make_asset(), make_constraint(confidence=0.3))
    assert result.verdict == VerdictType.NEEDS_REVIEW
    assert "confidence" in result.reason.lower()


def test_missing_value_needs_review():
    result = check(make_asset(), make_constraint(Operator.MIN, value=None))
    assert result.verdict == VerdictType.NEEDS_REVIEW


def test_result_type():
    result = check(make_asset(), make_constraint())
    assert isinstance(result, CheckResult)


# --------------------------------------------------------------------- area


def area_asset(area: float | None = 12.02) -> PhysicalAsset:
    measurements = {} if area is None else {Parameter.AREA: area}
    return make_asset(type_=AssetType.ROOM, measurements=measurements)


def area_constraint(operator=Operator.MIN, value=9.0, value_high=None, unit="m²") -> Constraint:
    return make_constraint(
        operator=operator,
        value=value,
        value_high=value_high,
        unit=unit,
        applies_to=AssetType.ROOM,
        parameter=Parameter.AREA,
    )


def test_area_min_complies():
    result = check(area_asset(12.02), area_constraint(value=9.0))
    assert result.verdict == VerdictType.COMPLIES_WITH
    assert result.required == ">= 9 m²"


def test_area_min_violates():
    result = check(area_asset(7.5), area_constraint(value=9.0))
    assert result.verdict == VerdictType.VIOLATES
    assert "7.5 m²" in result.reason and "9 m² minimum" in result.reason


def test_area_max_and_range():
    assert check(area_asset(12.0), area_constraint(Operator.MAX, 15.0)).verdict == (
        VerdictType.COMPLIES_WITH
    )
    assert check(
        area_asset(12.0), area_constraint(Operator.RANGE, 9.0, value_high=11.0)
    ).verdict == VerdictType.VIOLATES


def test_area_square_feet_converts():
    # 100 sq ft = 9.29 m²; a 12.02 m² room complies.
    result = check(area_asset(12.02), area_constraint(value=100.0, unit="sqft"))
    assert result.verdict == VerdictType.COMPLIES_WITH


def test_area_unknown_unit_needs_review():
    result = check(area_asset(), area_constraint(unit="acres"))
    assert result.verdict == VerdictType.NEEDS_REVIEW


def test_area_without_measurement_needs_review():
    result = check(area_asset(area=None), area_constraint())
    assert result.verdict == VerdictType.NEEDS_REVIEW
    assert "area_m2" in result.reason


# -------------------------------------------------------------------- slope


def ramp_asset(grade: float | None = 0.083) -> PhysicalAsset:
    measurements = {} if grade is None else {Parameter.SLOPE: grade}
    return make_asset(type_=AssetType.RAMP, measurements=measurements)


def slope_constraint(operator=Operator.MAX, value=0.083, value_high=None) -> Constraint:
    return make_constraint(
        operator=operator, value=value, value_high=value_high, unit="ratio",
        applies_to=AssetType.RAMP, parameter=Parameter.SLOPE,
    )


def test_slope_max_complies():
    # a 1:15 ramp (0.067) is flatter than the 1:12 maximum (0.083)
    result = check(ramp_asset(0.067), slope_constraint(value=0.083))
    assert result.verdict == VerdictType.COMPLIES_WITH
    assert result.required == "<= 1:12"


def test_slope_max_violates():
    # a 1:10 ramp (0.1) is steeper than the 1:12 maximum
    result = check(ramp_asset(0.1), slope_constraint(value=0.083))
    assert result.verdict == VerdictType.VIOLATES
    assert "1:10" in result.reason and "1:12" in result.reason


def test_slope_max_boundary_complies():
    result = check(ramp_asset(0.083), slope_constraint(value=0.083))
    assert result.verdict == VerdictType.COMPLIES_WITH


def test_slope_without_measurement_needs_review():
    result = check(ramp_asset(grade=None), slope_constraint())
    assert result.verdict == VerdictType.NEEDS_REVIEW
    assert "slope" in result.reason.lower()
