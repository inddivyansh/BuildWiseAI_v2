"""End-to-end API linkage tests verifying all frontend client calls against the unified backend.

Simulates the exact HTTP requests made by `BuildwiseAIv2/frontend/src/lib/api.ts`
against the unified FastAPI application (`planlint.api.main:create_app`).
"""

import io
import json
import httpx
import pytest

from planlint.api.main import create_app
from tests.conftest import FakeEmbedder
from tests.test_api import ApiFakeRepository


@pytest.fixture
async def e2e_client(tmp_path, monkeypatch):
    from planlint.config import settings

    monkeypatch.setattr(settings, "planlint_data_dir", tmp_path)
    monkeypatch.setattr(settings, "planlint_offline_sample", True)

    repo = ApiFakeRepository()
    embedder = FakeEmbedder()
    app = create_app(repo=repo, embedder=embedder)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": "Bearer test-token"},
    ) as http:
        async with app.router.lifespan_context(app):
            yield http, repo


async def test_health_endpoints(e2e_client):
    """Verify health endpoints accessible with and without /api prefix."""
    http, _ = e2e_client
    resp1 = await http.get("/health")
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "ok"

    resp2 = await http.get("/api/health")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "ok"


async def test_dual_routing_project_lifecycle(e2e_client):
    """Verify projects CRUD accessible through both /projects and /api/projects (Next.js proxy)."""
    http, _ = e2e_client

    # 1. Create project via /projects
    create_resp = await http.post("/projects", json={"name": "Skyline Tower", "description": "Phase 1"})
    assert create_resp.status_code == 201
    project = create_resp.json()
    pid = project["id"]
    assert project["name"] == "Skyline Tower"

    # 2. List via /projects
    list1 = await http.get("/projects")
    assert list1.status_code == 200
    assert any(p["id"] == pid for p in list1.json())

    # 3. List via /api/projects (mirrors frontend next.config.js rewrite)
    list2 = await http.get("/api/projects")
    assert list2.status_code == 200
    assert any(p["id"] == pid for p in list2.json())

    # 4. Get project via both paths
    get1 = await http.get(f"/projects/{pid}")
    get2 = await http.get(f"/api/projects/{pid}")
    assert get1.status_code == 200
    assert get2.status_code == 200
    assert get1.json()["name"] == "Skyline Tower"
    assert get2.json()["name"] == "Skyline Tower"

    # 5. Patch project
    patch_resp = await http.patch(f"/projects/{pid}", json={"name": "Skyline Tower North"})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Skyline Tower North"


async def test_generation_and_blueprint_persistence(e2e_client):
    """Test floor plan generation, durable Neo4j blueprint persistence, and blueprint CRUD."""
    http, _ = e2e_client

    # Create project first
    proj_resp = await http.post("/projects", json={"name": "Residential Villa"})
    assert proj_resp.status_code == 201
    pid = proj_resp.json()["id"]

    # Generate floor plan scoped to project_id
    gen_resp = await http.post(
        "/api/generate",
        json={
            "prompt": "Modern 3-bedroom bungalow on a 15x25m plot with patio",
            "project_id": pid,
            "plot_width_m": 15.0,
            "plot_depth_m": 25.0,
            "locale": "CA-QC",
        },
    )
    assert gen_resp.status_code == 200
    gen_data = gen_resp.json()
    assert gen_data["status"] == "completed"
    assert gen_data["generation_id"]
    assert gen_data["floor_plan"] is not None
    assert gen_data["project_id"] == pid
    bp_id = gen_data["generation_id"]

    # Verify blueprint is returned in project blueprint history
    bp_list_resp = await http.get(f"/api/projects/{pid}/blueprints")
    assert bp_list_resp.status_code == 200
    bps = bp_list_resp.json()
    assert len(bps) >= 1
    assert any(b["id"] == bp_id for b in bps)

    # Get single blueprint detail with full floor_plan geometry
    bp_detail_resp = await http.get(f"/api/projects/{pid}/blueprints/{bp_id}")
    assert bp_detail_resp.status_code == 200
    bp_detail = bp_detail_resp.json()
    assert bp_detail["id"] == bp_id
    assert "rooms" in bp_detail["floor_plan"]["spec"]


async def test_spatial_editing_workflow(e2e_client):
    """Test editing a generated floor plan via structured actions."""
    http, _ = e2e_client

    # Generate baseline plan
    gen_resp = await http.post(
        "/api/generate",
        json={"prompt": "2-bedroom compact apartment with kitchen and bath"},
    )
    assert gen_resp.status_code == 200
    gen_data = gen_resp.json()
    gen_id = gen_data["generation_id"]
    room_id = gen_data["floor_plan"]["spec"]["rooms"][0]["id"]

    # Apply edit action
    edit_resp = await http.post(
        "/api/edit",
        json={
            "generation_id": gen_id,
            "actions": [
                {
                    "action": "resize_room",
                    "target_room_id": room_id,
                    "parameters": {"width_m": 5.0, "height_m": 4.5},
                }
            ],
        },
    )
    assert edit_resp.status_code == 200
    edit_data = edit_resp.json()
    assert edit_data["status"] == "completed"
    assert edit_data["changes_summary"] is not None


async def test_export_formats_and_cad_download(e2e_client):
    """Test CAD/BIM export format endpoints matching frontend ExportMenu."""
    http, _ = e2e_client

    # Formats catalog
    fmt_resp = await http.get("/api/export/formats")
    assert fmt_resp.status_code == 200
    formats = fmt_resp.json()["formats"]
    format_ids = [f["id"] for f in formats]
    assert "dxf" in format_ids
    assert "svg" in format_ids

    # Generate plan to export
    gen_resp = await http.post("/api/generate", json={"prompt": "Single family house"})
    assert gen_resp.status_code == 200
    gen_id = gen_resp.json()["generation_id"]

    # Export SVG
    svg_resp = await http.get(f"/api/export/{gen_id}/svg")
    assert svg_resp.status_code == 200
    assert svg_resp.headers["content-type"].startswith("image/svg+xml")

    # Export DXF
    dxf_resp = await http.get(f"/api/export/{gen_id}/dxf")
    assert dxf_resp.status_code == 200
    assert len(dxf_resp.content) > 0


async def test_compliance_engine_endpoints(e2e_client):
    """Test compliance checker and zoning specification queries."""
    http, _ = e2e_client

    # Zones list
    zones_resp = await http.get("/api/compliance/zones")
    assert zones_resp.status_code == 200
    assert "zones" in zones_resp.json()

    # Generate plan and check compliance
    gen_resp = await http.post("/api/generate", json={"prompt": "Standard bungalow"})
    assert gen_resp.status_code == 200
    floor_plan = gen_resp.json()["floor_plan"]

    check_resp = await http.post(
        "/api/compliance/check",
        json={"floor_plan": floor_plan, "locale": "CA-QC"},
    )
    assert check_resp.status_code == 200
    check_data = check_resp.json()
    assert "is_compliant" in check_data
    assert "violations" in check_data
    assert check_data["jurisdiction"] == "CA-QC"


async def test_document_ingestion_and_pdf_stream(e2e_client):
    """Test uploading drawings/codebooks and retrieving PDF stream for PlanViewer."""
    http, repo = e2e_client

    # Create project
    p_resp = await http.post("/projects", json={"name": "Audit Test Project"})
    pid = p_resp.json()["id"]

    # Upload floorplan PDF
    fake_pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"
    upload_resp = await http.post(
        f"/projects/{pid}/documents",
        params={"kind": "floorplan"},
        files={"file": ("drawing_A101.pdf", fake_pdf, "application/pdf")},
    )
    assert upload_resp.status_code in (200, 201)
    doc = upload_resp.json()
    doc_id = doc["id"]

    # List documents
    docs_resp = await http.get(f"/projects/{pid}/documents")
    assert docs_resp.status_code == 200
    assert any(d["id"] == doc_id for d in docs_resp.json())

    # Stream PDF for client PlanViewer
    stream_resp = await http.get(f"/documents/{doc_id}/pdf")
    assert stream_resp.status_code == 200
    assert stream_resp.headers["content-type"] == "application/pdf"
    assert stream_resp.content == fake_pdf

    # Delete document
    del_doc = await http.delete(f"/documents/{doc_id}")
    assert del_doc.status_code in (200, 204)
