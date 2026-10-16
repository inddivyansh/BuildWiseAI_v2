"""Vision agent: classifies physical entities on a rendered plan page.

The VLM's boxes are treated as approximate — on vector PDFs they are snapped
to the exact geometry afterwards (see spatial.py). Rendering zoom is handled
here so callers speak PDF points only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent, ModelRetry

from planlint.ingest.sheet_type import SheetType
from planlint.models import AssetType, BBox

import re
import struct

RENDER_ZOOM = 2.0  # pixels per PDF point when rasterizing pages for the VLM

# Printed areas ('39.37 m²', '12 m2', '450 sq ft') do not belong in names.
_AREA_IN_NAME = re.compile(r"\d\s*(?:m²|m2\b|sq\s*\.?\s*(?:m|ft)|ft²|ft2\b)", re.IGNORECASE)

_SPACE_TYPES = (AssetType.ROOM, AssetType.CORRIDOR)
# A bare number ('1') or single letter ('D') labelling a room is a callout /
# detail-marker bubble (often on a dashed section-cut line), not a space name.
_ROOM_CALLOUT = re.compile(r"^(?:\d+|[A-Za-z])$")
# A reference callout drawn as text ('3/A1.7') — the deterministic offline reader
# for PLANLINT_OFFLINE_SAMPLE (the real VLM classifies the marker glyph instead).
_REF_LABEL = re.compile(r"^(\d+)\s*/\s*([A-Z]{1,2}\d{1,2}\.\d{1,2})\b", re.IGNORECASE)


def _resolve_space_name(entity: "VlmEntity") -> str | None:
    """The room/corridor name cleaned of callouts, or None to drop the entity.

    A bare callout ('A', '1') labelling a space is a section/detail cut marker,
    not a room. With no printed area it is a phantom room — dropped entirely.
    With an area (a numbered room) the callout name is blanked but the space
    kept. Non-space entities pass through unchanged."""
    if entity.entity_type not in _SPACE_TYPES:
        return entity.name
    if _ROOM_CALLOUT.match(entity.name.strip()):
        return "" if entity.floor_area_m2 else None
    return entity.name


class VlmEntity(BaseModel):
    entity_type: AssetType
    name: str = ""  # clean identifier only ('GARAGE', 'D1') — no dimensions
    floor_area_m2: float | None = None  # printed floor area, rooms only
    mark: str | None = None  # door/window schedule callout ('A', '2') — the schedule key
    box: tuple[float, float, float, float]  # pixels in the rendered image


class VlmReference(BaseModel):
    """A cross-sheet reference marker read off a plan — a section / detail /
    elevation callout pointing to another sheet. The target is grounded against the
    text layer + sheet registry downstream (ingest/references.py) before use."""

    kind: Literal["section", "detail", "elevation"]
    detail_num: str = ""  # the number in the marker bubble ('1', '3')
    target_sheet: str  # the sheet it points to, as printed ('A3.0')
    box: tuple[float, float, float, float]  # pixels in the rendered image (0-1000)


class VlmPage(BaseModel):
    entities: list[VlmEntity] = Field(default_factory=list)
    references: list[VlmReference] = Field(default_factory=list)
    scale_text: str | None = None  # e.g. 'SCALE: 1/4" = 1\'-0"' from the title block


_INSTRUCTIONS = f"""\
You analyze architectural floor plan drawings.

Identify every physical entity relevant to building-code compliance:
{", ".join(t.value for t in AssetType if t != AssetType.OTHER)}.
Doors marked as fire/emergency exits are fire_exit, not door.

Drawing conventions — apply them strictly before classifying:
- Walls are thick double lines; solid or masonry wall sections are filled
  with diagonal cross-hatching. A hatched or filled wall segment is NEVER a
  window or a door. Never report a wall itself as an entity.
- window: a thin opening in a wall drawn as 2-3 parallel lines across the
  wall thickness (often with a sill line). A window's interior is always
  white/unhatched: if diagonal hatching or solid fill runs through the
  shape, it is wall — not a window. Small numbers printed on wall segments
  (e.g. '22') are wall-thickness callouts, not window marks.
- door: a wall opening with a quarter-circle swing arc. A garage door is a
  wide straight opening spanning most of a garage wall with no swing arc
  (usually a single thin panel line, wide enough for a vehicle) — classify
  it as door and name it 'GARAGE DOOR' unless the drawing names it.
  A garage almost always has at least one garage door on an exterior wall:
  inspect the garage perimeter for long, thin, unhatched openings before
  finishing — two side by side separated by a short hatched pier means two
  garage doors.
- wall piers: a short hatched or solid wall stub between two openings (for
  example between two garage doors) is wall — never a window.
- stair: a run of parallel treads, usually with a direction arrow.
- room: a named enclosed space bounded by walls. A callout/detail bubble — a
  small circle or hexagon holding a letter or number ('A', '1'), often sitting
  on a dashed or dotted section/detail cut line — is NOT a room and NOT a
  physical entity; never report it (as a room or anything else).

Only report an entity you are confident about; omit anything ambiguous
rather than guessing.

For each entity return:
- entity_type (from the list above).
- name: the entity's clean identifier exactly as printed — a room name
  ('GARAGE', 'CHAMBRE 2'), a door code ('D1'), or 'GARAGE DOOR' / 'FIRE
  EXIT'. NEVER include areas, dimension strings, wall-thickness numbers, or
  any other annotation text in the name. Empty string if unnamed.
  For a room, the name is its descriptive space name in words ('KITCHEN',
  'BEDROOM 2'). It is NEVER a callout or detail-marker bubble (a circled
  number or single letter such as '1' or 'D') or a door tag — those are not
  room names. If a room shows no descriptive name, return an empty string.
- floor_area_m2: for rooms, the printed floor area as a bare number (39.37
  from '39.37 m²'); null when not printed or not a room.
- mark: for a door or window, the schedule callout tag sitting on it — the
  circled letter or number that keys the door/window schedule ('A', '2'). It is
  the schedule key, not a separate entity; null for other types or when untagged.
- box: [ymin, xmin, ymax, xmax] normalized to 1000 (0-1000 scale) covering
  the entity's FULL PHYSICAL EXTENT:
  * room: the entire interior from wall to wall — never just the room's
    name text. A room's box is usually large.
  * door / window / garage door: the wall opening itself — from one jamb to
    the other, centered on the wall line (the glazing lines for a window).
    Box the gap in the wall, NOT the solid wall beside it and NOT the door's
    swing arc; the box must sit on the opening, not next to it.
  * stair: the complete flight of treads.

Reference markers link this sheet to others — return them in `references`:
- section marker: a heavy cut line ending in an arrow and a bubble split as
  detail-number over sheet-number (e.g. 1 / A3.0). kind='section'.
- detail marker: a dashed circle or box around a feature with a leader to a
  bubble reading number / sheet (e.g. 3 / A1.7). kind='detail'.
- elevation marker: a bubble with an arrow or triangle pointing at a wall, keyed
  to an interior-elevation sheet. kind='elevation'.
For each marker return: kind; detail_num (the number in the bubble); target_sheet
(the sheet number it points to, EXACTLY as printed, e.g. 'A3.0'); and box covering
the marker. Only report a marker whose target sheet number is actually printed on
the page — omit anything ambiguous.

Also read the drawing scale from the title block if present and return it
verbatim as scale_text (e.g. 'SCALE: 1/4" = 1\'-0"'), else null.
"""


def build_detection_agent(model) -> Agent:
    """VLM agent with an output validator: malformed boxes (inverted or
    out-of-range coordinates) are bounced back with ModelRetry instead of
    corrupting the overlay."""
    # temperature 0: detection should be reproducible run-to-run.
    # retries=2 (above the default 1): a busy or non-plan-view page often needs
    # a second correction before the boxes validate; the caller isolates the
    # page if even that fails, so exhausting retries never sinks the run.
    agent = Agent(
        model,
        output_type=VlmPage,
        instructions=_INSTRUCTIONS,
        model_settings={"temperature": 0.0},
        retries=2,
    )

    @agent.output_validator
    async def validate_boxes(output: VlmPage) -> VlmPage:
        problems: list[str] = []
        room_areas: list[float] = []
        for i, entity in enumerate(output.entities):
            ymin, xmin, ymax, xmax = entity.box
            where = f"entities[{i}] ({entity.entity_type.value} {entity.name!r})"
            if not (0 <= ymin < ymax <= 1000 and 0 <= xmin < xmax <= 1000):
                problems.append(
                    f"{where}: box {entity.box} is not [ymin, xmin, ymax, xmax] "
                    "on the 0-1000 scale with min < max"
                )
                continue
            if _AREA_IN_NAME.search(entity.name):
                problems.append(
                    f"{where}: name {entity.name!r} contains a printed area — "
                    "put the number in floor_area_m2 and keep name clean"
                )
            if entity.entity_type == AssetType.ROOM:
                room_areas.append((ymax - ymin) * (xmax - xmin))
        for i, ref in enumerate(output.references):
            ymin, xmin, ymax, xmax = ref.box
            if not (0 <= ymin < ymax <= 1000 and 0 <= xmin < xmax <= 1000):
                problems.append(
                    f"references[{i}] ({ref.kind} -> {ref.target_sheet!r}): box "
                    f"{ref.box} is not [ymin, xmin, ymax, xmax] on the 0-1000 scale "
                    "with min < max"
                )
        # Rooms boxed around their name text instead of their walls: on the
        # 0-1000 grid a text label is a sliver, while a plan's largest room
        # always covers well over 1.5% of the page.
        if room_areas and max(room_areas) < 15_000:
            problems.append(
                "every room box is label-sized — each room's box must cover "
                "the room's full interior from wall to wall, not its printed "
                "name"
            )
        if problems:
            raise ModelRetry(
                "Invalid entities — re-emit all entities corrected:\n"
                + "\n".join(problems)
            )
        return output

    return agent


async def detect_page(image_png: bytes, model) -> VlmPage:
    """Run the VLM over one rendered page; boxes converted to PDF points."""
    # PNG dimensions start at byte offset 16 (4 bytes width, 4 bytes height)
    img_width, img_height = struct.unpack(">II", image_png[16:24])
    agent = build_detection_agent(model)
    result = await agent.run(
        [
            "Detect all compliance-relevant entities on this floor plan page.",
            BinaryContent(data=image_png, media_type="image/png"),
        ]
    )
    def to_points(box: BBox) -> BBox:
        # De-normalize [0, 1000] → image pixels → PDF points.
        ymin, xmin, ymax, xmax = box
        return (
            (xmin / 1000.0) * img_width / RENDER_ZOOM,
            (ymin / 1000.0) * img_height / RENDER_ZOOM,
            (xmax / 1000.0) * img_width / RENDER_ZOOM,
            (ymax / 1000.0) * img_height / RENDER_ZOOM,
        )

    page = result.output
    scaled = []
    for entity in page.entities:
        name = _resolve_space_name(entity)
        if name is None:
            continue  # a bare callout marker mis-detected as a space — drop it
        scaled.append(entity.model_copy(update={"box": to_points(entity.box), "name": name}))
    refs = [ref.model_copy(update={"box": to_points(ref.box)}) for ref in page.references]
    return VlmPage(entities=scaled, references=refs, scale_text=page.scale_text)


class VlmSheetType(BaseModel):
    """A sheet's drawing type as read by the vision model — used only to route the
    page (skip / plan detector / elevation detector / schedule), never to decide
    compliance. The fallback for a page with no text layer, where the deterministic
    title-driven classifier has nothing to read."""

    sheet_type: SheetType


_SHEET_INSTRUCTIONS = """\
Classify this single architectural drawing sheet into exactly one type, reading its
title block and overall layout. Return one sheet_type:

- floor_plan: a top-down plan of rooms and walls for one level.
- elevation: an exterior vertical view of a facade (front / rear / side).
- section: a vertical cut through the building, floors and roof shown in profile.
- schedule: a table/matrix of doors, windows, finishes or fixtures (rows & columns).
- foundation: a foundation or footing plan.
- roof: a roof plan or roof-framing plan.
- site: a site, plot, survey or landscape plan.
- rcp_electrical: a reflected ceiling plan, or an electrical / lighting / power plan
  (ceiling grid, light fixtures, switches, outlets). Choose this over floor_plan
  whenever the sheet is about ceilings or electrical, even if the title says "PLAN".
- detail: a zoomed-in construction detail (a wall section, a stair or joint detail).
- cover_notes: a cover sheet, drawing index, general notes, specifications, or an
  abbreviations/legend page — no drawn building geometry to measure.
- other: none of the above, or genuinely ambiguous.

Precedence when a sheet mixes content:
- If it carries a door and/or window SCHEDULE — a table with MARK / SIZE columns
  listing openings — return schedule, EVEN IF the rest of the sheet is details or
  elevations. A schedule holds opening sizes that must not be skipped.
- Otherwise prefer the specific type over floor_plan when the sheet is clearly an
  elevation, section, or RCP/electrical."""


def build_sheet_classifier(model) -> Agent:
    """VLM agent that names a sheet's drawing type for routing. Temperature 0 for
    reproducibility; the output is constrained to the SheetType enum."""
    return Agent(
        model,
        output_type=VlmSheetType,
        instructions=_SHEET_INSTRUCTIONS,
        model_settings={"temperature": 0.0},
        retries=1,
    )


async def classify_sheet_page(image_png: bytes, model) -> SheetType:
    """Classify one rendered sheet image into a SheetType via the VLM — the no-text
    fallback for the deterministic title classifier."""
    agent = build_sheet_classifier(model)
    result = await agent.run(
        [
            "Classify this architectural drawing sheet.",
            BinaryContent(data=image_png, media_type="image/png"),
        ]
    )
    return result.output.sheet_type


def detect_from_labels(labels) -> VlmPage:
    """Deterministic stand-in for the VLM (PLANLINT_OFFLINE_SAMPLE=1): derives
    entities from CAD text labels. Door labels look like 'D1 36\"'; a
    'FIRE EXIT' label marks a fire exit. Used by tests and the offline demo
    path — never in real deployments."""
    entities: list[VlmEntity] = []
    references: list[VlmReference] = []
    scale_text = None
    for label in labels:
        text = label.text.strip()
        upper = text.upper()
        ref = _REF_LABEL.match(text)
        if "SCALE" in upper:
            scale_text = text
        elif ref:
            references.append(
                VlmReference(
                    kind="detail",  # offline can't read the glyph; default to detail
                    detail_num=ref.group(1),
                    target_sheet=ref.group(2).upper(),
                    box=label.bbox,
                )
            )
        elif "FIRE EXIT" in upper:
            entities.append(
                VlmEntity(entity_type=AssetType.FIRE_EXIT, name=text, box=label.bbox)
            )
        elif upper.startswith("D") and '"' in text:
            entities.append(VlmEntity(entity_type=AssetType.DOOR, name=text, box=label.bbox))
    return VlmPage(entities=entities, references=references, scale_text=scale_text)
