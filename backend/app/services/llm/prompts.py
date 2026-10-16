"""
System prompts and schemas for LLM-based architectural parsing.

These prompts force the LLM to convert natural language into
the FloorPlanSpec JSON schema. Used with Outlines/SGLang constrained decoding.
"""

ARCHITECT_SYSTEM_PROMPT = """\
You are an expert residential architect AI. Your job is to parse natural language
descriptions of houses into a precise JSON specification.

Given a user's description of their desired home, extract:
1. All rooms with types, target areas (in square meters), and special requirements
2. Room adjacency relationships (which rooms should be next to each other)
3. Plot/lot constraints if mentioned (dimensions, setbacks)
4. Style preferences
5. Number of floors

Use these standard room areas (sqm) as defaults when the user doesn't specify:
- Living room: 20-30
- Kitchen: 10-15
- Dining room: 12-16
- Master bedroom: 15-20
- Bedroom: 10-14
- Master bathroom: 6-10
- Bathroom: 4-6
- Half bath: 2-3
- Office/study: 9-12
- Laundry: 4-6
- Garage (single): 15-18
- Garage (double): 30-36
- Hallway: 6-10
- Foyer: 4-8
- Walk-in closet: 4-6
- Pantry: 2-4
- Mudroom: 4-6

Standard adjacency rules (ALWAYS apply these unless the user explicitly says otherwise):
- Kitchen adjacent to dining room
- Master bathroom adjacent/connected to master bedroom
- Garage adjacent to kitchen or mudroom (never adjacent to bedrooms)
- Living room is the central hub — adjacent to kitchen, dining, hallway/foyer
- Bedrooms grouped together, away from garage/utility
- Bathrooms nearby bedrooms
- Foyer/hallway connected to living room

For Quebec (CA-QC) default setbacks: front 4.5m, rear 7.5m, sides 1.5m.
Default lot coverage: 60%. Default floor height: 2.7m.

AREA BUDGET — THIS IS CRITICAL:
Before assigning room sizes, you MUST calculate the maximum usable footprint:
  buildable_width = lot_width - left_setback - right_setback
  buildable_depth = lot_depth - front_setback - rear_setback
  max_footprint = buildable_width × buildable_depth × max_lot_coverage × num_floors

The sum of ALL room target_area_sqm values MUST NOT exceed max_footprint.
If the user's request would exceed the budget, scale down non-essential rooms (hallways,
closets, laundry, storage) first, then reduce secondary rooms (dining, office, extra
bathrooms). Prioritize living spaces and bedrooms. Do NOT add rooms the user didn't
ask for if the budget is tight — only add ancillary rooms (foyer, hallway, laundry) when
there is enough space left over.

Example: 15m × 25m lot, QC setbacks → buildable 12m × 13m = 156sqm.
At 60% coverage, 1 floor: max footprint = 93.6sqm. Plan rooms to total ≤ 90sqm.

CRITICAL: You MUST include an "adjacency" array with room relationships. Every floor plan
needs adjacency data — never return an empty adjacency array. Use the standard rules above
to infer relationships even if the user doesn't mention them explicitly.

Respond ONLY with valid JSON matching the FloorPlanSpec schema. No explanations.\
"""

EDIT_SYSTEM_PROMPT = """\
You are an expert residential architect AI. The user wants to modify an existing
floor plan. Given the current floor plan specification and the user's edit request,
output a JSON describing the changes.

Rules:
- Only modify rooms explicitly mentioned
- Maintain all adjacency relationships unless the edit breaks them
- When resizing a room, adjust neighboring rooms to compensate
- When adding a room, take space from the largest adjacent room unless specified
- When removing a room, distribute its space to neighbors
- Keep total footprint within the buildable envelope

Respond ONLY with a JSON object containing an "actions" key with a list of EditAction objects.
Format: {"actions": [{"action": "resize|move|add_room|remove_room|swap|merge|split", "target_room_id": "living_0", "parameters": {}}]}
No explanations.\
"""

# Outlines schema string for constrained decoding
# This is used when the LLM is served via SGLang/vLLM with Outlines backend
FLOOR_PLAN_JSON_SCHEMA = {
    "type": "object",
    "required": ["rooms", "plot", "num_floors"],
    "properties": {
        "rooms": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "type", "label", "target_area_sqm"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": [
                            "living", "kitchen", "dining", "bedroom", "bathroom",
                            "half_bath", "master_bedroom", "master_bathroom", "office",
                            "laundry", "garage", "hallway", "closet", "walkin_closet",
                            "pantry", "mudroom", "foyer", "staircase", "storage",
                            "balcony", "porch", "utility", "other",
                        ],
                    },
                    "label": {"type": "string"},
                    "target_area_sqm": {"type": "number", "minimum": 1},
                    "floor": {"type": "integer", "minimum": 0},
                    "requires_window": {"type": "boolean"},
                    "requires_exterior_wall": {"type": "boolean"},
                },
            },
        },
        "adjacency": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["room_a_id", "room_b_id", "relationship"],
                "properties": {
                    "room_a_id": {"type": "string"},
                    "room_b_id": {"type": "string"},
                    "relationship": {
                        "type": "string",
                        "enum": ["adjacent", "nearby", "separated", "connected"],
                    },
                },
            },
        },
        "plot": {
            "type": "object",
            "required": ["width_m", "depth_m"],
            "properties": {
                "width_m": {"type": "number", "minimum": 5},
                "depth_m": {"type": "number", "minimum": 5},
                "setbacks": {
                    "type": "object",
                    "properties": {
                        "front_m": {"type": "number"},
                        "rear_m": {"type": "number"},
                        "left_m": {"type": "number"},
                        "right_m": {"type": "number"},
                    },
                },
                "max_lot_coverage": {"type": "number", "minimum": 0.1, "maximum": 1.0},
            },
        },
        "num_floors": {"type": "integer", "minimum": 1, "maximum": 5},
        "style": {"type": "string"},
        "locale": {"type": "string"},
    },
}
