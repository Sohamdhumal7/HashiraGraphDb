"""Read-only, explainable hard/soft dependency failure simulation."""
from __future__ import annotations

from typing import Any

from graph_store import graph_health, query_rows

MAX_HOPS = 4

SERVICE_LIST_QUERY = "MATCH (s:Service) RETURN s.name ORDER BY s.name"
TARGET_QUERY = "MATCH (s:Service) WHERE s.name IN $targets RETURN s.name, s.criticality"
PATH_QUERY = """
MATCH p = (a:Service)-[:DEPENDS_ON*1..4]->(f:Service)
WHERE f.name IN $failed AND NOT a.name IN $failed
RETURN a.name AS service, f.name AS failed_service, length(p) AS hops,
       a.criticality AS criticality,
       [n IN nodes(p) | n.name] AS path,
       [r IN relationships(p) | coalesce(r.mode, 'unknown')] AS modes
"""
OWNERS_QUERY = """
MATCH (t:Team)-[r:OWNS]->(s:Service)
WHERE s.name IN $services AND (r.status IS NULL OR r.status='active')
RETURN s.name, t.name ORDER BY s.name, t.name
"""


class SimulationError(ValueError):
    """The requested what-if scenario is invalid."""


def list_services(graph: Any) -> list[str]:
    return [str(row[0]) for row in query_rows(graph, SERVICE_LIST_QUERY, read_only=True) if row and row[0]]


def _criticality_rank(value: Any) -> int:
    return {"critical": 3, "high": 2, "medium": 1, "low": 0}.get(str(value or "").lower(), -1)


def simulate_failure(graph: Any, targets: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Simulate selected failures from live graph paths; this function never writes.

    A path is DOWN-propagating only when every relationship has mode ``hard``.
    Any other value, including a missing/unknown mode, is conservatively treated
    as soft: it can support DEGRADED but never establish a propagated outage.
    """
    requested = list(dict.fromkeys(str(name).strip() for name in targets if str(name).strip()))
    if not requested:
        raise SimulationError("Select at least one failure target.")

    before = graph_health(graph)
    target_rows = query_rows(graph, TARGET_QUERY, {"targets": requested}, read_only=True)
    found = {str(row[0]): row[1] if len(row) > 1 else None for row in target_rows if row and row[0]}
    missing = [name for name in requested if name not in found]
    if missing:
        raise SimulationError("Unknown service target(s): " + ", ".join(missing))

    raw_paths = query_rows(graph, PATH_QUERY, {"failed": requested}, read_only=True)
    paths: list[dict[str, Any]] = []
    services: set[str] = set()
    for row in raw_paths:
        service, failed, hops, criticality, path, modes = row
        if service in requested:
            continue
        mode_values = [str(mode or "unknown").lower() for mode in (modes or [])]
        if len(path or []) != len(mode_values) + 1:
            raise RuntimeError("Traversal returned a path whose node and relationship evidence lengths do not match.")
        item = {"service": str(service), "failed_target": str(failed), "hops": int(hops),
                "criticality": criticality, "path": list(path), "modes": mode_values,
                "path_status": "DOWN" if mode_values and all(mode == "hard" for mode in mode_values) else "DEGRADED"}
        paths.append(item)
        services.add(str(service))

    owners: dict[str, list[str]] = {name: [] for name in services}
    if services:
        for row in query_rows(graph, OWNERS_QUERY, {"services": sorted(services)}, read_only=True):
            if row and row[0] in owners and row[1] and row[1] not in owners[row[0]]:
                owners[row[0]].append(str(row[1]))

    by_service: dict[str, list[dict[str, Any]]] = {}
    for path in paths:
        by_service.setdefault(path["service"], []).append(path)
    affected = []
    for service, candidates in by_service.items():
        down_paths = [path for path in candidates if path["path_status"] == "DOWN"]
        status = "DOWN" if down_paths else "DEGRADED"
        ranked_paths = sorted(down_paths or candidates, key=lambda p: (p["hops"], p["failed_target"], p["path"]))
        representative = ranked_paths[0]
        affected.append({"service": service, "status": status, "criticality": representative["criticality"],
                         "owners": sorted(owners.get(service, [])), "hops": representative["hops"],
                         "path": representative["path"], "modes": representative["modes"],
                         "failed_target": representative["failed_target"],
                         "evidence_paths": sorted(candidates, key=lambda p: (p["path_status"] != "DOWN", p["hops"], p["failed_target"], p["path"]))})
    affected.sort(key=lambda item: (item["status"] != "DOWN", -_criticality_rank(item["criticality"]), item["hops"], item["service"].casefold()))
    after = graph_health(graph)
    return {
        "failed_targets": [{"service": name, "status": "FAILED"} for name in requested],
        "affected": affected,
        "message": "No dependent services were impacted within four hops." if not affected else "",
        "max_hops": MAX_HOPS,
        "classification_model": "DOWN requires an all-hard path; otherwise reachable services are DEGRADED. Unknown modes are treated as soft.",
        "graph_health_before": before,
        "graph_health_after": after,
        "source_graph_unchanged": before == after,
        "trace": {"target_query": TARGET_QUERY, "path_query": PATH_QUERY.strip(), "owners_query": OWNERS_QUERY.strip(),
                  "target_count": len(found), "path_count": len(paths), "owner_service_count": len(services), "query_mode": "read-only"},
    }
