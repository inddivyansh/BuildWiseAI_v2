# BuildWise v2 — Unified Backend API

High-performance asynchronous FastAPI service powering **BuildWise v2**, providing the **Plan Compliance Engine** (spatial and semantic building code verification, deterministic rule execution, and Neo4j property knowledge graph) and the **Blueprint Studio Engine** (natural language architectural floor plan synthesis, spatial editing, and multi-format CAD/BIM export).

---

## ⚡ Quick Start

### 1. Python Environment Setup
```bash
# Ensure Python 3.10 or 3.11 is active
python -m venv .venv
source .venv/Scripts/activate  # Windows Git Bash
# or: source .venv/bin/activate # Linux / macOS

# Install dependencies
pip install -r requirements.txt
```

### 2. Run the Development Server
```bash
uvicorn planlint.api.main:app --reload --port 8000
```
- API Base: `http://localhost:8000`
- Interactive OpenAPI Docs: `http://localhost:8000/docs`
- Health Endpoint: `http://localhost:8000/api/health`

---

## 🏗️ Architecture & Routers

The unified application factory (`planlint/api/main.py:create_app`) mounts:
- **Plan Compliance & Projects**: `/projects/*`, `/documents/*`, `/runs/*`
- **Durable Blueprint History**: `/projects/{id}/blueprints/*`
- **Blueprint Studio Generation**: `/api/generate`, `/api/generate/from-spec`
- **Spatial Editing Engine**: `/api/edit`
- **Multi-Format CAD/BIM Export**: `/api/export/{generation_id}/{format}` (`dxf`, `svg`)
- **Drawing Ingestion**: `/api/ingest` (CAD, raster, PDF)
- **Zoning & Compliance Check**: `/api/compliance/check`, `/api/compliance/zones`

---

## 🧪 Testing

```bash
# Full test suite (34 tests covering API, Studio, and Compliance)
pytest tests/ -v

# Run with offline sample mode (zero API keys required)
PLANLINT_OFFLINE_SAMPLE=1 pytest tests/test_api.py -v
```
