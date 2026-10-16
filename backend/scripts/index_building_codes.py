#!/usr/bin/env python3
"""Master indexing script for building code compliance engine.

Processes all three code layers (NBC, CCQ, Municipal) and loads into
Neo4j graph + LanceDB vector index.

Usage:
    cd backend
    python scripts/index_building_codes.py \
        --nbc-pdf data/building_codes/nbc/NBC_2020.pdf \
        --ccq-pdf data/building_codes/ccq/CCQ_2023.pdf \
        --rca-zones data/building_codes/quebec/sainte-foy \
        --neo4j-uri bolt://localhost:7687 \
        --lancedb-path data/building_codes/lancedb
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main():
    parser = argparse.ArgumentParser(description="Index building codes")
    parser.add_argument("--nbc-pdf", type=Path, help="Path to NBC PDF")
    parser.add_argument("--ccq-pdf", type=Path, help="Path to CCQ PDF")
    parser.add_argument("--rca-zones", type=Path, help="Path to rca-zones directory")
    parser.add_argument("--nbc-clauses", type=Path, help="Path to pre-extracted NBC clauses JSON")
    parser.add_argument("--zone-summary", type=Path, help="Path to zone_specs_summary.json")
    parser.add_argument("--neo4j-uri", default=None)
    parser.add_argument("--neo4j-user", default=None)
    parser.add_argument("--neo4j-password", default=None)
    parser.add_argument("--neo4j-database", default=None)
    parser.add_argument("--lancedb-path", default="data/building_codes/lancedb")
    parser.add_argument("--embedding-model", default="paraphrase-multilingual-MiniLM-L12-v2")
    parser.add_argument("--skip-graphrag", action="store_true", help="Skip LLM topic extraction")
    args = parser.parse_args()

    all_clauses: list[dict] = []
    all_definitions: list[dict] = []

    # =============================================
    # Step 1: Extract clauses from PDFs or pre-extracted JSON
    # =============================================
    if args.nbc_clauses and args.nbc_clauses.exists():
        # Fast path: load pre-extracted clauses from JSON
        print(f"Loading pre-extracted NBC clauses from {args.nbc_clauses}...")
        with open(args.nbc_clauses, encoding="utf-8") as f:
            raw_clauses = json.load(f)
        for c in raw_clauses:
            all_clauses.append({
                "id": c["id"],
                "title": c.get("title", ""),
                "text": c.get("text", ""),
                "page": c.get("page", 0),
                "chapter": c.get("chapter", ""),
                "cross_refs": c.get("cross_references", c.get("cross_refs", [])),
                "jurisdiction": c.get("jurisdiction", "NBC"),
                "source_file": c.get("source", c.get("source_file", "")),
            })
        from app.services.compliance.pdf_extractor import extract_definitions
        nbc_defs = extract_definitions(all_clauses, "NBC")
        all_definitions.extend(nbc_defs)
        print(f"  NBC (JSON): {len(raw_clauses)} clauses, {len(nbc_defs)} definitions")
    else:
        # PDF extraction path (original)
        from app.services.compliance.pdf_extractor import BuildingCodeExtractor, extract_definitions
        extractor = BuildingCodeExtractor()

        if args.nbc_pdf and args.nbc_pdf.exists():
            print(f"Extracting NBC clauses from {args.nbc_pdf}...")
            nbc_clauses = extractor.extract_clauses(args.nbc_pdf, "NBC")
            all_clauses.extend(nbc_clauses)
            nbc_defs = extract_definitions(nbc_clauses, "NBC")
            all_definitions.extend(nbc_defs)
            print(f"  NBC: {len(nbc_clauses)} clauses, {len(nbc_defs)} definitions")
        else:
            print("Skipping NBC (no PDF provided)")

        if args.ccq_pdf and args.ccq_pdf.exists():
            print(f"Extracting CCQ clauses from {args.ccq_pdf}...")
            ccq_clauses = extractor.extract_clauses(args.ccq_pdf, "CCQ")
            all_clauses.extend(ccq_clauses)
            ccq_defs = extract_definitions(ccq_clauses, "CCQ")
            all_definitions.extend(ccq_defs)
            print(f"  CCQ: {len(ccq_clauses)} clauses, {len(ccq_defs)} definitions")
        else:
            print("Skipping CCQ (no PDF provided)")

    # =============================================
    # Step 2: Load municipal articles from bylaw_articles.json
    # =============================================
    if args.rca_zones and args.rca_zones.exists():
        bylaw_file = args.rca_zones / "bylaw_articles.json"
        if bylaw_file.exists():
            print(f"Loading municipal articles from {bylaw_file}...")
            with open(bylaw_file, encoding="utf-8") as f:
                articles = json.load(f)
            for art in articles:
                all_clauses.append({
                    "id": f"municipal:{art['id']}",
                    "title": art.get("text", "")[:100],
                    "text": art.get("text", ""),
                    "page": 0,
                    "chapter": "",
                    "cross_refs": [],
                    "jurisdiction": "municipal",
                    "source_file": "bylaw_articles.json",
                })
            mun_defs = extract_definitions(all_clauses[-len(articles):], "municipal")
            all_definitions.extend(mun_defs)
            print(f"  Municipal: {len(articles)} articles, {len(mun_defs)} definitions")

    print(f"\nTotal: {len(all_clauses)} clauses, {len(all_definitions)} definitions")

    # =============================================
    # Step 3: Parse zone specs (from summary JSON or spec.json dirs)
    # =============================================
    zone_specs = {}
    if args.zone_summary and args.zone_summary.exists():
        # Fast path: build ZoneSpec objects from pre-extracted summary
        print(f"Loading zone specs from {args.zone_summary}...")
        from app.services.compliance.zone_spec_parser import ZoneSpec
        with open(args.zone_summary, encoding="utf-8") as f:
            summary = json.load(f)
        for zone_id, vals in summary.items():
            zone_specs[zone_id] = ZoneSpec(
                zone_id=zone_id,
                authorized_uses=vals.get("authorized_uses", []),
                max_height_m=vals.get("max_height_m"),
                max_stories=vals.get("max_stories"),
                min_stories=vals.get("min_stories"),
                front_margin_m=vals.get("front_margin_m"),
                side_margin_m=vals.get("side_margin_m"),
                rear_margin_m=vals.get("rear_margin_m"),
                min_lot_area_sqm=vals.get("min_lot_area_sqm"),
                min_green_area_pct=vals.get("min_green_area_pct"),
                max_density_units_per_ha=vals.get("max_density_units_per_ha"),
                parking_type=vals.get("parking_type"),
                min_underground_parking_pct=vals.get("min_underground_parking_pct"),
                referenced_articles=vals.get("referenced_articles", []),
                special_provisions=vals.get("special_provisions", []),
            )
        print(f"  Loaded {len(zone_specs)} zone specs from summary")
    elif args.rca_zones and args.rca_zones.exists():
        print("Parsing zone specs from spec.json files...")
        from app.services.compliance.zone_spec_parser import parse_zone_spec
        for zone_dir in sorted(args.rca_zones.iterdir()):
            if not zone_dir.is_dir():
                continue
            spec_file = zone_dir / "spec.json"
            if spec_file.exists():
                try:
                    with open(spec_file, encoding="utf-8") as f:
                        data = json.load(f)
                    zs = parse_zone_spec(data)
                    zone_specs[zs.zone_id] = zs
                except Exception:
                    pass
        print(f"  Parsed {len(zone_specs)} zone specs")

    # =============================================
    # Step 4: GraphRAG topic extraction (optional)
    # =============================================
    clause_topics: dict[str, list[str]] = {}
    if not args.skip_graphrag and all_clauses:
        print("Running GraphRAG topic extraction...")
        try:
            from app.services.compliance.graphrag_extractor import GraphRAGExtractor
            grag = GraphRAGExtractor()
            result = await grag.extract_from_clauses(all_clauses)
            clause_topics = result.get("clause_topics", {})
            print(f"  Extracted topics for {len(clause_topics)} clauses")
        except Exception as e:
            print(f"  GraphRAG extraction failed: {e}")
            print("  Continuing without topic extraction")

    # =============================================
    # Step 5: Load into Neo4j
    # =============================================
    # Resolve Neo4j connection from CLI args or app config
    from app.config import settings
    neo4j_uri = args.neo4j_uri or settings.neo4j_uri
    neo4j_user = args.neo4j_user or settings.neo4j_user
    neo4j_password = args.neo4j_password or settings.neo4j_password
    neo4j_database = args.neo4j_database or settings.neo4j_database

    print(f"\nLoading into Neo4j ({neo4j_uri})...")
    try:
        from app.services.compliance.neo4j_loader import Neo4jLoader
        loader = Neo4jLoader(neo4j_uri, neo4j_user, neo4j_password, database=neo4j_database)

        await loader.load_jurisdictions()
        await loader.create_fulltext_indexes()
        n_clauses = await loader.load_clauses(all_clauses)
        n_refs = await loader.load_cross_references(all_clauses)
        n_defs = await loader.load_definitions(all_definitions)

        if clause_topics:
            n_topics = await loader.load_topic_relationships(clause_topics)
        else:
            n_topics = 0

        # Load zones from GeoJSON
        geojson_path = args.rca_zones / "vdq-zonagemunicipalzones.geojson.json" if args.rca_zones else None
        n_zones = 0
        if geojson_path and geojson_path.exists():
            n_zones = await loader.load_zones(geojson_path)

        n_specs = 0
        if zone_specs:
            n_specs = await loader.load_zone_specs(zone_specs)

        n_amendments = await loader.build_amendment_chain()

        await loader.close()
        print(f"  Neo4j loaded: {n_clauses} clauses, {n_refs} refs, {n_defs} defs, "
              f"{n_topics} topics, {n_zones} zones, {n_specs} specs, {n_amendments} amendments")
    except Exception as e:
        print(f"  Neo4j loading failed: {e}")
        print("  Continuing without Neo4j")

    # =============================================
    # Step 6: Build LanceDB vector index
    # =============================================
    if all_clauses:
        print(f"\nBuilding LanceDB index at {args.lancedb_path}...")
        try:
            from app.services.compliance.lancedb_indexer import LanceDBIndexer
            Path(args.lancedb_path).mkdir(parents=True, exist_ok=True)
            indexer = LanceDBIndexer(args.lancedb_path, args.embedding_model)
            n_indexed = await indexer.index_clauses(all_clauses)
            print(f"  LanceDB: indexed {n_indexed} clauses")
        except Exception as e:
            print(f"  LanceDB indexing failed: {e}")

    # =============================================
    # Summary
    # =============================================
    print("\n=== Indexing Complete ===")
    print(f"  Clauses: {len(all_clauses)}")
    print(f"  Definitions: {len(all_definitions)}")
    print(f"  Zone specs: {len(zone_specs)}")
    print(f"  Topics: {len(clause_topics)}")


if __name__ == "__main__":
    asyncio.run(main())
