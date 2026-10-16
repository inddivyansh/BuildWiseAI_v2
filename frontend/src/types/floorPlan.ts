/**
 * TypeScript types mirroring backend/app/schemas/floor_plan.py
 * Keep in sync — these are the API contract.
 */

export type RoomType =
  | "living" | "kitchen" | "dining" | "bedroom" | "bathroom"
  | "half_bath" | "master_bedroom" | "master_bathroom" | "office"
  | "laundry" | "garage" | "hallway" | "closet" | "walkin_closet"
  | "pantry" | "mudroom" | "foyer" | "staircase" | "storage"
  | "balcony" | "porch" | "utility" | "other";

export type Relationship = "adjacent" | "nearby" | "separated" | "connected";

export interface Coordinate {
  x: number;
  y: number;
}

export interface Room {
  id: string;
  type: RoomType;
  label: string;
  target_area_sqm: number;
  min_area_sqm?: number;
  max_area_sqm?: number;
  floor: number;
  polygon?: Coordinate[];
  actual_area_sqm?: number;
  min_width_m?: number;
  min_depth_m?: number;
  requires_window: boolean;
  requires_exterior_wall: boolean;
}

export interface Setbacks {
  front_m: number;
  rear_m: number;
  left_m: number;
  right_m: number;
}

export interface PlotConstraints {
  width_m: number;
  depth_m: number;
  area_sqm?: number;
  setbacks: Setbacks;
  max_lot_coverage: number;
  max_far?: number;
  max_height_m?: number;
  max_stories?: number;
  buildable_width_m?: number;
  buildable_depth_m?: number;
  buildable_area_sqm?: number;
}

export interface AdjacencyRequirement {
  room_a_id: string;
  room_b_id: string;
  relationship: Relationship;
}

export interface FloorPlanSpec {
  rooms: Room[];
  adjacency: AdjacencyRequirement[];
  plot: PlotConstraints;
  num_floors: number;
  style?: string;
  building_boundary?: Coordinate[];
  locale: string;
  zone_id?: string;
}

export interface WallSegment {
  id: string;
  start: Coordinate;
  end: Coordinate;
  thickness_m: number;
  is_exterior: boolean;
  is_load_bearing: boolean;
  floor: number;
}

export interface DoorPlacement {
  id: string;
  wall_id: string;
  position_along_wall: number;
  width_m: number;
  height_m: number;
  door_type: string;
}

export interface WindowPlacement {
  id: string;
  wall_id: string;
  position_along_wall: number;
  width_m: number;
  height_m: number;
  sill_height_m: number;
}

export interface GeneratedFloorPlan {
  spec: FloorPlanSpec;
  walls: WallSegment[];
  doors: DoorPlacement[];
  windows: WindowPlacement[];
  generation_id: string;
  generator_model: string;
  generation_time_s: number;
  total_area_sqm: number;
  lot_coverage_ratio: number;
  area_deviation_pct: number;
}

export interface ComplianceViolation {
  rule_id: string;
  severity: "error" | "warning" | "info";
  description: string;
  affected_rooms: string[];
  code_reference: string;
  suggestion?: string;
}

export interface ComplianceResult {
  is_compliant: boolean;
  violations: ComplianceViolation[];
  checked_rules: number;
  jurisdiction: string;
}

// --- API Request/Response Types ---

export interface GenerateRequest {
  prompt: string;
  project_id?: string;
  plot_width_m?: number;
  plot_depth_m?: number;
  locale?: string;
  zone_id?: string;
  run_compliance?: boolean;
  generator?: string;
  model?: string;
  provider?: string;
}

export interface GenerateResponse {
  generation_id: string;
  status: "completed" | "processing" | "failed";
  floor_plan?: GeneratedFloorPlan;
  parsed_spec?: FloorPlanSpec;
  compliance?: ComplianceResult;
  error?: string;
  project_id?: string;
}

export interface EditRequest {
  generation_id: string;
  prompt?: string;
  model?: string;
  provider?: string;
  actions?: Array<{
    action: string;
    target_room_id?: string;
    parameters: Record<string, unknown>;
  }>;
  edit_region?: { x: number; y: number; width: number; height: number };
}

export interface EditResponse {
  generation_id: string;
  status: string;
  floor_plan?: GeneratedFloorPlan;
  changes_summary?: string;
  error?: string;
}

// --- Room color mapping for canvas rendering ---

export const ROOM_COLORS: Record<RoomType, { fill: string; dark: string }> = {
  living:           { fill: "#FDE68A", dark: "#92700A" },
  kitchen:          { fill: "#FCA5A5", dark: "#9B1C1C" },
  dining:           { fill: "#FDBA74", dark: "#9A3412" },
  bedroom:          { fill: "#93C5FD", dark: "#1E40AF" },
  bathroom:         { fill: "#A5B4FC", dark: "#3730A3" },
  half_bath:        { fill: "#C4B5FD", dark: "#5B21B6" },
  master_bedroom:   { fill: "#6EE7B7", dark: "#065F46" },
  master_bathroom:  { fill: "#A7F3D0", dark: "#047857" },
  office:           { fill: "#FCD34D", dark: "#854D0E" },
  laundry:          { fill: "#D1D5DB", dark: "#4B5563" },
  garage:           { fill: "#9CA3AF", dark: "#374151" },
  hallway:          { fill: "#E5E7EB", dark: "#6B7280" },
  closet:           { fill: "#D1D5DB", dark: "#4B5563" },
  walkin_closet:    { fill: "#C4B5FD", dark: "#5B21B6" },
  pantry:           { fill: "#FDE68A", dark: "#92700A" },
  mudroom:          { fill: "#D1D5DB", dark: "#4B5563" },
  foyer:            { fill: "#E5E7EB", dark: "#6B7280" },
  staircase:        { fill: "#F3F4F6", dark: "#6B7280" },
  storage:          { fill: "#D1D5DB", dark: "#4B5563" },
  balcony:          { fill: "#BAE6FD", dark: "#075985" },
  porch:            { fill: "#BAE6FD", dark: "#075985" },
  utility:          { fill: "#D1D5DB", dark: "#4B5563" },
  other:            { fill: "#F3F4F6", dark: "#6B7280" },
};
