"""Compliance engine — 3-tier building code checking.

Tier 1: DeterministicRuleChecker — pure geometry (NBC Part 9)
Tier 2: GraphRuleChecker — Neo4j graph queries (zone-specific)
Tier 3: LLMComplianceInterpreter — Gemini RAG interpretation
"""

from app.services.compliance.checker import ComplianceChecker
from app.services.compliance.deterministic_rules import DeterministicRuleChecker
from app.services.compliance.zone_loader import ZoneDataLoader
from app.services.compliance.zone_spec_parser import ZoneSpec, parse_zone_spec

__all__ = [
    "ComplianceChecker",
    "DeterministicRuleChecker",
    "ZoneDataLoader",
    "ZoneSpec",
    "parse_zone_spec",
]
