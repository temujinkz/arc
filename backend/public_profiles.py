"""Public Neo4j profile and separately attributed peer statements."""

import json
import os
from neo4j import AsyncGraphDatabase

from backend.graph_models import AgentConstructedGraph


async def query(cypher: str, **params):
    async with AsyncGraphDatabase.driver(os.environ["NEO4J_URI"], auth=(
        os.getenv("NEO4J_USERNAME") or os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])) as driver:
        async with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            result = await session.run(cypher, **params)
            return await result.data()


async def get_profile(profile_id: str) -> dict | None:
    rows = await query("MATCH (r:ARCInvestigation {session_id:$sid}) WHERE coalesce(r.withdrawn, false) = false "
                       "RETURN r.graph_json AS graph, r.summary AS summary", sid=profile_id)
    if not rows or not rows[0]["graph"]:
        return None
    graph = AgentConstructedGraph.model_validate(json.loads(rows[0]["graph"])).model_dump()
    person = next(n for n in graph["nodes"] if n["label"] == "Person")
    return {"profile_id": profile_id, "name": person["title"], "summary": rows[0]["summary"],
            "graph": graph, "judgment_kind": "arc_inferred"}


async def peer_claims(profile_id: str) -> list[dict]:
    rows = await query("MATCH (p:PeerClaim)-[:ABOUT_PROFILE]->(r:ARCInvestigation {session_id:$sid}) "
                       "RETURN p.claim_id AS claim_id, p.author_name AS author_name, p.author_url AS author_url, "
                       "p.relationship AS relationship, p.statement AS statement, p.attribution AS attribution, "
                       "toString(p.created_at) AS created_at ORDER BY p.created_at DESC", sid=profile_id)
    return rows


async def add_peer_claim(profile_id: str, body: dict) -> dict | None:
    await query("CREATE CONSTRAINT arc_peer_claim_identity IF NOT EXISTS "
                "FOR (p:PeerClaim) REQUIRE (p.profile_id, p.claim_id) IS UNIQUE")
    # Idempotent create; a reused ID cannot change someone else's attributed text.
    rows = await query("MATCH (r:ARCInvestigation {session_id:$sid}) WHERE coalesce(r.withdrawn, false) = false "
        "MERGE (p:PeerClaim {profile_id:$sid, claim_id:$claim_id}) "
        "ON CREATE SET p.author_name=$author_name, p.author_url=$author_url, "
        "p.relationship=$relationship, p.statement=$statement, "
        "p.attribution='self_attributed', p.created_at=datetime() "
        "MERGE (p)-[:ABOUT_PROFILE]->(r) "
        "RETURN p.author_name AS author_name, p.author_url AS author_url, p.relationship AS relationship, "
        "p.statement AS statement, p.claim_id AS claim_id", sid=profile_id, **body)
    return rows[0] if rows else None
