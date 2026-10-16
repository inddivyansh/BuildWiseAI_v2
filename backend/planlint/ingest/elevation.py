"""Elevation/section detector: the vertical dimensions a plan can't show.

Floor plans give clear widths and areas; stair riser height and tread depth are
only dimensioned on elevations and sections. This detector reads them there —
but never *estimates* a magnitude. The vision model only points at an element
and cites the dimension string printed beside it; a deterministic check
(`ingest/vlm.py` in spirit) confirms that string actually appears in the page's
text layer and parses it into inches. A cited dimension the drawing does not
print is bounced with ModelRetry, so no hallucinated number reaches the graph.

Scope is deliberately stairs only: the pure-Python checker compares lengths in
inches, so riser/tread (inches) are verdictable, while ramp slope (a ratio the
checker has no unit for) would only ever yield NEEDS_REVIEW — not worth
emitting. The model classifies; the checker still owns every verdict.
"""

from __future__ import annotations

import re
import struct

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent, ModelRetry, RunContext

from planlint.ingest.vlm import RENDER_ZOOM
from planlint.models import AssetType, BBox, Parameter, PhysicalAsset

# Sane inch ranges for a real stair (mirrors rule_extractor's constraint
# bounds): a cited value outside these is a misread, not a dimension.
_RISER_RANGE = (2.0, 12.0)
_TREAD_RANGE = (6.0, 24.0)

_INSTRUCTIONS = """\
You read architectural elevation and section drawings — vertical views, not
plan views. Do NOT report rooms, walls, or plan-view geometry.

Find each staircase shown in section/elevation and report ONLY the dimensions
printed on the drawing for it:
- riser_text: the printed riser height beside the stair (e.g. '7 1/4"', '7"').
- tread_text: the printed tread depth / run (e.g. '11"', '10 1/2"').

Copy each dimension VERBATIM as printed. If a stair's riser or tread is not
printed on the drawing, leave that field null — never guess or compute a value.
Omit any stair for which no dimension is printed. Return an empty list if the
page shows no dimensioned stairs.

box: [ymin, xmin, ymax, xmax] normalized to 0-1000, covering the stair run.
"""

# Match both straight and typographic inch/foot marks.
_QUOTES = str.maketrans({"”": '"', "″": '"', "’": "'", "′": "'"})


class StairDimensions(BaseModel):
    label: str = ""  # the stair's name if labelled ('MAIN STAIR'), else ''
    riser_text: str | None = None  # verbatim printed riser dimension
    tread_text: str | None = None  # verbatim printed tread dimension
    box: tuple[float, float, float, float]  # [ymin, xmin, ymax, xmax], 0-1000


class ElevationPage(BaseModel):
    stairs: list[StairDimensions] = Field(default_factory=list)


def _plain_inches(text: str) -> float | None:
    """Inches from '11', '7.25', '1/4', or '7 1/4' (no unit marks)."""
    s = text.strip()
    m = re.fullmatch(r"(\d+)\s+(\d+)/(\d+)", s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    m = re.fullmatch(r"(\d+)/(\d+)", s)
    if m:
        return int(m.group(1)) / int(m.group(2))
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)
    return None


def parse_inches(text: str) -> float | None:
    """Parse a printed dimension into inches: '7 1/4\"', '11\"', "3'-6\"", '7.25\"'."""
    t = text.translate(_QUOTES).strip()
    feet = re.match(r"^\s*(\d+)\s*'\s*-?\s*(.*)$", t)
    if feet:
        rest = feet.group(2).replace('"', "").strip()
        inches = _plain_inches(rest) if rest else 0.0
        if inches is None:
            return None
        return int(feet.group(1)) * 12 + inches
    return _plain_inches(t.replace('"', "").strip())


def _normalize(text: str) -> str:
    """Lowercase, unify quotes, drop inch marks, collapse whitespace — so a
    cited dimension matches the text layer despite tokenization differences."""
    text = text.translate(_QUOTES).lower().replace('"', " ")
    return re.sub(r"\s+", " ", text).strip()


def build_elevation_agent(model) -> Agent:
    """Elevation/section agent; deps carry the page text layer so the validator
    can reject any cited dimension the drawing does not actually print."""
    agent = Agent(
        model,
        output_type=ElevationPage,
        deps_type=str,
        instructions=_INSTRUCTIONS,
        model_settings={"temperature": 0.0},
        retries=2,
    )

    @agent.output_validator
    async def verify(ctx: RunContext[str], output: ElevationPage) -> ElevationPage:
        layer = _normalize(ctx.deps)
        problems: list[str] = []
        for i, stair in enumerate(output.stairs):
            ymin, xmin, ymax, xmax = stair.box
            if not (0 <= ymin < ymax <= 1000 and 0 <= xmin < xmax <= 1000):
                problems.append(f"stairs[{i}]: box {stair.box} is not [ymin,xmin,ymax,xmax] in 0-1000")
            for field, text, rng in (
                ("riser_text", stair.riser_text, _RISER_RANGE),
                ("tread_text", stair.tread_text, _TREAD_RANGE),
            ):
                if text is None:
                    continue
                if _normalize(text) and _normalize(text) not in layer:
                    problems.append(
                        f"stairs[{i}].{field}={text!r} is not printed on the page — "
                        "cite only dimensions the drawing actually shows"
                    )
                    continue
                value = parse_inches(text)
                if value is None or not (rng[0] <= value <= rng[1]):
                    problems.append(
                        f"stairs[{i}].{field}={text!r} is not a plausible stair dimension"
                    )
        if problems:
            raise ModelRetry("Correct these dimensions:\n" + "\n".join(problems))
        return output

    return agent


def _to_points(box: tuple[float, float, float, float], img_w: int, img_h: int) -> BBox:
    ymin, xmin, ymax, xmax = box
    return (
        (xmin / 1000.0) * img_w / RENDER_ZOOM,
        (ymin / 1000.0) * img_h / RENDER_ZOOM,
        (xmax / 1000.0) * img_w / RENDER_ZOOM,
        (ymax / 1000.0) * img_h / RENDER_ZOOM,
    )


async def detect_elevation_page(image_png: bytes, text_layer: str, model) -> list[PhysicalAsset]:
    """Stair riser/tread assets read off one elevation/section page. Every
    measurement is parsed from a printed dimension verified against the text
    layer — nothing is estimated."""
    img_width, img_height = struct.unpack(">II", image_png[16:24])
    agent = build_elevation_agent(model)
    result = await agent.run(
        [
            "Read the printed stair dimensions on this elevation/section page.",
            BinaryContent(data=image_png, media_type="image/png"),
        ],
        deps=text_layer,
    )

    assets: list[PhysicalAsset] = []
    for stair in result.output.stairs:
        measurements: dict[Parameter, float] = {}
        if stair.riser_text is not None and (r := parse_inches(stair.riser_text)) is not None:
            measurements[Parameter.RISER_HEIGHT] = round(r, 2)
        if stair.tread_text is not None and (t := parse_inches(stair.tread_text)) is not None:
            measurements[Parameter.TREAD_DEPTH] = round(t, 2)
        if not measurements:
            continue  # a stair with no parseable printed dimension is not an asset
        assets.append(
            PhysicalAsset(
                type=AssetType.STAIR,
                label=stair.label,
                bbox=_to_points(stair.box, img_width, img_height),
                confidence=0.85,  # a printed dimension, cross-checked against the text layer
                source="vlm-only",  # off plan-view; no vector/raster snap applies
                measurements=measurements,
            )
        )
    return assets
