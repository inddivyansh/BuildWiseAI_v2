"""Elevation/section detector: stair riser/tread read off a vertical drawing,
every value parsed from a printed dimension verified against the text layer.
FunctionModel-scripted — no real vision calls."""

from __future__ import annotations

import pymupdf
import pytest
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from planlint.ingest.elevation import detect_elevation_page, parse_inches
from planlint.models import AssetType, Parameter

models.ALLOW_MODEL_REQUESTS = False

# A stair with its riser and tread dimensioned, as printed on a section sheet.
LAYER = 'MAIN STAIR 7 1/4" RISER 11" TREAD +10\'-0" (T.O. FINISH)'


def png_100x80() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 80))
    pix.clear_with(255)
    return pix.tobytes("png")


def structured(info, args) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])


@pytest.mark.parametrize(
    "text, inches",
    [
        ('7 1/4"', 7.25),
        ('11"', 11.0),
        ('10 1/2"', 10.5),
        ("3'-6\"", 42.0),
        ('7.25"', 7.25),
        ("SEE PLAN", None),
    ],
)
def test_parse_inches(text, inches):
    assert parse_inches(text) == inches


async def test_detects_stair_riser_and_tread():
    args = {
        "stairs": [
            {"label": "MAIN STAIR", "riser_text": '7 1/4"', "tread_text": '11"',
             "box": [100, 100, 700, 400]}
        ]
    }
    assets = await detect_elevation_page(png_100x80(), LAYER, FunctionModel(lambda m, i: structured(i, args)))
    assert len(assets) == 1
    stair = assets[0]
    assert stair.type == AssetType.STAIR
    assert stair.label == "MAIN STAIR"
    assert stair.measurements[Parameter.RISER_HEIGHT] == 7.25
    assert stair.measurements[Parameter.TREAD_DEPTH] == 11.0
    assert stair.source == "vlm-only"


async def test_hallucinated_dimension_is_bounced_then_corrected():
    # '5"' is not printed anywhere on the page; the model must correct to the
    # real, printed dimension after the retry.
    bad = {"stairs": [{"riser_text": '5"', "tread_text": '11"', "box": [100, 100, 700, 400]}]}
    good = {"stairs": [{"riser_text": '7 1/4"', "tread_text": '11"', "box": [100, 100, 700, 400]}]}
    calls = {"n": 0}

    def model_fn(messages, info):
        calls["n"] += 1
        if calls["n"] == 1:
            return structured(info, bad)
        assert any(
            isinstance(p, RetryPromptPart) for msg in messages for p in msg.parts
        ), "expected a retry after the un-printed dimension"
        return structured(info, good)

    assets = await detect_elevation_page(png_100x80(), LAYER, FunctionModel(model_fn))
    assert calls["n"] == 2
    assert assets[0].measurements[Parameter.RISER_HEIGHT] == 7.25


async def test_stair_without_any_printed_dimension_is_dropped():
    args = {"stairs": [{"riser_text": None, "tread_text": None, "box": [100, 100, 700, 400]}]}
    assets = await detect_elevation_page(png_100x80(), LAYER, FunctionModel(lambda m, i: structured(i, args)))
    assert assets == []
