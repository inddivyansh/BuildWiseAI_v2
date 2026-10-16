"""Rule extractor tests: FunctionModel-scripted LLM, validator retry loop,
inline_snapshot on the structured constraint set."""

import pytest
from inline_snapshot import snapshot
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from planlint.models import AssetType, Operator, Parameter
from planlint.verify.rule_extractor import extract_constraints

models.ALLOW_MODEL_REQUESTS = False

CLAUSE = {
    "id": "reg-4042 3",
    "clause_id": "404.2.3",
    "title": "Clear Width",
    "text": (
        "404.2.3 Clear Width. Door openings shall provide a clear width of "
        "32 inches (815 mm) minimum."
    ),
}
ANCESTORS = [
    {"clause_id": "404", "title": "Doors, Doorways, and Gates", "text": "404 Doors..."},
    {"clause_id": "404.2", "title": "Manual Doors", "text": "404.2 Manual Doors..."},
]


def structured(info: AgentInfo, args: dict) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])


GOOD_ARGS = {
    "constraints": [
        {
            "applies_to": "door",
            "parameter": "clear_width",
            "operator": "min",
            "value": 32.0,
            "value_high": None,
            "unit": "in",
            "extraction_confidence": 1.0,
            "summary": "Door openings must have a clear width of at least 32 inches.",
        }
    ]
}


async def test_valid_extraction():
    def model_fn(messages, info):
        return structured(info, GOOD_ARGS)

    constraints = await extract_constraints(CLAUSE, ANCESTORS, FunctionModel(model_fn))
    assert len(constraints) == 1
    c = constraints[0]
    assert c.regulation_id == CLAUSE["id"]
    normalized = c.model_dump(exclude={"id", "regulation_id"}, mode="json")
    assert normalized == snapshot(
        {
            "applies_to": "door",
            "parameter": "clear_width",
            "operator": "min",
            "value": 32.0,
            "value_high": None,
            "unit": "in",
            "extraction_confidence": 1.0,
            "summary": "Door openings must have a clear width of at least 32 inches.",
        }
    )


async def test_negative_value_triggers_retry_then_corrects():
    calls = []

    def model_fn(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            bad = {**GOOD_ARGS, "constraints": [{**GOOD_ARGS["constraints"][0], "value": -32.0}]}
            return structured(info, bad)
        # Second call must have received the validator's complaint.
        retry_parts = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, RetryPromptPart)
        ]
        assert retry_parts, "expected a RetryPromptPart after ModelRetry"
        assert "negative" in str(retry_parts[-1].content).lower()
        return structured(info, GOOD_ARGS)

    constraints = await extract_constraints(CLAUSE, ANCESTORS, FunctionModel(model_fn))
    assert len(calls) == 2
    assert constraints[0].value == 32.0


async def test_implausible_value_triggers_retry():
    calls = []

    def model_fn(messages, info):
        calls.append(1)
        if len(calls) == 1:
            bad = {**GOOD_ARGS, "constraints": [{**GOOD_ARGS["constraints"][0], "value": 600.0}]}
            return structured(info, bad)
        return structured(info, GOOD_ARGS)

    constraints = await extract_constraints(CLAUSE, ANCESTORS, FunctionModel(model_fn))
    assert len(calls) == 2
    assert constraints[0].value == 32.0


async def test_bad_unit_triggers_retry():
    calls = []

    def model_fn(messages, info):
        calls.append(1)
        if len(calls) == 1:
            bad = {**GOOD_ARGS, "constraints": [{**GOOD_ARGS["constraints"][0], "unit": "cubits"}]}
            return structured(info, bad)
        return structured(info, GOOD_ARGS)

    constraints = await extract_constraints(CLAUSE, ANCESTORS, FunctionModel(model_fn))
    assert len(calls) == 2
    assert constraints[0].unit == "in"


async def test_qualitative_clause_passes_without_value():
    qualitative = {
        "constraints": [
            {
                "applies_to": "door",
                "parameter": None,
                "operator": "qualitative",
                "value": None,
                "value_high": None,
                "unit": None,
                "extraction_confidence": 0.9,
                "summary": "Door hardware must be operable with one hand without tight grasping.",
            }
        ]
    }

    def model_fn(messages, info):
        return structured(info, qualitative)

    constraints = await extract_constraints(CLAUSE, ANCESTORS, FunctionModel(model_fn))
    assert constraints[0].operator == Operator.QUALITATIVE
    assert constraints[0].value is None


async def test_empty_extraction_allowed():
    def model_fn(messages, info):
        return structured(info, {"constraints": []})

    constraints = await extract_constraints(CLAUSE, ANCESTORS, FunctionModel(model_fn))
    assert constraints == []


async def test_persistent_bad_output_raises():
    """A model that never self-corrects exhausts retries and raises."""

    def model_fn(messages, info):
        bad = {**GOOD_ARGS, "constraints": [{**GOOD_ARGS["constraints"][0], "value": -1.0}]}
        return structured(info, bad)

    with pytest.raises(Exception):
        await extract_constraints(CLAUSE, ANCESTORS, FunctionModel(model_fn))


AREA_ARGS = {
    "constraints": [
        {
            "applies_to": "room",
            "parameter": "area_m2",
            "operator": "min",
            "value": 9.0,
            "value_high": None,
            "unit": "m2",
            "extraction_confidence": 1.0,
            "summary": "Habitable rooms must have at least 9 square metres of floor area.",
        }
    ]
}


SLOPE_ARGS = {
    "constraints": [
        {
            "applies_to": "ramp",
            "parameter": "slope",
            "operator": "max",
            "value": 0.083,
            "value_high": None,
            "unit": "ratio",
            "extraction_confidence": 1.0,
            "summary": "Ramp running slope shall not exceed 1:12.",
        }
    ]
}


async def test_slope_constraint_accepted():
    def model_fn(messages, info):
        return structured(info, SLOPE_ARGS)

    constraints = await extract_constraints(CLAUSE, ANCESTORS, FunctionModel(model_fn))
    assert len(constraints) == 1
    assert constraints[0].parameter == Parameter.SLOPE
    assert constraints[0].value == 0.083


async def test_implausible_slope_triggers_retry():
    calls = {"n": 0}

    def model_fn(messages, info):
        calls["n"] += 1
        if calls["n"] == 1:
            # 12.0 as a "slope" (should be a decimal grade ~0.083) is implausible
            bad = {"constraints": [{**SLOPE_ARGS["constraints"][0], "value": 12.0}]}
            return structured(info, bad)
        return structured(info, SLOPE_ARGS)

    constraints = await extract_constraints(CLAUSE, ANCESTORS, FunctionModel(model_fn))
    assert calls["n"] == 2
    assert constraints[0].value == 0.083


async def test_area_constraint_with_area_unit_accepted():
    def model_fn(messages, info):
        return structured(info, AREA_ARGS)

    constraints = await extract_constraints(CLAUSE, ANCESTORS, FunctionModel(model_fn))
    assert len(constraints) == 1
    assert constraints[0].parameter == Parameter.AREA
    assert constraints[0].unit == "m2"


async def test_length_parameter_with_area_unit_retries_then_corrects():
    """Unit dimension must match the parameter: clear_width in m2 is bounced."""
    bad = {
        "constraints": [
            {
                "applies_to": "door",
                "parameter": "clear_width",
                "operator": "min",
                "value": 32.0,
                "value_high": None,
                "unit": "m2",
                "extraction_confidence": 1.0,
                "summary": "bad unit dimension",
            }
        ]
    }
    calls = {"n": 0}

    def model_fn(messages, info):
        calls["n"] += 1
        if calls["n"] == 1:
            return structured(info, bad)
        retry_parts = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, RetryPromptPart)
        ]
        assert retry_parts, "expected a RetryPromptPart after ModelRetry"
        return structured(info, GOOD_ARGS)

    constraints = await extract_constraints(CLAUSE, ANCESTORS, FunctionModel(model_fn))
    assert calls["n"] == 2
    assert constraints[0].parameter == Parameter.CLEAR_WIDTH
