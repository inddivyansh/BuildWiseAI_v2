"""Tests for SVG/DXF export services and export API route."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.store import plan_store
from app.services.export.svg_exporter import export_svg
from app.services.export.dxf_exporter import export_dxf
from app.services.generation.mock import generate_mock_floor_plan

client = TestClient(app)


@pytest.fixture
def mock_plan():
    """Generate a mock floor plan for export testing."""
    return generate_mock_floor_plan(
        prompt="3-bedroom bungalow test plan",
        plot_width_m=12.0,
        plot_depth_m=20.0,
    )


# --- SVG Exporter ---

class TestSVGExporter:
    def test_exports_valid_svg(self, mock_plan):
        svg = export_svg(mock_plan)
        assert svg.startswith("<?xml")
        assert "<svg" in svg
        assert "</svg>" in svg

    def test_svg_contains_rooms(self, mock_plan):
        svg = export_svg(mock_plan)
        # Should have polygon elements for rooms
        assert "<polygon" in svg
        # Should have room labels
        for room in mock_plan.spec.rooms:
            assert room.label in svg

    def test_svg_contains_area_labels(self, mock_plan):
        svg = export_svg(mock_plan)
        assert "m²" in svg

    def test_svg_contains_plot_boundary(self, mock_plan):
        svg = export_svg(mock_plan)
        # Plot boundary is a dashed rect
        assert "stroke-dasharray" in svg

    def test_svg_contains_walls(self, mock_plan):
        svg = export_svg(mock_plan)
        # Walls rendered as lines
        assert "<line" in svg

    def test_svg_contains_title(self, mock_plan):
        svg = export_svg(mock_plan)
        assert "Floor Plan" in svg
        assert "m²" in svg

    def test_svg_has_dimensions(self, mock_plan):
        svg = export_svg(mock_plan)
        assert "12.0 m" in svg
        assert "20.0 m" in svg


# --- DXF Exporter ---

class TestDXFExporter:
    def test_exports_valid_dxf(self, mock_plan):
        dxf_bytes = export_dxf(mock_plan)
        assert isinstance(dxf_bytes, bytes)
        assert len(dxf_bytes) > 100
        # DXF files start with a section marker
        content = dxf_bytes.decode("utf-8", errors="replace")
        assert "SECTION" in content

    def test_dxf_has_layers(self, mock_plan):
        import ezdxf
        import io
        dxf_bytes = export_dxf(mock_plan)
        doc = ezdxf.read(io.StringIO(dxf_bytes.decode("utf-8")))
        layer_names = [l.dxf.name for l in doc.layers]
        assert "ROOMS" in layer_names
        assert "WALLS_EXTERIOR" in layer_names
        assert "WALLS_INTERIOR" in layer_names
        assert "DOORS" in layer_names
        assert "WINDOWS" in layer_names
        assert "PLOT" in layer_names
        assert "ANNOTATIONS" in layer_names

    def test_dxf_has_room_polygons(self, mock_plan):
        import ezdxf
        import io
        dxf_bytes = export_dxf(mock_plan)
        doc = ezdxf.read(io.StringIO(dxf_bytes.decode("utf-8")))
        msp = doc.modelspace()
        # Count polylines on ROOMS layer
        room_polys = [e for e in msp if e.dxf.layer == "ROOMS" and e.dxftype() == "LWPOLYLINE"]
        assert len(room_polys) > 0

    def test_dxf_has_annotations(self, mock_plan):
        import ezdxf
        import io
        dxf_bytes = export_dxf(mock_plan)
        doc = ezdxf.read(io.StringIO(dxf_bytes.decode("utf-8")))
        msp = doc.modelspace()
        texts = [e for e in msp if e.dxftype() == "TEXT"]
        assert len(texts) > 0
        # Should include room labels
        text_values = [e.dxf.text for e in texts]
        assert any("m²" in t for t in text_values)

    def test_dxf_save_to_file(self, mock_plan, tmp_path):
        out = tmp_path / "test.dxf"
        dxf_bytes = export_dxf(mock_plan, output_path=out)
        assert out.exists()
        assert out.stat().st_size == len(dxf_bytes)


# --- Plan Store ---

class TestPlanStore:
    def test_put_and_get(self, mock_plan):
        plan_store.put(mock_plan)
        retrieved = plan_store.get(mock_plan.generation_id)
        assert retrieved is not None
        assert retrieved.generation_id == mock_plan.generation_id

    def test_get_nonexistent(self):
        assert plan_store.get("nonexistent-id") is None

    def test_eviction(self, mock_plan):
        from app.core.store import PlanStore
        store = PlanStore(max_entries=2)
        plans = []
        for i in range(3):
            p = generate_mock_floor_plan(f"test plan number {i} for eviction test")
            plans.append(p)
            store.put(p)
        # First plan should be evicted
        assert store.get(plans[0].generation_id) is None
        # Last two should remain
        assert store.get(plans[1].generation_id) is not None
        assert store.get(plans[2].generation_id) is not None


# --- Export API ---

class TestExportAPI:
    def test_export_svg_via_api(self, mock_plan):
        # Store the plan first
        plan_store.put(mock_plan)
        resp = client.get(f"/api/export/{mock_plan.generation_id}/svg")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/svg+xml"
        assert "<svg" in resp.text

    def test_export_dxf_via_api(self, mock_plan):
        plan_store.put(mock_plan)
        resp = client.get(f"/api/export/{mock_plan.generation_id}/dxf")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/dxf"
        assert len(resp.content) > 100

    def test_export_unknown_plan(self):
        resp = client.get("/api/export/nonexistent-id/svg")
        assert resp.status_code == 404

    def test_export_unsupported_format(self, mock_plan):
        plan_store.put(mock_plan)
        resp = client.get(f"/api/export/{mock_plan.generation_id}/pdf")
        assert resp.status_code == 400

    def test_export_formats_list(self):
        resp = client.get("/api/export/formats")
        assert resp.status_code == 200
        data = resp.json()
        fmt_ids = [f["id"] for f in data["formats"]]
        assert "svg" in fmt_ids
        assert "dxf" in fmt_ids

    def test_generate_then_export_svg(self):
        """E2E: generate a plan, then export it as SVG."""
        gen_resp = client.post("/api/generate", json={
            "prompt": "3-bedroom bungalow on a 12x20m lot"
        })
        assert gen_resp.status_code == 200
        gen_id = gen_resp.json()["generation_id"]

        export_resp = client.get(f"/api/export/{gen_id}/svg")
        assert export_resp.status_code == 200
        assert "<svg" in export_resp.text

    def test_generate_then_export_dxf(self):
        """E2E: generate a plan, then export it as DXF."""
        gen_resp = client.post("/api/generate", json={
            "prompt": "Simple studio apartment with open living area"
        })
        gen_id = gen_resp.json()["generation_id"]

        export_resp = client.get(f"/api/export/{gen_id}/dxf")
        assert export_resp.status_code == 200
        assert len(export_resp.content) > 100
