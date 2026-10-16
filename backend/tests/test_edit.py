"""Tests for the edit service and edit API route."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.store import plan_store
from app.schemas.floor_plan import (
    AdjacencyRequirement,
    FloorPlanSpec,
    PlotConstraints,
    Relationship,
    Room,
    RoomType,
    Setbacks,
)
from app.schemas.generation import EditAction
from app.services.edit.editor import apply_edits
from app.services.generation.mock import generate_mock_floor_plan

client = TestClient(app)


def _simple_spec() -> FloorPlanSpec:
    return FloorPlanSpec(
        rooms=[
            Room(id="living_1", type=RoomType.LIVING, label="Living Room", target_area_sqm=25),
            Room(id="kitchen_1", type=RoomType.KITCHEN, label="Kitchen", target_area_sqm=12),
            Room(id="bed_1", type=RoomType.BEDROOM, label="Bedroom", target_area_sqm=14),
            Room(id="bath_1", type=RoomType.BATHROOM, label="Bathroom", target_area_sqm=5),
        ],
        adjacency=[
            AdjacencyRequirement(room_a_id="kitchen_1", room_b_id="living_1", relationship=Relationship.ADJACENT),
        ],
        plot=PlotConstraints(
            width_m=12.0, depth_m=20.0,
            setbacks=Setbacks(front_m=2.0, rear_m=3.0, left_m=1.5, right_m=1.5),
        ),
    )


# --- Editor Service Tests ---

class TestEditorActions:
    @pytest.mark.asyncio
    async def test_resize_room(self):
        spec = _simple_spec()
        actions = [EditAction(action="resize", target_room_id="living_1", parameters={"target_area_sqm": 30})]
        plan, changes = await apply_edits(spec, actions)
        assert plan is not None
        assert "Resized" in changes[0]
        # The spec should have the new target area
        living = next(r for r in plan.spec.rooms if r.id == "living_1")
        assert living.target_area_sqm == 30

    @pytest.mark.asyncio
    async def test_resize_by_scale(self):
        spec = _simple_spec()
        actions = [EditAction(action="resize", target_room_id="kitchen_1", parameters={"scale": 1.5})]
        plan, changes = await apply_edits(spec, actions)
        assert plan is not None
        assert "Scaled" in changes[0]
        kitchen = next(r for r in plan.spec.rooms if r.id == "kitchen_1")
        assert kitchen.target_area_sqm == 18.0  # 12 * 1.5

    @pytest.mark.asyncio
    async def test_add_room(self):
        spec = _simple_spec()
        actions = [EditAction(
            action="add_room",
            parameters={"type": "office", "label": "Home Office", "target_area_sqm": 10, "adjacent_to": "living_1"},
        )]
        plan, changes = await apply_edits(spec, actions)
        assert plan is not None
        assert "Added Home Office" in changes[0]
        assert len(plan.spec.rooms) == 5
        office = next(r for r in plan.spec.rooms if r.type == RoomType.OFFICE)
        assert office.target_area_sqm == 10

    @pytest.mark.asyncio
    async def test_remove_room(self):
        spec = _simple_spec()
        actions = [EditAction(action="remove_room", target_room_id="bath_1")]
        plan, changes = await apply_edits(spec, actions)
        assert plan is not None
        assert "Removed Bathroom" in changes[0]
        assert len(plan.spec.rooms) == 3
        assert not any(r.id == "bath_1" for r in plan.spec.rooms)

    @pytest.mark.asyncio
    async def test_swap_rooms(self):
        spec = _simple_spec()
        actions = [EditAction(
            action="swap", target_room_id="living_1",
            parameters={"swap_with": "kitchen_1"},
        )]
        plan, changes = await apply_edits(spec, actions)
        assert plan is not None
        assert "Swapped" in changes[0]

    @pytest.mark.asyncio
    async def test_merge_rooms(self):
        spec = _simple_spec()
        actions = [EditAction(
            action="merge", target_room_id="living_1",
            parameters={"merge_with": "kitchen_1"},
        )]
        plan, changes = await apply_edits(spec, actions)
        assert plan is not None
        assert "Merged" in changes[0]
        assert len(plan.spec.rooms) == 3
        living = next(r for r in plan.spec.rooms if r.id == "living_1")
        assert living.target_area_sqm == 37  # 25 + 12

    @pytest.mark.asyncio
    async def test_split_room(self):
        spec = _simple_spec()
        actions = [EditAction(
            action="split", target_room_id="living_1",
            parameters={"ratio": 0.6, "new_type": "office", "new_label": "Office"},
        )]
        plan, changes = await apply_edits(spec, actions)
        assert plan is not None
        assert "Split" in changes[0]
        assert len(plan.spec.rooms) == 5
        living = next(r for r in plan.spec.rooms if r.id == "living_1")
        assert living.target_area_sqm == 15.0  # 25 * 0.6

    @pytest.mark.asyncio
    async def test_multiple_actions(self):
        spec = _simple_spec()
        actions = [
            EditAction(action="resize", target_room_id="living_1", parameters={"target_area_sqm": 30}),
            EditAction(action="remove_room", target_room_id="bath_1"),
            EditAction(action="add_room", parameters={"type": "half_bath", "label": "Powder Room", "target_area_sqm": 3}),
        ]
        plan, changes = await apply_edits(spec, actions)
        assert plan is not None
        assert len(changes) == 3
        assert len(plan.spec.rooms) == 4  # removed 1, added 1

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        spec = _simple_spec()
        actions = [EditAction(action="fly_rooms_into_space", parameters={})]
        plan, changes = await apply_edits(spec, actions)
        assert plan is not None
        assert "No edits applied" in changes[0]

    @pytest.mark.asyncio
    async def test_nonexistent_room(self):
        spec = _simple_spec()
        actions = [EditAction(action="resize", target_room_id="nonexistent", parameters={"target_area_sqm": 20})]
        plan, changes = await apply_edits(spec, actions)
        assert plan is not None
        assert "No edits applied" in changes[0]


# --- Edit API Tests ---

class TestEditAPI:
    def test_edit_with_prompt_fallback(self):
        resp = client.post("/api/edit", json={
            "generation_id": "fake-id",
            "prompt": "Make the master bedroom bigger and add a walk-in closet",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["changes_summary"] is not None

    def test_edit_with_structured_actions(self):
        # First generate a plan
        gen_resp = client.post("/api/generate", json={
            "prompt": "3-bedroom bungalow on a 12x20m lot"
        })
        gen_id = gen_resp.json()["generation_id"]

        # Get a room ID from the generated plan
        rooms = gen_resp.json()["floor_plan"]["spec"]["rooms"]
        first_room_id = rooms[0]["id"]

        # Now edit it
        resp = client.post("/api/edit", json={
            "generation_id": gen_id,
            "actions": [
                {"action": "resize", "target_room_id": first_room_id, "parameters": {"scale": 1.2}},
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert "Scaled" in data["changes_summary"]

    def test_generate_edit_export_flow(self):
        """E2E: generate → edit → export."""
        # Generate
        gen_resp = client.post("/api/generate", json={
            "prompt": "Simple studio apartment with open living area"
        })
        gen_id = gen_resp.json()["generation_id"]
        rooms = gen_resp.json()["floor_plan"]["spec"]["rooms"]
        first_room_id = rooms[0]["id"]

        # Edit
        edit_resp = client.post("/api/edit", json={
            "generation_id": gen_id,
            "actions": [
                {"action": "add_room", "parameters": {"type": "office", "label": "Office", "target_area_sqm": 8}},
            ],
        })
        new_gen_id = edit_resp.json()["generation_id"]
        assert edit_resp.json()["status"] == "completed"

        # Export the edited plan
        export_resp = client.get(f"/api/export/{new_gen_id}/svg")
        assert export_resp.status_code == 200
        assert "<svg" in export_resp.text
