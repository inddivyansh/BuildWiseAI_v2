"""Integration tests for API routes using FastAPI TestClient."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, headers={"Authorization": "Bearer test-token"})


class TestHealthEndpoint:
    def test_health(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestGenerateEndpoint:
    def test_generate_basic(self):
        resp = client.post("/api/generate", json={
            "prompt": "3-bedroom bungalow on a 12x20m lot"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["generation_id"]
        assert data["floor_plan"] is not None
        assert len(data["floor_plan"]["spec"]["rooms"]) > 0

    def test_generate_with_plot_dims(self):
        resp = client.post("/api/generate", json={
            "prompt": "Simple studio apartment with open kitchen",
            "plot_width_m": 10.0,
            "plot_depth_m": 15.0,
        })
        data = resp.json()
        assert data["status"] == "completed"
        assert data["floor_plan"]["spec"]["plot"]["width_m"] == 10.0

    def test_generate_returns_walls(self):
        resp = client.post("/api/generate", json={
            "prompt": "3-bedroom bungalow on a 12x20m lot"
        })
        data = resp.json()
        assert len(data["floor_plan"]["walls"]) > 0

    def test_generate_returns_doors_and_windows(self):
        resp = client.post("/api/generate", json={
            "prompt": "3-bedroom bungalow on a 12x20m lot"
        })
        data = resp.json()
        assert len(data["floor_plan"]["doors"]) > 0
        assert len(data["floor_plan"]["windows"]) > 0

    def test_generate_rooms_have_polygons(self):
        resp = client.post("/api/generate", json={
            "prompt": "3-bedroom bungalow on a 12x20m lot"
        })
        data = resp.json()
        for room in data["floor_plan"]["spec"]["rooms"]:
            assert room["polygon"] is not None
            assert len(room["polygon"]) >= 4

    def test_generate_prompt_too_short(self):
        resp = client.post("/api/generate", json={"prompt": "hi"})
        assert resp.status_code == 422  # Pydantic validation

    def test_generate_studio_preset(self):
        resp = client.post("/api/generate", json={
            "prompt": "Simple studio apartment with open living"
        })
        data = resp.json()
        assert data["status"] == "completed"
        # Studio should have fewer rooms
        assert len(data["floor_plan"]["spec"]["rooms"]) <= 4


class TestEditEndpoint:
    def test_edit_basic(self):
        gen_resp = client.post("/api/generate", json={
            "prompt": "3-bedroom bungalow on a 12x20m lot"
        })
        assert gen_resp.status_code == 200
        gen_data = gen_resp.json()
        gen_id = gen_data["generation_id"]
        room_id = gen_data["floor_plan"]["spec"]["rooms"][0]["id"]
        resp = client.post("/api/edit", json={
            "generation_id": gen_id,
            "actions": [
                {
                    "action": "resize_room",
                    "target_room_id": room_id,
                    "parameters": {"width_m": 4.5, "height_m": 4.0},
                }
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["changes_summary"] is not None


class TestProjectsEndpoint:
    def test_create_and_list(self):
        # Create
        resp = client.post("/api/projects", json={"name": "Test House"})
        assert resp.status_code == 201
        data = resp.json()
        project_id = data["id"]
        assert data["name"] == "Test House"

        # List
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        projects = resp.json()
        assert any(p["id"] == project_id for p in projects)

        # Get
        resp = client.get(f"/api/projects/{project_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test House"

        # Delete
        resp = client.delete(f"/api/projects/{project_id}")
        assert resp.status_code == 204

    def test_get_nonexistent_project(self):
        resp = client.get("/api/projects/nonexistent")
        assert resp.status_code == 404


class TestExportEndpoint:
    def test_export_not_found(self):
        resp = client.get("/api/export/fake-id/svg")
        assert resp.status_code == 404

    def test_export_unsupported_format(self):
        resp = client.get("/api/export/fake-id/xyz")
        assert resp.status_code == 400


class TestComplianceEndpoint:
    def test_compliance_check(self):
        gen_resp = client.post("/api/generate", json={
            "prompt": "3-bedroom bungalow on a 12x20m lot"
        })
        assert gen_resp.status_code == 200
        floor_plan = gen_resp.json()["floor_plan"]
        resp = client.post("/api/compliance/check", json={
            "floor_plan": floor_plan,
            "locale": "CA-QC",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "is_compliant" in data
        assert data["jurisdiction"] == "CA-QC"
