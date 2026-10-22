"""Geometry services — cleanup, operations, adjacency, extrusion, IFC."""

from app.services.geometry.cleanup import cleanup_pipeline
from app.services.geometry.operations import (
    boolean_difference,
    boolean_intersection,
    boolean_union,
    buffer_polygon,
    clip_to_boundary,
    compute_buildable_envelope,
    compute_area_deviation,
    merge_rooms,
    split_polygon_horizontal,
    split_polygon_vertical,
)
from app.services.geometry.adjacency import (
    build_adjacency_graph,
    validate_adjacency_constraints,
)
from app.services.geometry.spatial_validator import (
    BlueprintType,
    DynamicSpatialValidator,
    detect_blueprint_type,
)

__all__ = [
    "cleanup_pipeline",
    "boolean_difference",
    "boolean_intersection",
    "boolean_union",
    "buffer_polygon",
    "clip_to_boundary",
    "compute_buildable_envelope",
    "compute_area_deviation",
    "merge_rooms",
    "split_polygon_horizontal",
    "split_polygon_vertical",
    "build_adjacency_graph",
    "validate_adjacency_constraints",
    "BlueprintType",
    "DynamicSpatialValidator",
    "detect_blueprint_type",
]
