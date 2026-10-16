#!/usr/bin/env bash
# Setup Neo4j for ArchiGen compliance engine.
# Usage: bash infra/scripts/setup_neo4j.sh

set -euo pipefail

NEO4J_CONTAINER="archigen-neo4j"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-archigen_dev}"
NEO4J_VERSION="5-community"

echo "=== ArchiGen Neo4j Setup ==="

# Stop existing container if running
if docker ps -a --format '{{.Names}}' | grep -q "^${NEO4J_CONTAINER}$"; then
  echo "Stopping existing container..."
  docker rm -f "$NEO4J_CONTAINER" >/dev/null 2>&1
fi

echo "Starting Neo4j ${NEO4J_VERSION}..."
docker run -d --name "$NEO4J_CONTAINER" \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH="neo4j/${NEO4J_PASSWORD}" \
  -e NEO4J_PLUGINS='["apoc"]' \
  -v archigen-neo4j-data:/data \
  "neo4j:${NEO4J_VERSION}"

echo "Waiting for Neo4j to start..."
for i in $(seq 1 30); do
  if docker exec "$NEO4J_CONTAINER" cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
    "RETURN 1" >/dev/null 2>&1; then
    echo "Neo4j is ready."
    break
  fi
  sleep 2
done

echo "Creating constraints and indexes..."
docker exec "$NEO4J_CONTAINER" cypher-shell -u neo4j -p "$NEO4J_PASSWORD" <<'EOF'
CREATE CONSTRAINT clause_id IF NOT EXISTS FOR (c:Clause) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT zone_id IF NOT EXISTS FOR (z:Zone) REQUIRE z.id IS UNIQUE;
CREATE CONSTRAINT topic_name IF NOT EXISTS FOR (t:Topic) REQUIRE t.name IS UNIQUE;
CREATE CONSTRAINT jurisdiction_name IF NOT EXISTS FOR (j:Jurisdiction) REQUIRE j.name IS UNIQUE;
CREATE INDEX clause_jurisdiction IF NOT EXISTS FOR (c:Clause) ON (c.jurisdiction);
CREATE FULLTEXT INDEX clause_text IF NOT EXISTS FOR (n:Clause) ON EACH [n.text, n.title];
EOF

echo "=== Neo4j setup complete ==="
echo "  Browser: http://localhost:7474"
echo "  Bolt:    bolt://localhost:7687"
echo "  User:    neo4j / ${NEO4J_PASSWORD}"
