"""Auditable, bounded maintenance; ambiguous facts are reported, not mutated."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from graph_store import graph_health, query_rows


def inspect_schema(graph: Any) -> list[dict[str, Any]]:
    rows = query_rows(graph, "MATCH (a)-[r]->(b) RETURN labels(a), type(r), labels(b), count(r) ORDER BY type(r)", read_only=True)
    return [{"source_labels": row[0], "relationship": row[1], "target_labels": row[2], "count": row[3]} for row in rows]


def run_curator(graph: Any, tenant_id: str) -> dict[str, Any]:
    before = graph_health(graph)
    schema = inspect_schema(graph)
    high_degree = query_rows(graph, "MATCH (n)-[r]-() WITH n, count(r) AS degree WHERE degree > 20 RETURN labels(n), n.name, n.id, degree ORDER BY degree DESC", read_only=True)
    # Generic edges are promoted only with an explicit, unambiguous annotation.
    # The canonical relationship string is allowlisted and labels are checked.
    generic = query_rows(graph, "MATCH (a)-[r:RELATED_TO]->(b) WHERE r.verified = true AND r.canonical_type IS NOT NULL RETURN id(a), labels(a), id(b), labels(b), r.canonical_type, r.source, count(r) LIMIT 50", read_only=True)
    candidates, promoted = [], 0
    label_pairs = {
        "DEPENDS_ON": ("Service", "Service"), "OWNS": ("Team", "Service"),
        "MEMBER_OF": ("Person", "Team"), "AFFECTED_BY": ("Incident", "Service"),
        "GOVERNS": ("ArchitectureDecision", "Service"),
    }
    for row in generic:
        _, source_labels, _, target_labels, canonical, source, _count = row
        valid = ((canonical == "DEPENDS_ON" and "Service" in source_labels and "Service" in target_labels) or
                 (canonical == "OWNS" and "Team" in source_labels and "Service" in target_labels) or
                 (canonical == "MEMBER_OF" and "Person" in source_labels and "Team" in target_labels) or
                 (canonical == "AFFECTED_BY" and "Incident" in source_labels and "Service" in target_labels) or
                 (canonical == "GOVERNS" and "ArchitectureDecision" in source_labels and "Service" in target_labels))
        if valid and isinstance(source, str) and source.strip():
            source_label, target_label = label_pairs[canonical]
            run_id = uuid.uuid4().hex
            result = query_rows(graph,
                       f"MATCH (a:{source_label})-[r:RELATED_TO]->(b:{target_label}) "
                       "WHERE r.verified = true AND r.canonical_type = $canonical "
                       "AND r.source = $source "
                       f"MERGE (a)-[c:{canonical} {{source_provenance:$source}}]->(b) "
                       "ON CREATE SET c._curator_run_id=$run_id, c.canonicalized_from='RELATED_TO', "
                       "c.status=CASE WHEN r.ended_at IS NOT NULL THEN 'closed' ELSE coalesce(r.status,'active') END, "
                       "c.started_at=r.started_at, c.ended_at=r.ended_at "
                       "RETURN c._curator_run_id=$run_id",
                       {"canonical": canonical, "source": source, "run_id": run_id})
            created = bool(result and result[0] and result[0][0])
            if created:
                promoted += 1
            candidates.append({"canonical_type": canonical, "source": source,
                               "decision": "new canonical relationship created; original edge retained" if created else "matching canonical relationship already existed; no mutation"})
        else:
            candidates.append({"canonical_type": str(canonical), "source": str(source or "unknown"), "decision": "not promoted: labels or provenance do not verify semantics"})
    # Do not infer that one of multiple owners is obsolete. Close history only
    # when an authoritative relationship already carries ended_at.
    expired = query_rows(graph, "MATCH (:Team)-[r:OWNS]->(:Service) WHERE r.ended_at IS NOT NULL AND (r.status IS NULL OR r.status <> 'closed') RETURN count(r)", read_only=True)
    closed_rows = []
    if expired and int(expired[0][0] or 0):
        closed_rows = query_rows(graph, "MATCH (:Team)-[r:OWNS]->(:Service) WHERE r.ended_at IS NOT NULL AND (r.status IS NULL OR r.status <> 'closed') SET r.status='closed' RETURN count(r)")
    close_count = int(closed_rows[0][0] or 0) if closed_rows else 0
    # Build an index of source episode identifiers, not generated/concatenated prose.
    memory_rows = query_rows(graph, "MATCH (m:MemoryEpisode {tenant_id:$tenant}) RETURN m.id ORDER BY m.created_at DESC LIMIT 20", {"tenant": tenant_id}, read_only=True)
    rollup_count = 0
    episode_ids = [row[0] for row in memory_rows if row and row[0]]
    if episode_ids:
        rollup_id = uuid.uuid4().hex
        made = query_rows(graph,
            "MERGE (s:MemoryRollup {tenant_id:$tenant, rollup_key:$key}) "
            "ON CREATE SET s._curator_run_id=$run_id, s.episode_ids=$ids, s.source_count=size($ids), s.updated_at=$now "
            "WITH s UNWIND $ids AS episode_id MATCH (m:MemoryEpisode {id:episode_id, tenant_id:$tenant}) "
            "MERGE (m)-[:INCLUDED_IN]->(s) RETURN s._curator_run_id=$run_id",
            {"tenant": tenant_id, "key": "recent_episodes_v1", "run_id": rollup_id, "ids": episode_ids,
             "now": datetime.now(timezone.utc).isoformat()})
        rollup_count = int(bool(made and made[0] and made[0][0]))
    after = graph_health(graph)
    return {"before": before, "after": after, "schema": schema, "high_degree_nodes": [list(row) for row in high_degree],
            "generic_relationship_candidates": candidates, "ownership_facts_closed_with_explicit_ended_at": close_count,
            "generic_relationships_promoted": promoted,
            "memory_rollups_created": rollup_count,
            "notes": ["High degree is reported for review; it is not treated as a defect.", "Memory rollups store source episode IDs and links, not semantic summaries.", "Original episodes and generic source relationships are retained."]}
