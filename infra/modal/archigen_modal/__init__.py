"""
archigen_modal — shared preprocessing/postprocessing for HouseDiffusion inference.

Used by both the Modal serverless app (GPU) and the local CPU fallback.
"""

from archigen_modal.preprocessing import spec_to_housediff_input
from archigen_modal.postprocessing import housediff_output_to_polygons

__all__ = ["spec_to_housediff_input", "housediff_output_to_polygons"]
