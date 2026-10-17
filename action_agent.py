"""Bounded, explainable graph question answering for HashiraGraph."""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from graph_store import DEMO_TENANTS, query_rows
from seed_graph import BLAST_RADIUS_QUERY, run_blast_radius_query

TOOLS = {"blast_radius", "ownership", "incidents", "decisions", "memories"}


def authorize_tenant(requested_tenant: str, allowed_tenants: set[str]) -> str:
    """Fail closed unless the application has explicitly authorized the tenant."""
    if requested_tenant not in allowed_tenants:
        raise PermissionError("This demo session is not authorized for the requested tenant.")
    return requested_tenant


def parse_tool_plan(text: str) -> dict[str, str]:
    """Validate a small JSON intent object; this is never interpreted as Cypher."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    data = json.loads(cleaned)
    if not isinstance(data, dict) or set(data) - {"tool", "service", "session_id"}:
        raise ValueError("Intent response has unsupported fields.")
    tool = data.get("tool")
    if tool not in TOOLS:
        raise ValueError("Intent response selected an unsupported tool.")
    service = data.get("service", "")
    session_id = data.get("session_id", "")
    if not isinstance(service, str) or len(service) > 120 or not isinstance(session_id, str) or len(session_id) > 120:
        raise ValueError("Intent fields must be short strings.")
    return {"tool": tool, "service": service, "session_id": session_id}


def is_remember_statement(text: str) -> bool:
    """Identify if the user is instructing the agent to store/remember a fact or note."""
    t = text.strip()
    if t.endswith("?"):
        return False
    patterns = [
        r"^remember\s+this\s+project\s+note\b",
        r"^remember\s+this\s+note\b",
        r"^remember\s+this\b",
        r"^remember\s+that\b",
        r"^remember\s*:",
        r"^remember\b",
        r"^please\s+remember\b",
        r"^save\s+this\s+note\b",
        r"^note\s*:",
        r"^project\s+note\s*:",
    ]
    return any(re.search(p, t, re.IGNORECASE) for p in patterns)


def extract_note(text: str) -> str:
    """Extract the factual payload from a remember/note instruction."""
    cleaned = text.strip()
    patterns = [
        r"^remember\s+this\s+project\s+note\s*:\s*(.*)$",
        r"^remember\s+this\s+note\s*:\s*(.*)$",
        r"^remember\s+this\s*:\s*(.*)$",
        r"^remember\s+that\s+(.*)$",
        r"^remember\s*:\s*(.*)$",
        r"^remember\s+(.*)$",
        r"^please\s+remember\s+that\s+(.*)$",
        r"^please\s+remember\s*:\s*(.*)$",
        r"^please\s+remember\s+(.*)$",
        r"^save\s+this\s+note\s*:\s*(.*)$",
        r"^note\s*:\s*(.*)$",
        r"^project\s+note\s*:\s*(.*)$",
    ]
    for pattern in patterns:
        m = re.match(pattern, cleaned, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return cleaned


def heuristic_plan(question: str) -> dict[str, str]:
    q = question.lower()
    service = next((name for name in ("Web Checkout", "Checkout API", "Payment API", "Notification Service", "Fraud Service", "Ledger Service", "Identity Service") if name.lower() in q), "")
    memory_cues = ("remember", "recall", "what did i tell", "what have i told", "what do we know", "what do you know", "saved note", "previous conversation", "previous session", "history", "memory", "this session", "current session", "just said", "just asked")
    if any(cue in q for cue in memory_cues):
        session_id = "current" if any(phrase in q for phrase in ("this session", "current session", "just said", "just asked")) else ""
        return {"tool": "memories", "service": service, "session_id": session_id}
    if any(word in q for word in ("fail", "blast", "depend", "affected", "impact")):
        return {"tool": "blast_radius", "service": service or "Payment API", "session_id": ""}
    if "incident" in q or "outage" in q:
        return {"tool": "incidents", "service": service, "session_id": ""}
    if any(word in q for word in ("decision", "adr", "architecture")):
        return {"tool": "decisions", "service": service, "session_id": ""}
    return {"tool": "ownership", "service": service or question.strip(), "session_id": ""}


def interpret_intent(question: str) -> tuple[dict[str, str], str | None]:
    """Ask Gemini for a constrained tool choice; use deterministic intent on failure."""
    deterministic = heuristic_plan(question)
    if deterministic["tool"] == "memories":
        return deterministic, "Used deterministic memory routing; model output cannot change memory scope."
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return heuristic_plan(question), "Gemini not configured; used deterministic intent selection."
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
        response = client.models.generate_content(
            model=model,
            contents=(
                "Classify this question into one JSON object only. Allowed tool: blast_radius, ownership, incidents, decisions, memories. "
                "Fields: tool, service (exact service name if present, else empty), session_id (use 'current' only if user asks specifically about current session, otherwise empty string). "
                "Never output Cypher. Question: " + question[:1000]),
            config=genai.types.GenerateContentConfig(response_mime_type="application/json"))
        return parse_tool_plan(response.text or ""), None
    except Exception as exc:
        return heuristic_plan(question), f"Gemini intent failed ({type(exc).__name__}); used deterministic intent."


def parse_answer_response(text: str, evidence_count: int) -> str:
    """Accept only a concise answer that cites retrieved evidence row indices."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    payload = json.loads(cleaned)
    if not isinstance(payload, dict) or set(payload) != {"answer", "evidence_indices"}:
        raise ValueError("answer response must contain only answer and evidence_indices")
    answer, indices = payload["answer"], payload["evidence_indices"]
    if not isinstance(answer, str) or not answer.strip() or not isinstance(indices, list) or not indices:
        raise ValueError("answer or evidence citations are missing")
    if any(type(index) is not int or index < 0 or index >= evidence_count for index in indices):
        raise ValueError("answer cited an evidence row that was not retrieved")
    return answer.strip()


def run_tool(graph: Any, plan: dict[str, str]) -> tuple[list[dict[str, Any]], str, str]:
    tool, service = plan["tool"], plan.get("service", "")
    if tool == "blast_radius":
        query = BLAST_RADIUS_QUERY
        rows = run_blast_radius_query(graph, service)
        return ([{"service": r[0], "owner": r[1], "nodes": r[2], "edges": r[3]} for r in rows], query, "blast_radius")
    specs = {
        "ownership": ("MATCH (t:Team)-[r:OWNS]->(s:Service {name:$name}) WHERE r.status IS NULL OR r.status='active' RETURN t.name,s.name,type(r)", ["team", "service", "relationship"]),
        "incidents": ("MATCH (i:Incident)-[r:AFFECTED_BY]->(s:Service) WHERE ($name='' OR s.name=$name) RETURN i.id,i.title,i.severity,i.status,s.name,type(r) ORDER BY i.id", ["id", "title", "severity", "status", "service", "relationship"]),
        "decisions": ("MATCH (a:ArchitectureDecision)-[r:GOVERNS]->(s:Service) WHERE ($name='' OR s.name=$name) RETURN a.id,a.title,a.status,a.rationale,s.name,type(r) ORDER BY a.id", ["id", "title", "status", "rationale", "service", "relationship"]),
        "memories": ("MATCH (m:MemoryEpisode) WHERE m.tenant_id=$tenant AND ($session='' OR m.session_id=$session) "
                     "OPTIONAL MATCH (m)-[:MENTIONS]->(s:Service) "
                     "WITH m, collect(DISTINCT s.name) AS services "
                     "WHERE $service='' OR $service IN services OR toLower(coalesce(m.user_message,'')) CONTAINS toLower($service) OR toLower(coalesce(m.summary,'')) CONTAINS toLower($service) "
                     "RETURN m.id,m.session_id,m.created_at,m.kind,m.summary,m.user_message,m.source,services "
                     "ORDER BY m.created_at DESC LIMIT 20", ["episode_id", "session_id", "created_at", "kind", "summary", "user_message", "source", "services"]),
    }
    query, keys = specs[tool]
    params = {"name": service, "service": service, "tenant": plan.get("tenant_id", ""), "session": plan.get("session_id", "")}
    rows = query_rows(graph, query, params, read_only=True)
    return ([dict(zip(keys, row)) for row in rows], query, tool)


def _format_deterministic_evidence(evidence: list[dict[str, Any]]) -> str:
    """Format graph evidence clearly when Gemini is unavailable or not configured."""
    if evidence and ("summary" in evidence[0] or "user_message" in evidence[0]):
        lines = ["Retrieved persistent memory records from FalkorDB:"]
        for row in evidence:
            summary = row.get("summary", "").strip()
            user_msg = row.get("user_message", "").strip()
            created = row.get("created_at", "")
            ts = f" [{created[:10]}]" if created else ""
            if summary and user_msg:
                lines.append(f"-{ts} {summary} (from prompt: \"{user_msg}\")")
            elif summary:
                lines.append(f"-{ts} {summary}")
            elif user_msg:
                lines.append(f"-{ts} \"{user_msg}\"")
            else:
                lines.append(f"- {json.dumps(row, ensure_ascii=False)}")
        return "\n".join(lines)
    return "Graph evidence found:\n" + "\n".join(json.dumps(row, ensure_ascii=False) for row in evidence)


def compose_answer(question: str, evidence: list[dict[str, Any]]) -> tuple[str, str | None]:
    if not evidence:
        return "I found no matching records in the selected graph, so I can't support an answer from graph evidence.", None
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return _format_deterministic_evidence(evidence), "Gemini not configured; returned deterministic evidence." 
    try:
        from google import genai
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
            contents=("Answer using only the supplied graph evidence. Say when it is incomplete. Do not add facts. "
                      "Return JSON only with fields answer (string) and evidence_indices (array of integer row indices used). "
                      "Cite at least one valid index. "
                      f"Question: {question[:1000]}\nEvidence rows: {json.dumps(evidence, default=str)[:12000]}"),
            config=genai.types.GenerateContentConfig(response_mime_type="application/json"))
        return parse_answer_response(response.text or "", len(evidence)), None
    except Exception as exc:
        return _format_deterministic_evidence(evidence), f"Gemini answer unavailable ({type(exc).__name__}); returned deterministic evidence."


class ActionAgent:
    ALLOWED_TENANTS = DEMO_TENANTS

    def __init__(self, graph: Any, tenant_id: str, session_id: str):
        self.tenant_id = authorize_tenant(tenant_id, self.ALLOWED_TENANTS)
        self.graph = graph
        self.session_id = session_id

    def _handle_remember(self, question: str) -> dict[str, Any]:
        note = extract_note(question)
        answer = f"I couldn't save the note to persistent memory."
        trace = {"tool": "memories", "query": None, "rows": 0, "episode_ids": [], "notes": []}
        try:
            episode_id = persist_episode(self.graph, self.tenant_id, self.session_id, question, note, [], source="User project note", kind="user_note")
            answer = "Saved your note to persistent memory."
            trace.update({"query": "MERGE MemoryEpisode; on create set metadata; link an unambiguous canonical Service", "rows": 1, "episode_ids": [episode_id], "notes": ["Memory write completed."]})
        except Exception as exc:
            trace.update({"error": f"{type(exc).__name__}: {exc}", "notes": ["Memory write failed; no save is claimed."]})
        return {
            "answer": answer,
            "evidence": [],
            "trace": trace
        }

    def ask(self, question: str) -> dict[str, Any]:
        if is_remember_statement(question):
            return self._handle_remember(question)

        deterministic_plan = heuristic_plan(question)
        plan, intent_note = interpret_intent(question)
        if deterministic_plan["tool"] == "memories":
            plan = deterministic_plan
            intent_note = "Used deterministic memory routing; model output cannot change memory scope."
        plan["tenant_id"] = self.tenant_id

        # Determine session scope:
        # If user explicitly specifies current session, use self.session_id.
        # Otherwise, keep session_id empty to allow cross-session memory recall for this tenant.
        q_lower = question.lower()
        if any(phrase in q_lower for phrase in ("this session", "current session", "just said", "just asked")):
            plan["session_id"] = self.session_id
        else:
            plan["session_id"] = ""

        try:
            evidence, query, tool = run_tool(self.graph, plan)
            answer, llm_note = compose_answer(question, evidence)
            trace = {"tool": tool, "query": query, "rows": len(evidence), "episode_ids": [r["episode_id"] for r in evidence if r.get("episode_id")], "notes": [n for n in (intent_note, llm_note) if n]}
        except Exception as exc:
            answer, evidence, tool = f"The graph query did not complete: {type(exc).__name__}: {exc}", [], plan.get("tool", "unknown")
            trace = {"tool": tool, "query": None, "error": str(exc), "rows": 0, "episode_ids": [], "notes": [intent_note] if intent_note else []}
        try:
            episode_id = persist_episode(self.graph, self.tenant_id, self.session_id, question, answer, evidence)
            trace["written_episode_ids"] = [episode_id]
        except Exception as exc:
            trace["memory_write_error"] = f"{type(exc).__name__}: {exc}"
            trace.setdefault("notes", []).append("The exchange could not be saved to persistent memory.")
        return {"answer": answer, "evidence": evidence, "trace": trace}


def persist_episode(graph: Any, tenant_id: str, session_id: str, user_message: str, answer: str,
                    evidence: list[dict[str, Any]], source: str = "HashiraGraph Action Agent", *,
                    kind: str = "exchange", episode_id: str | None = None) -> str:
    """Persist one logical event once; retry with episode_id for idempotency.

    The Action Agent owns conversation persistence. Streamlit only renders its
    result. Exact service links are added only for one unambiguous seeded name.
    """
    if kind not in {"user_note", "exchange", "simulation"}:
        raise ValueError("Unsupported memory episode kind.")
    episode_id = episode_id or uuid.uuid4().hex
    from seed_graph import SERVICES
    mentions = [name for name, *_ in SERVICES if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", user_message, re.IGNORECASE)]
    service_name = mentions[0] if len(set(mentions)) == 1 else ""
    query = (
        "MERGE (m:MemoryEpisode {id:$id}) "
        "ON CREATE SET m.tenant_id=$tenant, m.session_id=$session, m.created_at=$created, "
        "m.kind=$kind, m.user_message=$message, m.summary=$summary, m.source=$source, "
        "m.evidence_count=$evidence_count, m.evidence_json=$evidence_json "
        "WITH m OPTIONAL MATCH (s:Service {name:$service}) "
        "WITH m, collect(s) AS services "
        "FOREACH (canonical IN CASE WHEN size(services)=1 THEN services ELSE [] END | MERGE (m)-[:MENTIONS]->(canonical)) "
        "RETURN m.id"
    )
    rows = query_rows(graph, query, {"id": episode_id, "tenant": tenant_id, "session": session_id,
        "created": datetime.now(timezone.utc).isoformat(), "kind": kind, "message": user_message[:2000],
        "summary": answer[:2000], "source": source, "evidence_count": len(evidence),
        "evidence_json": json.dumps(evidence, ensure_ascii=False, default=str)[:8000], "service": service_name})
    if not rows or not rows[0]:
        raise RuntimeError("Memory persistence did not return a saved episode.")
    if rows[0][0] != episode_id:
        raise RuntimeError("Memory persistence returned an unexpected episode identifier.")
    return episode_id

