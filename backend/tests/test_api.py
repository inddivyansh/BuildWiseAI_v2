"""API tests over the ASGI app with the in-memory repository injected."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from planlint.api.main import create_app
from planlint.models import CheckResult, Constraint, PhysicalAsset, RegulationClause  # noqa: F401
from tests.conftest import FakeEmbedder, FakeRepository


class ApiFakeRepository(FakeRepository):
    """Extends the pipeline fake with the project/document/results methods
    the routes need."""

    def __init__(self):
        super().__init__()
        self.projects: dict[str, dict] = {}
        self.blueprints: dict[str, dict] = {}

    async def create_project(self, project_id: str, name: str, description: str | None = None, **kwargs) -> None:
        self.projects[project_id] = {
            "id": project_id,
            "name": name,
            "description": description,
            "created_at": "2026-10-16T00:00:00Z",
            "user_id": kwargs.get("user_id"),
        }

    async def list_projects(self, **kwargs):
        return [
            {
                **p,
                "document_count": sum(1 for d in self.documents.values() if d["project_id"] == p["id"]),
                "blueprint_count": 0,
            }
            for p in self.projects.values()
        ]

    async def create_document(self, document_id, project_id, kind, filename, path, **kwargs) -> None:
        self.documents[document_id] = {
            "id": document_id, "project_id": project_id, "kind": kind,
            "filename": filename, "path": path, "ingested": True,  # skip real ingestion
            "user_id": kwargs.get("user_id"),
        }

    async def get_document(self, document_id, **kwargs):
        return self.documents.get(document_id)

    async def get_project(self, project_id, **kwargs):
        p = self.projects.get(project_id)
        if p is None:
            return None
        return {
            **p,
            "document_count": sum(1 for d in self.documents.values() if d["project_id"] == project_id),
            "blueprint_count": 0,
        }

    async def update_project(self, project_id: str, name: str | None = None, description: str | None = None, **kwargs):
        p = self.projects.get(project_id)
        if p is None:
            return None
        if name is not None:
            p["name"] = name
        if description is not None:
            p["description"] = description
        return await self.get_project(project_id)

    async def list_runs(self, project_id: str, **kwargs):
        if project_id not in self.projects:
            return []
        run_ids = {v.get("run_id") for v in self.verdicts if v.get("run_id")}
        return [
            {
                "run_id": rid,
                "checked_at": "2026-10-16T00:00:00Z",
                "verdict_count": sum(1 for v in self.verdicts if v.get("run_id") == rid),
                "complies_count": 0,
                "violates_count": 0,
                "review_count": 0,
            }
            for rid in run_ids
        ]

    async def delete_project(self, project_id, **kwargs):
        if project_id not in self.projects:
            return None
        del self.projects[project_id]
        owned = [d_id for d_id, d in self.documents.items() if d["project_id"] == project_id]
        return [self.documents.pop(d_id)["path"] for d_id in owned]

    async def delete_document(self, document_id, **kwargs):
        document = self.documents.pop(document_id, None)
        if document is None:
            return None
        return {
            "path": document["path"],
            "kind": document["kind"],
            "project_id": document["project_id"],
        }

    async def results_payload(self, project_id, run_id=None, **kwargs):
        if project_id not in self.projects:
            return {}
        return {
            "project": self.projects[project_id],
            "documents": [d for d in self.documents.values() if d["project_id"] == project_id],
            "sheets": [],
            "clauses": [],
        }

    async def set_document_manual_scale(self, document_id, scale_text, **kwargs) -> None:
        self.documents[document_id]["manual_scale_text"] = scale_text
        self.documents[document_id]["ingested"] = False

    async def create_blueprint(
        self, blueprint_id, project_id, floor_plan_json, generator_model, total_area_sqm, compliance_json=None, user_id=None, **kwargs
    ) -> None:
        self.blueprints[blueprint_id] = {
            "id": blueprint_id,
            "project_id": project_id,
            "floor_plan": floor_plan_json,
            "generator_model": generator_model,
            "total_area_sqm": total_area_sqm,
            "compliance": compliance_json,
            "created_at": "2026-10-16T00:00:00Z",
            "user_id": user_id,
        }

    async def get_blueprint(self, blueprint_id, user_id=None, **kwargs) -> dict | None:
        return self.blueprints.get(blueprint_id)

    async def list_blueprints(self, project_id, user_id=None, **kwargs) -> list[dict]:
        return [
            {
                "id": b["id"],
                "generator_model": b["generator_model"],
                "total_area_sqm": b["total_area_sqm"],
                "created_at": b["created_at"],
            }
            for b in self.blueprints.values()
            if b["project_id"] == project_id
        ]

    async def delete_blueprint(self, blueprint_id, project_id=None, user_id=None, **kwargs) -> bool:
        if blueprint_id in self.blueprints:
            del self.blueprints[blueprint_id]
            return True
        return False


@pytest.fixture
async def client(tmp_path, monkeypatch):
    from planlint.config import settings

    monkeypatch.setattr(settings, "planlint_data_dir", tmp_path)
    monkeypatch.setattr(settings, "planlint_offline_sample", True)
    app = create_app(repo=ApiFakeRepository(), embedder=FakeEmbedder())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": "Bearer test-token"},
    ) as http:
        async with app.router.lifespan_context(app):
            yield http, app


async def test_create_and_list_projects(client):
    http, _ = client
    created = await http.post("/projects", json={"name": "Tower A", "description": "High-rise"})
    assert created.status_code == 201
    pid = created.json()["id"]
    listed = await http.get("/projects")
    assert listed.json()[0]["name"] == "Tower A"

    # Test single project retrieve (GET /projects/{id})
    got = await http.get(f"/projects/{pid}")
    assert got.status_code == 200
    assert got.json()["name"] == "Tower A"
    assert got.json()["description"] == "High-rise"

    # Test project update (PATCH /projects/{id})
    patched = await http.patch(f"/projects/{pid}", json={"name": "Tower A Renamed"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Tower A Renamed"

    # Verify updated state via GET
    got_updated = await http.get(f"/projects/{pid}")
    assert got_updated.json()["name"] == "Tower A Renamed"


async def test_upload_document_stores_file(client, tmp_path):
    http, app = client
    project = (await http.post("/projects", json={"name": "P"})).json()
    response = await http.post(
        f"/projects/{project['id']}/documents",
        params={"kind": "codebook"},
        files={"file": ("ada.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert response.status_code == 201
    document = await app.state.repo.get_document(response.json()["id"])
    assert document is not None
    with open(document["path"], "rb") as fh:
        assert fh.read().startswith(b"%PDF")


async def test_upload_rejects_bad_kind(client):
    http, _ = client
    project = (await http.post("/projects", json={"name": "P"})).json()
    response = await http.post(
        f"/projects/{project['id']}/documents",
        params={"kind": "blueprint"},
        files={"file": ("x.pdf", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 422


async def test_verify_streams_events_to_done(client):
    http, app = client
    project = (await http.post("/projects", json={"name": "P"})).json()
    started = await http.post(f"/projects/{project['id']}/verify")
    assert started.status_code == 202
    run_id = started.json()["run_id"]

    # Let the background job finish (no documents -> immediate done).
    run = app.state.runs.get(run_id)
    await asyncio.wait_for(run.task, timeout=10)

    events = []
    async with http.stream("GET", f"/runs/{run_id}/events") as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line.removeprefix("data:").strip()))
    assert events[-1]["stage"] == "done"


async def test_results_unknown_project_404(client):
    http, _ = client
    assert (await http.get("/projects/nope/results")).status_code == 404


async def test_verify_unknown_project_404(client):
    http, _ = client
    assert (await http.post("/projects/proj-missing/verify")).status_code == 404


async def test_upload_unknown_project_404_leaves_no_file(client, tmp_path):
    http, _ = client
    response = await http.post(
        "/projects/proj-missing/documents",
        params={"kind": "codebook"},
        files={"file": ("x.pdf", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 404
    assert list(tmp_path.rglob("*")) == []  # nothing written to the data dir


async def test_delete_project_removes_graph_and_files(client):
    http, app = client
    project = (await http.post("/projects", json={"name": "P"})).json()
    upload = await http.post(
        f"/projects/{project['id']}/documents",
        params={"kind": "floorplan"},
        files={"file": ("plan.pdf", b"%PDF-1.4", "application/pdf")},
    )
    path = Path((await app.state.repo.get_document(upload.json()["id"]))["path"])
    assert path.exists()

    response = await http.delete(f"/projects/{project['id']}")
    assert response.status_code == 204
    assert not path.exists()
    assert (await http.get(f"/projects/{project['id']}/results")).status_code == 404


async def test_delete_project_unknown_404(client):
    http, _ = client
    assert (await http.delete("/projects/proj-doesnotexist")).status_code == 404


async def test_delete_project_bad_id_404(client, tmp_path):
    http, _ = client
    # ids with path separators or spaces must be rejected before touching disk
    assert (await http.delete("/projects/..%2F..%2Fpwn")).status_code == 404
    assert (await http.delete("/projects/a b")).status_code == 404
    assert list(tmp_path.rglob("*")) == []


async def test_delete_document_removes_file(client):
    http, app = client
    project = (await http.post("/projects", json={"name": "P"})).json()
    upload = await http.post(
        f"/projects/{project['id']}/documents",
        params={"kind": "codebook"},
        files={"file": ("code.pdf", b"%PDF-1.4", "application/pdf")},
    )
    document_id = upload.json()["id"]
    path = Path((await app.state.repo.get_document(document_id))["path"])
    assert path.exists()

    assert (await http.delete(f"/documents/{document_id}")).status_code == 204
    assert not path.exists()
    assert await app.state.repo.get_document(document_id) is None
    assert (await http.delete(f"/documents/{document_id}")).status_code == 404


async def test_delete_project_evicts_its_runs(client):
    http, app = client
    project = (await http.post("/projects", json={"name": "P"})).json()
    run_id = (await http.post(f"/projects/{project['id']}/verify")).json()["run_id"]
    await asyncio.wait_for(app.state.runs.get(run_id).task, timeout=10)

    assert (await http.delete(f"/projects/{project['id']}")).status_code == 204
    assert app.state.runs.get(run_id) is None


async def test_scale_patch_validates(client):
    http, app = client
    project = (await http.post("/projects", json={"name": "P"})).json()
    upload = await http.post(
        f"/projects/{project['id']}/documents",
        params={"kind": "floorplan"},
        files={"file": ("plan.pdf", b"%PDF-1.4", "application/pdf")},
    )
    document_id = upload.json()["id"]

    bad = await http.patch(f"/documents/{document_id}/scale", json={"scale_text": "banana"})
    assert bad.status_code == 422

    good = await http.patch(
        f"/documents/{document_id}/scale", json={"scale_text": '1/4" = 1\'-0"'}
    )
    assert good.status_code == 200
    document = await app.state.repo.get_document(document_id)
    assert document["manual_scale_text"] == '1/4" = 1\'-0"'
    assert document["ingested"] is False
