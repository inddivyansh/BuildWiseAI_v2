"""In-memory session cache for generated floor plans.

Provides O(1) lookup by generation_id so the export and edit routes
can retrieve previously generated plans within the same server process.
Capped at MAX_ENTRIES to prevent unbounded memory growth.

Role in the unified backend
---------------------------
This is a **session cache only**, not durable storage.  Durable, cross-
restart storage lives in the Neo4j graph as `:Blueprint` nodes, written
synchronously inside the generate and edit route handlers when the caller
supplies a `project_id` (see app/api/routes/generate.py and
app/api/routes/edit.py).

The export and edit routes look up plans from this cache because they run
in the same process and need low-latency access without a database round-trip.
Plans evicted from the cache (LRU, MAX_ENTRIES cap) can still be recovered
from Neo4j via GET /projects/{project_id}/blueprints/{blueprint_id} as long
as they were originally generated with a project_id.
"""

from collections import OrderedDict

from app.schemas.floor_plan import GeneratedFloorPlan

MAX_ENTRIES = 200


class PlanStore:
    """Ordered-dict LRU cache keyed by generation_id with tenant isolation."""

    def __init__(self, max_entries: int = MAX_ENTRIES):
        self._plans: OrderedDict[str, GeneratedFloorPlan] = OrderedDict()
        self._meta: OrderedDict[str, dict] = OrderedDict()
        self._max = max_entries

    def put(
        self,
        plan: GeneratedFloorPlan,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        gid = plan.generation_id
        if gid in self._plans:
            self._plans.move_to_end(gid)
            self._meta.move_to_end(gid)
        self._plans[gid] = plan
        self._meta[gid] = {"user_id": user_id, "project_id": project_id}
        while len(self._plans) > self._max:
            self._plans.popitem(last=False)
            self._meta.popitem(last=False)

    def get(
        self, generation_id: str, user_id: str | None = None
    ) -> GeneratedFloorPlan | None:
        plan = self._plans.get(generation_id)
        if not plan:
            return None
        # Enforce user isolation if user_id is provided and the plan has an owner
        meta = self._meta.get(generation_id, {})
        owner_id = meta.get("user_id")
        if user_id and owner_id and owner_id != user_id:
            return None
        self._plans.move_to_end(generation_id)
        self._meta.move_to_end(generation_id)
        return plan

    def get_metadata(self, generation_id: str) -> dict:
        return self._meta.get(generation_id, {})

    def list_ids(self) -> list[str]:
        return list(self._plans.keys())

    def __len__(self) -> int:
        return len(self._plans)


# Module-level singleton used by generate, edit, export, and ingest routes
plan_store = PlanStore()
