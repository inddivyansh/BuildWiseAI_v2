"""Detail-region localization: box each numbered detail/view on a sheet.

A referenced sheet (A1.7, A3.0…) draws several details, each labelled with a number.
A callout '3/A1.7' points at detail 3 specifically — so to read the right dimensions
we must first find *where detail 3 is* on that sheet. The VLM boxes each detail and
reads its number (temperature 0, box output-validator, mirroring ingest/elevation.py);
the number is then grounded against the referencing callout downstream (spatial.py),
and the region's dimensions are grounded by the region-scoped dimension grid. The VLM
only locates and reads a label — it never supplies a magnitude.
"""

from __future__ import annotations

import struct

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent, ModelRetry

from planlint.ingest.vlm import RENDER_ZOOM
from planlint.models import BBox

_INSTRUCTIONS = """\
You read an architectural sheet that holds one or more DETAILS / sections / views,
each labelled with a number (in a bubble, or beside a title) and drawn at some scale.

For each distinct detail on the page return:
- number: the detail's number exactly as printed ('3', '1'). Every detail has one.
- title: the detail's title if printed ('STAIR SECTION', 'WINDOW JAMB'), else ''.
- box: [ymin, xmin, ymax, xmax] normalized to 0-1000, covering that detail's FULL
  drawing region — the drawing with its dimensions and notes, not just the number
  bubble or the title text.

Return one entry per detail. Empty list if the page shows no numbered details.
"""


class VlmDetail(BaseModel):
    number: str = ""
    title: str = ""
    box: tuple[float, float, float, float]  # [ymin, xmin, ymax, xmax], 0-1000


class DetailPage(BaseModel):
    details: list[VlmDetail] = Field(default_factory=list)


def build_detail_agent(model) -> Agent:
    agent = Agent(
        model,
        output_type=DetailPage,
        instructions=_INSTRUCTIONS,
        model_settings={"temperature": 0.0},
        retries=2,
    )

    @agent.output_validator
    async def verify(output: DetailPage) -> DetailPage:
        problems: list[str] = []
        for i, d in enumerate(output.details):
            ymin, xmin, ymax, xmax = d.box
            if not (0 <= ymin < ymax <= 1000 and 0 <= xmin < xmax <= 1000):
                problems.append(
                    f"details[{i}]: box {d.box} is not [ymin, xmin, ymax, xmax] on "
                    "the 0-1000 scale with min < max"
                )
            if not d.number.strip():
                problems.append(f"details[{i}]: number is empty — every detail is numbered")
        if problems:
            raise ModelRetry("Correct these details:\n" + "\n".join(problems))
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


def region_notes(labels, region: BBox, limit: int = 6) -> list[str]:
    """Short alphabetic text labels inside a detail's region — its title and material
    notes — for display. Pure text, no measurement is derived here."""
    out: list[str] = []
    x0, y0, x1, y1 = region
    for lbl in labels:
        cx = (lbl.bbox[0] + lbl.bbox[2]) / 2
        cy = (lbl.bbox[1] + lbl.bbox[3]) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            text = lbl.text.strip()
            if len(text) >= 4 and any(c.isalpha() for c in text):
                out.append(text)
    return out[:limit]


async def detect_details(image_png: bytes, model) -> list[VlmDetail]:
    """Every numbered detail region on one sheet, boxes converted to PDF points."""
    img_width, img_height = struct.unpack(">II", image_png[16:24])
    agent = build_detail_agent(model)
    result = await agent.run(
        [
            "Box and number each detail on this sheet.",
            BinaryContent(data=image_png, media_type="image/png"),
        ]
    )
    return [
        d.model_copy(update={"box": _to_points(d.box, img_width, img_height)})
        for d in result.output.details
    ]
