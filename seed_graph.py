"""Idempotently seed synthetic HashiraGraph company data."""
from __future__ import annotations

import sys
import argparse
from typing import Any

from graph_store import DEMO_TENANTS, GraphConfigurationError, GraphConnectionError, close_client, connect_graph, graph_health, query_rows

SERVICES = [
    ("Web Checkout", "Customer checkout web experience", "high"),
    ("Checkout API", "Validates carts and coordinates checkout", "critical"),
    ("Payment API", "Authorizes and captures payments", "critical"),
    ("Notification Service", "Sends order and payment notifications", "medium"),
    ("Fraud Service", "Evaluates transaction risk", "high"),
    ("Ledger Service", "Records financial ledger entries", "critical"),
    ("Identity Service", "Authenticates customer sessions", "high"),
]
TEAMS = ["Web Team", "Checkout Team", "Payments Team", "Platform Team", "Risk Team"]
DEPENDENCIES = [
    ("Web Checkout", "Checkout API", "hard"),
    ("Checkout API", "Payment API", "hard"),
    ("Payment API", "Notification Service", "soft"),
    ("Checkout API", "Fraud Service", "soft"),
    ("Payment API", "Ledger Service", "hard"),
    ("Checkout API", "Identity Service", "hard"),
]
OWNERSHIPS = [("Web Team", "Web Checkout"), ("Checkout Team", "Checkout API"), ("Payments Team", "Payment API"), ("Platform Team", "Notification Service"), ("Risk Team", "Fraud Service"), ("Payments Team", "Ledger Service"), ("Platform Team", "Identity Service")]
PEOPLE = [("Maya Patel", "Payments Lead", "Payments Team"), ("Leo Kim", "Checkout Engineer", "Checkout Team"), ("Anika Rao", "Platform Engineer", "Platform Team"), ("Dev Shah", "Risk Engineer", "Risk Team"), ("Iris Chen", "Web Engineer", "Web Team")]


def run_query(graph: Any, query: str, params: dict[str, Any] | None = None) -> list:
    return query_rows(graph, query, params)


def seed_graph(graph: Any) -> None:
    for name, description, criticality in SERVICES:
        run_query(graph, "MERGE (s:Service {name:$name}) SET s.description=$description, s.criticality=$criticality", {"name": name, "description": description, "criticality": criticality})
    for name in TEAMS:
        run_query(graph, "MERGE (t:Team {name:$name}) SET t.description=$description", {"name": name, "description": f"Fictional {name} responsible for demo operations."})
    for source, target, mode in DEPENDENCIES:
        run_query(graph, "MATCH (a:Service {name:$source}), (b:Service {name:$target}) MERGE (a)-[r:DEPENDS_ON]->(b) SET r.mode=$mode", {"source": source, "target": target, "mode": mode})
    for team, service in OWNERSHIPS:
        run_query(graph, "MATCH (t:Team {name:$team}), (s:Service {name:$service}) MERGE (t)-[r:OWNS]->(s) SET r.status=coalesce(r.status,'active')", {"team": team, "service": service})
    for name, role, team in PEOPLE:
        run_query(graph, "MERGE (p:Person {name:$name}) SET p.role=$role WITH p MATCH (t:Team {name:$team}) MERGE (p)-[:MEMBER_OF]->(t)", {"name": name, "role": role, "team": team})
    run_query(graph, "MERGE (i:Incident {id:$id}) SET i.title=$title, i.severity=$severity, i.status=$status, i.source=$source WITH i MATCH (s:Service {name:$service}) MERGE (i)-[:AFFECTED_BY]->(s)", {"id":"INC-1001", "title":"Payment gateway timeout", "severity":"SEV-2", "status":"resolved", "source":"Synthetic HashiraGraph demo seed", "service":"Payment API"})
    run_query(graph, "MERGE (a:ArchitectureDecision {id:$id}) SET a.title=$title, a.status=$status, a.rationale=$rationale, a.source=$source WITH a MATCH (s:Service {name:$service}) MERGE (a)-[:GOVERNS]->(s)", {"id":"ADR-0007", "title":"Keep the ledger as the payment source of truth", "status":"accepted", "rationale":"Payment records are reconciled against the ledger service.", "source":"Synthetic HashiraGraph demo seed", "service":"Ledger Service"})


BLAST_RADIUS_QUERY = """
MATCH path = (affected:Service)-[:DEPENDS_ON*1..4]->(failed:Service {name:$failed_service})
OPTIONAL MATCH (team:Team)-[ownership:OWNS]->(affected)
WHERE ownership.status IS NULL OR ownership.status = 'active'
RETURN DISTINCT affected.name AS affected_service, team.name AS owner_team,
       [n IN nodes(path) | n.name] AS path_nodes,
       [r IN relationships(path) | type(r)] AS path_edges
ORDER BY affected_service
"""


def run_blast_radius_query(graph: Any, failed_service: str) -> list:
    return query_rows(graph, BLAST_RADIUS_QUERY, {"failed_service": failed_service}, read_only=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed and verify the synthetic HashiraGraph dataset.")
    parser.add_argument("--tenant", choices=sorted(DEMO_TENANTS), help="Select one of the fixed demo tenants; omit to use FALKORDB_GRAPH.")
    args = parser.parse_args(argv)
    client = None
    try:
        client, graph = connect_graph(args.tenant)
        print(f"Connected to FalkorDB graph: {graph.name}")
        seed_graph(graph)
        health = graph_health(graph)
        print(f"Graph health (live query): {health['nodes']} nodes, {health['relationships']} relationships")
        rows = run_blast_radius_query(graph, "Payment API")
        got = {row[0]: row[1] for row in rows}
        expected = {"Checkout API": "Checkout Team", "Web Checkout": "Web Team"}
        print("Blast radius for Payment API:")
        for service, owner, path_nodes, path_edges in rows:
            edges = " ".join(f"-[{edge}]-> {node}" for edge, node in zip(path_edges, path_nodes[1:]))
            print(f"  {path_nodes[0]} {edges}; owner: {owner or 'unassigned'}")
        if not rows:
            print("No dependent service paths found.")
        if any(got.get(service) != owner for service, owner in expected.items()):
            raise RuntimeError(f"Validation failed: expected {expected}; query returned {got}")
        incident = query_rows(graph, "MATCH (i:Incident)-[:AFFECTED_BY]->(s:Service {name:$name}) RETURN i.id, i.title, i.status", {"name":"Payment API"}, read_only=True)
        adr = query_rows(graph, "MATCH (a:ArchitectureDecision)-[:GOVERNS]->(s:Service {name:$name}) RETURN a.id, a.title, a.status", {"name":"Ledger Service"}, read_only=True)
        print(f"Related payment incident records: {len(incident)}; ledger decisions: {len(adr)}")
        print("Live graph seed and blast-radius validation succeeded.")
        return 0
    except GraphConfigurationError as exc:
        print(f"Configuration failed: {exc}", file=sys.stderr)
        print("Run `python check_config.py` for a safe presence check; it does not print credential values.", file=sys.stderr)
        return 1
    except GraphConnectionError as exc:
        print(f"Connection setup failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Seed/verification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("If the message names a connection category, follow its next step; otherwise inspect the graph query and schema.", file=sys.stderr)
        return 1
    finally:
        close_client(client)


if __name__ == "__main__":
    raise SystemExit(main())
