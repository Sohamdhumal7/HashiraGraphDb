"""Gemini-powered graph intelligence and analysis for HashiraGraph."""
from __future__ import annotations

import json
import os
from typing import Any

from graph_store import load_configuration


def get_gemini_client():
    """Retrieve an authenticated Google GenAI client if configured."""
    config = load_configuration()
    api_key = config.get("GEMINI_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None, None
    model = config.get("GEMINI_MODEL", "").strip() or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        return client, model
    except Exception:
        return None, None


def is_gemini_available() -> bool:
    """Check if Gemini API is configured and accessible."""
    client, _ = get_gemini_client()
    return client is not None


def generate_incident_playbook(failed_targets: list[str], affected: list[dict[str, Any]]) -> str:
    """Generate an on-call incident remediation playbook and mitigation plan based on simulated failure propagation."""
    client, model = get_gemini_client()
    if not client:
        return "Gemini API is not configured. Add GEMINI_API_KEY in .env to generate AI incident playbooks."

    prompt = f"""You are a Principal Site Reliability Engineer (SRE).
Analyze this simulated service outage from an enterprise dependency graph:

Failed Target Services: {', '.join(failed_targets)}
Impact Summary: {len(affected)} downstream services affected.

Downstream Dependencies Impacted:
{json.dumps(affected, indent=2, default=str)[:6000]}

Provide a concise, professional engineering response in standard markdown:
1. **Executive Impact Summary**: 2 sentences explaining customer impact.
2. **Immediate On-Call Incident Playbook**: 3 numbered triage steps for on-call engineers.
3. **Traffic Rerouting & Degraded Mode**: How dependent services should gracefully degrade (e.g. fallback caches, circuit breakers).
4. **Long-Term Architectural Recommendations**: 2 concrete architectural mitigations to eliminate this single point of failure (e.g. asynchronous queues, read replicas, bulkheads).

Keep it concise, technical, and actionable. Do not use emojis anywhere in your response.
"""
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
        return (response.text or "").strip()
    except Exception as exc:
        return f"Gemini incident analysis failed: {type(exc).__name__}: {exc}"


def generate_architecture_brief(tenant_id: str, node_count: int, rel_count: int, fragility: list[dict[str, Any]]) -> str:
    """Generate an executive architecture and resilience brief."""
    client, model = get_gemini_client()
    if not client:
        return "Gemini API is not configured. Add GEMINI_API_KEY in .env to generate architecture briefs."

    top_fragile = fragility[:5] if fragility else []
    prompt = f"""You are a Principal Enterprise Systems Architect.
Review the structural topology of the company's dependency graph:

Tenant: {tenant_id}
Total Knowledge Entities: {node_count} nodes
Total Directed Relationships: {rel_count} relationships
Top Fragile Services (Ranked by Downstream Blast Radius & Centrality):
{json.dumps(top_fragile, indent=2, default=str)}

Provide a concise, high-impact Architecture Brief in markdown:
1. **System Topology Assessment**: Structural resilience observation.
2. **Key Single Points of Failure**: Why the top-ranked services present vulnerability.
3. **Decoupling Strategy**: Concrete recommendation to reduce coupling and blast radius.

Keep it strictly technical, objective, and executive-ready. Do not use emojis anywhere in your response.
"""
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
        return (response.text or "").strip()
    except Exception as exc:
        return f"Gemini architecture brief failed: {type(exc).__name__}: {exc}"


def generate_adr_draft(service: str, proposal: str, related_dependencies: list[str]) -> str:
    """Draft a production-grade Architecture Decision Record (ADR)."""
    client, model = get_gemini_client()
    if not client:
        return "Gemini API is not configured. Add GEMINI_API_KEY in .env to draft ADRs."

    prompt = f"""You are a Staff Software Architect.
Draft an Architecture Decision Record (ADR) according to standard industry structure for:

Service: {service}
Architectural Proposal: {proposal}
Related Connected Dependencies: {', '.join(related_dependencies) if related_dependencies else 'None specified'}

Structure the document as:
- **Title**: ADR-[Timestamp]: [Clear Title]
- **Status**: Proposed
- **Context**: Problem statement and architectural drivers.
- **Decision**: The chosen technical approach and interface boundaries.
- **Consequences**: Positive tradeoffs, negative tradeoffs, and operational risks.
- **Verification & Rollback Strategy**: How to validate in staging and rollback safely.

Do not use emojis anywhere in your response.
"""
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
        return (response.text or "").strip()
    except Exception as exc:
        return f"Gemini ADR draft failed: {type(exc).__name__}: {exc}"
