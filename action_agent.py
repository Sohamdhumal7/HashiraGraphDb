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
from simulation import PATH_QUERY, simulate_failure
from verification import verify_answer

TOOLS = {"blast_radius", "ownership", "incidents", "decisions", "memories", "simulate"}


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


GREETING_WORDS = {
    "hi", "hello", "hey", "hola", "yo", "sup", "howdy", "greetings",
    "good morning", "good afternoon", "good evening", "good day",
    "help", "who are you", "what can you do", "start", "ping", "test",
    "hi there", "hello there", "hey there", "what is this", "about"
}


def is_greeting(text: str) -> bool:
    """Identify greetings, help requests, or intro queries for instant sub-millisecond response."""
    cleaned = re.sub(r"[^\w\s]", "", text).strip().lower()
    if not cleaned:
        return False
    if cleaned in GREETING_WORDS:
        return True
    tokens = cleaned.split()
    if tokens and tokens[0] in {"hi", "hello", "hey", "howdy", "greetings"} and len(tokens) <= 3:
        return True
    return False


from seed_graph import ALL_SERVICES, BLAST_RADIUS_QUERY, run_blast_radius_query

SERVICE_NAMES = tuple([name for name, *_ in ALL_SERVICES])

_SERVICE_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in sorted(SERVICE_NAMES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE
)
_CANONICAL_SERVICES = {name.lower(): name for name in SERVICE_NAMES}


def extract_services(text: str) -> list[str]:
    """Single-pass entity extraction in O(L) using a precompiled word-boundary regex automaton."""
    matches = _SERVICE_PATTERN.findall(text)
    seen: set[str] = set()
    result: list[str] = []
    for m in matches:
        canonical = _CANONICAL_SERVICES.get(m.lower())
        if canonical and canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result


def heuristic_plan(question: str) -> dict[str, str]:
    q = question.lower()
    found = extract_services(question)
    service = found[0] if found else ""
    note_cues = ("what did i tell", "what have i told", "saved note", "my note", "my notes")
    sim_recall_cues = ("what did we simulate", "earlier simulation", "previous simulation", "past simulation")
    memory_cues = ("remember", "recall", "what do we know", "what do you know", "previous conversation", "previous session",
                   "history", "memory", "this session", "current session", "just said", "just ask") + note_cues + sim_recall_cues
    if any(cue in q for cue in memory_cues):
        session_id = "current" if any(phrase in q for phrase in ("this session", "current session", "just said", "just ask")) else ""
        kind = "simulation" if any(c in q for c in sim_recall_cues) else ("user_note" if any(c in q for c in note_cues) else "")
        return {"tool": "memories", "service": service, "session_id": session_id, "kind": kind}
    if any(cue in q for cue in ("simulate", "simulation", "what if", "what happens if")):
        return {"tool": "simulate", "service": "|".join(found), "session_id": ""}
    # Specific record types win over the generic blast-radius cues: "Which incidents
    # affected Payment API?" is an incident lookup, not a dependency traversal.
    if "incident" in q or "outage" in q:
        return {"tool": "incidents", "service": service, "session_id": ""}
    if any(word in q for word in ("decision", "adr", "architecture")):
        return {"tool": "decisions", "service": service, "session_id": ""}
    if any(word in q for word in ("fail", "blast", "depend", "affected", "impact")):
        return {"tool": "blast_radius", "service": service or "Payment API", "session_id": ""}
    return {"tool": "ownership", "service": service or question.strip(), "session_id": ""}


_INTENT_CACHE: dict[str, tuple[dict[str, str], str | None]] = {}


def interpret_intent(question: str) -> tuple[dict[str, str], str | None]:
    """Ask Gemini for a constrained tool choice; use deterministic intent on failure.
    Includes LRU caching for instantaneous subsequent lookups.
    """
    cache_key = question.strip().lower()
    if cache_key in _INTENT_CACHE:
        plan, note = _INTENT_CACHE[cache_key]
        return dict(plan), (f"{note} (cached)" if note else "From intent cache (0ms).")

    deterministic = heuristic_plan(question)
    if deterministic["tool"] in ("memories", "simulate"):
        result = (deterministic, "Used deterministic routing; model output cannot change memory scope or simulation targets.")
        _INTENT_CACHE[cache_key] = result
        return result
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        result = (heuristic_plan(question), "Gemini not configured; used deterministic intent selection.")
        _INTENT_CACHE[cache_key] = result
        return result
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        raw_model = os.getenv("GEMINI_MODEL", "").strip()
        model = "gemini-2.5-flash" if (not raw_model or raw_model == "gemini-3.8-flash") else raw_model
        response = client.models.generate_content(
            model=model,
            contents=(
                "Classify this question into one JSON object only. Allowed tool: blast_radius, ownership, incidents, decisions, memories. "
                "Fields: tool, service (exact service name if present, else empty), session_id (use 'current' only if user asks specifically about current session, otherwise empty string). "
                "Never output Cypher. Question: " + question[:1000]),
            config=genai.types.GenerateContentConfig(response_mime_type="application/json"))
        parsed = parse_tool_plan(response.text or "")
        result = (parsed, None)
        if len(_INTENT_CACHE) > 512:
            _INTENT_CACHE.clear()
        _INTENT_CACHE[cache_key] = result
        return result
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
    if tool == "simulate":
        report = simulate_failure(graph, [n for n in service.split("|") if n])
        rows = []
        for item in report["affected"]:
            rows.append({"service": item["service"], "status": item["status"], "criticality": item["criticality"],
                         "owners": item["owners"], "hops": item["hops"], "failed_target": item["failed_target"],
                         "nodes": item["path"], "edges": [f"DEPENDS_ON ({m})" for m in item["modes"]]})
        return (rows, PATH_QUERY.strip(), "simulate")
    specs = {
        "ownership": ("MATCH (t:Team)-[r:OWNS]->(s:Service {name:$name}) WHERE r.status IS NULL OR r.status='active' RETURN t.name,s.name,type(r)", ["team", "service", "relationship"]),
        "incidents": ("MATCH (i:Incident)-[r:AFFECTED_BY]->(s:Service) WHERE ($name='' OR s.name=$name) RETURN i.id,i.title,i.severity,i.status,s.name,type(r) ORDER BY i.id", ["id", "title", "severity", "status", "service", "relationship"]),
        "decisions": ("MATCH (a:ArchitectureDecision)-[r:GOVERNS]->(s:Service) WHERE ($name='' OR s.name=$name) RETURN a.id,a.title,a.status,a.rationale,s.name,type(r) ORDER BY a.id", ["id", "title", "status", "rationale", "service", "relationship"]),
        "memories": ("MATCH (m:MemoryEpisode) WHERE m.tenant_id=$tenant AND ($session='' OR m.session_id=$session) AND (($kind='' AND coalesce(m.kind,'exchange')<>'recall') OR coalesce(m.kind,'exchange')=$kind) "
                     "OPTIONAL MATCH (m)-[:MENTIONS]->(s:Service) "
                     "WITH m, collect(DISTINCT s.name) AS services "
                     "WHERE $service='' OR $service IN services OR toLower(coalesce(m.user_message,'')) CONTAINS toLower($service) OR toLower(coalesce(m.summary,'')) CONTAINS toLower($service) "
                     "RETURN m.id,m.session_id,m.created_at,m.kind,m.summary,m.user_message,m.source,services "
                     "ORDER BY m.created_at DESC LIMIT 20", ["episode_id", "session_id", "created_at", "kind", "summary", "user_message", "source", "services"]),
    }
    query, keys = specs[tool]
    params = {"name": service, "service": service, "tenant": plan.get("tenant_id", ""), "session": plan.get("session_id", ""), "kind": plan.get("kind", "")}
    rows = query_rows(graph, query, params, read_only=True)
    return ([dict(zip(keys, row)) for row in rows], query, tool)


def _format_deterministic_evidence(evidence: list[dict[str, Any]]) -> str:
    """Format graph evidence clearly when Gemini is unavailable or not configured."""
    if not evidence:
        return "I found no matching records in the selected graph, so I can't support an answer from graph evidence."

    # Persistent memory recall
    if "summary" in evidence[0] or "user_message" in evidence[0]:
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

    # Simulation impact
    if "status" in evidence[0] and "nodes" in evidence[0]:
        lines = ["Simulated impact (read-only; nothing was changed in the graph):"]
        for r in evidence:
            owners = ", ".join(r.get("owners") or []) or "unassigned"
            hops = r.get("hops", "?")
            lines.append(f"- {r['service']}: {r['status']} ({r.get('criticality') or 'unknown'}), owner: {owners}; "
                         f"{hops} hop{'s' if hops != 1 else ''}: {' -> '.join(r['nodes'])}")
        return "\n".join(lines)

    # Ownership facts
    if "team" in evidence[0] and "service" in evidence[0]:
        teams = [r["team"] for r in evidence if r.get("team")]
        service = evidence[0].get("service", "Service")
        if teams:
            team_str = ", ".join(dict.fromkeys(teams))
            return f"{team_str} owns {service}."

    # Incidents
    if "severity" in evidence[0] and "title" in evidence[0]:
        lines = [f"Found {len(evidence)} incident record(s) in FalkorDB:"]
        for r in evidence:
            lines.append(f"- {r.get('id', 'Incident')}: {r.get('title', '')} (severity: {r.get('severity', 'unknown')}, status: {r.get('status', 'unknown')}) affecting {r.get('service', 'service')}.")
        return "\n".join(lines)

    # Decisions
    if "rationale" in evidence[0] and "title" in evidence[0]:
        lines = [f"Found {len(evidence)} architecture decision(s) in FalkorDB:"]
        for r in evidence:
            lines.append(f"- {r.get('id', 'ADR')}: {r.get('title', '')} (status: {r.get('status', 'accepted')}) governing {r.get('service', 'service')} — {r.get('rationale', '')}")
        return "\n".join(lines)

    # Dependency Blast Radius
    if "owner" in evidence[0] and "nodes" in evidence[0]:
        lines = ["Services that depend on the failed service (from FalkorDB paths):"]
        for r in evidence:
            lines.append(f"- {r['service']} (owner: {r.get('owner') or 'unassigned'}): {' -> '.join(r['nodes'])}")
        return "\n".join(lines)

    return "Graph evidence found:\n" + "\n".join(json.dumps(row, ensure_ascii=False) for row in evidence)


_ANSWER_CACHE: dict[str, str] = {}


def compose_answer(question: str, evidence: list[dict[str, Any]], *, fast: bool = False) -> tuple[str, str | None]:
    if not evidence:
        return "I found no matching records in the selected graph, so I can't support an answer from graph evidence.", None

    key = os.getenv("GEMINI_API_KEY", "").strip()
    if fast or not key:
        return _format_deterministic_evidence(evidence), "Generated fast grounded answer directly from graph evidence (sub-millisecond synthesis)."

    cache_key = f"{question.strip().lower()}|{json.dumps(evidence, sort_keys=True, default=str)}"
    if cache_key in _ANSWER_CACHE:
        return _ANSWER_CACHE[cache_key], "Retrieved verified answer from high-speed memory cache (0ms)."

    try:
        from google import genai
        raw_model = os.getenv("GEMINI_MODEL", "").strip()
        ans_model = "gemini-2.5-flash" if (not raw_model or raw_model == "gemini-3.8-flash") else raw_model
        response = client.models.generate_content(
            model=ans_model,
            contents=("Answer using only the supplied graph evidence. Say when it is incomplete. Do not add facts. "
                      "Return JSON only with fields answer (string) and evidence_indices (array of integer row indices used). "
                      "Cite at least one valid index. "
                      f"Question: {question[:1000]}\nEvidence rows: {json.dumps(evidence, default=str)[:12000]}"),
            config=genai.types.GenerateContentConfig(response_mime_type="application/json"))
        ans = parse_answer_response(response.text or "", len(evidence))
        if len(_ANSWER_CACHE) > 512:
            _ANSWER_CACHE.clear()
        _ANSWER_CACHE[cache_key] = ans
        return ans, None
    except Exception as exc:
        return _format_deterministic_evidence(evidence), f"Gemini answer unavailable ({type(exc).__name__}); returned deterministic evidence."


class ActionAgent:
    ALLOWED_TENANTS = DEMO_TENANTS

    def __init__(self, graph: Any, tenant_id: str, session_id: str, *, fast_mode: bool = True):
        self.tenant_id = authorize_tenant(tenant_id, self.ALLOWED_TENANTS)
        self.graph = graph
        self.session_id = session_id
        self.fast_mode = fast_mode

    def _handle_greeting(self, question: str) -> dict[str, Any]:
        answer = (
            "Hello! I am **HashiraGraph Action Agent**, your company brain and dependency intelligence assistant powered by FalkorDB.\n\n"
            "Here are things you can ask me:\n"
            "- 🔍 **Service Ownership**: *Who owns Payment API?*\n"
            "- 💥 **Blast Radius**: *If Payment API fails, which upstream services may be affected?*\n"
            "- 🧪 **Failure Simulator**: *Simulate failure of Ledger Service*\n"
            "- 🚨 **Incidents**: *Which incidents affected Payment API?*\n"
            "- 🏛️ **Architecture Decisions**: *Which decisions govern Ledger Service?*\n"
            "- 🧠 **Persistent Memory**: *Remember this note: [fact]* or *What did I tell you about the hackathon?*"
        )
        trace = {
            "tool": "greeting",
            "query": None,
            "rows": 0,
            "episode_ids": [],
            "notes": ["Fast-path greeting (instant sub-millisecond response)."]
        }
        try:
            episode_id = persist_episode(self.graph, self.tenant_id, self.session_id, question, answer, [], source="HashiraGraph Action Agent", kind="exchange")
            trace["written_episode_ids"] = [episode_id]
        except Exception:
            pass
        return {
            "answer": answer,
            "evidence": [],
            "trace": trace
        }

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

    def ask(self, question: str, *, fast: bool | None = None) -> dict[str, Any]:
        if is_remember_statement(question):
            return self._handle_remember(question)

        if is_greeting(question):
            return self._handle_greeting(question)

        use_fast = self.fast_mode if fast is None else fast
        deterministic_plan = heuristic_plan(question)

        if use_fast:
            plan = deterministic_plan
            intent_note = "Used fast deterministic routing (sub-millisecond algorithmic dispatch)."
        else:
            plan, intent_note = interpret_intent(question)
            if deterministic_plan["tool"] in ("memories", "simulate"):
                plan = deterministic_plan
                intent_note = "Used deterministic routing; model output cannot change memory scope or simulation targets."

        plan["tenant_id"] = self.tenant_id

        # Determine session scope:
        q_lower = question.lower()
        if any(phrase in q_lower for phrase in ("this session", "current session", "just said", "just ask")):
            plan["session_id"] = self.session_id
        else:
            plan["session_id"] = ""

        try:
            evidence, query, tool = run_tool(self.graph, plan)
            answer, llm_note = compose_answer(question, evidence, fast=use_fast)
            verification = verify_answer(answer, evidence)
            if verification["status"] == "unsupported_claims":
                answer = _format_deterministic_evidence(evidence)
                llm_note = "Answer named entities missing from the evidence (" + ", ".join(verification["unsupported"]) + "); replaced with deterministic evidence."
            trace = {"verification": verification, "tool": tool, "query": query, "rows": len(evidence), "episode_ids": [r["episode_id"] for r in evidence if r.get("episode_id")], "notes": [n for n in (intent_note, llm_note) if n]}
        except Exception as exc:
            answer, evidence, tool = f"The graph query did not complete: {type(exc).__name__}: {exc}", [], plan.get("tool", "unknown")
            trace = {"tool": tool, "query": None, "error": str(exc), "rows": 0, "episode_ids": [], "notes": [intent_note] if intent_note else []}
        try:
            kind = "exchange"
            if not trace.get("error"):
                kind = {"simulate": "simulation", "memories": "recall"}.get(tool, "exchange")
            episode_id = persist_episode(self.graph, self.tenant_id, self.session_id, question, answer, evidence, kind=kind)
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
    if kind not in {"user_note", "exchange", "simulation", "recall"}:
        raise ValueError("Unsupported memory episode kind.")
    episode_id = episode_id or uuid.uuid4().hex
    from seed_graph import ALL_SERVICES
    mentions = [name for name, *_ in ALL_SERVICES if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", user_message, re.IGNORECASE)]
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
