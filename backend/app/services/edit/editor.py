"""
Structured editing of floor plans.

Applies deterministic edit actions to a FloorPlanSpec, then re-runs
the constraint solver to produce an updated layout.  No LLM needed.
"""

import copy
import uuid

import structlog

from app.schemas.floor_plan import (
    AdjacencyRequirement,
    FloorPlanSpec,
    GeneratedFloorPlan,
    Relationship,
    Room,
    RoomType,
)
from app.schemas.generation import EditAction
from app.core.pipeline import GenerationPipeline

logger = structlog.get_logger()


async def apply_edits(
    spec: FloorPlanSpec,
    actions: list[EditAction],
    pipeline: GenerationPipeline | None = None,
) -> tuple[GeneratedFloorPlan | None, list[str]]:
    """
    Apply a list of edit actions to a FloorPlanSpec, then re-generate.

    Returns (floor_plan, changes_summary_list).
    """
    spec = copy.deepcopy(spec)
    changes: list[str] = []

    for action in actions:
        result = _apply_action(spec, action)
        if result:
            changes.append(result)

    if not changes:
        changes.append("No edits applied — actions did not match any rooms.")

    # Re-generate layout with modified spec
    pipeline = pipeline or GenerationPipeline()
    result = await pipeline.generate_from_spec(
        spec, generator="constraint_solver"
    )

    return result.get("floor_plan"), changes


def _apply_action(spec: FloorPlanSpec, action: EditAction) -> str | None:
    """Apply a single edit action. Returns a description of what changed."""
    handler = _HANDLERS.get(action.action)
    if handler is None:
        logger.warning("unknown_edit_action", action=action.action)
        return None
    return handler(spec, action)


# --- Action handlers ---

def _resize(spec: FloorPlanSpec, action: EditAction) -> str | None:
    room = _find_room(spec, action.target_room_id)
    if not room:
        return None
    new_area = action.parameters.get("target_area_sqm")
    scale = action.parameters.get("scale")
    if new_area:
        old = room.target_area_sqm
        room.target_area_sqm = float(new_area)
        return f"Resized {room.label} from {old:.1f} to {room.target_area_sqm:.1f} m²"
    elif scale:
        old = room.target_area_sqm
        room.target_area_sqm = round(old * float(scale), 1)
        return f"Scaled {room.label} by {scale}x ({old:.1f} → {room.target_area_sqm:.1f} m²)"
    return None


def _add_room(spec: FloorPlanSpec, action: EditAction) -> str | None:
    params = action.parameters
    room_type = params.get("type", "other")
    label = params.get("label", room_type.replace("_", " ").title())
    area = float(params.get("target_area_sqm", 10))

    # Generate unique ID
    base_id = f"{room_type}_new"
    existing_ids = {r.id for r in spec.rooms}
    room_id = base_id
    counter = 2
    while room_id in existing_ids:
        room_id = f"{room_type}_{counter}"
        counter += 1

    try:
        rtype = RoomType(room_type)
    except ValueError:
        rtype = RoomType.OTHER

    new_room = Room(
        id=room_id, type=rtype, label=label,
        target_area_sqm=area, floor=0,
    )
    spec.rooms.append(new_room)

    # Add adjacency if specified
    adjacent_to = params.get("adjacent_to")
    if adjacent_to and _find_room(spec, adjacent_to):
        spec.adjacency.append(AdjacencyRequirement(
            room_a_id=room_id, room_b_id=adjacent_to,
            relationship=Relationship.ADJACENT,
        ))

    return f"Added {label} ({area:.0f} m²)"


def _remove_room(spec: FloorPlanSpec, action: EditAction) -> str | None:
    room = _find_room(spec, action.target_room_id)
    if not room:
        return None
    label = room.label
    spec.rooms = [r for r in spec.rooms if r.id != room.id]
    # Remove adjacency requirements involving this room
    spec.adjacency = [
        a for a in spec.adjacency
        if a.room_a_id != room.id and a.room_b_id != room.id
    ]
    return f"Removed {label}"


def _swap(spec: FloorPlanSpec, action: EditAction) -> str | None:
    room_a = _find_room(spec, action.target_room_id)
    other_id = action.parameters.get("swap_with")
    room_b = _find_room(spec, other_id) if other_id else None
    if not room_a or not room_b:
        return None
    # Swap areas (the solver will reposition them)
    room_a.target_area_sqm, room_b.target_area_sqm = (
        room_b.target_area_sqm, room_a.target_area_sqm,
    )
    room_a.label, room_b.label = room_b.label, room_a.label
    room_a.type, room_b.type = room_b.type, room_a.type
    return f"Swapped {room_a.label} and {room_b.label}"


def _merge(spec: FloorPlanSpec, action: EditAction) -> str | None:
    room_a = _find_room(spec, action.target_room_id)
    other_id = action.parameters.get("merge_with")
    room_b = _find_room(spec, other_id) if other_id else None
    if not room_a or not room_b:
        return None
    combined_area = room_a.target_area_sqm + room_b.target_area_sqm
    label_a, label_b = room_a.label, room_b.label
    room_a.target_area_sqm = combined_area
    spec.rooms = [r for r in spec.rooms if r.id != room_b.id]
    spec.adjacency = [
        a for a in spec.adjacency
        if a.room_a_id != room_b.id and a.room_b_id != room_b.id
    ]
    return f"Merged {label_b} into {label_a} ({combined_area:.0f} m²)"


def _split(spec: FloorPlanSpec, action: EditAction) -> str | None:
    room = _find_room(spec, action.target_room_id)
    if not room:
        return None
    ratio = float(action.parameters.get("ratio", 0.5))
    new_type = action.parameters.get("new_type", room.type.value)
    new_label = action.parameters.get("new_label", f"{room.label} (split)")

    area_a = round(room.target_area_sqm * ratio, 1)
    area_b = round(room.target_area_sqm * (1 - ratio), 1)
    room.target_area_sqm = area_a

    try:
        rtype = RoomType(new_type)
    except ValueError:
        rtype = room.type

    existing_ids = {r.id for r in spec.rooms}
    new_id = f"{new_type}_split"
    counter = 2
    while new_id in existing_ids:
        new_id = f"{new_type}_{counter}"
        counter += 1

    new_room = Room(
        id=new_id, type=rtype, label=new_label,
        target_area_sqm=area_b, floor=room.floor,
    )
    spec.rooms.append(new_room)
    spec.adjacency.append(AdjacencyRequirement(
        room_a_id=room.id, room_b_id=new_id,
        relationship=Relationship.ADJACENT,
    ))
    return f"Split {room.label} into {area_a:.0f} + {area_b:.0f} m²"


def _find_room(spec: FloorPlanSpec, room_id: str | None) -> Room | None:
    if not room_id:
        return None
    for r in spec.rooms:
        if r.id == room_id:
            return r
    return None


_HANDLERS = {
    "resize": _resize,
    "add_room": _add_room,
    "remove_room": _remove_room,
    "swap": _swap,
    "merge": _merge,
    "split": _split,
}
