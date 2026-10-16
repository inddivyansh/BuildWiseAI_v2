"""Tests for zone spec parser."""

import json
import pytest
from pathlib import Path

from app.services.compliance.zone_spec_parser import parse_zone_spec, ZoneSpec


# Sample spec.json content based on real 31001Ma data
SAMPLE_SPEC_JSON = {
    "zone_id": "31001Ma",
    "source_file": "31001Ma.pdf",
    "tables": [
        (
            "| USAGES AUTORISÉS |\n"
            "| HABITATION H1 Logement |\n"
            "| H2 Habitation avec services communautaires |\n"
            "| COMMERCE DE CONSOMMATION ET DE SERVICES C1 Services administratifs |\n"
            "| C2 Vente au détail et services |\n"
            "| COMMERCE DE RESTAURATION C20 Restaurant |\n"
            "| PUBLIQUE P5 Établissement de santé sans hébergement |\n"
            "| RÉCRÉATION EXTÉRIEURE R1 Parc |\n"
            "| Marge avant | 6 m |\n"
            "| Marge arrière | 9 m |\n"
            "| maximale | 22 m |\n"
            "| Hauteur maximale 22 m |\n"
            "| Pourcentage d'aire verte minimale | 15 % |\n"
            "| 65 log/ha |\n"
            "| Superficie minimale | 10000 m² |\n"
            "| TYPE Urbain dense |\n"
            "| souterraines est de 85% |\n"
            "| DISPOSITIONS PARTICULIÈRES "
            "Calcul de la hauteur maximale d'un bâtiment sur un terrain en pente - article 340 "
            "Toute partie d'un bâtiment principal dans la zone doit être confinée à l'intérieur "
            "du volume compris à l'intérieur d'un angle d'éloignement de 45° à la limite de la "
            "zone 31019Ha - article 331.0.1 |\n"
            "| Le stationnement doit être situé à l'intérieur à au moins 100 % - article 585 |\n"
            "| Le pourcentage minimal du nombre de cases de stationnement aménagées sur un lot "
            "qui doivent être souterraines est de 85% - article 586 |\n"
        )
    ],
}


class TestParseZoneSpec:
    def test_basic_parsing(self):
        zs = parse_zone_spec(SAMPLE_SPEC_JSON)
        assert isinstance(zs, ZoneSpec)
        assert zs.zone_id == "31001Ma"
        assert zs.source_file == "31001Ma.pdf"

    def test_authorized_uses(self):
        zs = parse_zone_spec(SAMPLE_SPEC_JSON)
        # Should find H1, H2, C1, C2, C20, P5, R1
        assert "H1" in zs.authorized_uses
        assert "H2" in zs.authorized_uses
        assert "C1" in zs.authorized_uses
        assert "C2" in zs.authorized_uses
        assert "C20" in zs.authorized_uses
        assert "P5" in zs.authorized_uses
        assert "R1" in zs.authorized_uses

    def test_height_extraction(self):
        zs = parse_zone_spec(SAMPLE_SPEC_JSON)
        assert zs.max_height_m == 22.0

    def test_front_margin(self):
        zs = parse_zone_spec(SAMPLE_SPEC_JSON)
        assert zs.front_margin_m == 6.0

    def test_rear_margin(self):
        zs = parse_zone_spec(SAMPLE_SPEC_JSON)
        assert zs.rear_margin_m == 9.0

    def test_green_area(self):
        zs = parse_zone_spec(SAMPLE_SPEC_JSON)
        assert zs.min_green_area_pct == 15.0

    def test_lot_area(self):
        zs = parse_zone_spec(SAMPLE_SPEC_JSON)
        assert zs.min_lot_area_sqm == 10000.0

    def test_underground_parking(self):
        zs = parse_zone_spec(SAMPLE_SPEC_JSON)
        assert zs.min_underground_parking_pct == 85.0

    def test_parking_type(self):
        zs = parse_zone_spec(SAMPLE_SPEC_JSON)
        assert zs.parking_type is not None
        assert "urbain" in zs.parking_type.lower() or "dense" in zs.parking_type.lower()

    def test_referenced_articles(self):
        zs = parse_zone_spec(SAMPLE_SPEC_JSON)
        assert "340" in zs.referenced_articles
        assert "331.0.1" in zs.referenced_articles
        assert "585" in zs.referenced_articles
        assert "586" in zs.referenced_articles

    def test_special_provisions(self):
        zs = parse_zone_spec(SAMPLE_SPEC_JSON)
        assert len(zs.special_provisions) >= 1
        # Should mention article 340 or angle d'éloignement
        any_provision_mentions_340 = any(
            "340" in prov for prov in zs.special_provisions
        )
        assert any_provision_mentions_340

    def test_raw_tables_preserved(self):
        zs = parse_zone_spec(SAMPLE_SPEC_JSON)
        assert len(zs.raw_tables) == 1


class TestEmptySpec:
    def test_empty_tables(self):
        spec = {"zone_id": "empty", "tables": []}
        zs = parse_zone_spec(spec)
        assert zs.zone_id == "empty"
        assert zs.authorized_uses == []
        assert zs.max_height_m is None
        assert zs.front_margin_m is None

    def test_missing_fields(self):
        spec = {}
        zs = parse_zone_spec(spec)
        assert zs.zone_id == "unknown"


class TestRealSpecFile:
    """Test with real spec.json file if available."""

    @pytest.fixture
    def real_spec_path(self):
        path = Path(r"C:\Users\anass\AppData\Local\Temp\rca-zones\31001Ma\spec.json")
        if not path.exists():
            pytest.skip("Real spec.json not available")
        return path

    def test_real_31001Ma(self, real_spec_path):
        with open(real_spec_path, encoding="utf-8") as f:
            data = json.load(f)
        zs = parse_zone_spec(data)
        assert zs.zone_id == "31001Ma"
        # Should extract at least some uses
        assert len(zs.authorized_uses) > 0
        # Should find height
        assert zs.max_height_m is not None
        assert zs.max_height_m > 0
        # Should find referenced articles
        assert len(zs.referenced_articles) > 0
