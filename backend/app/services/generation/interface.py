"""Abstract interface for floor plan generators."""

from abc import ABC, abstractmethod
from shapely.geometry import Polygon

from app.schemas.floor_plan import FloorPlanSpec


class FloorPlanGenerator(ABC):
    """Base class for all floor plan generators (constraint solver, GSDiff, etc.)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Model identifier string."""
        ...

    @property
    def requires_gpu(self) -> bool:
        return False

    @abstractmethod
    async def generate(
        self,
        spec: FloorPlanSpec,
    ) -> list[Polygon]:
        """
        Generate room polygons from a FloorPlanSpec.

        Args:
            spec: Complete floor plan specification with rooms, adjacency, plot.

        Returns:
            List of Shapely Polygons, one per room in spec.rooms order.
        """
        ...

    async def is_available(self) -> bool:
        """Check if this generator is ready to use."""
        return True


class EditableFloorPlanGenerator(FloorPlanGenerator):
    """Extended generator that supports editing an existing floor plan.

    ChatHouseDiffusion can blend attention maps from a previous plan with
    new generation, enabling NL-guided editing (e.g. "make the kitchen bigger").
    """

    @property
    def supports_editing(self) -> bool:
        return True

    @abstractmethod
    async def edit(
        self,
        spec: FloorPlanSpec,
        previous_polygons: list[Polygon],
        edit_strength: float = 0.7,
    ) -> list[Polygon]:
        """
        Edit an existing floor plan by blending with new generation.

        Args:
            spec: Updated FloorPlanSpec (with modified room sizes, etc.)
            previous_polygons: The current room polygons to edit from.
            edit_strength: How much to deviate from the original plan (0=keep, 1=regenerate).

        Returns:
            List of Shapely Polygons, one per room in spec.rooms order.
        """
        ...
