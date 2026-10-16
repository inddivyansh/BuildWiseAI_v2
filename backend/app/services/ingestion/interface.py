"""Abstract interface for floor plan parsers (ingestion)."""

from abc import ABC, abstractmethod
from pathlib import Path

import structlog

logger = structlog.get_logger()


class FloorPlanParser(ABC):
    """Base class for all floor plan parsers (DXF, IFC, image).

    Mirrors the FloorPlanGenerator interface from generation/interface.py
    but works in the reverse direction: file -> polygons + labels.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Parser identifier string."""
        ...

    @property
    def supported_extensions(self) -> list[str]:
        """File extensions this parser can handle (e.g. ['.dxf'])."""
        return []

    @abstractmethod
    async def parse(
        self,
        file_path: Path,
        scale_m_per_pixel: float | None = None,
        locale: str = "CA-QC",
    ) -> dict:
        """
        Parse a floor plan file into raw geometry.

        Args:
            file_path: Path to the uploaded file.
            scale_m_per_pixel: Scale factor (mainly for images). None = auto-detect.
            locale: Jurisdiction for room label language detection.

        Returns:
            {
                "polygons": list[list[tuple[float, float]]],  # Room boundary coords in meters
                "room_labels": list[str],                      # Detected room names
                "room_types": list[str | None],                # Mapped RoomType values
                "walls": list[dict],                           # Wall segments
                "doors": list[dict],                           # Door placements (if detected)
                "windows": list[dict],                         # Window placements (if detected)
                "confidence": float,                           # 0-1 parse confidence
                "warnings": list[str],                         # Issues encountered
                "plot_bounds": tuple[float, float] | None,     # (width_m, depth_m) if detected
            }
        """
        ...

    async def is_available(self) -> bool:
        """Check if this parser's dependencies are installed and ready."""
        return True
