#!/usr/bin/env python3
"""Build LanceDB vector index from extracted building code clauses.

Indexes NBC clauses and RCA zone spec tables into a vector database
for semantic search during Tier 3 compliance checking.

Uses Gemini gemini-embedding-001 (768-dim) for embeddings.

Usage:
    cd backend
    python scripts/build_lancedb_index.py \
        --nbc-clauses data/building_codes/nbc/nbc_clauses.json \
        --zone-summary data/building_codes/quebec/zone_specs_summary.json \
        --lancedb-path data/building_codes/lancedb
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import lancedb

# Gemini embedding config
_GEMINI_BATCH_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents"
_EMBED_DIM = 768
_BATCH_SIZE = 100  # Gemini supports up to 100 per batch call


def embed_batch(texts: list[str], api_key: str, max_retries: int = 5) -> list[list[float]]:
    """Embed a batch of texts via Gemini batchEmbedContents with retry."""
    requests = [
        {
            "model": "models/gemini-embedding-001",
            "content": {"parts": [{"text": t[:2048]}]},
            "outputDimensionality": _EMBED_DIM,
        }
        for t in texts
    ]
    for attempt in range(max_retries):
        resp = httpx.post(
            _GEMINI_BATCH_URL,
            params={"key": api_key},
            json={"requests": requests},
            timeout=120,
        )
        if resp.status_code == 429:
            wait = 2 ** attempt + 1
            print(f"    Rate limited, waiting {wait}s (attempt {attempt + 1}/{max_retries})...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        data = resp.json()
        return [e["values"] for e in data["embeddings"]]
    resp.raise_for_status()  # Final attempt error
    return []


def load_nbc_clauses(path: Path) -> list[dict]:
    """Load NBC clauses from JSON."""
    if not path.exists():
        print(f"  NBC clauses not found: {path}")
        return []
    with open(path, encoding="utf-8") as f:
        clauses = json.load(f)
    print(f"  Loaded {len(clauses)} NBC clauses")
    return clauses


def load_zone_specs(path: Path) -> list[dict]:
    """Convert zone spec summary into indexable documents."""
    if not path.exists():
        print(f"  Zone specs not found: {path}")
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    docs = []
    for zone_id, spec in data.items():
        # Build text description for vector search
        parts = [f"Zone {zone_id}"]
        if spec.get("authorized_uses"):
            parts.append(f"Usages autorisés: {', '.join(spec['authorized_uses'])}")
        if spec.get("max_height_m"):
            parts.append(f"Hauteur maximale: {spec['max_height_m']} m")
        if spec.get("front_margin_m"):
            parts.append(f"Marge avant: {spec['front_margin_m']} m")
        if spec.get("rear_margin_m"):
            parts.append(f"Marge arrière: {spec['rear_margin_m']} m")
        if spec.get("side_margin_m"):
            parts.append(f"Marge latérale: {spec['side_margin_m']} m")
        if spec.get("min_green_area_pct"):
            parts.append(f"Aire verte minimale: {spec['min_green_area_pct']}%")
        if spec.get("max_density_units_per_ha"):
            parts.append(f"Densité maximale: {spec['max_density_units_per_ha']} log/ha")
        if spec.get("min_lot_area_sqm"):
            parts.append(f"Superficie minimale du lot: {spec['min_lot_area_sqm']} m²")
        if spec.get("max_stories"):
            parts.append(f"Nombre d'étages maximal: {spec['max_stories']}")
        if spec.get("parking_type"):
            parts.append(f"Stationnement type: {spec['parking_type']}")

        text = ". ".join(parts)
        docs.append({
            "id": f"zone:{zone_id}",
            "text": text,
            "jurisdiction": "municipal",
            "chapter": "Grille de spécifications",
            "page": 0,
            "source": f"R.C.A.3V.Q.4 - {zone_id}",
            "topic": "zoning",
        })

    print(f"  Loaded {len(docs)} zone spec documents")
    return docs


def main():
    parser = argparse.ArgumentParser(description="Build LanceDB vector index")
    parser.add_argument(
        "--nbc-clauses", type=Path,
        default=Path("data/building_codes/nbc/nbc_clauses.json"),
    )
    parser.add_argument(
        "--zone-summary", type=Path,
        default=Path("data/building_codes/quebec/zone_specs_summary.json"),
    )
    parser.add_argument(
        "--lancedb-path", type=Path,
        default=Path("data/building_codes/lancedb"),
    )
    parser.add_argument("--gemini-api-key", type=str, default=None)
    parser.add_argument("--batch-size", type=int, default=_BATCH_SIZE)
    args = parser.parse_args()

    # Resolve API key
    api_key = args.gemini_api_key
    if not api_key:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from app.config import settings
            api_key = settings.gemini_api_key
        except Exception:
            pass
    if not api_key:
        import os
        api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: No Gemini API key. Use --gemini-api-key or set GEMINI_API_KEY.")
        sys.exit(1)

    print("Loading data...")
    nbc_clauses = load_nbc_clauses(args.nbc_clauses)
    zone_docs = load_zone_specs(args.zone_summary)

    # Combine all documents
    all_docs = []
    for c in nbc_clauses:
        all_docs.append({
            "id": c["id"],
            "text": c["text"],
            "jurisdiction": c.get("jurisdiction", "NBC"),
            "chapter": c.get("chapter", ""),
            "page": c.get("page", 0),
            "source": c.get("source", ""),
            "topic": "",
        })
    all_docs.extend(zone_docs)

    if not all_docs:
        print("No documents to index!")
        sys.exit(1)

    print(f"\nTotal documents to index: {len(all_docs)}")

    # Embed with Gemini in batches
    print(f"Embedding {len(all_docs)} documents via Gemini gemini-embedding-001 (batch_size={args.batch_size})...")
    t0 = time.time()
    all_embeddings = []
    texts = [d["text"][:2048] for d in all_docs]

    for i in range(0, len(texts), args.batch_size):
        batch = texts[i:i + args.batch_size]
        batch_num = i // args.batch_size + 1
        total_batches = (len(texts) + args.batch_size - 1) // args.batch_size
        print(f"  Batch {batch_num}/{total_batches} ({len(batch)} texts)...")
        embs = embed_batch(batch, api_key)
        all_embeddings.extend(embs)
        # Small delay to respect rate limits
        if i + args.batch_size < len(texts):
            time.sleep(0.5)

    print(f"  Embedded in {time.time() - t0:.1f}s")

    # Create LanceDB table
    print(f"Creating LanceDB index at: {args.lancedb_path}")
    args.lancedb_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(args.lancedb_path))

    # Build records
    records = []
    for i, doc in enumerate(all_docs):
        records.append({
            "id": doc["id"],
            "text": doc["text"][:2000],
            "jurisdiction": doc["jurisdiction"],
            "chapter": doc["chapter"],
            "page": doc["page"],
            "source": doc["source"],
            "topic": doc["topic"],
            "vector": all_embeddings[i],
        })

    # Drop existing table if present
    try:
        db.drop_table("clauses")
    except Exception:
        pass

    table = db.create_table("clauses", records)

    print(f"\nIndex created:")
    print(f"  Table: clauses")
    print(f"  Records: {len(records)}")
    print(f"  Vector dim: {_EMBED_DIM}")
    print(f"  Embedding model: gemini-embedding-001 (Gemini)")

    # Test search
    print("\nTest searches:")
    for query in [
        "hauteur maximale bâtiment",
        "marge avant setback",
        "minimum room area bedroom",
        "exit egress door",
    ]:
        q_emb = embed_batch([query], api_key)[0]
        results = table.search(q_emb).limit(3).to_list()
        print(f"\n  Query: '{query}'")
        for r in results:
            print(f"    [{r['jurisdiction']}] {r['id']}: {r['text'][:80]}...")


if __name__ == "__main__":
    main()
