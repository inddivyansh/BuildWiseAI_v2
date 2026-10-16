"""Tests for Neo4j compliance graph client (mocked)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.compliance.neo4j_client import ComplianceGraphClient


class MockRecord:
    """Mock a Neo4j record."""
    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()


class MockResult:
    """Mock a Neo4j result."""
    def __init__(self, records):
        self._records = records
        self._index = 0

    async def single(self):
        return self._records[0] if self._records else None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._records):
            raise StopAsyncIteration
        record = self._records[self._index]
        self._index += 1
        return record


class MockSession:
    """Mock a Neo4j async session."""
    def __init__(self, results=None):
        self._results = results or {}

    async def run(self, cypher, **kwargs):
        # Return a mock result based on the cypher query string
        for key, result in self._results.items():
            if key in cypher:
                return result
        return MockResult([])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class MockDriver:
    """Mock a Neo4j async driver."""
    def __init__(self, session):
        self._session = session

    def session(self, **kwargs):
        return self._session

    async def close(self):
        pass


@pytest.fixture
def mock_client():
    """Create a ComplianceGraphClient with mocked driver."""
    with patch("app.services.compliance.neo4j_client.AsyncGraphDatabase") as mock_db:
        client = ComplianceGraphClient.__new__(ComplianceGraphClient)

        # Setup mock session with results for common queries
        results = {
            "RETURN 1 AS ok": MockResult([MockRecord({"ok": 1})]),
            "get_zone_rules": MockResult([MockRecord({
                "s": {"max_height_m": 22.0, "front_margin_m": 6.0},
                "clauses": [],
                "refs": [],
            })]),
            "COVERS_TOPIC": MockResult([
                MockRecord({
                    "id": "NBC:9.5.3.1",
                    "text": "Minimum ceiling height 2.1m",
                    "title": "Ceiling Height",
                    "jurisdiction": "NBC",
                    "level": "national",
                }),
                MockRecord({
                    "id": "municipal:340",
                    "text": "Hauteur maximale 22m",
                    "title": "Height",
                    "jurisdiction": "municipal",
                    "level": "municipal",
                }),
            ]),
            "REFERENCES*1": MockResult([
                MockRecord({
                    "id": "municipal:331.0.1",
                    "text": "Angle d'éloignement",
                    "title": "Setback angle",
                    "jurisdiction": "municipal",
                }),
            ]),
            "Definition": MockResult([
                MockRecord({
                    "term": "aire constructible",
                    "text": "La superficie d'un lot excluant les marges",
                    "jurisdiction": "municipal",
                }),
            ]),
            "fulltext": MockResult([
                MockRecord({
                    "id": "municipal:340",
                    "text": "Calcul de la hauteur maximale",
                    "title": "Height calculation",
                    "jurisdiction": "municipal",
                    "score": 0.95,
                }),
            ]),
        }

        session = MockSession(results)
        driver = MockDriver(session)
        client._driver = driver
        client._database = None

        yield client


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_ok(self, mock_client):
        result = await mock_client.health()
        assert result is True


class TestGetRulesForTopic:
    @pytest.mark.asyncio
    async def test_returns_rules_ordered_by_jurisdiction(self, mock_client):
        rules = await mock_client.get_rules_for_topic("height")
        assert len(rules) >= 1
        # Should have jurisdiction info
        for rule in rules:
            assert "jurisdiction" in rule
            assert "text" in rule


class TestResolveCrossReferences:
    @pytest.mark.asyncio
    async def test_returns_referenced_clauses(self, mock_client):
        refs = await mock_client.resolve_cross_references("municipal:340", depth=2)
        assert len(refs) >= 1
        assert refs[0]["id"] == "municipal:331.0.1"


class TestGetDefinitions:
    @pytest.mark.asyncio
    async def test_returns_definitions(self, mock_client):
        defs = await mock_client.get_definitions(["aire constructible"])
        assert len(defs) >= 1
        assert defs[0]["term"] == "aire constructible"


class TestSearchClauses:
    @pytest.mark.asyncio
    async def test_fulltext_search(self, mock_client):
        results = await mock_client.search_clauses("hauteur maximale", limit=5)
        assert len(results) >= 1
        assert "score" in results[0]


class TestClose:
    @pytest.mark.asyncio
    async def test_close(self, mock_client):
        await mock_client.close()  # Should not raise
