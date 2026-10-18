"""Graph-derived fragility ranking: which service is most dangerous to lose?

Uses the simulator's own semantics (DOWN = all-hard path, max 4 hops) plus FalkorDB's
`algo.betweenness` when the server provides it. Read-only.
"""
from __future__ import annotations

from typing import Any

from graph_store import query_rows

SERVICES_QUERY = "MATCH (s:Service) RETURN s.name, s.criticality"
HARD_QUERY = ("MATCH p = (a:Service)-[:DEPENDS_ON*1..4]->(f:Service) "
              "WHERE all(r IN relationships(p) WHERE r.mode = 'hard') "
              "RETURN f.name, collect(DISTINCT a.name)")
ANY_QUERY = "MATCH p = (a:Service)-[:DEPENDS_ON*1..4]->(f:Service) RETURN f.name, collect(DISTINCT a.name)"
BETWEENNESS_QUERY = ("CALL algo.betweenness({nodeLabels:['Service'], relationshipTypes:['DEPENDS_ON'], samplingSeed:42}) "
                     "YIELD node, score RETURN node.name, score")
BETWEENNESS_FALLBACK_QUERY = ("CALL algo.betweenness({nodeLabels:['Service'], relationshipTypes:['DEPENDS_ON']}) "
                              "YIELD node, score RETURN node.name, score")
_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}


def fragility_ranking(graph: Any) -> dict[str, Any]:
    services = {str(r[0]): r[1] for r in query_rows(graph, SERVICES_QUERY, read_only=True) if r and r[0]}
    hard = {str(r[0]): set(r[1]) for r in query_rows(graph, HARD_QUERY, read_only=True)}
    anyp = {str(r[0]): set(r[1]) for r in query_rows(graph, ANY_QUERY, read_only=True)}
    betweenness: dict[str, float] = {}
    algo_status = "ok"
    try:
        try:  # fixed sampling seed keeps the demo output reproducible
            rows_b = query_rows(graph, BETWEENNESS_QUERY, read_only=True)
        except Exception:  # server build without the samplingSeed option
            rows_b = query_rows(graph, BETWEENNESS_FALLBACK_QUERY, read_only=True)
            algo_status = "ok (unseeded sampling: values may vary between runs)"
        betweenness = {str(r[0]): float(r[1]) for r in rows_b}
    except Exception as exc:  # older/other server builds may not ship the procedure
        algo_status = f"unavailable on this server ({type(exc).__name__}); ranking uses path counts only"
    rows = []
    for name, crit in services.items():
        down = hard.get(name, set())
        degraded = anyp.get(name, set()) - down
        rows.append({"service": name, "criticality": crit, "hard_dependents_down": len(down),
                     "soft_dependents_degraded": len(degraded), "betweenness": round(betweenness.get(name, 0.0), 3) if betweenness else None,
                     "down_if_lost": sorted(down), "degraded_if_lost": sorted(degraded)})
    rows.sort(key=lambda r: (-r["hard_dependents_down"], -r["soft_dependents_degraded"], -_RANK.get(str(r["criticality"]).lower(), -1), r["service"].casefold()))
    return {"ranking": rows, "algorithm_status": algo_status,
            "method": "Sorted by how many services go DOWN (all-hard paths, <=4 hops), then DEGRADED, then criticality. Betweenness comes from FalkorDB's algo.betweenness, which samples paths, so treat it as an approximate chokepoint hint; it does not affect the ordering and is not a risk probability.",
            "queries": {"hard": HARD_QUERY, "any": ANY_QUERY, "betweenness": BETWEENNESS_QUERY}}
