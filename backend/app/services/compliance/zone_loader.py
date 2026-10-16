"""Load zone specifications and provide point-in-polygon zone lookup."""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from shapely.geometry import Point, shape

from app.services.compliance.zone_spec_parser import ZoneSpec, parse_zone_spec

logger = structlog.get_logger()


class ZoneDataLoader:
    """Loads zone specs and GeoJSON for zone lookup."""

    def __init__(
        self,
        zones_dir: Path | None = None,
        geojson_path: Path | None = None,
    ):
        self._specs: dict[str, ZoneSpec] = {}
        self._zone_polygons: list[tuple[str, object]] = []  # (zone_id, shapely geom)

        if zones_dir and zones_dir.exists():
            self._load_zone_specs(zones_dir)
        if geojson_path and geojson_path.exists():
            self._load_geojson(geojson_path)

    def _load_zone_specs(self, zones_dir: Path) -> None:
        """Load all spec.json files from zone directories."""
        count = 0
        for zone_dir in zones_dir.iterdir():
            if not zone_dir.is_dir():
                continue
            spec_file = zone_dir / "spec.json"
            if not spec_file.exists():
                continue
            try:
                with open(spec_file, encoding="utf-8") as f:
                    data = json.load(f)
                zs = parse_zone_spec(data)
                self._specs[zs.zone_id] = zs
                count += 1
            except Exception as e:
                logger.warning("zone_spec_load_failed", zone=zone_dir.name, error=str(e))
        logger.info("zone_specs_loaded", count=count)

    def _load_geojson(self, geojson_path: Path) -> None:
        """Load GeoJSON for point-in-polygon zone identification."""
        try:
            with open(geojson_path, encoding="utf-8") as f:
                data = json.load(f)
            features = data.get("features", [])
            for feat in features:
                props = feat.get("properties", {})
                zone_id = props.get("ZONE_ID") or props.get("zone_id") or props.get("ID", "")
                if not zone_id:
                    continue
                geom = feat.get("geometry")
                if geom:
                    try:
                        poly = shape(geom)
                        self._zone_polygons.append((zone_id, poly))
                    except Exception:
                        pass
            logger.info("geojson_loaded", zones=len(self._zone_polygons))
        except Exception as e:
            logger.warning("geojson_load_failed", error=str(e))

    def get_zone_spec(self, zone_id: str) -> ZoneSpec | None:
        """Get a zone spec by ID."""
        return self._specs.get(zone_id)

    def get_zone_by_point(self, lon: float, lat: float) -> str | None:
        """Find zone ID for a geographic point."""
        pt = Point(lon, lat)
        for zone_id, poly in self._zone_polygons:
            if poly.contains(pt):
                return zone_id
        return None

    def list_zones(self) -> list[str]:
        """List all loaded zone IDs."""
        return sorted(self._specs.keys())

    @property
    def zone_count(self) -> int:
        return len(self._specs)

    def get_zone_setbacks(self, zone_id: str) -> dict[str, float | None]:
        """Get setback values for a zone."""
        zs = self._specs.get(zone_id)
        if not zs:
            return {}
        return {
            "front_m": zs.front_margin_m,
            "rear_m": zs.rear_margin_m,
            "side_m": zs.side_margin_m,
        }

    def get_zone_height_limit(self, zone_id: str) -> float | None:
        """Get max building height for a zone."""
        zs = self._specs.get(zone_id)
        return zs.max_height_m if zs else None
