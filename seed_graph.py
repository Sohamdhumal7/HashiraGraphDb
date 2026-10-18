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

EXPANDED_SERVICES = [
    ("Order Service", "Coordinates order creation, state transitions, and fulfillment", "critical"),
    ("Inventory Service", "Maintains real-time stock levels and reservations across warehouses", "critical"),
    ("Billing Engine", "Generates invoices, manages recurring subscriptions and tax compliance", "critical"),
    ("Tax Calculator", "Calculates jurisdictional sales taxes and VAT in real-time", "high"),
    ("Pricing Service", "Dynamic pricing, promotional rules, and coupon valuation engine", "high"),
    ("Mobile App Gateway", "Backend-for-Frontend (BFF) aggregator for iOS and Android clients", "high"),
    ("Catalog Service", "Product listings, SKU attributes, categories, and inventory display", "high"),
    ("Search API", "Full-text and semantic product search with faceted filtering", "high"),
    ("Recommendation Engine", "Collaborative filtering and personalized product recommendations", "medium"),
    ("Analytics Service", "Event stream aggregation, customer funnel tracking and telemetry", "low"),
    ("Vector Store Service", "Vector embeddings storage for semantic similarity search", "high"),
    ("Warehouse API", "Warehouse management system (WMS) integration and fulfillment dispatch", "critical"),
    ("Shipping Gateway", "Coordinates multi-carrier label generation, rate quoting, and pickup scheduling", "high"),
    ("Carrier Tracking API", "Polls external logistics carrier APIs and sends milestone webhooks", "medium"),
    ("Auth Gateway", "OAuth2/OIDC token issuance, MFA challenges, and session policy enforcement", "critical"),
    ("Token Vault", "Hardware security module (HSM) backed encryption keys and secrets storage", "critical"),
    ("Audit Logger", "Immutable append-only compliance and security audit event logging", "critical"),
    ("Data Warehouse", "Columnar analytics warehouse for financial reconciliation and reporting", "medium"),
]

EXPANDED_TEAMS = [
    "Logistics Team",
    "Catalog Team",
    "Data & AI Team",
    "Security Team",
    "Core Infra Team",
    "Order Management Team",
]

EXPANDED_DEPENDENCIES = [
    ("Checkout API", "Inventory Service", "hard"),
    ("Checkout API", "Order Service", "hard"),
    ("Checkout API", "Pricing Service", "soft"),
    ("Payment API", "Billing Engine", "hard"),
    ("Payment API", "Tax Calculator", "soft"),
    ("Order Service", "Warehouse API", "hard"),
    ("Order Service", "Notification Service", "soft"),
    ("Mobile App Gateway", "Web Checkout", "soft"),
    ("Mobile App Gateway", "Catalog Service", "hard"),
    ("Mobile App Gateway", "Identity Service", "hard"),
    ("Catalog Service", "Search API", "hard"),
    ("Catalog Service", "Pricing Service", "hard"),
    ("Catalog Service", "Recommendation Engine", "soft"),
    ("Search API", "Vector Store Service", "soft"),
    ("Recommendation Engine", "Vector Store Service", "hard"),
    ("Recommendation Engine", "Analytics Service", "soft"),
    ("Warehouse API", "Inventory Service", "hard"),
    ("Warehouse API", "Shipping Gateway", "hard"),
    ("Shipping Gateway", "Carrier Tracking API", "soft"),
    ("Shipping Gateway", "Notification Service", "soft"),
    ("Identity Service", "Auth Gateway", "hard"),
    ("Auth Gateway", "Token Vault", "hard"),
    ("Ledger Service", "Audit Logger", "soft"),
    ("Analytics Service", "Data Warehouse", "hard"),
]

EXPANDED_OWNERSHIPS = [
    ("Order Management Team", "Order Service"),
    ("Logistics Team", "Inventory Service"),
    ("Payments Team", "Billing Engine"),
    ("Payments Team", "Tax Calculator"),
    ("Catalog Team", "Pricing Service"),
    ("Web Team", "Mobile App Gateway"),
    ("Catalog Team", "Catalog Service"),
    ("Catalog Team", "Search API"),
    ("Data & AI Team", "Recommendation Engine"),
    ("Data & AI Team", "Analytics Service"),
    ("Data & AI Team", "Vector Store Service"),
    ("Logistics Team", "Warehouse API"),
    ("Logistics Team", "Shipping Gateway"),
    ("Logistics Team", "Carrier Tracking API"),
    ("Security Team", "Auth Gateway"),
    ("Security Team", "Token Vault"),
    ("Core Infra Team", "Audit Logger"),
    ("Core Infra Team", "Data Warehouse"),
]

EXPANDED_PEOPLE = [
    ("Marcus Vance", "Logistics Lead", "Logistics Team"),
    ("Sophia Lin", "Principal Search Architect", "Catalog Team"),
    ("Tariq Al-Mansoor", "Staff AI Engineer", "Data & AI Team"),
    ("Elena Rostova", "Security Director", "Security Team"),
    ("Carlos Gomez", "Site Reliability Engineer", "Core Infra Team"),
    ("Chloe Dubois", "Data Platform Engineer", "Data & AI Team"),
    ("Liam O'Connor", "Fulfillment Operations Engineer", "Logistics Team"),
    ("Nadia Rossi", "Order Experience Lead", "Order Management Team"),
    ("Siddharth Mehta", "Crypto & Vault Specialist", "Security Team"),
]

EXPANDED_INCIDENTS = [
    ("INC-1002", "Search index latency spike during flash sale", "SEV-2", "resolved", "Synthetic HashiraGraph demo seed", "Search API"),
    ("INC-1003", "Warehouse inventory sync deadlock across distributed partitions", "SEV-1", "resolved", "Synthetic HashiraGraph demo seed", "Warehouse API"),
    ("INC-1004", "Auth token signing key rotation failure", "SEV-1", "resolved", "Synthetic HashiraGraph demo seed", "Auth Gateway"),
    ("INC-1005", "Carrier webhook timeout during regional winter storm", "SEV-3", "resolved", "Synthetic HashiraGraph demo seed", "Shipping Gateway"),
    ("INC-1006", "Vector store memory pressure during catalog re-indexing", "SEV-2", "resolved", "Synthetic HashiraGraph demo seed", "Vector Store Service"),
]

EXPANDED_ADRS = [
    ("ADR-0012", "Isolate token vault credentials from application state", "accepted", "High security credentials and encryption keys must reside exclusively inside HSM-backed token vault.", "Synthetic HashiraGraph demo seed", "Token Vault"),
    ("ADR-0015", "Asynchronous fallback for recommendation degradation", "accepted", "Recommendation Engine failure must gracefully degrade to popularity heuristics without impacting catalog latency.", "Synthetic HashiraGraph demo seed", "Recommendation Engine"),
    ("ADR-0019", "Eventual consistency model for warehouse inventory reservations", "accepted", "Warehouse reservations are reconciled with distributed locks and two-phase commit over Inventory Service.", "Synthetic HashiraGraph demo seed", "Inventory Service"),
    ("ADR-0023", "Read replica routing for catalog queries", "accepted", "Product listing queries route to read replicas to protect primary catalog availability.", "Synthetic HashiraGraph demo seed", "Catalog Service"),
    ("ADR-0028", "Write-ahead audit logging for all balance adjustments", "accepted", "All mutations to ledger balance entries must emit append-only audit log events before committing.", "Synthetic HashiraGraph demo seed", "Audit Logger"),
]

ALL_SERVICES = SERVICES + EXPANDED_SERVICES
ALL_TEAMS = TEAMS + EXPANDED_TEAMS


def run_query(graph: Any, query: str, params: dict[str, Any] | None = None) -> list:
    return query_rows(graph, query, params)


def seed_graph(graph: Any, expanded: bool = False) -> None:
    services_to_seed = ALL_SERVICES if expanded else SERVICES
    teams_to_seed = ALL_TEAMS if expanded else TEAMS
    deps_to_seed = (DEPENDENCIES + EXPANDED_DEPENDENCIES) if expanded else DEPENDENCIES
    owners_to_seed = (OWNERSHIPS + EXPANDED_OWNERSHIPS) if expanded else OWNERSHIPS
    people_to_seed = (PEOPLE + EXPANDED_PEOPLE) if expanded else PEOPLE

    for name, description, criticality in services_to_seed:
        run_query(graph, "MERGE (s:Service {name:$name}) SET s.description=$description, s.criticality=$criticality", {"name": name, "description": description, "criticality": criticality})
    for name in teams_to_seed:
        run_query(graph, "MERGE (t:Team {name:$name}) SET t.description=$description", {"name": name, "description": f"Fictional {name} responsible for demo operations."})
    for source, target, mode in deps_to_seed:
        run_query(graph, "MATCH (a:Service {name:$source}), (b:Service {name:$target}) MERGE (a)-[r:DEPENDS_ON]->(b) SET r.mode=$mode", {"source": source, "target": target, "mode": mode})
    for team, service in owners_to_seed:
        run_query(graph, "MATCH (t:Team {name:$team}), (s:Service {name:$service}) MERGE (t)-[r:OWNS]->(s) SET r.status=coalesce(r.status,'active')", {"team": team, "service": service})
    for name, role, team in people_to_seed:
        run_query(graph, "MERGE (p:Person {name:$name}) SET p.role=$role WITH p MATCH (t:Team {name:$team}) MERGE (p)-[:MEMBER_OF]->(t)", {"name": name, "role": role, "team": team})

    # Incidents
    run_query(graph, "MERGE (i:Incident {id:$id}) SET i.title=$title, i.severity=$severity, i.status=$status, i.source=$source WITH i MATCH (s:Service {name:$service}) MERGE (i)-[:AFFECTED_BY]->(s)", {"id":"INC-1001", "title":"Payment gateway timeout", "severity":"SEV-2", "status":"resolved", "source":"Synthetic HashiraGraph demo seed", "service":"Payment API"})
    if expanded:
        for inc_id, title, sev, status, src, svc in EXPANDED_INCIDENTS:
            run_query(graph, "MERGE (i:Incident {id:$id}) SET i.title=$title, i.severity=$severity, i.status=$status, i.source=$source WITH i MATCH (s:Service {name:$service}) MERGE (i)-[:AFFECTED_BY]->(s)",
                      {"id": inc_id, "title": title, "severity": sev, "status": status, "source": src, "service": svc})

    # Architecture Decisions
    run_query(graph, "MERGE (a:ArchitectureDecision {id:$id}) SET a.title=$title, a.status=$status, a.rationale=$rationale, a.source=$source WITH a MATCH (s:Service {name:$service}) MERGE (a)-[:GOVERNS]->(s)", {"id":"ADR-0007", "title":"Keep the ledger as the payment source of truth", "status":"accepted", "rationale":"Payment records are reconciled against the ledger service.", "source":"Synthetic HashiraGraph demo seed", "service":"Ledger Service"})
    if expanded:
        for adr_id, title, status, rationale, src, svc in EXPANDED_ADRS:
            run_query(graph, "MERGE (a:ArchitectureDecision {id:$id}) SET a.title=$title, a.status=$status, a.rationale=$rationale, a.source=$source WITH a MATCH (s:Service {name:$service}) MERGE (a)-[:GOVERNS]->(s)",
                      {"id": adr_id, "title": title, "status": status, "rationale": rationale, "source": src, "service": svc})


def seed_enterprise_graph(graph: Any) -> None:
    """Convenience helper to seed the comprehensive 75+ node and relation enterprise graph."""
    seed_graph(graph, expanded=True)


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
    parser.add_argument("--expanded", action="store_true", default=True, help="Seed comprehensive enterprise dataset (75+ nodes and relations). Default is True.")
    parser.add_argument("--base-only", action="store_true", help="Seed only the 19 baseline demo nodes.")
    args = parser.parse_args(argv)
    client = None
    try:
        client, graph = connect_graph(args.tenant)
        print(f"Connected to FalkorDB graph: {graph.name}")
        is_expanded = not args.base_only
        seed_graph(graph, expanded=is_expanded)
        health = graph_health(graph)
        print(f"Graph health (live query): {health['nodes']} nodes, {health['relationships']} relationships (mode: {'enterprise expanded' if is_expanded else 'baseline'})")
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
