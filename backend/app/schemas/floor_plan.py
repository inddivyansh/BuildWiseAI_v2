"""Core floor plan schemas — single source of truth for the entire pipeline."""

from enum import Enum
from pydantic import BaseModel, Field, field_validator


class RoomType(str, Enum):
    LIVING = "living"
    KITCHEN = "kitchen"
    DINING = "dining"
    BEDROOM = "bedroom"
    BATHROOM = "bathroom"
    HALF_BATH = "half_bath"
    MASTER_BEDROOM = "master_bedroom"
    MASTER_BATHROOM = "master_bathroom"
    OFFICE = "office"
    LAUNDRY = "laundry"
    GARAGE = "garage"
    HALLWAY = "hallway"
    CLOSET = "closet"
    WALKIN_CLOSET = "walkin_closet"
    PANTRY = "pantry"
    MUDROOM = "mudroom"
    FOYER = "foyer"
    STAIRCASE = "staircase"
    STORAGE = "storage"
    BALCONY = "balcony"
    PORCH = "porch"
    UTILITY = "utility"
    OTHER = "other"


class Relationship(str, Enum):
    ADJACENT = "adjacent"       # Rooms share a wall
    NEARBY = "nearby"           # Rooms are close but don't share a wall
    SEPARATED = "separated"     # Rooms should be far apart
    CONNECTED = "connected"     # Direct door/opening between rooms


class Coordinate(BaseModel):
    """A 2D point in meters."""
    x: float
    y: float


class Room(BaseModel):
    """A single room in the floor plan."""
    id: str = Field(..., description="Unique room ID, e.g. 'bedroom_1'")
    type: RoomType
    label: str = Field(..., description="Display name, e.g. 'Master Bedroom'")
    target_area_sqm: float = Field(..., gt=0, description="Desired area in square meters")
    min_area_sqm: float | None = Field(None, gt=0)
    max_area_sqm: float | None = Field(None, gt=0)
    floor: int = Field(0, ge=0, description="Floor number, 0 = ground")
    
    # Filled by generator — None on input, populated after generation
    polygon: list[Coordinate] | None = Field(
        None, description="Room boundary as CCW polygon. First == last point."
    )
    actual_area_sqm: float | None = Field(None, description="Computed from polygon")
    
    # Optional constraints
    min_width_m: float | None = None
    min_depth_m: float | None = None
    requires_window: bool = True
    requires_exterior_wall: bool = False  # e.g., garage, porch


class AdjacencyRequirement(BaseModel):
    """Spatial relationship between two rooms."""
    room_a_id: str
    room_b_id: str
    relationship: Relationship = Relationship.ADJACENT


class Setbacks(BaseModel):
    """Municipal setback requirements in meters."""
    front_m: float = Field(4.5, ge=0)
    rear_m: float = Field(7.5, ge=0)
    left_m: float = Field(1.5, ge=0)
    right_m: float = Field(1.5, ge=0)


class PlotConstraints(BaseModel):
    """The physical lot and zoning constraints."""
    width_m: float = Field(..., gt=0, description="Lot width (east-west)")
    depth_m: float = Field(..., gt=0, description="Lot depth (north-south)")
    area_sqm: float | None = Field(None, description="Auto-computed if None")
    setbacks: Setbacks = Field(default_factory=Setbacks)
    
    # Zoning
    max_lot_coverage: float = Field(0.6, gt=0, le=1.0, description="Max footprint / lot area")
    max_far: float | None = Field(None, description="Floor Area Ratio")
    max_height_m: float | None = None
    max_stories: int | None = None
    
    # Derived (computed)
    buildable_width_m: float | None = None
    buildable_depth_m: float | None = None
    buildable_area_sqm: float | None = None
    
    def model_post_init(self, __context):
        if self.area_sqm is None:
            self.area_sqm = self.width_m * self.depth_m
        self.buildable_width_m = self.width_m - self.setbacks.left_m - self.setbacks.right_m
        self.buildable_depth_m = self.depth_m - self.setbacks.front_m - self.setbacks.rear_m
        self.buildable_area_sqm = self.buildable_width_m * self.buildable_depth_m


class WallSegment(BaseModel):
    """A wall defined by two endpoints."""
    id: str
    start: Coordinate
    end: Coordinate
    thickness_m: float = 0.15  # 15cm default
    is_exterior: bool = False
    is_load_bearing: bool = False
    floor: int = 0


class DoorPlacement(BaseModel):
    """A door placed along a wall."""
    id: str
    wall_id: str
    position_along_wall: float = Field(..., ge=0, le=1, description="0-1 normalized position")
    width_m: float = 0.9   # Standard door
    height_m: float = 2.1
    door_type: str = "single"  # single, double, sliding, pocket, french


class WindowPlacement(BaseModel):
    """A window placed along an exterior wall."""
    id: str
    wall_id: str
    position_along_wall: float = Field(..., ge=0, le=1)
    width_m: float = 1.2
    height_m: float = 1.2
    sill_height_m: float = 0.9


class FloorPlanSpec(BaseModel):
    """The complete specification for a floor plan — central data object."""
    rooms: list[Room]
    adjacency: list[AdjacencyRequirement] = []
    plot: PlotConstraints
    
    # Generation parameters
    num_floors: int = Field(1, ge=1, le=5)
    style: str | None = Field(None, description="e.g. 'open concept', 'traditional', 'modern'")
    building_boundary: list[Coordinate] | None = Field(
        None, description="Custom footprint polygon. If None, uses rectangular buildable envelope."
    )
    
    # Metadata
    locale: str = Field("CA-QC", description="Jurisdiction for compliance, e.g. CA-QC, CA-ON")
    zone_id: str | None = Field(None, description="Municipal zone ID, e.g. '31001Ma'")
    
    @field_validator("rooms")
    @classmethod
    def validate_unique_room_ids(cls, rooms: list[Room]) -> list[Room]:
        ids = [r.id for r in rooms]
        if len(ids) != len(set(ids)):
            raise ValueError("Room IDs must be unique")
        return rooms
    
    @property
    def total_target_area(self) -> float:
        return sum(r.target_area_sqm for r in self.rooms)
    
    @property
    def room_ids(self) -> set[str]:
        return {r.id for r in self.rooms}


class GeneratedFloorPlan(BaseModel):
    """The complete output of the generation pipeline."""
    spec: FloorPlanSpec         # Original spec with polygons filled in
    walls: list[WallSegment]
    doors: list[DoorPlacement]
    windows: list[WindowPlacement]
    
    # Metadata
    generation_id: str
    generator_model: str        # e.g. "gsdiff_v1", "chathousediffusion"
    generation_time_s: float
    
    # Quality metrics
    total_area_sqm: float
    lot_coverage_ratio: float
    area_deviation_pct: float   # How far from target areas


class ComplianceViolation(BaseModel):
    """A single building code violation."""
    rule_id: str
    severity: str               # "error", "warning", "info"
    description: str
    affected_rooms: list[str]
    code_reference: str         # e.g. "NBC 9.5.3.1"
    suggestion: str | None = None


class ComplianceResult(BaseModel):
    """Result of compliance checking."""
    is_compliant: bool
    violations: list[ComplianceViolation]
    checked_rules: int
    jurisdiction: str
