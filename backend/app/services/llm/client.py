"""
LLM client abstraction for Ollama and SGLang.

Both expose OpenAI-compatible /v1/chat/completions endpoints,
so we use the openai Python SDK as the unified client.

Structured output is enforced via:
  1. Ollama: `format` parameter with JSON schema
  2. SGLang: `response_format` with Outlines-backed constrained decoding
  3. Fallback: JSON mode + post-validation with Pydantic
"""

import json
import structlog
from openai import AsyncOpenAI

from app.config import settings
from app.services.llm.prompts import (
    ARCHITECT_SYSTEM_PROMPT,
    EDIT_SYSTEM_PROMPT,
    FLOOR_PLAN_JSON_SCHEMA,
)
from app.schemas.floor_plan import FloorPlanSpec

logger = structlog.get_logger()


class LLMClient:
    """Unified LLM client for Ollama / SGLang / Gemini / any OpenAI-compatible server."""

    def __init__(
        self,
        provider: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ):
        import os

        self.provider = (provider or settings.llm_provider).lower()
        self.model = model or settings.llm_model

        # Map older or decommissioned model names to active working provider endpoints
        MODEL_UPGRADE_MAP = {
            "gemini-2.5-flash": "gemini-3.5-flash",
            "gemini-1.5-flash": "gemini-3.5-flash",
            "gemini-2.0-flash": "gemini-3.5-flash",
            "qwen-2.5-32b": "qwen/qwen3.8-27b",
            "qwen-2.5-coder-32b": "qwen/qwen3.8-27b",
        }
        effective_model = MODEL_UPGRADE_MAP.get(self.model, self.model)

        # Extract pure model name for endpoint
        if self.provider == "ollama":
            raw_model = self.model.replace("ollama:", "")
            if "gemma" in raw_model.lower():
                self.model_call_name = "gemma4:e4b"
            elif "qwen" in raw_model.lower():
                self.model_call_name = "qwen2.5"
            else:
                self.model_call_name = raw_model
        elif effective_model.startswith("ollama:"):
            self.model_call_name = effective_model.split(":", 1)[1]
        else:
            self.model_call_name = effective_model

        if base_url:
            api_base = base_url
            resolved_key = api_key or "not-needed"
        elif self.provider == "gemini":
            api_base = settings.gemini_base_url
            resolved_key = (
                api_key
                or settings.gemini_api_key
                or os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY")
                or "not-needed"
            )
        elif self.provider == "groq":
            api_base = getattr(settings, "groq_base_url", "https://api.groq.com/openai/v1")
            resolved_key = (
                api_key
                or getattr(settings, "groq_api_key", "")
                or os.environ.get("GROQ_API_KEY")
                or "not-needed"
            )
        elif self.provider == "xai":
            api_base = getattr(settings, "xai_base_url", "https://api.x.ai/v1")
            resolved_key = (
                api_key
                or getattr(settings, "xai_api_key", "")
                or os.environ.get("XAI_API_KEY")
                or "not-needed"
            )
        elif self.provider == "ollama":
            api_base = f"{settings.ollama_base_url}/v1"
            resolved_key = "ollama"
        elif self.provider == "sglang":
            api_base = f"{settings.sglang_base_url}/v1"
            resolved_key = "sglang"
        elif self.provider == "openai":
            api_base = "https://api.openai.com/v1"
            resolved_key = (
                api_key
                or getattr(settings, "openai_api_key", "")
                or os.environ.get("OPENAI_API_KEY")
                or "not-needed"
            )
        elif self.provider == "anthropic":
            api_base = "https://api.anthropic.com/v1"
            resolved_key = (
                api_key
                or getattr(settings, "anthropic_api_key", "")
                or os.environ.get("ANTHROPIC_API_KEY")
                or "not-needed"
            )
        else:
            api_base = f"{settings.ollama_base_url}/v1"
            resolved_key = "not-needed"

        self.base_url = api_base
        extra_headers = {}
        if self.provider == "anthropic":
            extra_headers["anthropic-version"] = "2023-06-01"

        self._client = AsyncOpenAI(
            base_url=api_base,
            api_key=resolved_key,
            default_headers=extra_headers if extra_headers else None,
        )

        logger.info(
            "llm_client_init",
            provider=self.provider,
            model=self.model,
            base_url=api_base,
        )

    async def parse_nl_to_spec(
        self,
        prompt: str,
        plot_width_m: float | None = None,
        plot_depth_m: float | None = None,
        locale: str = "CA-QC",
    ) -> FloorPlanSpec:
        """
        Parse a natural language description into a FloorPlanSpec.

        Sends the architect system prompt + user prompt to the LLM,
        enforces JSON output, and validates against the Pydantic schema.
        """
        user_message = prompt

        # Detect explicit target area in prompt (e.g. "72m²", "72 sqm", "120 square meters")
        import re
        area_match = re.search(r"(\d+(\.\d+)?)\s*(sqm|m2|m²|square meters?)", prompt, re.IGNORECASE)
        if area_match:
            try:
                target_sqm = float(area_match.group(1))
                user_message += (
                    f"\n\nCRITICAL AREA CONSTRAINT: Total requested blueprint area is {target_sqm:.1f} m²."
                    f"\nThe sum of ALL room target_area_sqm values MUST equal approximately {target_sqm:.1f} m²."
                    f"\nAllocate space proportionally with architectural common sense: core primary spaces must be largest,"
                    f" and secondary/sanitary spaces (e.g. bathrooms) must remain compact and proportional (never oversized)."
                )
            except ValueError:
                pass

        if plot_width_m and plot_depth_m:
            # Compute and inject actual budget so the LLM can't ignore it
            front = 4.5
            rear = 7.5
            side = 1.5
            bw = plot_width_m - 2 * side
            bd = plot_depth_m - front - rear
            budget = round(bw * bd * 0.6, 1)  # 60% lot coverage
            user_message += (
                f"\n\nLot dimensions: {plot_width_m}m wide × {plot_depth_m}m deep."
                f"\nBuildable area: {round(bw, 1)}m × {round(bd, 1)}m = {round(bw * bd, 1)}sqm."
                f"\nAt 60% lot coverage, MAXIMUM total room area = {budget}sqm."
                f"\nAll room areas MUST sum to ≤ {budget}sqm. Do NOT exceed this."
            )
        if locale:
            user_message += f"\nLocale: {locale}"

        raw_json = await self._chat_json(
            system_prompt=ARCHITECT_SYSTEM_PROMPT,
            user_message=user_message,
            json_schema=FLOOR_PLAN_JSON_SCHEMA,
        )

        # Unwrap if LLM wrapped in an outer key
        raw_json = self._unwrap_spec(raw_json)

        # Normalize camelCase → snake_case
        raw_json = self._normalize_keys(raw_json)

        # Coerce LLM output to match our schema exactly
        raw_json = self._coerce_spec(raw_json, plot_width_m, plot_depth_m)

        # Validate with Pydantic
        spec = FloorPlanSpec(**raw_json)
        logger.info(
            "nl_parsed",
            num_rooms=len(spec.rooms),
            total_area=spec.total_target_area,
        )
        return spec

    async def parse_edit_command(
        self,
        prompt: str,
        current_spec: dict,
    ) -> dict:
        """Parse an NL edit command against an existing floor plan."""
        user_message = (
            f"Current floor plan:\n{json.dumps(current_spec, indent=2)}\n\n"
            f"Edit request: {prompt}"
        )
        return await self._chat_json(
            system_prompt=EDIT_SYSTEM_PROMPT,
            user_message=user_message,
        )

    async def _chat_json(
        self,
        system_prompt: str,
        user_message: str,
        json_schema: dict | None = None,
    ) -> dict:
        """
        Send a chat completion request and return parsed JSON.

        Tries structured output first, falls back to JSON mode,
        then falls back to raw text extraction.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        target_model = getattr(self, "model_call_name", self.model)
        kwargs: dict = {
            "model": target_model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 4096,
        }

        # Provider-specific structured output
        if self.provider == "sglang" and json_schema:
            # SGLang supports response_format with Outlines-backed JSON schema
            kwargs["extra_body"] = {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"schema": json_schema},
                }
            }
            kwargs["response_format"] = {"type": "json_object"}
        elif self.provider == "ollama":
            # Ollama supports format parameter for JSON output
            kwargs["extra_body"] = {"format": "json"}
            kwargs["response_format"] = {"type": "json_object"}
        elif self.provider in ("gemini", "groq", "xai", "openai"):
            # OpenAI-compatible endpoints with json_object mode
            kwargs["response_format"] = {"type": "json_object"}
        else:
            kwargs["response_format"] = {"type": "json_object"}

        logger.debug("llm_request", provider=self.provider, model=target_model)

        if self.provider == "gemini":
            # Pool of Google AI Studio free tier models ordered for optimal rate-limit tolerance
            gemini_pool = [
                "gemini-3.5-flash-lite",
                "gemini-3.5-flash",
                "gemini-3.8-flash",
                "gemini-3.7-flash",
                "gemini-3.6-flash",
                "gemini-flash-latest",
                "gemini-flash-lite-latest",
                "gemini-2.5-flash",
            ]
            if target_model and target_model in gemini_pool:
                candidates = [target_model] + [m for m in gemini_pool if m != target_model]
            else:
                candidates = list(gemini_pool)

            last_exc = None
            for idx, candidate in enumerate(candidates):
                kwargs["model"] = candidate
                logger.info("gemini_inference_attempt", model=candidate, attempt=idx + 1, total=len(candidates))
                try:
                    response = await self._client.chat.completions.create(**kwargs)
                    content = response.choices[0].message.content
                    if not content:
                        raise ValueError(f"Empty response received from Gemini ({candidate})")
                    logger.info("gemini_inference_success", model=candidate)
                    return self._extract_json(content)
                except Exception as exc:
                    err_str = str(exc)
                    last_exc = exc
                    status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
                    is_rate_limit = (
                        status_code == 429
                        or "429" in err_str
                        or "quota" in err_str.lower()
                        or "rate limit" in err_str.lower()
                        or "resource_exhausted" in err_str.lower()
                        or "RateLimitError" in type(exc).__name__
                    )
                    is_transient = (
                        is_rate_limit
                        or status_code in (503, 502, 504, 404)
                        or "503" in err_str
                        or "404" in err_str
                        or "unavailable" in err_str.lower()
                        or "high demand" in err_str.lower()
                        or "spikes in demand" in err_str.lower()
                        or "not found" in err_str.lower()
                        or "timeout" in err_str.lower()
                    )
                    if is_transient and idx < len(candidates) - 1:
                        next_model = candidates[idx + 1]
                        logger.warning(
                            "gemini_model_quota_exceeded_auto_shifting",
                            exhausted_model=candidate,
                            next_model=next_model,
                            is_rate_limit=is_rate_limit,
                            error=err_str[:120],
                        )
                        if is_rate_limit:
                            # On 429 / quota limit, Gemini free tier enforces a per-minute limit (RPM).
                            # We MUST wait ~60s before calling the next model so the rate limit window
                            # resets, otherwise all models in the pool will fail instantly back-to-back.
                            import asyncio as _asyncio
                            logger.info(
                                "gemini_429_waiting_60s_rpm_reset",
                                exhausted_model=candidate,
                                next_model=next_model,
                                wait_seconds=60,
                            )
                            await _asyncio.sleep(60.0)
                        elif "503" in err_str or status_code == 503:
                            import asyncio as _asyncio
                            await _asyncio.sleep(3.0)
                        continue
                    break

            err_str = str(last_exc)
            status_code = getattr(last_exc, "status_code", None) or getattr(getattr(last_exc, "response", None), "status_code", None)
            if status_code == 429 or "429" in err_str or "rate limit" in err_str.lower() or "quota" in err_str.lower() or "resource_exhausted" in err_str.lower():
                raise RuntimeError(
                    f"All Google Gemini models reached their quota / rate limits ({len(candidates)} models tried with backoff). "
                    "Please wait a minute or switch to a local Ollama model (Qwen 2.5 / Gemma)."
                ) from last_exc
            if status_code == 503 or "503" in err_str or "unavailable" in err_str.lower() or "high demand" in err_str.lower():
                raise RuntimeError(
                    "Google Gemini is currently experiencing temporary high demand across all models (503). "
                    "Please retry shortly or use your offline local model."
                ) from last_exc
            if status_code == 401 or "401" in err_str or "auth" in err_str.lower() or "api key" in err_str.lower():
                raise RuntimeError(
                    "Google Gemini authentication failed (401). Please verify GEMINI_API_KEY in your .env file."
                ) from last_exc
            raise RuntimeError(f"Google Gemini inference failed: {err_str[:200]}") from last_exc

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            err_str = str(exc)
            logger.error("llm_call_failed", provider=self.provider, model=target_model, error=err_str)
            if "429" in err_str or "rate limit" in err_str.lower() or "quota" in err_str.lower():
                raise RuntimeError(
                    f"AI model quota exceeded or rate limit reached (429) for {self.provider} ({target_model}). "
                    "Please wait a few moments before retrying."
                ) from exc
            if "503" in err_str or "unavailable" in err_str.lower() or "high demand" in err_str.lower():
                raise RuntimeError(
                    f"AI model ({target_model}) is currently experiencing high demand / unavailable (503). "
                    "Please try again in a few moments."
                ) from exc
            if "timeout" in err_str.lower() or "connection" in err_str.lower():
                raise RuntimeError(
                    f"Connection to {self.provider} timed out. The model was unresponsive. Please retry."
                ) from exc
            raise RuntimeError(
                f"AI model inference failed on {self.provider} ({target_model}): {err_str[:200]}"
            ) from exc

        content = response.choices[0].message.content

        logger.debug(
            "llm_response",
            tokens_used=response.usage.total_tokens if response.usage else None,
            content_length=len(content) if content else 0,
        )

        return self._extract_json(content)

    def _extract_json(self, text: str) -> dict:
        """Extract JSON from LLM response, handling markdown fences and extra text."""
        if not text:
            raise ValueError("Empty LLM response")

        text = text.strip()

        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code fences
        if "```" in text:
            # Find content between ```json and ```  or ``` and ```
            import re
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    pass

        # Try finding first { to last }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Could not extract JSON from LLM response: {text[:200]}")

    # Maps human-readable room names → our enum values across all blueprint types
    _ROOM_TYPE_MAP = {
        # Residential
        "living room": "living", "living": "living", "family room": "living", "salon": "living",
        "kitchen": "kitchen", "kitchenette": "kitchen", "cuisine": "kitchen",
        "dining room": "dining", "dining": "dining", "salle à manger": "dining",
        "bedroom": "bedroom", "bed": "bedroom", "chambre": "bedroom", "guest bedroom": "bedroom",
        "bathroom": "bathroom", "bath": "bathroom", "restroom": "bathroom", "washroom": "bathroom", "wc": "half_bath",
        "half bath": "half_bath", "half bathroom": "half_bath", "powder room": "half_bath",
        "master bedroom": "master_bedroom", "master bed": "master_bedroom", "primary bedroom": "master_bedroom",
        "master bathroom": "master_bathroom", "master bath": "master_bathroom", "ensuite": "master_bathroom",
        "office": "office", "study": "office", "home office": "office", "bureau": "office",
        "laundry": "laundry", "laundry room": "laundry", "buanderie": "laundry",
        "garage": "garage", "double garage": "garage",
        "hallway": "hallway", "hall": "hallway", "corridor": "hallway", "circulation": "hallway",
        "closet": "closet",
        "walk-in closet": "walkin_closet", "walkin closet": "walkin_closet", "walk in closet": "walkin_closet",
        "pantry": "pantry",
        "mudroom": "mudroom", "mud room": "mudroom",
        "foyer": "foyer", "entry": "foyer", "entryway": "foyer", "entrance": "foyer", "lobby": "foyer",
        "staircase": "staircase", "stairs": "staircase",
        "storage": "storage", "storage room": "storage",
        "balcony": "balcony",
        "porch": "porch", "front porch": "porch",
        "utility": "utility", "utility room": "utility", "mechanical": "utility",
        # Commercial & Retail
        "sales floor": "other", "retail floor": "other", "showroom": "other", "boutique": "other",
        "checkout": "foyer", "customer area": "other", "stockroom": "storage", "inventory": "storage",
        # Workplace & Office
        "open office": "office", "workstation": "office", "workspace": "office",
        "conference room": "office", "meeting room": "office", "boardroom": "office", "huddle room": "office",
        "reception": "foyer", "breakroom": "kitchen", "server room": "utility", "it closet": "utility",
        # Industrial & Warehouse
        "warehouse bay": "storage", "storage bay": "storage", "loading dock": "utility",
        "staging area": "utility", "production floor": "other", "workshop": "other", "control room": "office",
        # Healthcare & Medical
        "exam room": "office", "examination room": "office", "consultation room": "office",
        "triage": "foyer", "waiting room": "foyer", "waiting area": "foyer",
        "procedure room": "other", "treatment room": "other", "pharmacy": "storage",
        # Educational & Institutional
        "classroom": "other", "lecture hall": "other", "lab": "other", "laboratory": "other",
        "library": "other", "faculty office": "office", "staff room": "office",
    }

    def _coerce_spec(
        self, data: dict,
        plot_width_m: float | None,
        plot_depth_m: float | None,
    ) -> dict:
        """Coerce messy LLM output into our exact schema shape."""
        # Fix rooms
        if "rooms" in data:
            fixed_rooms = []
            seen_ids = set()
            for i, room in enumerate(data["rooms"]):
                fixed = self._coerce_room(room, i, seen_ids)
                if fixed:
                    fixed_rooms.append(fixed)
                    seen_ids.add(fixed["id"])
            data["rooms"] = fixed_rooms

        # Fix plot
        if "plot" not in data:
            data["plot"] = {}
        plot = data["plot"]
        if "width_m" not in plot:
            plot["width_m"] = plot_width_m or 12.0
        if "depth_m" not in plot:
            plot["depth_m"] = plot_depth_m or 20.0

        # Preserve style if present
        if "style" not in data or not data.get("style"):
            data.pop("style", None)  # let schema default handle it

        # Fix adjacency — coerce formats, then infer defaults if still empty
        if "adjacency" not in data:
            data["adjacency"] = []
        if data["adjacency"] and data.get("rooms"):
            data["adjacency"] = self._coerce_adjacency(data["adjacency"], data["rooms"])
        if not data["adjacency"] and data.get("rooms"):
            data["adjacency"] = self._infer_default_adjacency(data["rooms"])

        return data

    def _coerce_adjacency(self, raw_adj: list, rooms: list[dict]) -> list[dict]:
        """Coerce messy LLM adjacency output into proper schema objects.

        Handles formats like:
          - ["kitchen", "dining room"]          (list of two names/types)
          - {"room_a": "kitchen", "room_b": "dining", "type": "adjacent"}
          - {"room_a_id": "kitchen_1", "room_b_id": "dining_0", ...}  (correct)
        """
        # Build lookup: lowered type/label/id → room id
        lookup: dict[str, str] = {}
        for r in rooms:
            rid = r.get("id", "")
            rtype = r.get("type", "")
            rlabel = r.get("label", "")
            lookup[rid.lower()] = rid
            lookup[rtype.lower()] = rid
            if rlabel:
                lookup[rlabel.lower()] = rid

        def _resolve(name: str) -> str | None:
            name_l = name.strip().lower()
            if name_l in lookup:
                return lookup[name_l]
            # Partial match
            for key, rid in lookup.items():
                if name_l in key or key in name_l:
                    return rid
            return None

        result = []
        seen = set()
        for item in raw_adj:
            a_id = b_id = rel = None

            if isinstance(item, list) and len(item) >= 2:
                # ["kitchen", "dining room"] or ["kitchen", "dining", "adjacent"]
                a_id = _resolve(str(item[0]))
                b_id = _resolve(str(item[1]))
                rel = str(item[2]) if len(item) > 2 else "adjacent"
            elif isinstance(item, dict):
                # Various key names the LLM might use
                a_raw = (
                    item.get("room_a_id") or item.get("room_a")
                    or item.get("source") or item.get("from") or ""
                )
                b_raw = (
                    item.get("room_b_id") or item.get("room_b")
                    or item.get("target") or item.get("to") or ""
                )
                a_id = _resolve(str(a_raw))
                b_id = _resolve(str(b_raw))
                rel = (
                    item.get("relationship") or item.get("type")
                    or item.get("relation") or "adjacent"
                )
            else:
                continue

            if not a_id or not b_id or a_id == b_id:
                continue

            # Normalize relationship
            rel = str(rel).lower().strip()
            if rel not in ("adjacent", "nearby", "separated", "connected"):
                rel = "adjacent"

            pair = tuple(sorted((a_id, b_id)))
            if pair in seen:
                continue
            seen.add(pair)

            result.append({
                "room_a_id": a_id,
                "room_b_id": b_id,
                "relationship": rel,
            })

        return result

    # Default adjacency rules keyed by room type.
    # Each entry: (other_type, relationship).
    # "living" acts as the hub — connected to most public rooms.
    _DEFAULT_ADJACENCY_RULES: list[tuple[str, str, str]] = [
        # Public zone
        ("living", "kitchen", "adjacent"),
        ("living", "dining", "adjacent"),
        ("living", "foyer", "connected"),
        ("living", "hallway", "connected"),
        ("kitchen", "dining", "adjacent"),
        ("kitchen", "pantry", "adjacent"),
        ("kitchen", "laundry", "nearby"),
        # Master suite
        ("master_bedroom", "master_bathroom", "connected"),
        ("master_bedroom", "walkin_closet", "connected"),
        # Bedrooms & baths
        ("bedroom", "bathroom", "nearby"),
        ("bedroom", "hallway", "nearby"),
        # Garage / utility
        ("garage", "kitchen", "adjacent"),
        ("garage", "mudroom", "adjacent"),
        ("garage", "laundry", "nearby"),
        # Entry
        ("foyer", "hallway", "connected"),
        ("mudroom", "foyer", "nearby"),
    ]

    def _infer_default_adjacency(self, rooms: list[dict]) -> list[dict]:
        """Build adjacency list from standard architectural rules when LLM omits it."""
        # Index rooms by type → list of room ids
        type_to_ids: dict[str, list[str]] = {}
        for r in rooms:
            rtype = r.get("type", "other")
            type_to_ids.setdefault(rtype, []).append(r.get("id", ""))

        adjacency = []
        seen = set()

        for type_a, type_b, rel in self._DEFAULT_ADJACENCY_RULES:
            ids_a = type_to_ids.get(type_a, [])
            ids_b = type_to_ids.get(type_b, [])
            if not ids_a or not ids_b:
                continue
            # Connect first matching pair (for multi-instance types like bedroom)
            for a_id in ids_a:
                for b_id in ids_b:
                    if a_id == b_id:
                        continue
                    pair = tuple(sorted((a_id, b_id)))
                    if pair in seen:
                        continue
                    seen.add(pair)
                    adjacency.append({
                        "room_a_id": a_id,
                        "room_b_id": b_id,
                        "relationship": rel,
                    })
                    # For "nearby" rules (bedroom↔bathroom), connect one pair per bedroom
                    break
                else:
                    continue
                break  # Only one pair per rule for 1:1 types

        # Ensure all bedrooms connect to at least one bathroom if available
        bath_ids = type_to_ids.get("bathroom", [])
        bed_ids = type_to_ids.get("bedroom", [])
        if bath_ids and bed_ids:
            for i, bed_id in enumerate(bed_ids):
                bath_id = bath_ids[i % len(bath_ids)]
                pair = tuple(sorted((bed_id, bath_id)))
                if pair not in seen:
                    seen.add(pair)
                    adjacency.append({
                        "room_a_id": bed_id,
                        "room_b_id": bath_id,
                        "relationship": "nearby",
                    })

        logger.info("adjacency_inferred", num_rules=len(adjacency))
        return adjacency

    def _coerce_room(self, room: dict, index: int, seen_ids: set) -> dict | None:
        """Coerce a single room dict into our schema."""
        if not isinstance(room, dict):
            return None

        # Resolve room type
        raw_type = str(room.get("type", room.get("room_type", "other")))
        resolved_type = self._ROOM_TYPE_MAP.get(raw_type.lower().strip(), None)
        if not resolved_type:
            # Try partial match
            for key, val in self._ROOM_TYPE_MAP.items():
                if key in raw_type.lower():
                    resolved_type = val
                    break
            if not resolved_type:
                resolved_type = "other"

        # Generate label from type or name field
        label = room.get("label") or room.get("name") or room.get("type", f"Room {index}")

        # Generate ID
        room_id = room.get("id", "")
        if not room_id:
            base = f"{resolved_type}_{index}"
            room_id = base
            counter = 2
            while room_id in seen_ids:
                room_id = f"{resolved_type}_{counter}"
                counter += 1

        # Resolve target area
        area = (
            room.get("target_area_sqm")
            or room.get("target_area")
            or room.get("area_sqm")
            or room.get("area")
            or room.get("size")
            or room.get("size_sqm")
        )
        if area is None:
            # Use defaults based on type
            area = {
                "living": 24, "kitchen": 10, "dining": 12,
                "bedroom": 11, "bathroom": 4.5, "half_bath": 2.2,
                "master_bedroom": 16, "master_bathroom": 5.5,
                "office": 12, "laundry": 4.5, "garage": 18,
                "hallway": 6, "closet": 3, "walkin_closet": 4.5,
                "pantry": 3, "mudroom": 4.5, "foyer": 5,
                "staircase": 5, "storage": 6, "utility": 6, "other": 15,
            }.get(resolved_type, 10)

        try:
            area = float(area)
        except (TypeError, ValueError):
            area = 10.0

        return {
            "id": room_id,
            "type": resolved_type,
            "label": str(label),
            "target_area_sqm": max(area, 1.0),
            "floor": int(room.get("floor", 0)),
        }

    def _unwrap_spec(self, data: dict) -> dict:
        """Unwrap LLM output that wraps the spec in an outer key."""
        # Common patterns: {"floorPlanSpec": {...}}, {"floor_plan_spec": {...}}, etc.
        if "rooms" in data and "plot" in data:
            return data  # Already unwrapped
        # Try common wrapper keys
        for key in ["floorPlanSpec", "floor_plan_spec", "spec", "floorPlan", "floor_plan", "result"]:
            if key in data and isinstance(data[key], dict):
                inner = data[key]
                if "rooms" in inner:
                    return inner
        # If there's exactly one key and its value is a dict with rooms, unwrap it
        if len(data) == 1:
            val = next(iter(data.values()))
            if isinstance(val, dict) and "rooms" in val:
                return val
        return data

    def _normalize_keys(self, obj) -> dict | list:
        """Recursively convert camelCase keys to snake_case."""
        import re
        def _to_snake(name: str) -> str:
            # Convert camelCase/PascalCase to snake_case
            s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
            s = re.sub(r'([a-z\d])([A-Z])', r'\1_\2', s)
            return s.lower()

        if isinstance(obj, dict):
            return {_to_snake(k): self._normalize_keys(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._normalize_keys(item) for item in obj]
        return obj

    async def verify_availability(self) -> tuple[bool, str | None]:
        """Check if the LLM server is reachable and responsive, returning (is_ok, error_detail)."""
        target_model = getattr(self, "model_call_name", self.model)
        try:
            if self.provider == "gemini":
                # For Gemini, verify the API key is present without burning precious RPM/RPD quotas with throwaway probes.
                # Auto-shifting across the candidate Gemini model pool is handled dynamically during inference in _chat_json.
                api_key = (
                    getattr(settings, "gemini_api_key", "")
                    or getattr(settings, "google_api_key", "")
                    or os.environ.get("GEMINI_API_KEY")
                    or os.environ.get("GOOGLE_API_KEY")
                )
                if not api_key or api_key == "not-needed" or api_key.startswith("your-"):
                    return False, "GEMINI_API_KEY is not configured in .env."
                return True, None

            if self.provider in ("groq", "xai", "openai", "anthropic"):
                response = await self._client.chat.completions.create(
                    model=target_model,
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=5,
                )
                if response.choices and response.choices[0].message.content is not None:
                    return True, None
                return False, f"Model {target_model} on {self.provider} returned an empty response."

            models = await self._client.models.list()
            model_ids = [m.id for m in models.data]
            if target_model in model_ids or self.model in model_ids:
                return True, None
            matched = next(
                (
                    mid for mid in model_ids
                    if target_model.lower() in mid.lower()
                    or any(p in mid.lower() for p in target_model.lower().split(":"))
                    or ("gemma" in target_model.lower() and "gemma" in mid.lower())
                    or ("qwen" in target_model.lower() and "qwen" in mid.lower())
                ),
                None,
            )
            if matched:
                self.model_call_name = matched
                return True, None
            return False, f"Model '{target_model}' not found in local Ollama. Installed: {', '.join(model_ids) if model_ids else 'None'}."
        except Exception as e:
            err_str = str(e)
            logger.warning("llm_health_check_failed", provider=self.provider, model=target_model, error=err_str)
            if self.provider == "ollama":
                if "connection error" in err_str.lower() or "connect" in err_str.lower() or "refused" in err_str.lower() or "11434" in err_str:
                    msg = f"Ollama is not running locally. Please start Ollama (`ollama serve`) on your machine."
                elif "not found" in err_str.lower() or "404" in err_str:
                    msg = f"Ollama model '{target_model}' not found. Please run: ollama pull {target_model}"
                else:
                    msg = f"Local Ollama error: {err_str[:120]}"
                return False, msg

            provider_name = (
                "Google Gemini" if self.provider == "gemini"
                else "Groq" if self.provider == "groq"
                else "Anthropic" if self.provider == "anthropic"
                else "OpenAI" if self.provider == "openai"
                else "xAI" if self.provider == "xai"
                else self.provider.upper()
            )
            key_name = (
                "GEMINI_API_KEY" if self.provider == "gemini"
                else "GROQ_API_KEY" if self.provider == "groq"
                else "OPENAI_API_KEY" if self.provider == "openai"
                else "ANTHROPIC_API_KEY" if self.provider == "anthropic"
                else "XAI_API_KEY" if self.provider == "xai"
                else f"{self.provider.upper()}_API_KEY"
            )
            if "429" in err_str or "quota" in err_str.lower() or "rate limit" in err_str.lower() or "resource_exhausted" in err_str.lower():
                msg = f"{provider_name} API quota exceeded or rate limit reached (429) for model '{target_model}'. Please wait before retrying or check your {provider_name} quota."
            elif "503" in err_str or "unavailable" in err_str.lower() or "high demand" in err_str.lower() or "spikes in demand" in err_str.lower():
                msg = f"{provider_name} model '{target_model}' is currently experiencing high demand / unavailable (503). Please try again in a few moments."
            elif "401" in err_str or "403" in err_str or "auth" in err_str.lower() or "api key" in err_str.lower():
                msg = f"{provider_name} authentication failed (401/403). Please verify {key_name} in .env."
            elif "timeout" in err_str.lower() or "timed out" in err_str.lower():
                msg = f"{provider_name} connection timed out. The model was unresponsive. Please retry."
            else:
                msg = f"Failed to connect to {provider_name} ({target_model}): {err_str[:150]}"
            return False, msg

    async def health_check(self) -> bool:
        """Check if the LLM server is reachable and the model is available."""
        ok, _ = await self.verify_availability()
        return ok
