"""Rule Extractor: the ONE place an LLM interprets legal text.

It converts clause prose (plus its full ancestral chain) into structured
Constraint objects. It never decides compliance — that's the checker's job.
Semantic sanity is enforced by an output validator: implausible values are
bounced back to the model with a specific complaint (ModelRetry) before they
can enter the pipeline.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry

from planlint.models import AssetType, Constraint, Operator, Parameter
from planlint.verify.checker import to_inches, to_square_metres

# Plausibility ranges in inches for length-like parameters. A value outside
# its range is as wrong as a negative one — force the model to re-read.
PLAUSIBLE_RANGES: dict[Parameter, tuple[float, float]] = {
    Parameter.CLEAR_WIDTH: (10.0, 120.0),
    Parameter.OPENING_HEIGHT: (60.0, 144.0),
    Parameter.THRESHOLD_HEIGHT: (0.05, 4.0),
    Parameter.MANEUVERING_CLEARANCE: (10.0, 120.0),
    Parameter.RISER_HEIGHT: (2.0, 12.0),
    Parameter.TREAD_DEPTH: (6.0, 24.0),
    Parameter.LANDING_LENGTH: (12.0, 240.0),
}

# Slope is a grade fraction (rise/run), not a length — checked on the raw value.
SLOPE_RANGE = (0.01, 0.5)  # 1:100 (nearly flat) to 1:2 (very steep)

_ALLOWED_LENGTH_UNITS = {"in", "inch", "inches", "ft", "feet", "mm", "cm", "m", "ratio"}
_ALLOWED_AREA_UNITS = {"m²", "m2", "sqm", "sq m", "ft²", "ft2", "sqft", "sq ft"}
_ALLOWED_UNITS = _ALLOWED_LENGTH_UNITS | _ALLOWED_AREA_UNITS


class ConstraintDraft(BaseModel):
    """What the LLM emits. Mapped to a Constraint after validation."""

    applies_to: AssetType
    parameter: Parameter | None = None
    operator: Operator
    value: float | None = None
    value_high: float | None = None
    unit: str | None = None
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    summary: str


class ExtractedConstraints(BaseModel):
    constraints: list[ConstraintDraft]


_INSTRUCTIONS = f"""\
You extract machine-readable constraints from building-code clauses.

Rules:
- Emit one constraint per distinct requirement in the clause. Emit an empty
  list if the clause imposes no requirement on a physical asset.
- applies_to must be one of: {", ".join(t.value for t in AssetType)}.
- parameter must be one of: {", ".join(p.value for p in Parameter)} — or null
  only for qualitative/boolean constraints.
- operator: "min" (at least), "max" (at most), "range" (between value and
  value_high), "qualitative" (not expressible numerically), "boolean".
- unit: one of {", ".join(sorted(_ALLOWED_UNITS))}. Use the unit the clause
  states; do not convert.
- Requirements phrased as "shall provide … minimum" are min; "… maximum" are max.
- Area requirements: use parameter "area_m2" with an area unit (m2, sqft) ONLY
  when the clause constrains the floor area of a room or space itself.
  Constraints about lots, parcels, sites, yards, frontages, or setbacks govern
  the site — no listed asset type matches them, so emit nothing for those.
- Slope/grade (ramps): use parameter "slope", unit "ratio", operator "max", and
  express value as the maximum rise-over-run as a DECIMAL fraction — "1:12" is
  0.083, "8.33%" is 0.083, "1:20" is 0.05.
- extraction_confidence: your confidence the constraint faithfully represents
  the clause (1.0 = verbatim numeric requirement).
- summary: one sentence restating the requirement in plain language.
- The ancestral chain gives scope only — extract requirements from the target
  clause itself, not from ancestors.
"""


def build_extractor_agent(model) -> Agent:
    agent = Agent(model, output_type=ExtractedConstraints, instructions=_INSTRUCTIONS)

    @agent.output_validator
    async def validate_constraints(output: ExtractedConstraints) -> ExtractedConstraints:
        problems: list[str] = []
        for i, draft in enumerate(output.constraints):
            where = f"constraints[{i}] ({draft.summary!r})"
            if draft.operator in (Operator.MIN, Operator.MAX, Operator.RANGE):
                if draft.value is None:
                    problems.append(f"{where}: numeric operator but value is null")
                    continue
                if draft.parameter is None:
                    problems.append(f"{where}: numeric operator but parameter is null")
                    continue
                if draft.unit is None or draft.unit.lower() not in _ALLOWED_UNITS:
                    problems.append(f"{where}: unit {draft.unit!r} is not an allowed unit")
                    continue
                # Units must match the parameter's dimension: areas take area
                # units, everything else takes length units (or ratio).
                is_area_unit = draft.unit.lower() in _ALLOWED_AREA_UNITS
                if (draft.parameter == Parameter.AREA) != is_area_unit:
                    problems.append(
                        f"{where}: unit {draft.unit!r} does not match parameter "
                        f"{draft.parameter.value!r} (area_m2 takes area units like m2; "
                        "other parameters take length units)"
                    )
                    continue
                if draft.value < 0:
                    problems.append(
                        f"{where}: measurements cannot be negative. Re-read the clause."
                    )
                    continue
                if draft.operator == Operator.RANGE and draft.value_high is None:
                    problems.append(f"{where}: range operator requires value_high")
                    continue
                if draft.parameter == Parameter.AREA:
                    sqm = to_square_metres(draft.value, draft.unit)
                    if sqm is not None and not (0.5 <= sqm <= 100_000):
                        problems.append(
                            f"{where}: area of {draft.value} {draft.unit} (= {sqm:g} m²) "
                            "is implausible for a room. Re-read the clause."
                        )
                        continue
                if draft.parameter == Parameter.SLOPE:
                    if not (SLOPE_RANGE[0] <= draft.value <= SLOPE_RANGE[1]):
                        problems.append(
                            f"{where}: slope {draft.value} is outside "
                            f"{SLOPE_RANGE[0]}–{SLOPE_RANGE[1]} (rise/run). Express it "
                            "as a decimal fraction, e.g. 1:12 = 0.083."
                        )
                    continue
                plausible = PLAUSIBLE_RANGES.get(draft.parameter)
                if plausible and draft.unit.lower() != "ratio":
                    inches = to_inches(draft.value, draft.unit)
                    if inches is not None and not (plausible[0] <= inches <= plausible[1]):
                        problems.append(
                            f"{where}: {draft.parameter.value} of {draft.value} {draft.unit} "
                            f"(= {inches:g} in) is outside the plausible range "
                            f"{plausible[0]:g}–{plausible[1]:g} in. Re-read the clause."
                        )
        if problems:
            raise ModelRetry("Invalid constraints:\n" + "\n".join(problems))
        return output

    return agent


async def extract_constraints(
    clause: dict,
    ancestors: list[dict],
    model,
) -> list[Constraint]:
    """Extract constraints for one clause. `clause`/`ancestors` are graph dicts
    with clause_id / title / text keys."""
    agent = build_extractor_agent(model)
    breadcrumb = "\n".join(
        f"{'  ' * i}{a.get('clause_id', '')} {a.get('title') or a.get('text', '')[:80]}"
        for i, a in enumerate(ancestors)
    )
    prompt = (
        f"Ancestral chain (scope context):\n{breadcrumb or '(none)'}\n\n"
        f"Target clause {clause.get('clause_id', '')}:\n{clause['text']}"
    )
    result = await agent.run(prompt)
    return [
        Constraint(regulation_id=clause["id"], **draft.model_dump())
        for draft in result.output.constraints
    ]
